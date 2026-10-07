#!/usr/bin/env bash
# Put a backup of /data back. This replaces everything now in /data (sessions, uploads, files).
#     ./restore.sh deploy/backups/workbuddy-data-20261007-031500.tar.gz
# What it does, in this order:
#   1. copies the archive to a private temporary file (so nothing that follows can remove it);
#   2. takes a backup of what's in /data now, without deleting any older backup;
#   3. stops the app;
#   4. unpacks the archive next to the data, inside the volume; if that fails, /data is untouched
#      and the app is started again;
#   5. only then replaces /data's contents with it, and starts the app.
# Restoring goes into the same /data the image uses, which the saved records need: they hold
# absolute paths under /data.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { usage "${BASH_SOURCE[0]}"; exit 2; }
need docker tar
tar -tzf "$1" >/dev/null 2>&1 || die "$1 isn't a readable .tar.gz; nothing was changed"
tag="$(env_value "$DEPLOY_DIR/current.env" WORKBUDDY_TAG)"
image="${WORKBUDDY_BACKUP_IMAGE:-${tag:+workbuddy:$tag}}"
[ -n "$image" ] || die "no release is live; run ./release.sh <commit> first (it creates the volume)"

say "This replaces everything in /data with the contents of $1."
if [ "${WORKBUDDY_YES:-}" != "1" ]; then
  read -r -p "Type yes to go on: " answer
  [ "$answer" = "yes" ] || die "not confirmed; nothing changed"
fi

step "keep a private copy of the archive"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp -- "$1" "$work/backup.tar.gz"
tar -tzf "$work/backup.tar.gz" >/dev/null || die "the copy isn't readable; nothing was changed"

step "backup of what's there now (no older backup is deleted)"
pre="$(WORKBUDDY_BACKUP_PRUNE=0 "$DEPLOY_DIR/backup.sh" | tee /dev/stderr | sed -n 's/^backup: \(.*\) ([^()]*)$/\1/p')"
[ -n "$pre" ] && [ -f "$pre" ] || die "couldn't make the backup of the current data; nothing was changed"
undo="./deploy/restore.sh '$pre'"

step "stop the app"
compose stop app

helper() {  # helper SCRIPT: run SCRIPT (sh) as root in a throwaway container with /data and the archive
  docker run --rm --user 0 --network none -v "$DATA_VOLUME:/data" \
    --mount "type=bind,src=$work/backup.tar.gz,dst=/backup.tar.gz,readonly" \
    --entrypoint sh "$image" -c "$1"
}

step "unpack the archive beside the data"
# shellcheck disable=SC2016  # the script is for the container's shell, not this one
if ! helper 'set -e
  rm -rf /data/.restore-staging && mkdir /data/.restore-staging
  tar -xzf /backup.tar.gz -C /data/.restore-staging
  chown -R 10001:10001 /data/.restore-staging'; then
  helper 'rm -rf /data/.restore-staging' >/dev/null 2>&1 || true
  compose up -d >/dev/null 2>&1 || true
  die "unpacking the archive failed (not enough disk space, or a damaged archive). /data is as it was, and the app was started again."
fi

step "replace /data"
# shellcheck disable=SC2016  # the script is for the container's shell, not this one
if ! helper 'set -e
  cd /data
  for entry in $(ls -A); do [ "$entry" = ".restore-staging" ] || rm -rf -- "$entry"; done
  cd /data/.restore-staging
  for entry in $(ls -A); do mv -- "$entry" /data/; done
  cd /data && rmdir /data/.restore-staging'; then
  printf '\nERROR: replacing /data failed halfway. The app is stopped.\nThe data as it was before this restore is in:\n  %s\nPut it back with:\n  %s\n' "$pre" "$undo" >&2
  exit 2
fi

step "start the app"
compose up -d
wait_healthy 180 || die "the app didn't come back healthy: ./compose.sh logs --tail 100 app. To undo this restore: $undo"
say "Restored. Check it with ./smoke.sh, and open the page. The data from before is in $pre"
