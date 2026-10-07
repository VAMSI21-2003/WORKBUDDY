#!/usr/bin/env bash
# Reviewer probes of deploy/ on this machine's Docker, with the LLM faked (fake_groq.py).
#     scripts/review_harness/feat-release-safety/deploy_probes.sh SCRATCH [REV]
# SCRATCH: an empty scratch folder (the stack runs from a clone there, never from this checkout).
# REV: the commit under review (default HEAD). Output goes to SCRATCH/out/ (copy what you keep
# into out/ next to this script).
#
# WARNING: the stack's volume names are fixed (workbuddy-data, workbuddy-caddy-*). Save any
# volume you care about first, e.g.:
#   docker run --rm -v workbuddy-data:/data:ro -v "$PWD":/o python:3.11-slim tar -C /data -czf /o/saved.tgz .
set -uo pipefail
S="${1:?scratch folder}"; REV="${2:-HEAD}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/../../.." && pwd)"
mkdir -p "$S/out"; OUT="$S/out"
export WORKBUDDY_PIP_CA="${WORKBUDDY_PIP_CA:-/root/.ccr/ca-bundle.crt}"

# -- the stack: a clone at REV, the fake LLM, placeholder settings -------------------------------
git clone -q "$REPO" "$S/stack" && cd "$S/stack" && git checkout -q "$(git -C "$REPO" rev-parse "$REV")"
cp "$HERE/review-override.yml" deploy/docker-compose.override.yml
cp "$HERE/fake_groq.py" deploy/
printf 'WORKBUDDY_DOMAIN=localhost\nWORKBUDDY_DEMO_PASSWORD=review-probe-password-1234\nGROQ_API_KEY=placeholder-not-a-real-key\n' > deploy/.env
chmod 600 deploy/.env
git config user.email reviewer@example.invalid; git config user.name reviewer
base="$(git rev-parse HEAD)"

# -- probe commits (local to the scratch clone, never pushed) ------------------------------------
git checkout -q -b review-probes
sed -i '1i raise SystemExit("review probe: this release cannot start")' backend/main.py
git commit -qam "probe: app cannot start"; BAD_START="$(git rev-parse HEAD)"; git checkout -q "$base" -- backend/main.py
sed -i 's/^RUN useradd/RUN false \&\& useradd/' Dockerfile
git commit -qam "probe: build fails"; BAD_BUILD="$(git rev-parse HEAD)"; git checkout -q "$base" -- Dockerfile
python3 - <<'EOF'
p = "skills/excel/dedupe.py"
s = open(p).read()
s = s.replace("    def execute(self, task: SkillTask) -> SkillResult:\n",
              "    def execute(self, task: SkillTask) -> SkillResult:\n        raise RuntimeError('probe')\n", 1)
open(p, "w").write(s)
EOF
git commit -qam "probe: dedupe broken"; BAD_TURN="$(git rev-parse HEAD)"; git checkout -q "$base" -- skills/excel/dedupe.py
echo "# probe" >> README.md; git commit -qam "probe: good release"; GOOD2="$(git rev-parse HEAD)"

state() {
  echo "   current.env: $(cat deploy/current.env 2>/dev/null)"
  echo "   app: $(./deploy/compose.sh ps -a app --format '{{.Image}} {{.Status}}' 2>/dev/null)"
  echo "   healthz: $(curl -sk --max-time 5 https://localhost/healthz)"
}

# d01 first release (fresh volume), d02 the same commit again
docker volume rm workbuddy-data >/dev/null 2>&1
./deploy/release.sh "$base" > "$OUT/d01_first_release.txt" 2>&1; echo "d01 exit=$?"
./deploy/release.sh "$base" > "$OUT/d02_same_again.txt" 2>&1; echo "d02 exit=$?"

