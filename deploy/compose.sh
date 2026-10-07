#!/usr/bin/env bash
# docker compose for this stack, with the settings files added: ./compose.sh ps | logs -f app | ...
# .env holds the VM's settings (the name, the password, the key). current.env holds which release
# is live (WORKBUDDY_TAG); only release.sh writes it.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Refuse a .env that compose would read differently from the scripts (it would also print part of
# a password); see lib.sh.
# shellcheck source=lib.sh
. "$here/lib.sh"
check_env_syntax
files=(--env-file "$here/.env")
[ -f "$here/current.env" ] && files+=(--env-file "$here/current.env")
exec docker compose --project-directory "$here" "${files[@]}" "$@"
