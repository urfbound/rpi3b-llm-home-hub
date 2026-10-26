#!/usr/bin/env bash

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
