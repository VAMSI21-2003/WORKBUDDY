#!/usr/bin/env bash
# Round 2 add-ons on the real deploy stack, run AFTER ../deploy_probes.sh SCRATCH (it reuses
# SCRATCH/stack, the scratch clone that script set up; the LLM is faked).
#     scripts/review_harness/feat-release-safety/round2/stack_round2.sh SCRATCH
# e01 restore the oldest archive with the folder at its limit (H1), then smoke and a turn
# e02 a release of a commit whose deploy/ differs from the checkout (M5): refused, nothing changed
# e03 the same commit again while the app is stopped (M2): released again, healthy
# e04 a $ in the password: compose.sh and release.sh refuse, no part of it printed (M3)
# e05 Caddy's log after turns: no session ids (M4)
set -uo pipefail
S="${1:?scratch folder}"; HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$S/out"; cd "$S/stack" || exit 1
appid() { docker ps -q --filter name=workbuddy-app; }
state() {
  echo "   current.env: $(cat deploy/current.env 2>/dev/null)"
  echo "   app: $(./deploy/compose.sh ps -a app --format '{{.Image}} {{.Status}}' 2>/dev/null) id=$(appid)"
  echo "   healthz: $(curl -sk --max-time 5 https://localhost/healthz)"
}
./deploy/compose.sh up -d >/dev/null 2>&1; sleep 10

# e00 the turns d03 couldn't run when the sign-in rate limit answered 429 (by design), after a pause
sleep 60
for f in "$S/files/twosheets.xlsx"; do
  python3 "$HERE/../stack_turn.py" --stack "$S/stack" --mode focus --file "$f" --label "e00-focus-twosheets" | tail -1
done > "$OUT/e00_turns.jsonl"
python3 "$HERE/../stack_turn.py" --stack "$S/stack" --mode focus --file deploy/smoke.xlsx --decision reject --label e00-focus-reject | tail -1 >> "$OUT/e00_turns.jsonl"

{ # e01
  rm -f deploy/backups/*.tar.gz
  WORKBUDDY_BACKUP_KEEP=2 ./deploy/backup.sh; sleep 1
  oldest="$(ls -1tr deploy/backups/*.tar.gz | head -1)"
  n_oldest="$(tar -tzf "$oldest" | grep -c '^./sessions/[^/]*/session.json$')"
  python3 "$HERE/../stack_turn.py" --stack "$S/stack" --mode focus --file deploy/smoke.xlsx --label e01-extra | tail -1
  WORKBUDDY_BACKUP_KEEP=2 ./deploy/backup.sh
  echo "backups before restore: $(ls deploy/backups | tr '\n' ' ')"
  WORKBUDDY_BACKUP_KEEP=2 WORKBUDDY_YES=1 ./deploy/restore.sh "$oldest"; echo "restore exit=$?"
  echo "asked-for archive is a file: $([ -f "$oldest" ] && echo yes || echo NO)"
  echo "sessions in archive=$n_oldest, in /data now=$(docker run --rm -v workbuddy-data:/data:ro python:3.11-slim sh -c 'ls /data/sessions | wc -l')"
  echo "owner of /data/sessions: $(docker run --rm -v workbuddy-data:/data:ro python:3.11-slim stat -c %u /data/sessions)"
  echo "staging left: $(docker run --rm -v workbuddy-data:/data:ro python:3.11-slim sh -c 'ls -A /data | tr "\n" " "')"
  ./deploy/smoke.sh >/dev/null 2>&1; echo "smoke after restore exit=$?"
  python3 "$HERE/../stack_turn.py" --stack "$S/stack" --mode friend --file deploy/smoke.xlsx --label e01-after | tail -1
} > "$OUT/e01_restore_oldest.txt" 2>&1; echo "e01 done"

{ # e02
  live_before="$(cat deploy/current.env)"; id_before="$(appid)"
  base="$(git rev-parse HEAD)"
  git checkout -q -b e02-probe
  echo "# probe change" >> deploy/Caddyfile; git commit -qam "probe: deploy change"; changed="$(git rev-parse HEAD)"
  git checkout -q "$base"
  ./deploy/release.sh "$changed"; echo "release exit=$?"
  echo "current.env unchanged: $([ "$(cat deploy/current.env)" = "$live_before" ] && echo yes || echo NO); app container unchanged: $([ "$(appid)" = "$id_before" ] && echo yes || echo NO)"
  git checkout -q "$base"
} > "$OUT/e02_deploy_differs.txt" 2>&1; echo "e02 done"

{ # e03
  live="$(sed -n 's/^WORKBUDDY_TAG=//p' deploy/current.env)"
  ./deploy/compose.sh stop app >/dev/null 2>&1; state
  ./deploy/release.sh "$live"; echo "release exit=$?"; state
  ./deploy/release.sh "$live"; echo "again exit=$?"
} > "$OUT/e03_same_commit_app_stopped.txt" 2>&1; echo "e03 done"

{ # e04
  cp deploy/.env "$S/env.good2"
  sed -i 's/^WORKBUDDY_DEMO_PASSWORD=.*/WORKBUDDY_DEMO_PASSWORD=Correct$horse-battery-staple-91/' deploy/.env
  ./deploy/compose.sh ps; echo "compose.sh exit=$?"
  ./deploy/release.sh HEAD; echo "release exit=$?"
  ./deploy/smoke.sh; echo "smoke exit=$?"
  cp "$S/env.good2" deploy/.env
} > "$OUT/e04_dollar_password.txt" 2>&1
echo "e04 'horse' printed: $(grep -c horse "$OUT/e04_dollar_password.txt") times" | tee -a "$OUT/e04_dollar_password.txt"

{ # e05
  echo "caddy log lines with a session id: $(./deploy/compose.sh logs caddy 2>/dev/null | grep -c 'sessions/s_')"
  echo "caddy log lines in total: $(./deploy/compose.sh logs caddy 2>/dev/null | wc -l)"
  echo "app log raw session ids: $(./deploy/compose.sh logs app 2>/dev/null | grep -c 's_[A-Za-z0-9_-]\{22\}')"
} > "$OUT/e05_logs.txt" 2>&1; cat "$OUT/e05_logs.txt"
