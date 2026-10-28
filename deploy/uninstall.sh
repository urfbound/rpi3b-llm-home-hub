#!/usr/bin/env bash
set -Eeuo pipefail

readonly INSTALL_ROOT=/opt/homehub
readonly DATA_ROOT=/var/lib/homehub
readonly CONFIG_ROOT=/etc/homehub
readonly ENV_FILE=/etc/homehub/homehub.env
readonly SERVICE_USER=homehub
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f $SCRIPT_DIR/uninstall_helpers.sh ]]; then
  printf 'Required uninstall helper is missing: %s\n' "$SCRIPT_DIR/uninstall_helpers.sh" >&2
  exit 1
fi
source "$SCRIPT_DIR/uninstall_helpers.sh"
if ! homehub_parse_uninstall_args "$@"; then exit 2; fi
if (( HOMEHUB_SHOW_HELP )); then exit 0; fi

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  printf 'Run this script as your regular Pi user; it will request sudo when needed.\n' >&2
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  printf 'sudo is required for system removal steps.\n' >&2
  exit 1
fi
if [[ ! -x $INSTALL_ROOT/.venv/bin/homehub-backup ]]; then
  printf 'Home Hub installation was not found at %s; refusing to remove system files.\n' "$INSTALL_ROOT" >&2
  exit 1
fi

printf 'This removes the Home Hub service, timer, installed application, and environment file.\n'
printf 'The source repository is preserved. Application data is preserved unless you choose deletion.\n'
sudo -v

as_homehub() {
  sudo -u "$SERVICE_USER" /bin/sh -c '
    set -a
    . /etc/homehub/homehub.env
    set +a
    cd /opt/homehub
    exec "$@"
  ' homehub "$@"
}

database_path="$(as_homehub "$INSTALL_ROOT/.venv/bin/python" -c \
  'import os; from pathlib import Path; print(Path(os.environ.get("HOMEHUB_DB", Path.home()/".local/share/homehub/homehub.sqlite3")).expanduser().resolve())')"
if [[ $HOMEHUB_DATA_CHOICE == ask ]]; then
  read -r -p "Delete the database at $database_path and Home Hub data under $DATA_ROOT, including archives and verified backups? [y/N] " answer
  if [[ $answer =~ ^[Yy]$ ]]; then
    HOMEHUB_DATA_CHOICE=delete
    HOMEHUB_BACKUP_CHOICE=delete
  else
    HOMEHUB_DATA_CHOICE=keep
  fi
fi
if [[ -f $database_path ]]; then
  printf 'Creating an immediate verified backup before stopping Home Hub.\n'
  if ! as_homehub "$INSTALL_ROOT/.venv/bin/homehub-backup" --force; then
    printf 'Backup failed. Nothing was uninstalled; resolve the backup problem and retry.\n' >&2
    exit 1
  fi
else
  printf 'No Home Hub database exists at %s; no backup is needed.\n' "$database_path"
fi

backup_directory=''
if [[ -f $database_path ]]; then
  backup_directory="$(as_homehub "$INSTALL_ROOT/.venv/bin/python" -c '
import os, sqlite3
from pathlib import Path
from homehub.backups import default_backup_directory
database = Path(os.environ.get("HOMEHUB_DB", Path.home()/".local/share/homehub/homehub.sqlite3"))
with sqlite3.connect(database) as db:
    row = db.execute("SELECT value FROM app_settings WHERE key=?", ("backup_folder",)).fetchone()
print(Path(row[0]) if row else default_backup_directory(database))
')"
fi

# When only backup deletion is selected, remove recognized Home Hub backup
# files only; leave unrelated contents of the configured folder untouched.
if [[ -n $backup_directory && -d $backup_directory && $HOMEHUB_BACKUP_CHOICE == ask ]]; then
  backup_count="$(as_homehub "$INSTALL_ROOT/.venv/bin/python" -c '
