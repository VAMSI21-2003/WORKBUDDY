#!/usr/bin/env bash
# Put a backup of /data back. This replaces everything now in /data (sessions, uploads, files).
#     ./restore.sh deploy/backups/workbuddy-data-20261007-031500.tar.gz
# It first takes a backup of what's there now, stops the app, empties the volume, unpacks the
# archive, and starts the app again. Restoring goes into the same /data the image uses, which the
# saved records need: they hold absolute paths under /data.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
need docker tar
archive="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
tar -tzf "$archive" >/dev/null || die "$archive isn't a readable .tar.gz"
tag="$(env_value "$DEPLOY_DIR/current.env" WORKBUDDY_TAG)"
[ -n "$tag" ] || die "no release is live; run ./release.sh <commit> first (it creates the volume)"

say "This replaces everything in /data with the contents of $archive."
if [ "${WORKBUDDY_YES:-}" != "1" ]; then
  read -r -p "Type yes to go on: " answer
  [ "$answer" = "yes" ] || die "not confirmed; nothing changed"
fi

step "backup of what's there now"
"$DEPLOY_DIR/backup.sh"
step "stop the app"
compose stop app
step "replace /data"
docker run --rm --user 0 --network none -v "$DATA_VOLUME:/data" -v "$archive:/backup.tar.gz:ro" \
  --entrypoint sh "workbuddy:$tag" -c \
  'find /data -mindepth 1 -delete && tar -xzf /backup.tar.gz -C /data && chown -R 10001:10001 /data'
step "start the app"
compose up -d
wait_healthy 180 || die "the app didn't come back healthy: ./compose.sh logs --tail 100 app"
say "Restored. Check it with ./smoke.sh, and open the page."