# d03 turns through Caddy, two modes, odd files, a rejected plan (relaxed upload/turn limits)
printf 'WORKBUDDY_RATE_UPLOADS_BURST=200\nWORKBUDDY_RATE_TURNS_BURST=200\n' >> deploy/.env
./deploy/compose.sh up -d >/dev/null 2>&1; sleep 15
python3 "$HERE/make_files.py" "$S/files"
for m in friend focus; do
  for f in deploy/smoke.xlsx "$S"/files/*.xlsx; do
    python3 "$HERE/stack_turn.py" --stack "$S/stack" --mode "$m" --file "$f" --label "t-$m-$(basename "$f")" | tail -1
  done
  python3 "$HERE/stack_turn.py" --stack "$S/stack" --mode "$m" --file deploy/smoke.xlsx --decision reject --label "t-$m-reject" | tail -1
done > "$OUT/stack_turns.jsonl"
sed -i '/^WORKBUDDY_RATE_/d' deploy/.env; ./deploy/compose.sh up -d >/dev/null 2>&1; sleep 12

# d04 check-proxy with hops 1 (as shipped), then the controls 0 and 2
./deploy/check-proxy.sh > "$OUT/d04_checkproxy.txt" 2>&1; echo "d04 exit=$?"
cp deploy/docker-compose.override.yml "$S/override.bak"
for hops in 0 2; do
  sed "s|GROQ_BASE_URL: http://fakegroq:8080|GROQ_BASE_URL: http://fakegroq:8080\n      WORKBUDDY_TRUSTED_PROXY_HOPS: \"$hops\"|" "$S/override.bak" > deploy/docker-compose.override.yml
  ./deploy/compose.sh up -d >/dev/null 2>&1; sleep 12
  ./deploy/check-proxy.sh > "$OUT/d04_checkproxy_hops$hops.txt" 2>&1; echo "d04 hops=$hops exit=$?"
done
cp "$S/override.bak" deploy/docker-compose.override.yml; ./deploy/compose.sh up -d >/dev/null 2>&1; sleep 12

# d05 backup, restore, corrupt archive; d06 restore the OLDEST backup at the retention limit (H1)
./deploy/backup.sh > "$OUT/d05_backup.txt" 2>&1
./deploy/smoke.sh >/dev/null 2>&1
WORKBUDDY_BACKUP_KEEP=2 ./deploy/backup.sh >> "$OUT/d05_backup.txt" 2>&1
oldest="$(ls -1tr deploy/backups/*.tar.gz | head -1)"
WORKBUDDY_BACKUP_KEEP=2 WORKBUDDY_YES=1 ./deploy/restore.sh "$oldest" > "$OUT/d06_restore_oldest.txt" 2>&1
echo "d06 exit=$? asked-for archive is now: $(stat -c %F "$oldest")" | tee -a "$OUT/d06_restore_oldest.txt"
echo "sessions in /data: $(docker run --rm -v workbuddy-data:/data:ro python:3.11-slim sh -c 'ls /data/sessions 2>/dev/null | wc -l')" | tee -a "$OUT/d06_restore_oldest.txt"
[ -d "$oldest" ] && rmdir "$oldest"
newest="$(ls -1t deploy/backups/*.tar.gz | head -1)"; WORKBUDDY_YES=1 ./deploy/restore.sh "$newest" >/dev/null 2>&1

# d07 releases that fail: build, start, smoke; then a good one
for c in BAD_BUILD BAD_START BAD_TURN GOOD2; do
  ./deploy/release.sh "${!c}" > "$OUT/d07_release_$c.txt" 2>&1; echo "d07 $c exit=$?"; state
done > "$OUT/d07_release_sequence.txt"

# d08 the first release fails (LLM down), then the owner reruns the same command (M1)
./deploy/compose.sh down >/dev/null 2>&1; rm -f deploy/current.env deploy/previous.env
{ FAKE_MODE=error500 ./deploy/release.sh "$base"; echo "exit=$?"; state
  FAKE_MODE=ok ./deploy/release.sh "$base"; echo "exit=$?"; state; } > "$OUT/d08_first_release_fails.txt" 2>&1
rm -f deploy/current.env; ./deploy/release.sh "$base" >/dev/null 2>&1

# d09 a demo password with a $ in deploy/.env (M2)
cp deploy/.env "$S/env.good"
sed -i 's/^WORKBUDDY_DEMO_PASSWORD=.*/WORKBUDDY_DEMO_PASSWORD=Correct$horse-battery-staple-91/' deploy/.env
{ ./deploy/release.sh "$GOOD2"; echo "exit=$?"; } > "$OUT/d09_dollar_password.txt" 2>&1
cp "$S/env.good" deploy/.env; ./deploy/compose.sh up -d >/dev/null 2>&1

# d10 Caddy's access log holds session ids (M3)
./deploy/compose.sh logs caddy 2>/dev/null | grep -c 'sessions/s_' > "$OUT/d10_caddy_session_ids.txt"
./deploy/compose.sh logs app 2>/dev/null | grep -c 's_[A-Za-z0-9_-]\{22\}' >> "$OUT/d10_caddy_session_ids.txt"
echo "done: $OUT"
