#!/usr/bin/env bash
# First-time setup of a fresh Ubuntu 24.04 VM: Docker, a swap file, and the firewall.
#     sudo ./install.sh
# Safe to run again: each step checks what's already there. It does not start WorkBuddy; the last
# lines say what to do next (fill in .env, then ./release.sh <commit>).
#
# What it changes on the machine:
#   docker   installs Docker Engine, its compose and buildx plugins, from Docker's own apt
#            repository, plus curl, jq and ufw
#   swap     makes a 2 GB swap file (/swapfile) if the machine has no swap, so a memory spike
#            slows the app instead of killing it (SWAP_SIZE=4G ./install.sh picks another size)
#   firewall allows incoming SSH (22) and web traffic (80, 443) and nothing else
#   user     lets the user who ran sudo use docker without sudo (they must log in again), and
#            creates deploy/.env from the template
# INSTALL_STEPS="docker swap" runs only the named steps (docker swap firewall user).
set -euo pipefail

SWAP_SIZE="${SWAP_SIZE:-2G}"
STEPS="${INSTALL_STEPS:-docker swap firewall user}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
wants() { case " $STEPS " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

[ "$(id -u)" -eq 0 ] || die "run it with sudo:  sudo ./install.sh"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ] \
    || say "WARNING: this was written for Ubuntu 24.04; this is ${PRETTY_NAME:-unknown}. Carrying on."
fi

install_docker() {
  step "Docker"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
     && docker buildx version >/dev/null 2>&1; then
    say "Docker, compose and buildx are already installed"
  else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat > /etc/apt/sources.list.d/docker.sources <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  apt-get install -y curl jq ufw
  systemctl enable --now docker
  docker --version
  docker compose version
}

make_swap() {
  step "Swap"
  if [ -n "$(swapon --show --noheadings)" ]; then
    say "the machine already has swap:"; swapon --show
    return
  fi
  if [ ! -e /swapfile ]; then
    fallocate -l "$SWAP_SIZE" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # Use swap only when memory is nearly full.
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-workbuddy.conf
  sysctl -p /etc/sysctl.d/99-workbuddy.conf >/dev/null
  say "swap on: $SWAP_SIZE (/swapfile)"
}

set_firewall() {
  step "Firewall"
  # SSH first, so turning the firewall on can't lock you out of this session.
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
  ufw default deny incoming
  ufw default allow outgoing
  ufw --force enable
  ufw status verbose
  say "Note: a cloud provider's own firewall (on Azure, the network security group) is separate and must also allow 80 and 443 (docs/deploy.md, Appendix)."
}

set_up_user() {
  step "User and settings file"
  local who="${SUDO_USER:-}"
  if [ -n "$who" ] && [ "$who" != "root" ]; then
    usermod -aG docker "$who"
    say "$who can now use docker without sudo after logging out and in again"
  fi
  if [ ! -f "$here/.env" ]; then
    cp "$here/.env.example" "$here/.env"
    chmod 600 "$here/.env"
    [ -z "$who" ] || [ "$who" = "root" ] || chown "$who:$who" "$here/.env"
    say "created $here/.env from the template; fill it in"
  else
    say "$here/.env already exists; left as it is"
  fi
}

wants docker   && install_docker
wants swap     && make_swap
wants firewall && set_firewall
wants user     && set_up_user

cat <<'NEXT'

Done. Next:
  1. Log out and back in (so docker works without sudo).
  2. Edit deploy/.env: nano deploy/.env   (the name, the Google client, the operator token, the key;
     docs/deploy.md, Section 7, step 6 says how to create the Google client)
  3. From the repository folder:  ./deploy/release.sh <commit>
NEXT
