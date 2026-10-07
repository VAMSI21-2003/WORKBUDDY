#!/usr/bin/env bash
# The health check, then one real turn through the API, the way the page does it: sign in, upload,
# send a message, approve the plan, download the file, and check its hash.
#     ./smoke.sh                 # against https://$WORKBUDDY_DOMAIN, from deploy/.env
#     ./smoke.sh https://host    # against another address
# It uses the real LLM, so it needs the key to work. It reads the password from deploy/.env and
# never prints it. One run uses 1 sign-in, 1 upload and 1 turn from the per-address rate limits
# (10, 5 and 10 an hour), so a handful of runs an hour is fine.
# On this machine's own stack the request goes to 127.0.0.1 whatever DNS says, keeping the name for
# the certificate check (SMOKE_RESOLVE_LOCAL=0 turns that off). A stack named "localhost" (a trial on
# one machine) has a certificate from Caddy's own authority, which smoke.sh fetches from the Caddy
# container and trusts for this run only; SMOKE_CACERT=file trusts another one instead.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need curl jq sha256sum docker
check_env_syntax
ENV_FILE="$DEPLOY_DIR/.env"
domain="$(env_value "$ENV_FILE" WORKBUDDY_DOMAIN)"
password="$(env_value "$ENV_FILE" WORKBUDDY_DEMO_PASSWORD)"
[ -n "$domain" ] && [ -n "$password" ] || die "deploy/.env needs WORKBUDDY_DOMAIN and WORKBUDDY_DEMO_PASSWORD"
base="${1:-https://$domain}"
origin="https://$domain"
fixture="$DEPLOY_DIR/smoke.xlsx"
[ -f "$fixture" ] || die "deploy/smoke.xlsx is missing"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
jar="$work/cookies"
curl_args=(--silent --show-error --max-time 60 --cookie "$jar" --cookie-jar "$jar")
cacert="${SMOKE_CACERT:-}"
if [ -z "$cacert" ] && [ "$domain" = "localhost" ]; then
  for _ in $(seq 1 15); do  # Caddy writes its authority's certificate as it starts
    docker cp "$(service_container caddy):/data/caddy/pki/authorities/local/root.crt" "$work/caddy-root.crt" \
      >/dev/null 2>&1 && cacert="$work/caddy-root.crt" && break
    sleep 2
  done
  [ -n "$cacert" ] || { echo "SMOKE FAILED at \"tls\": no certificate authority from the Caddy container" >&2; exit 1; }
fi
[ -z "$cacert" ] || curl_args+=(--cacert "$cacert")
if [ "$base" = "$origin" ] && [ "${SMOKE_RESOLVE_LOCAL:-1}" = "1" ]; then
  curl_args+=(--resolve "$domain:443:127.0.0.1")
fi

fail() { printf 'SMOKE FAILED at "%s": %s\n' "$1" "$2" >&2; exit 1; }
# api METHOD PATH [curl args...]: the response body on stdout; fails on any status but 2xx.
api() {
  local method="$1" path="$2"; shift 2
  local body="$work/body" status
  status="$(curl "${curl_args[@]}" --request "$method" --header "Origin: $origin" \
    --output "$body" --write-out '%{http_code}' "$@" "$base$path")" || fail "$method $path" "no answer from $base"
  case "$status" in 2*) cat "$body" ;; *) fail "$method $path" "HTTP $status $(head -c 200 "$body")" ;; esac
}

step "1. health"
health="$(api GET /healthz)"
[ "$(jq -r .ok <<<"$health")" = "true" ] || fail "health" "/healthz didn't say ok"
[ "$(jq -r .llm_configured <<<"$health")" = "true" ] || fail "health" "no LLM key is configured"
say "ok: /healthz answers, an LLM key is set"

step "2. sign in"
# The password goes in through a file, not the command line, where other users could see it.
( umask 077; printf '%s' "$password" > "$work/password" )
api POST /api/v1/access --data-urlencode "password@$work/password" >/dev/null
grep -q "__Host-wb_access" "$jar" || fail "sign in" "no access cookie came back"
say "ok: the password was exchanged for the access cookie"

step "3. upload"
before="$(sha256sum "$fixture" | cut -d' ' -f1)"
session="$(api POST /api/v1/sessions --header 'Content-Type: application/json' --data '{"mode":"friend"}' | jq -r .session_id)"
[ -n "$session" ] && [ "$session" != "null" ] || fail "session" "no session id"
upload="$(api POST "/api/v1/sessions/$session/files" --form "file=@$fixture;filename=smoke.xlsx;type=application/octet-stream")"
[ "$(jq -r .sha256 <<<"$upload")" = "$before" ] || fail "upload" "the server's hash of the upload isn't the file's hash"
say "ok: uploaded smoke.xlsx; the server's hash matches ($before)"

step "4. a message, and the plan"
turn="$(api POST "/api/v1/sessions/$session/turns" --header 'Content-Type: application/json' \
  --data '{"message":"remove duplicate rows"}' | jq -r .turn_id)"
[ -n "$turn" ] && [ "$turn" != "null" ] || fail "turn" "no turn id"
deadline=$((SECONDS + 240))
approved=0
while :; do
  state="$(api GET "/api/v1/sessions/$session/turns/$turn")"
  status="$(jq -r .status <<<"$state")"
  if [ "$status" = "awaiting_confirmation" ] && [ "$approved" = 0 ]; then
    plan="$(jq -r .pending_confirmation.confirmation_id <<<"$state")"
    say "ok: the plan was shown: $(jq -c '[.pending_confirmation.steps[].capability]' <<<"$state")"
    api POST "/api/v1/sessions/$session/turns/$turn/confirmation" --header 'Content-Type: application/json' \
      --data "$(jq -nc --arg id "$plan" '{confirmation_id: $id, decision: "approve"}')" >/dev/null
    approved=1
  fi
  [ "$status" = "finished" ] && break
  [ "$SECONDS" -lt "$deadline" ] || fail "turn" "not finished after 240 s (status: $status)"
  sleep 2
done
outcome="$(jq -r .outcome <<<"$state")"
[ "$approved" = 1 ] || fail "plan" "the turn finished without showing a plan"
[ "$outcome" = "delivered" ] || fail "turn" "outcome is $outcome, not delivered. Reply: $(jq -r .reply <<<"$state" | head -c 300)"
say "ok: the turn finished, outcome delivered"

step "5. download and hash"
file_id="$(jq -r '.delivered_files[0].file_id' <<<"$state")"
want="$(jq -r '.delivered_files[0].sha256' <<<"$state")"
curl "${curl_args[@]}" --fail --output "$work/delivered.xlsx" "$base/api/v1/sessions/$session/files/$file_id/download" \
  || fail "download" "the delivered file wouldn't download"
got="$(sha256sum "$work/delivered.xlsx" | cut -d' ' -f1)"
[ "$got" = "$want" ] || fail "download" "the downloaded file's hash ($got) isn't the one the server recorded ($want)"
[ "$(head -c 2 "$work/delivered.xlsx")" = "PK" ] || fail "download" "the file isn't a workbook (no zip header)"
[ "$(sha256sum "$fixture" | cut -d' ' -f1)" = "$before" ] || fail "original" "the local original changed"
[ "$got" != "$before" ] || fail "download" "the delivered file is byte for byte the original"
say "ok: the download's hash matches what the server recorded ($got), and it's different from the original's"

printf '\nSMOKE OK: %s\n' "$base"
