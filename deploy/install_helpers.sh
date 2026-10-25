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
