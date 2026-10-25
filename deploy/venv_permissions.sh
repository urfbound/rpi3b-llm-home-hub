#!/usr/bin/env bash

homehub_make_venv_service_accessible() {
  local venv_path=$1
  local privilege=${2:-sudo}
  if [[ $privilege == sudo ]]; then
    sudo chmod -R a+rX -- "$venv_path"
  else
    chmod -R a+rX -- "$venv_path"
  fi
}
