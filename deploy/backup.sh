#!/usr/bin/env bash
# A dated archive of /data (the sessions, uploads and delivered files), keeping the last few.
#     ./backup.sh            -> deploy/backups/workbuddy-data-YYYYmmdd-HHMMSS.tar.gz
# WORKBUDDY_BACKUP_DIR picks another folder; WORKBUDDY_BACKUP_KEEP (default 7) how many to keep.
# Safe while the app runs: the app writes every file whole (a temp file, then a rename), so the
# archive holds each file as it was at one moment. It reads the volume through a throwaway
# container of the image that's live, so nothing but Docker is needed on the VM.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need docker tar
docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1 \
  || die "there's no data volume ($DATA_VOLUME) yet: nothing to back up"
tag="$(env_value "$DEPLOY_DIR/current.env" WORKBUDDY_TAG)"
image="${WORKBUDDY_BACKUP_IMAGE:-${tag:+workbuddy:$tag}}"
[ -n "$image" ] || die "no release is live yet, so there's no image to read the volume with"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
archive="$BACKUP_DIR/workbuddy-data-$stamp.tar.gz"
n=1
while [ -e "$archive" ]; do archive="$BACKUP_DIR/workbuddy-data-$stamp-$n.tar.gz"; n=$((n + 1)); done
partial="$archive.partial"

# tar exit 1 only means "a file changed while it was read" (the app is live); 2 or more is a failure.
status=0
docker run --rm --user 0 --network none -v "$DATA_VOLUME:/data:ro" --entrypoint tar "$image" \
  -C /data -czf - . > "$partial" || status=$?
if [ "$status" -ge 2 ]; then rm -f "$partial"; die "tar failed (exit $status); no backup was made"; fi
tar -tzf "$partial" >/dev/null || { rm -f "$partial"; die "the archive can't be read back; no backup was made"; }
mv "$partial" "$archive"
chmod 600 "$archive"

# Keep the newest BACKUP_KEEP archives.
# shellcheck disable=SC2012
ls -1t "$BACKUP_DIR"/workbuddy-data-*.tar.gz | tail -n +"$((BACKUP_KEEP + 1))" | while read -r old; do
  rm -f -- "$old"
done

say "backup: $archive ($(du -h "$archive" | cut -f1), $(tar -tzf "$archive" | grep -c '[^/]$') files)"
