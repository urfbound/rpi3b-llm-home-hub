#!/usr/bin/env bash

homehub_parse_uninstall_args() {
  HOMEHUB_NO_PROMPT=0
  HOMEHUB_DATA_CHOICE=ask
  HOMEHUB_BACKUP_CHOICE=ask
  HOMEHUB_SHOW_HELP=0
  while (($#)); do
    case $1 in
      --no-prompt|-y|--yes) HOMEHUB_NO_PROMPT=1 ;;
      --delete-data)
        [[ $HOMEHUB_DATA_CHOICE != keep ]] || { echo '--delete-data conflicts with --keep-data' >&2; return 2; }
        HOMEHUB_DATA_CHOICE=delete
        HOMEHUB_BACKUP_CHOICE=delete
        ;;
      --keep-data)
        [[ $HOMEHUB_DATA_CHOICE != delete ]] || { echo '--keep-data conflicts with --delete-data' >&2; return 2; }
        HOMEHUB_DATA_CHOICE=keep
        ;;
      --delete-backups)
        [[ $HOMEHUB_BACKUP_CHOICE != keep ]] || { echo '--delete-backups conflicts with --keep-backups' >&2; return 2; }
        HOMEHUB_BACKUP_CHOICE=delete
        ;;
      --keep-backups)
        [[ $HOMEHUB_BACKUP_CHOICE != delete ]] || { echo '--keep-backups conflicts with --delete-backups' >&2; return 2; }
        HOMEHUB_BACKUP_CHOICE=keep
        ;;
      -h|--help)
        printf 'Usage: uninstall.sh [--no-prompt|--yes|-y] [--delete-data|--keep-data] [--delete-backups|--keep-backups]\n'
        HOMEHUB_SHOW_HELP=1
        return 0
        ;;
      *) printf 'Unknown uninstaller option: %s\n' "$1" >&2; return 2 ;;
    esac
    shift
  done
  if (( HOMEHUB_NO_PROMPT )); then
    [[ $HOMEHUB_DATA_CHOICE != ask ]] || HOMEHUB_DATA_CHOICE=keep
    [[ $HOMEHUB_BACKUP_CHOICE != ask ]] || HOMEHUB_BACKUP_CHOICE=keep
  fi
}

homehub_remove_data_paths() {
  local database_path=$1
  local data_root=$2
  local archive_directory=$3
  local privilege=${4:-sudo}

  [[ $data_root == */homehub && $data_root != / ]] || {
    echo "Refusing to remove unexpected Home Hub data directory: $data_root" >&2
    return 1
  }
  [[ $archive_directory == */calendar-archives && $archive_directory != /calendar-archives ]] || {
    echo "Refusing to remove unexpected calendar archive directory: $archive_directory" >&2
    return 1
  }
  [[ ! -d $database_path ]] || {
    echo "Refusing to remove a database path that is a directory: $database_path" >&2
    return 1
  }

  if [[ $privilege == sudo ]]; then
    sudo rm -f -- "$database_path" "$database_path-wal" "$database_path-shm"
    sudo rm -rf -- "$data_root"
    sudo rm -rf -- "$archive_directory"
  else
    rm -f -- "$database_path" "$database_path-wal" "$database_path-shm"
    rm -rf -- "$data_root"
    rm -rf -- "$archive_directory"
  fi
}

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
