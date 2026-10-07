#!/usr/bin/env bash
# Checklist item 7 (docs/deploy.md, Section 5), for this stack: with WORKBUDDY_TRUSTED_PROXY_HOPS
# set as docker-compose.yml sets it, two clients behind Caddy get separate rate-limit buckets, and a
# spoofed X-Forwarded-For changes nobody's.
#     ./check-proxy.sh
# It starts three throwaway containers on a temporary network that Caddy joins for the check, so
# each has its own address, and sends wrong-password sign-ins (which cost only the "access" bucket:
# 10 in a burst, then 429). Nothing is created; the running site isn't touched. Each run picks fresh
# addresses, so running it twice in a row is fine. Takes about 15 seconds.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need docker jq
tag="$(env_value "$DEPLOY_DIR/current.env" WORKBUDDY_TAG)"
domain="$(env_value "$DEPLOY_DIR/.env" WORKBUDDY_DOMAIN)"
[ -n "$tag" ] && [ -n "$domain" ] || die "no release is live (./release.sh <commit> first)"
image="workbuddy:$tag"
caddy="$(service_container caddy)"
[ -n "$caddy" ] || die "Caddy isn't running"

net="wb-proxycheck-$$"
clients=()
cleanup() {
  for c in "${clients[@]}"; do docker rm -f "$c" >/dev/null 2>&1 || true; done
  docker network disconnect "$net" "$caddy" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for _ in 1 2 3 4 5; do  # a random private /24 that isn't already in use
  prefix="10.$((RANDOM % 50 + 200)).$((RANDOM % 250 + 1))"
  docker network create --subnet "$prefix.0/24" "$net" >/dev/null 2>&1 && break
  prefix=""
done
[ -n "$prefix" ] || die "couldn't create a temporary network"
docker network connect "$net" "$caddy"
caddy_ip="$(docker inspect --format "{{(index .NetworkSettings.Networks \"$net\").IPAddress}}" "$caddy")"

cafile=""
if [ "$domain" = "localhost" ]; then  # the trial stack's own authority (see smoke.sh)
  cafile="$(mktemp)"; trap 'rm -f "$cafile"; cleanup' EXIT
  docker cp "$caddy:/data/caddy/pki/authorities/local/root.crt" "$cafile" >/dev/null
  chmod 644 "$cafile"  # the clients run as the app user
fi

start_client() {  # start_client LETTER LASTOCTET
  local name="wb-proxycheck-$$-$1"
  docker run -d --name "$name" --network "$net" --ip "$prefix.$2" --add-host "$domain:$caddy_ip" \
    --entrypoint sleep "$image" 600 >/dev/null
  clients+=("$name")
  [ -z "$cafile" ] || docker cp "$cafile" "$name:/ca.crt" >/dev/null
}
start_client a 11; start_client b 12; start_client c 13
A="${clients[0]}"; B="${clients[1]}"; C="${clients[2]}"
ip_a="$prefix.11"; ip_b="$prefix.12"; ip_c="$prefix.13"

send() {  # send CONTAINER COUNT [XFF]  -> JSON list of statuses
  docker exec -i "$1" python - "$domain" "$2" ${3:+"$3"} < "$DEPLOY_DIR/proxy_client.py"
}
failures=0
check() {  # check "what" "got" "expected jq test"
  if jq -e "$3" <<<"$2" >/dev/null; then printf 'PASS  %s  %s\n' "$1" "$2"
  else printf 'FAIL  %s  %s  (wanted: %s)\n' "$1" "$2" "$3"; failures=$((failures + 1)); fi
}

say "Caddy is at $caddy_ip; clients: A=$ip_a B=$ip_b C=$ip_c; hops set to $(docker exec "$(service_container app)" printenv WORKBUDDY_TRUSTED_PROXY_HOPS)"
r="$(send "$A" 14)"
check "A sends 14 wrong sign-ins: the first 10 are answered, then A is limited" "$r" '(.[:10] | all(. == 401)) and (.[10:] | length > 0 and all(. == 429))'
r="$(send "$B" 4)"
check "B (another address) is not limited by A's use" "$r" 'all(. == 401)'
r="$(send "$C" 3 "$ip_a")"
check "C claims to be A (X-Forwarded-For: A's address): A's bucket isn't charged to C" "$r" 'all(. == 401)'
r="$(send "$A" 2 "203.0.113.77")"
check "A claims to be somebody else: A still has its own, used-up bucket" "$r" 'all(. == 429)'
r="$(send "$A" 2 "203.0.113.78, 203.0.113.79")"
check "A sends a made-up chain of addresses: the same" "$r" 'all(. == 429)'
r="$(send "$B" 2 "203.0.113.80")"
check "B claims a fresh address: it neither gains nor loses anything" "$r" 'all(. == 401)'

if [ "$failures" -eq 0 ]; then printf '\nCHECK-PROXY OK: separate buckets, and a spoofed X-Forwarded-For changed nobody'"'"'s.\n'
else printf '\nCHECK-PROXY FAILED: %d check(s). WORKBUDDY_TRUSTED_PROXY_HOPS is probably wrong for this layout.\n' "$failures"; exit 1; fi
