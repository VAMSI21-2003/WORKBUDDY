# Shared by the deploy scripts (sourced, not run). Prints are plain text; no secret is ever printed.
# shellcheck disable=SC2034  # the variables are for the scripts that source this file
# shellcheck shell=bash

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$DEPLOY_DIR")"
DATA_VOLUME="${WORKBUDDY_DATA_VOLUME:-workbuddy-data}"
BACKUP_DIR="${WORKBUDDY_BACKUP_DIR:-$DEPLOY_DIR/backups}"
BACKUP_KEEP="${WORKBUDDY_BACKUP_KEEP:-7}"
LOG_DIR="${WORKBUDDY_LOG_DIR:-$DEPLOY_DIR/logs}"
LOG_KEEP="${WORKBUDDY_LOG_KEEP:-20}"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

compose() { "$DEPLOY_DIR/compose.sh" "$@"; }

# Print the comment block at the top of a script (its usage text): usage "$0"
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$1"; }

need() {
  local missing=()
  for tool in "$@"; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
  [ "${#missing[@]}" -eq 0 ] || die "missing on this machine: ${missing[*]} (install.sh installs them)"
}

# The value of NAME in a KEY=VALUE file, or nothing. Used to read the live release tag.
env_value() {
  local file="$1" name="$2"
  [ -f "$file" ] || return 0
  sed -n "s/^${name}=//p" "$file" | tail -n 1
}

# docker compose reads .env with its own rules (it expands $name, strips quotes, drops " #..."
# comments and trailing spaces), while the scripts read it literally. A value where the two
# readings differ would make the app's token or secret differ from the one smoke.sh uses, and
# compose prints the part after a $ as an unset variable. So such a value is refused, by the
# setting's name; its content is never printed.
check_env_syntax() {
  local file="$DEPLOY_DIR/.env" line name value bad=()
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ""|\#*) continue ;; esac
    name="${line%%=*}"
    value="${line#*=}"
    case "$value" in
      *'$'*|*'"'*|*"'"*|*$'\r'*|*'`'*|*' #'*|' '*|*' '|*$'\t'*) bad+=("$name") ;;
    esac
  done < "$file"
  [ "${#bad[@]}" -eq 0 ] \
    || die "deploy/.env: the value of ${bad[*]} has a \$, a quote, a backtick, a space at an end or before a #, a tab or a carriage return, which Docker reads differently from the scripts. Choose a value without those (docs/deploy.md, Section 7, step 6)"
}

# Names in .env that must be set (non-empty). Says which are missing, never what they hold.
require_settings() {
  local file="$DEPLOY_DIR/.env" name missing=()
  [ -f "$file" ] || die "deploy/.env doesn't exist. Run: cp deploy/.env.example deploy/.env  and fill it in"
  check_env_syntax
  for name in WORKBUDDY_DOMAIN WORKBUDDY_GOOGLE_CLIENT_ID WORKBUDDY_GOOGLE_CLIENT_SECRET \
    WORKBUDDY_OPERATOR_TOKEN GROQ_API_KEY; do
    [ -n "$(env_value "$file" "$name")" ] || missing+=("$name")
  done
  [ "${#missing[@]}" -eq 0 ] || die "deploy/.env has no value for: ${missing[*]}"
}

# Whether the app container is running and healthy right now (no waiting).
app_is_healthy() {
  local id state
  id="$(service_container app)"
  [ -n "$id" ] || return 1
  state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null || true)"
  [ "$state" = "running healthy" ]
}

# The container id of a compose service, or nothing.
service_container() { compose ps -q "$1" 2>/dev/null | head -n 1; }

# Wait until the app container reports healthy (the image's /healthz check). Returns 1 on timeout
# or if the container stops.
wait_healthy() {
  local timeout="${1:-180}" waited=0 id state
  while [ "$waited" -lt "$timeout" ]; do
    id="$(service_container app)"
    if [ -n "$id" ]; then
      state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null || true)"
      case "$state" in
        "running healthy") return 0 ;;
        exited*|dead*|restarting*) say "the app container stopped ($state)"; return 1 ;;
      esac
    fi
    sleep 3; waited=$((waited + 3))
  done
  say "the app wasn't healthy after ${timeout}s"
  return 1
}

# Save the app container's logs before a release replaces it (Docker deletes a container's logs
# with it). save_app_logs <commit the container runs> <image holding the log filter>
#   -> deploy/logs/app-<commit>-<UTC timestamp>.log   (folder 700, file 600, newest LOG_KEEP kept)
# The logs are passed through the app's own logging filter (backend/logging_filter.py, run from the
# given image) before they are kept, so a key, a token or a session id in a path is masked even if
# one reached a log line; if the filter can't run, nothing is kept. Best effort: it never stops
# a release, and says what happened. The folder is git-ignored.
save_app_logs() {
  local commit="${1:-unknown}" image="$2" id stamp file partial n=1
  id="$(service_container app)"
  if [ -z "$id" ]; then say "logs: no app container to save logs from"; return 0; fi
  (umask 077; mkdir -p "$LOG_DIR") && chmod 700 "$LOG_DIR" 2>/dev/null \
    || { say "WARNING: logs: couldn't make $LOG_DIR; the old logs are not saved"; return 0; }
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  file="$LOG_DIR/app-$commit-$stamp.log"
  while [ -e "$file" ]; do file="$LOG_DIR/app-$commit-$stamp-$n.log"; n=$((n + 1)); done
  partial="$file.partial"
  if (umask 077; docker logs "$id" 2>&1 \
        | docker run --rm -i --network none --entrypoint python "$image" -m backend.logging_filter \
        > "$partial") && [ -s "$partial" ]; then
    chmod 600 "$partial" && mv "$partial" "$file"
    say "logs: saved $file ($(wc -l < "$file" | tr -d ' ') lines)"
  else
    rm -f -- "$partial"
    say "WARNING: logs: the old logs were not saved (they were empty, or the filter couldn't run)"
    return 0
  fi
  # Keep the newest LOG_KEEP files.
  # shellcheck disable=SC2012
  ls -1t "$LOG_DIR"/app-*.log 2>/dev/null | tail -n +"$((LOG_KEEP + 1))" | while read -r old; do
    rm -f -- "$old"
  done
  return 0
}
