#!/usr/bin/env bash
set -uo pipefail
umask 077

output=''
scenario=''
result=''
while (($#)); do
  case $1 in
    --output)
      (($# >= 2)) || { echo '--output requires a file path' >&2; exit 2; }
      output=$2
      shift
      ;;
    --record)
      (($# >= 2)) || { echo '--record requires SCENARIO=RESULT' >&2; exit 2; }
      record=$2
      [[ $record == *=* ]] || { echo '--record requires SCENARIO=RESULT' >&2; exit 2; }
      scenario=${record%%=*}
      result=${record#*=}
      case $scenario in
        install-keep|install-delete|upgrade-preserves-data|upgrade-rollback|upgrade-newer-schema-refused|checksum-failure|uninstall-keep|uninstall-delete|timers-and-ownership) ;;
        *) echo "Unknown scenario: $scenario" >&2; exit 2 ;;
      esac
      case $result in pass|fail|skip) ;;
        *) echo 'Result must be pass, fail, or skip' >&2; exit 2 ;;
      esac
      shift
      ;;
    -h|--help)
      cat <<'HELP'
Usage: bash deploy/pi_validation_report.sh [--output FILE] [--record SCENARIO=RESULT]

Read-only Raspberry Pi deployment report. It does not use sudo, install,
remove, restart, or modify Home Hub files or services. It captures OS/Python
details, Home Hub service/timer state, relevant path ownership/modes, shell
syntax checks, and environment variable names only (never their values).

Run after each manual check and include the generated report in your feedback.
Record values: pass, fail, or skip.

Scenarios:
  install-keep                  clean install and repeat install with data retained
  install-delete                install after explicit data deletion
  upgrade-preserves-data        upgrade preserves data and creates a verified backup
  upgrade-rollback              failed upgrade rollback/recovery check
  upgrade-newer-schema-refused  newer database schema is refused safely
  checksum-failure              invalid release checksum stops before installation
  uninstall-keep                uninstall preserves database, archives, and backups
  uninstall-delete              explicit uninstall data/backups deletion
  timers-and-ownership          timer state and retained data/config ownership

Example:
  bash deploy/pi_validation_report.sh --record install-keep=pass \
    --output ~/homehub-pi-check.txt
  bash deploy/pi_validation_report.sh --record upgrade-preserves-data=pass \
    --output ~/homehub-pi-check.txt
HELP
      exit 0
      ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

if [[ -n $output ]]; then
  mkdir -p -- "$(dirname -- "$output")" || exit 1
  touch -- "$output" || exit 1
  chmod 600 -- "$output" || exit 1
  exec > >(tee -a -- "$output") 2>&1
fi

section() { printf '\n## %s\n' "$1"; }
show_path() {
  local path=$1
  if [[ -e $path ]]; then
    if stat -c '%A %a %U:%G %n' -- "$path" >/dev/null 2>&1; then
      stat -c '%A %a %U:%G %n' -- "$path" 2>&1
    else
      stat -f '%Sp %Lp %Su:%Sg %N' -- "$path" 2>&1 || true
    fi
  else
    printf 'MISSING %s\n' "$path"
  fi
}
service_state() {
  local unit=$1
  if command -v systemctl >/dev/null 2>&1; then
    printf '%s active=' "$unit"
    systemctl is-active "$unit" 2>&1 || true
    printf '%s enabled=' "$unit"
    systemctl is-enabled "$unit" 2>&1 || true
  else
    printf 'systemctl unavailable; cannot inspect %s\n' "$unit"
  fi
}

printf 'Home Hub Raspberry Pi deployment validation report\n'
printf 'Generated UTC: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date)"
printf 'Host: %s\n' "$(hostname 2>/dev/null || echo unknown)"
if [[ -n $scenario ]]; then printf 'Recorded scenario: %s=%s\n' "$scenario" "$result"; fi

section 'System'
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  printf 'OS: %s %s\n' "${PRETTY_NAME:-unknown}" "${VERSION_ID:-}"
fi
printf 'Kernel: %s\n' "$(uname -a 2>/dev/null || echo unknown)"
printf 'Architecture: %s\n' "$(uname -m 2>/dev/null || echo unknown)"
printf 'User: %s (uid %s)\n' "$(id -un 2>/dev/null || echo unknown)" "$(id -u 2>/dev/null || echo unknown)"
printf 'Python: %s\n' "$(python3 --version 2>&1 || echo unavailable)"
printf 'Disk: %s\n' "$(df -h / 2>/dev/null | tail -n 1 || echo unavailable)"
if [[ -r /proc/meminfo ]]; then awk '/^(MemTotal|MemAvailable):/ {print "Memory " $0}' /proc/meminfo; fi

section 'Deployment paths (ownership and mode only)'
for path in /opt/homehub /opt/homehub/homehub /opt/homehub/.venv \
  /var/lib/homehub /etc/homehub /etc/homehub/homehub.env \
  /etc/systemd/system/homehub.service \
  /etc/systemd/system/homehub-backup.timer \
  /etc/systemd/system/homehub-rss-refresh.timer; do
  show_path "$path"
done
if [[ -f /etc/homehub/homehub.env ]]; then
  printf 'Environment variable names only: '
  sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' /etc/homehub/homehub.env | paste -sd ',' -
fi

section 'Services and timers'
for unit in homehub.service homehub-backup.timer homehub-rss-refresh.timer \
  homehub-backup.service homehub-rss-refresh.service; do
  service_state "$unit"
done
if command -v systemctl >/dev/null 2>&1; then
  printf '\nMatching scheduled timers:\n'
  systemctl list-timers --all --no-pager 2>&1 | grep -E 'homehub|NEXT' || true
fi

section 'Installed versions and backups'
if [[ -x /opt/homehub/.venv/bin/python ]]; then
  /opt/homehub/.venv/bin/python -c 'import sys; print("Installed Python:", sys.version.split()[0])' 2>&1 || true
  /opt/homehub/.venv/bin/python -c 'import importlib.metadata as m; print("Home Hub package:", m.version("homehub"))' 2>&1 || true
fi
if [[ -d /var/lib/homehub ]]; then
  find /var/lib/homehub -maxdepth 3 -type f \( -iname '*backup*' -o -iname '*.sqlite3' -o -iname '*.ics' \) \
    -printf '%TY-%Tm-%Td %TH:%TM %s bytes %p\n' 2>/dev/null | head -n 80 || true
fi

section 'Deployment script syntax'
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for script in "$script_dir"/*.sh; do
  if bash -n "$script"; then printf 'PASS bash -n %s\n' "${script##*/}";
  else printf 'FAIL bash -n %s\n' "${script##*/}"; fi
done
printf 'Installer help: '
bash "$script_dir/install.sh" --help >/dev/null 2>&1 && echo PASS || echo FAIL
printf 'Uninstaller help: '
bash "$script_dir/uninstall.sh" --help >/dev/null 2>&1 && echo PASS || echo FAIL

section 'Manual scenario notes'
printf 'Only scenarios explicitly recorded above are marked. A report cannot prove a flow that was not run on this Pi.\n'
printf 'Keep this output; add a new --record invocation after each scenario and append it to the same --output file.\n'
