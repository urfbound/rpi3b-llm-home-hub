#!/usr/bin/env sh
set -eu

url='https://raw.githubusercontent.com/urfbound/rpi3b-llm-home-hub/main/deploy/release_bootstrap.sh'
bootstrap=$(mktemp)
trap 'rm -f "$bootstrap"' EXIT HUP INT TERM
curl -fsSL "$url" -o "$bootstrap"
bash "$bootstrap" upgrade "$@"
