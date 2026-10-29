#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPOSITORY='urfbound/rpi3b-llm-home-hub'
readonly RELEASE_API="https://api.github.com/repos/$REPOSITORY/releases/latest"

mode=${1:-}
if [[ $mode != install && $mode != upgrade ]]; then
  printf 'Usage: release_bootstrap.sh install|upgrade [installer options]\n' >&2
  exit 2
fi
shift
if [[ $mode == upgrade && ! -d /opt/homehub ]]; then
  printf 'Home Hub is not installed at /opt/homehub; use the install command instead.\n' >&2
  exit 1
fi
for command_name in curl python3 tar sha256sum; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Required command is missing: %s\n' "$command_name" >&2
    exit 1
  fi
done

temporary_directory=$(mktemp -d)
cleanup() { rm -rf -- "$temporary_directory"; }
trap cleanup EXIT HUP INT TERM

release_json="$temporary_directory/release.json"
if ! curl -fsSL -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2022-11-28' "$RELEASE_API" -o "$release_json"; then
  printf 'Could not read the latest Home Hub release. No files were installed.\n' >&2
  exit 1
fi
tag=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["tag_name"])' "$release_json")
if [[ ! $tag =~ ^v?[0-9][A-Za-z0-9.+-]*$ ]]; then
  printf 'The latest release has an invalid version tag.\n' >&2
  exit 1
fi

archive_name="homehub-${tag}.tar.gz"
checksum_name="${archive_name}.sha256"
release_base="https://github.com/$REPOSITORY/releases/download/$tag"
archive="$temporary_directory/$archive_name"
checksum="$temporary_directory/$checksum_name"
if ! curl -fL "$release_base/$archive_name" -o "$archive" \
  || ! curl -fL "$release_base/$checksum_name" -o "$checksum"; then
  printf 'Release %s is missing its install archive or checksum. No files were installed.\n' "$tag" >&2
  exit 1
fi
(cd -- "$temporary_directory" && sha256sum --check "$checksum_name")
mkdir "$temporary_directory/source"
tar -xzf "$archive" --strip-components=1 -C "$temporary_directory/source"
if [[ ! -f $temporary_directory/source/deploy/install.sh ]]; then
  printf 'Release archive %s does not contain the installer.\n' "$tag" >&2
  exit 1
fi

printf 'Installing Home Hub release %s.\n' "$tag"
bash "$temporary_directory/source/deploy/install.sh" "$@"
