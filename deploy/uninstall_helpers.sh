#!/usr/bin/env bash

homehub_remove_installed_files() {
  local install_root=$1
  local environment_file=$2
  local config_root=$3
  local privilege=${4:-sudo}

  if [[ $privilege == sudo ]]; then
    sudo rm -rf -- "$install_root"
    sudo rm -f -- "$environment_file"
    sudo rmdir -- "$config_root" 2>/dev/null || true
  else
    rm -rf -- "$install_root"
    rm -f -- "$environment_file"
    rmdir -- "$config_root" 2>/dev/null || true
  fi
}
