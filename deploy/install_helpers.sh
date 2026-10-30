#!/usr/bin/env bash

homehub_parse_install_args() {
  HOMEHUB_NO_PROMPT=0
  HOMEHUB_SHOW_HELP=0
  HOMEHUB_ADMIN_USERNAME=''
  HOMEHUB_ADMIN_PASSWORD_STDIN=0
  HOMEHUB_INSTALL_OLLAMA=0
  HOMEHUB_INSTALL_CADDY=0
  HOMEHUB_NO_RSS=0
  while (($#)); do
    case $1 in
      --no-prompt|-y|--yes) HOMEHUB_NO_PROMPT=1 ;;
      --admin-username)
        (($# >= 2)) || { echo '--admin-username requires a value' >&2; return 2; }
        HOMEHUB_ADMIN_USERNAME=$2
        shift
        ;;
      --admin-password-stdin) HOMEHUB_ADMIN_PASSWORD_STDIN=1 ;;
      --install-ollama) HOMEHUB_INSTALL_OLLAMA=1 ;;
      --install-caddy) HOMEHUB_INSTALL_CADDY=1 ;;
      --no-rss) HOMEHUB_NO_RSS=1 ;;
      -h|--help)
        cat <<'HELP'
Usage: install.sh [OPTIONS]

Install Home Hub on Raspberry Pi OS or another supported Linux system.

Options:
  --no-prompt, --yes, -y       Run without interactive prompts
  --admin-username NAME        Set the first administrator username
  --admin-password-stdin       Read the first administrator password from stdin
  --install-ollama             Optionally install the Ollama runtime
  --install-caddy              Optionally install Caddy from its official Debian repository
  --no-rss                     Disable scheduled RSS feed refresh
  -h, --help                   Show this help and exit

Ollama is optional and installs only on Linux x86_64 or ARM64. If Ollama
is already installed, its version and service state are left unchanged. No
model is selected or downloaded. A newly installed Ollama service is stopped
and disabled until you start it yourself.

Caddy is optional. On Debian-family Linux systems with apt-get, this adds
Caddy's official package repository and installs its package. The package
starts a persistent system service. An existing Caddy installation or service
is left unchanged. This does not configure Home Hub's Caddy site or local DNS; use
deploy/enable_https.sh after setting up local DNS.

During an interactive install, you can decline scheduled RSS refresh. The RSS
feature remains available, including immediate refresh when a feed is added.
Pass --no-rss to disable the scheduled systemd timer without a prompt.
HELP
        HOMEHUB_SHOW_HELP=1
        return 0
        ;;
      *) printf 'Unknown installer option: %s\n' "$1" >&2; return 2 ;;
    esac
    shift
  done
  if (( HOMEHUB_ADMIN_PASSWORD_STDIN )) && [[ -z $HOMEHUB_ADMIN_USERNAME ]]; then
    echo '--admin-password-stdin requires --admin-username' >&2
    return 2
  fi
}

homehub_prompt_install_choices() {
  if (( HOMEHUB_NO_PROMPT )); then return 0; fi
  printf 'Optional packages are installed only with command-line options: pass --install-ollama and/or --install-caddy.\n'
  local answer=''
  printf 'Begin Home Hub installation? [Y/n] '
  if ! read -r answer; then return 1; fi
  if [[ $answer =~ ^[Nn]$ ]]; then return 1; fi
  if (( ! HOMEHUB_NO_RSS )); then
    printf 'Enable scheduled RSS feed refresh? [Y/n] '
    if ! read -r answer; then return 1; fi
    if [[ $answer =~ ^[Nn]$ ]]; then HOMEHUB_NO_RSS=1; fi
  fi
  return 0
}

homehub_set_rss_refresh_timer() {
  local no_rss=${1:-0}
  if (( no_rss )); then
    sudo systemctl disable --now homehub-rss-refresh.timer
  else
    sudo systemctl enable --now homehub-rss-refresh.timer
    sudo systemctl start homehub-rss-refresh.service
  fi
}

homehub_ollama_platform_supported() {
  case "$1:$2" in
    Linux:x86_64|Linux:aarch64|Linux:arm64) return 0 ;;
    *) return 1 ;;
  esac
}

