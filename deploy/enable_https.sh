#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: sudo is requested by this script; run as your normal login user:
  ./deploy/enable_https.sh [--replace-existing-caddyfile] [homehub.home.arpa]

Configure Caddy for LAN HTTPS and keep Home Hub on loopback. Install the
official Caddy package and configure local DNS for the hostname first. The
replacement flag is required when /etc/caddy/Caddyfile already exists.
EOF
}

host=homehub.home.arpa
host_set=0
replace_existing_caddyfile=0
while (( $# )); do
  case $1 in
    --help|-h) usage; exit 0 ;;
    --replace-existing-caddyfile) replace_existing_caddyfile=1 ;;
    -*) usage >&2; exit 2 ;;
    *)
      if (( host_set )); then usage >&2; exit 2; fi
      host=$1
      host_set=1
      ;;
  esac
  shift
done
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/install_helpers.sh"

[[ $EUID -ne 0 ]] || { echo "Run this script as your normal user, not through sudo." >&2; exit 1; }
command -v sudo >/dev/null || { echo "sudo is required." >&2; exit 1; }
command -v caddy >/dev/null || {
  echo "Caddy is not installed. Install the official Caddy package first; see docs/TLS_DEPLOYMENT.md." >&2
  exit 1
}
sudo test -f /etc/homehub/homehub.env || {
  echo "Home Hub is not installed at /etc/homehub/homehub.env." >&2
  exit 1
}
sudo systemctl cat homehub.service >/dev/null || {
  echo "homehub.service is not installed." >&2
  exit 1
}

port=$(sudo awk -F= '/^HOMEHUB_PORT=/ { value=$2 } END { print value }' /etc/homehub/homehub.env)
port=${port:-8080}
caddyfile_tmp=$(mktemp)
trap 'rm -f -- "$caddyfile_tmp"' EXIT
homehub_render_caddyfile "$host" "$port" >"$caddyfile_tmp"

timestamp=$(date +%Y%m%d%H%M%S)
had_caddyfile=0
homehub_require_caddyfile_replacement \
  /etc/caddy/Caddyfile "$replace_existing_caddyfile"
if sudo test -f /etc/caddy/Caddyfile; then
  sudo cp -a /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.homehub-backup-$timestamp"
  had_caddyfile=1
fi
sudo install -o root -g root -m 0644 "$caddyfile_tmp" /etc/caddy/Caddyfile
if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
  if (( had_caddyfile )); then
    sudo cp -a "/etc/caddy/Caddyfile.homehub-backup-$timestamp" /etc/caddy/Caddyfile
  else
    sudo rm -f /etc/caddy/Caddyfile
  fi
  echo "Caddy configuration validation failed; the previous configuration was restored." >&2
  exit 1
fi

homehub_update_environment_file /etc/homehub/homehub.env homehub sudo \
  HOMEHUB_BIND=127.0.0.1 HOMEHUB_COOKIE_SECURE=1
sudo systemctl restart homehub.service
sudo systemctl enable --now caddy
sudo systemctl restart caddy

cat <<EOF
LAN HTTPS is configured for https://$host
Home Hub remains available to Caddy through 127.0.0.1:$port.
Create a local DNS entry for $host and trust Caddy's local root certificate
on each household device as described in docs/TLS_DEPLOYMENT.md.
Allow LAN access to Caddy ports 80/443 as desired; do not forward ports
from the Internet or expose Home Hub's upstream port.
EOF
