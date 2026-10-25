#!/usr/bin/env bash
set -Eeuo pipefail

# Run this from a normal user account. sudo is used only for system changes.
readonly INSTALL_ROOT=/opt/homehub
readonly DATA_ROOT=/var/lib/homehub
readonly CONFIG_ROOT=/etc/homehub
readonly ENV_FILE=/etc/homehub/homehub.env
readonly SERVICE_USER=homehub

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  printf 'Run this script as your regular Pi user; it will request sudo when needed.\n' >&2
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  printf 'sudo is required for system installation steps.\n' >&2
  exit 1
fi

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
for required in "$REPO_ROOT/homehub" "$REPO_ROOT/pyproject.toml" \
                "$SCRIPT_DIR/homehub.service.example" \
                "$SCRIPT_DIR/homehub-backup.service.example" \
                "$SCRIPT_DIR/homehub-backup.timer.example" \
                "$SCRIPT_DIR/homehub-rss-refresh.service.example" \
                "$SCRIPT_DIR/homehub-rss-refresh.timer.example"; do
  if [[ ! -e $required ]]; then
    printf 'Required project file is missing: %s\n' "$required" >&2
    exit 1
  fi
done

python_ok=0
if command -v python3 >/dev/null 2>&1 \
  && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  python_ok=1
fi
if (( ! python_ok )); then
  printf 'Home Hub requires Python 3.11 or newer. Install a supported Python on Raspberry Pi OS, then retry.\n' >&2
  exit 1
fi
if ! python3 -c 'import venv' >/dev/null 2>&1; then
  printf 'The Python venv module is missing. Install it with: sudo apt install python3-venv\n' >&2
  read -r -p 'Install python3-venv now with apt? [Y/n] ' answer
  if [[ $answer =~ ^[Nn]$ ]]; then exit 1; fi
  sudo -v
  sudo apt-get update
  sudo apt-get install -y python3-venv
fi

printf 'Home Hub will install into %s and keep data in %s.\n' "$INSTALL_ROOT" "$DATA_ROOT"
printf 'sudo may prompt for your Pi password during system changes.\n'
sudo -v

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  sudo useradd --system --user-group --home-dir "$DATA_ROOT" \
    --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
sudo install -d -o root -g root -m 0755 "$INSTALL_ROOT"
sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_ROOT"
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_ROOT"
sudo install -d -o root -g "$SERVICE_USER" -m 0750 "$CONFIG_ROOT"
if sudo systemctl cat homehub-backup.timer >/dev/null 2>&1; then
  sudo systemctl disable --now homehub-backup.timer
fi
if sudo systemctl is-active --quiet homehub-backup.service; then
  sudo systemctl stop homehub-backup.service
fi

if ! sudo test -f "$ENV_FILE"; then
  secret_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  env_tmp="$(mktemp)"
  trap 'rm -f -- "$env_tmp"' EXIT
  umask 077
  cat >"$env_tmp" <<EOF
HOMEHUB_SECRET_KEY=$secret_key
HOMEHUB_DB=$DATA_ROOT/homehub.sqlite3
HOMEHUB_BIND=127.0.0.1
HOMEHUB_PORT=8080
HOMEHUB_COOKIE_SECURE=0
EOF
  sudo install -o root -g "$SERVICE_USER" -m 0640 "$env_tmp" "$ENV_FILE"
  rm -f -- "$env_tmp"
  trap - EXIT
  unset secret_key
fi

as_homehub() {
  sudo -u "$SERVICE_USER" /bin/sh -c '
    set -a
    . /etc/homehub/homehub.env
    set +a
    cd /opt/homehub
    exec "$@"
  ' homehub "$@"
}

if sudo systemctl is-active --quiet homehub.service; then
  printf 'Stopping the existing service before replacing its code.\n'
  sudo systemctl stop homehub.service
fi