homehub_install_ollama_linux() (
  set -eu
  local os arch installer_script
  if command -v ollama >/dev/null 2>&1; then
    printf 'Ollama is already installed; leaving its version and service state unchanged.\n'
    return 0
  fi
  os=$(uname -s)
  arch=$(uname -m)
  if ! homehub_ollama_platform_supported "$os" "$arch"; then
    printf 'Ollama install is supported here only on Linux x86_64 or ARM64; found %s/%s.\n' "$os" "$arch" >&2
    return 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf 'Cannot install Ollama: curl is required to download the official installer. Install curl and retry.\n' >&2
    return 1
  fi

  installer_script=$(mktemp)
  trap 'rm -f -- "$installer_script"' EXIT
  printf 'Downloading the official Ollama installer. This option installs the runtime only; it does not select or download a model.\n'
  if ! curl -fsSL https://ollama.com/install.sh -o "$installer_script"; then
    printf 'Could not download the official Ollama installer from https://ollama.com/install.sh. Check the network connection and retry.\n' >&2
    return 1
  fi
  if ! sh "$installer_script"; then
    printf 'The official Ollama installer failed. Review its output above, then retry or install Ollama manually.\n' >&2
    return 1
  fi

  if command -v systemctl >/dev/null 2>&1 \
    && sudo systemctl cat ollama.service >/dev/null 2>&1; then
    sudo systemctl disable --now ollama.service
    printf 'Ollama is installed and stopped. Start it when needed with: sudo systemctl start ollama\n'
  fi
)

homehub_install_caddy_linux() (
  set -eu
  local os temporary_directory key_tmp keyring_tmp repo_tmp
  if command -v caddy >/dev/null 2>&1; then
    printf 'Caddy is already installed; leaving its version, service, and configuration unchanged.\n'
    return 0
  fi
  if command -v systemctl >/dev/null 2>&1 \
    && command -v sudo >/dev/null 2>&1 \
    && sudo systemctl cat caddy.service >/dev/null 2>&1; then
    printf 'A Caddy systemd service already exists; leaving its service and configuration unchanged.\n'
    return 0
  fi

  os=$(uname -s)
  if [[ $os != Linux ]] || ! command -v apt-get >/dev/null 2>&1; then
    printf 'Caddy installation is supported here only on Linux systems with apt-get (Debian, Ubuntu, or Raspberry Pi OS).\n' >&2
    return 1
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    printf 'Cannot install Caddy: sudo is required for package and system service setup.\n' >&2
    return 1
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    printf 'Cannot install Caddy as a persistent service: systemctl is not available.\n' >&2
    return 1
  fi
  temporary_directory=$(mktemp -d)
  key_tmp=$temporary_directory/caddy-signing-key.asc
  keyring_tmp=$temporary_directory/caddy-signing-key.gpg
  repo_tmp=$temporary_directory/caddy-stable.list
  trap 'rm -rf -- "$temporary_directory"' EXIT

  printf 'Installing Caddy from its official Debian-family package repository. The package starts a persistent system service.\n'
  sudo apt-get update
  sudo apt-get install --yes debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  if ! command -v curl >/dev/null 2>&1 || ! command -v gpg >/dev/null 2>&1; then
    printf 'Could not install the curl and gpg prerequisites through apt-get. Install them, then retry.\n' >&2
    return 1
  fi

  if ! curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key -o "$key_tmp"; then
    printf 'Could not download the official Caddy repository signing key. Check the network connection and retry.\n' >&2
    return 1
  fi
  if ! gpg --dearmor --output "$keyring_tmp" "$key_tmp"; then
    printf 'Could not prepare the official Caddy repository signing key.\n' >&2
    return 1
  fi
  if ! curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o "$repo_tmp"; then
    printf 'Could not download the official Caddy package repository configuration. Check the network connection and retry.\n' >&2
    return 1
  fi

  sudo install -D -o root -g root -m 0644 "$keyring_tmp" \
    /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  sudo install -D -o root -g root -m 0644 "$repo_tmp" \
    /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update
  sudo apt-get install --yes caddy
  sudo systemctl enable --now caddy
  printf 'Caddy is installed and its system service is enabled by the official package. Configure local DNS, then run deploy/enable_https.sh.\n'
)

homehub_ensure_environment_file() (
  local env_file=$1
  local data_root=$2
  local secret_key=$3
  local service_group=$4
  local privilege=${5:-sudo}

  if [[ $privilege == sudo ]]; then
    sudo test -f "$env_file" && return 0
  else
    [[ -f $env_file ]] && return 0
  fi

  local env_tmp
  env_tmp="$(mktemp)"
  trap 'rm -f -- "$env_tmp"' EXIT
  (
    umask 077
    cat >"$env_tmp" <<EOF
HOMEHUB_SECRET_KEY=$secret_key
HOMEHUB_DB=$data_root/homehub.sqlite3
HOMEHUB_BIND=127.0.0.1
HOMEHUB_PORT=8080
HOMEHUB_COOKIE_SECURE=0
EOF
  )
  if [[ $privilege == sudo ]]; then
    sudo install -o root -g "$service_group" -m 0640 "$env_tmp" "$env_file"
  else
    install -m 0640 "$env_tmp" "$env_file"
  fi
)