from pathlib import Path
from homehub.backups import list_backups
print(len(list_backups(Path(__import__("sys").argv[1]), limit=1000000)))
' "$backup_directory")"
  if (( backup_count > 0 )); then
    printf 'Found %s verified Home Hub backup file(s) in: %s\n' "$backup_count" "$backup_directory"
    read -r -p 'Delete these verified backup files, including the fresh uninstall backup? [y/N] ' answer
    if [[ $answer =~ ^[Yy]$ ]]; then
      HOMEHUB_BACKUP_CHOICE=delete
    else
      printf 'Backups will be preserved.\n'
    fi
  fi
fi

# Disable the timer before removing units, then stop any in-flight oneshot.
if sudo systemctl cat homehub-backup.timer >/dev/null 2>&1; then
  sudo systemctl disable --now homehub-backup.timer
fi
if sudo systemctl cat homehub-rss-refresh.timer >/dev/null 2>&1; then
  sudo systemctl disable --now homehub-rss-refresh.timer
fi
if sudo systemctl is-active --quiet homehub-backup.service; then
  sudo systemctl stop homehub-backup.service
fi
if sudo systemctl is-active --quiet homehub-rss-refresh.service; then
  sudo systemctl stop homehub-rss-refresh.service
fi
if sudo systemctl cat homehub.service >/dev/null 2>&1; then
  sudo systemctl disable --now homehub.service
fi
if sudo systemctl is-active --quiet homehub.service; then
  printf 'Home Hub is still active; refusing to remove its files.\n' >&2
  exit 1
fi

if [[ $HOMEHUB_BACKUP_CHOICE == delete && -n $backup_directory && -d $backup_directory ]]; then
  as_homehub "$INSTALL_ROOT/.venv/bin/python" -c '
from pathlib import Path
from homehub.backups import delete_backup, list_backups
folder = Path(__import__("sys").argv[1])
for backup in list_backups(folder, limit=1000000):
    delete_backup(folder, str(backup["name"]))
' "$backup_directory"
  printf 'Recognized Home Hub backups were deleted from %s.\n' "$backup_directory"
fi

if [[ $HOMEHUB_DATA_CHOICE == delete ]]; then
  archive_directory="$(as_homehub "$INSTALL_ROOT/.venv/bin/python" -c '
from pathlib import Path
from homehub.app import calendar_archive_directory
import sys
print(calendar_archive_directory(Path(sys.argv[1])))
' "$database_path")"
  homehub_remove_data_paths "$database_path" "$DATA_ROOT" "$archive_directory" sudo
  printf 'Home Hub database, data directory, archives, and recognized backups were deleted.\n'
fi

# Remove only the known Home Hub cron command from root/service-account crontabs.
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
sudo rm -f -- /etc/systemd/system/homehub.service \
  /etc/systemd/system/homehub-backup.service \
  /etc/systemd/system/homehub-backup.timer \
  /etc/systemd/system/homehub-rss-refresh.service \
  /etc/systemd/system/homehub-rss-refresh.timer
sudo systemctl daemon-reload
sudo systemctl reset-failed
homehub_remove_installed_files "$INSTALL_ROOT" "$ENV_FILE" "$CONFIG_ROOT" sudo
if [[ $HOMEHUB_DATA_CHOICE == keep && -d $DATA_ROOT ]]; then
  sudo chown -R root:root "$DATA_ROOT"
fi
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  sudo userdel "$SERVICE_USER"
fi

printf '\nHome Hub services and installed application files have been removed.\n'
printf 'The source repository was preserved.\n'
if [[ $HOMEHUB_DATA_CHOICE == keep ]]; then
  printf 'Application data was preserved and is now owned by root; reinstall will restore service ownership.\n'
fi
printf 'Database: %s\n' "$database_path"
if [[ -n $backup_directory ]]; then printf 'Backup folder: %s\n' "$backup_directory"; fi