# Copy only runtime files. The source checkout, .git directory, local venv,
# database, and backups stay in the user's repository directory untouched.
sudo install -d -o root -g root -m 0755 "$INSTALL_ROOT/homehub"
sudo rm -rf -- "$INSTALL_ROOT/homehub"
sudo install -d -o root -g root -m 0755 "$INSTALL_ROOT/homehub"
sudo cp -a "$REPO_ROOT/homehub/." "$INSTALL_ROOT/homehub/"
sudo install -o root -g root -m 0644 "$REPO_ROOT/pyproject.toml" "$INSTALL_ROOT/pyproject.toml"
sudo install -o root -g root -m 0644 "$REPO_ROOT/README.md" "$INSTALL_ROOT/README.md"
sudo install -o root -g root -m 0644 "$REPO_ROOT/LICENSE" "$INSTALL_ROOT/LICENSE"
sudo chown -R root:root "$INSTALL_ROOT/homehub"

if [[ ! -x $INSTALL_ROOT/.venv/bin/python ]]; then
  sudo install -d -o root -g root -m 0755 "$INSTALL_ROOT/.venv"
  sudo python3 -m venv "$INSTALL_ROOT/.venv"
fi
sudo chown -R root:root "$INSTALL_ROOT/.venv"
sudo "$INSTALL_ROOT/.venv/bin/python" -m pip install \
  --disable-pip-version-check "$INSTALL_ROOT"

database_path="$(as_homehub "$INSTALL_ROOT/.venv/bin/python" -c \
  'import os; from pathlib import Path; print(os.environ.get("HOMEHUB_DB", Path.home()/".local/share/homehub/homehub.sqlite3"))')"
if [[ -f $database_path ]]; then
  printf 'Creating an immediate verified backup before Home Hub starts or migrates data.\n'
  as_homehub "$INSTALL_ROOT/.venv/bin/homehub-backup" --force
fi

admin_count="$(as_homehub "$INSTALL_ROOT/.venv/bin/python" -c '
import os, sqlite3
from pathlib import Path
path = Path(os.environ.get("HOMEHUB_DB", Path.home()/".local/share/homehub/homehub.sqlite3"))
if not path.exists():
    print(0)
else:
    with sqlite3.connect(path) as db:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type=? AND name=?", ("table", "users")).fetchone()
        print(db.execute("SELECT count(*) FROM users WHERE active=1 AND role=?", ("admin",)).fetchone()[0] if table else 0)
')"
if [[ $admin_count == 0 ]]; then
  printf 'Create the first Home Hub administrator now. Use a unique username and a password of at least 12 characters.\n'
  as_homehub "$INSTALL_ROOT/.venv/bin/flask" --app homehub.app:create_app create-admin
fi

sudo install -o root -g root -m 0644 "$SCRIPT_DIR/homehub.service.example" \
  /etc/systemd/system/homehub.service
sudo install -o root -g root -m 0644 "$SCRIPT_DIR/homehub-backup.service.example" \
  /etc/systemd/system/homehub-backup.service
sudo install -o root -g root -m 0644 "$SCRIPT_DIR/homehub-backup.timer.example" \
  /etc/systemd/system/homehub-backup.timer
sudo install -o root -g root -m 0644 "$SCRIPT_DIR/homehub-rss-refresh.service.example" \
  /etc/systemd/system/homehub-rss-refresh.service
sudo install -o root -g root -m 0644 "$SCRIPT_DIR/homehub-rss-refresh.timer.example" \
  /etc/systemd/system/homehub-rss-refresh.timer

# Remove legacy Home Hub cron entries so only the systemd timer schedules backups.
for cron_user in root "$SERVICE_USER"; do
  if old_crontab="$(sudo crontab -u "$cron_user" -l 2>/dev/null)"; then
    new_crontab="$(printf '%s\n' "$old_crontab" | awk '
      index($0, "homehub-backup") == 0 && index($0, "backup_scheduler") == 0')"
    if [[ $new_crontab != "$old_crontab" ]]; then
      printf '%s\n' "$new_crontab" | sudo crontab -u "$cron_user" -
    fi
  fi
done
sudo rm -f -- /etc/cron.d/homehub-backup

sudo systemctl daemon-reload
sudo systemctl enable --now homehub.service homehub-backup.timer homehub-rss-refresh.timer

printf '\nHome Hub is installed. It listens on the Pi loopback interface only.\n'
printf 'For setup, open an SSH tunnel from Windows: ssh -L 8080:127.0.0.1:8080 %s@hostname\n' "$USER"
printf 'Then browse to http://127.0.0.1:8080 on your Windows PC.\n'
printf 'The source repository remains at %s; uninstall does not remove it.\n' "$REPO_ROOT"