homehub_render_caddyfile() {
  local host=$1
  local port=$2
  local label
  local numeric_port

  [[ $host =~ ^[A-Za-z0-9.-]+$ && $host == *.* && $host != .* && $host != *. ]] || {
    echo "Invalid local hostname: $host" >&2
    return 1
  }
  IFS=. read -r -a labels <<<"$host"
  for label in "${labels[@]}"; do
    [[ $label =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || {
      echo "Invalid local hostname: $host" >&2
      return 1
    }
  done
  [[ $port =~ ^[0-9]{1,5}$ ]] || {
    echo "Invalid Home Hub port: $port" >&2
    return 1
  }
  numeric_port=$((10#$port))
  (( numeric_port > 0 && numeric_port <= 65535 )) || {
    echo "Invalid Home Hub port: $port" >&2
    return 1
  }

  cat <<EOF
$host {
    tls internal
    reverse_proxy 127.0.0.1:$port
}
EOF
}

homehub_require_caddyfile_replacement() {
  local caddyfile=$1
  local replace_existing=${2:-0}

  if [[ -e $caddyfile && $replace_existing != 1 ]]; then
    echo "Refusing to replace existing Caddyfile: $caddyfile" >&2
    echo "Pass --replace-existing-caddyfile to back it up and replace it." >&2
    return 1
  fi
}

homehub_restore_caddyfile_after_activation_failure() {
  local caddyfile=$1
  local backup_file=$2
  local had_caddyfile=$3
  local privilege=${4:-sudo}

  if [[ $had_caddyfile == 1 ]]; then
    [[ -f $backup_file ]] || {
      echo "Caddyfile backup is missing: $backup_file" >&2
      return 1
    }
    if [[ $privilege == sudo ]]; then
      sudo cp -a -- "$backup_file" "$caddyfile"
    else
      cp -a -- "$backup_file" "$caddyfile"
    fi
  else
    if [[ $privilege == sudo ]]; then
      sudo rm -f -- "$caddyfile"
    else
      rm -f -- "$caddyfile"
    fi
  fi
}

homehub_update_environment_file() (
  local env_file=$1
  local service_group=$2
  local privilege=${3:-sudo}
  shift 3
  local current_tmp updated_tmp assignment

  if [[ $privilege == sudo ]]; then
    sudo test -f "$env_file" || { echo "Environment file not found: $env_file" >&2; return 1; }
  else
    [[ -f $env_file ]] || { echo "Environment file not found: $env_file" >&2; return 1; }
  fi
  current_tmp=$(mktemp)
  updated_tmp=$(mktemp)
  trap 'rm -f -- "$current_tmp" "$updated_tmp"' EXIT

  if [[ $privilege == sudo ]]; then
    sudo cat -- "$env_file" >"$current_tmp"
  else
    cat -- "$env_file" >"$current_tmp"
  fi

  for assignment in "$@"; do
    [[ $assignment =~ ^(HOMEHUB_BIND|HOMEHUB_COOKIE_SECURE)=[A-Za-z0-9._-]+$ ]] || {
      echo "Unsupported environment setting: $assignment" >&2
      return 1
    }
  done

  awk -v RS='\n' -v ORS='\n' '
    !/^(HOMEHUB_BIND|HOMEHUB_COOKIE_SECURE)=/
  ' "$current_tmp" >"$updated_tmp"
  for assignment in "$@"; do
    printf '%s\n' "$assignment" >>"$updated_tmp"
  done

  if [[ $privilege == sudo ]]; then
    sudo install -o root -g "$service_group" -m 0640 "$updated_tmp" "$env_file"
  else
    install -m 0640 "$updated_tmp" "$env_file"
  fi
)

homehub_replace_runtime_tree() {
  local source_tree=$1
  local destination=$2
  local privilege=${3:-sudo}

  if [[ $privilege == sudo ]]; then
    sudo rm -rf -- "$destination"
    sudo install -d -o root -g root -m 0755 "$destination"
    sudo cp -a "$source_tree/." "$destination/"
    sudo chown -R root:root "$destination"
  else
    rm -rf -- "$destination"
    install -d -m 0755 "$destination"
    cp -a "$source_tree/." "$destination/"
  fi
}

homehub_filter_legacy_backup_crontab() {
  awk '
    index($0, "homehub-backup") == 0 && index($0, "backup_scheduler") == 0
  '
}
