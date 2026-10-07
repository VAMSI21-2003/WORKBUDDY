#!/usr/bin/env bash
# Put one commit live, and take it back by itself if it doesn't work.
#     ./release.sh <commit>        # a commit id, a branch or a tag of this repository (git fetch first)
#
# 1. build the image from that commit, tagged with the commit id   (a failure here changes nothing)
# 2. back up /data                       (a failure here stops everything; nothing has changed)
# 3. start it                            \
# 4. wait until it reports healthy        > any failure: roll back to the previous image
# 5. run smoke.sh (a real turn)          /
# The previous image is kept until the next release, so rolling back needs no download or build.
# It refuses to run if deploy/ in this checkout differs from deploy/ in that commit (the compose
# file, the Caddyfile and these scripts come from the checkout, the image from the commit): check
# the commit out first (git pull, or git checkout <commit>).
# A release replaces the app container: the site is down for a few seconds while it starts.
# Rolling back puts the old image back; it doesn't restore /data (see docs/deploy.md, "Roll back").
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ $# -eq 1 ] || { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
need docker git curl jq
docker compose version >/dev/null 2>&1 || die "docker compose isn't available (install.sh installs it)"
require_settings

full="$(git -C "$REPO_DIR" rev-parse --verify --quiet "$1^{commit}")" || die "'$1' isn't a commit in this repository (did you run git fetch?)"
tag="${full:0:12}"
previous="$(env_value "$DEPLOY_DIR/current.env" WORKBUDDY_TAG)"
log() { printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$tag" "$*" >> "$DEPLOY_DIR/releases.log"; }

if ! git -C "$REPO_DIR" diff --quiet "$full" -- deploy/; then
  die "deploy/ in this checkout differs from deploy/ in $tag, and a release runs the checkout's deploy files. Check that commit out first (git pull, or git checkout $tag), then run this again. Nothing was changed."
fi
if [ "$previous" = "$tag" ]; then
  if app_is_healthy; then
    say "$tag is already the live release, and it's running and healthy. Nothing to do. (./smoke.sh checks it again.)"
    exit 0
  fi
  say "$tag is recorded as the live release but the app isn't running healthy; releasing it again."
  previous=""  # there is no other release to go back to
fi
if [ -n "$previous" ] && ! docker image inspect "workbuddy:$previous" >/dev/null 2>&1; then
  say "WARNING: the live release ($previous) has no image on this machine, so a failed release can't be rolled back."
  previous=""
fi
say "Releasing $tag${previous:+ (live now: $previous)}"

step "1/5 build the image"
build=(docker build --quiet --tag "workbuddy:$tag" --label "workbuddy.commit=$full")
[ -z "${WORKBUDDY_PIP_CA:-}" ] || build+=(--secret "id=pip_ca,src=$WORKBUDDY_PIP_CA")
if ! git -C "$REPO_DIR" archive --format=tar "$full" | "${build[@]}" - >/dev/null; then
  log "aborted: build failed"
  die "the build failed. Nothing changed: the live release${previous:+ ($previous)} is still running, and no backup was taken."
fi
say "built workbuddy:$tag"

step "2/5 backup"
if docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1; then
  # Read the volume with the live release's image if it's still here, else with the new one.
  reader="workbuddy:$tag"
  [ -z "$previous" ] || reader="workbuddy:$previous"
  WORKBUDDY_BACKUP_IMAGE="$reader" "$DEPLOY_DIR/backup.sh" \
    || { log "aborted: backup failed"; die "the backup failed, so the release didn't start. The live release is untouched."; }
else
  say "no data yet: nothing to back up"
fi

rollback() {  # rollback "what failed"
  local failed="$1"
  printf '\n!! RELEASE %s FAILED at: %s\n' "$tag" "$failed"
  if [ -z "$previous" ]; then
    say "There was no earlier release to go back to. Stopping the app so a broken one isn't left serving."
    compose stop app >/dev/null 2>&1 || true
    rm -f "$DEPLOY_DIR/current.env"  # nothing is live: a rerun must do the whole release
    log "FAILED at $failed; no previous release; app stopped"
    exit 1
  fi
  say "Rolling back to $previous ..."
  printf 'WORKBUDDY_TAG=%s\n' "$previous" > "$DEPLOY_DIR/current.env"
  if compose up -d --remove-orphans >/dev/null 2>&1 && wait_healthy 180; then
    if "$DEPLOY_DIR/smoke.sh"; then
      printf '\n!! ROLLED BACK to %s. Release %s failed at: %s. The site is serving the previous release.\n' "$previous" "$tag" "$failed"
      say "The backup taken before this release is in $BACKUP_DIR (restore it only if the data looks wrong)."
      log "FAILED at $failed; rolled back to $previous; smoke ok"
      exit 1
    fi
    printf '\n!! ROLLED BACK to %s and it is healthy, but its smoke test failed too. Look at: ./compose.sh logs --tail 100 app\n' "$previous"
    log "FAILED at $failed; rolled back to $previous; smoke FAILED"
    exit 2
  fi
  printf '\n!! ROLLBACK FAILED: %s did not come back healthy. The site is down. Look at: ./compose.sh logs --tail 100 app\n' "$previous"
  log "FAILED at $failed; ROLLBACK FAILED"
  exit 2
}

step "3/5 start it"
if [ -f "$DEPLOY_DIR/current.env" ]; then cp "$DEPLOY_DIR/current.env" "$DEPLOY_DIR/previous.env"; fi
printf 'WORKBUDDY_TAG=%s\n' "$tag" > "$DEPLOY_DIR/current.env"
compose up -d --remove-orphans || rollback "starting the containers"

step "4/5 wait until it's healthy"
wait_healthy 180 || { compose logs --tail 40 app 2>&1 | sed 's/^/   app: /'; rollback "the health check"; }
say "healthy"

step "5/5 smoke test (a real turn)"
"$DEPLOY_DIR/smoke.sh" || rollback "the smoke test"

# Keep this release and the one before it; remove older images.
for old in $(docker images workbuddy --format '{{.Tag}}' | grep -E '^[0-9a-f]{12}$' || true); do
  [ "$old" = "$tag" ] || [ "$old" = "$previous" ] || docker rmi "workbuddy:$old" >/dev/null 2>&1 || true
done
log "OK${previous:+ (was $previous)}"
printf '\nRELEASED %s. Roll back by hand with: ./release.sh %s\n' "$tag" "${previous:-<an older commit>}"
