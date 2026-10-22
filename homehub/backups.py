"""Verified SQLite backup operations used by the admin interface."""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

_BACKUP_NAME = re.compile(r"^homehub-\d{8}T\d{6}Z-[0-9a-f]{8}\.sqlite3$")
_BACKUP_LOCK = threading.Lock()


class BackupError(ValueError):
    """A backup operation could not safely complete."""


def default_backup_directory(database: Path) -> Path:
    return database.expanduser().resolve().parent / "backups"


def validate_backup_directory(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise BackupError("Choose an absolute backup folder path.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise BackupError("The backup folder path is not a directory.")
        with tempfile.NamedTemporaryFile(prefix=".homehub-write-check-", dir=path):
            pass
    except OSError as exc:
        raise BackupError("The backup folder is not available or writable.") from exc
    return path.resolve()


def backup_filename(name: str) -> str:
    if not _BACKUP_NAME.fullmatch(name):
        raise BackupError("That backup file is not recognized.")
    return name


def _check_database(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise BackupError("The database integrity check failed.")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise BackupError("The database relationship check failed.")


def _temporary_path(directory: Path, prefix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix=prefix, suffix=".tmp", dir=directory,
                                         delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _sync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_backup(database: Path, directory: Path) -> Path:
    """Create a snapshot, verify it, then atomically publish the file."""
    directory = validate_backup_directory(directory)
    temporary = _temporary_path(directory, ".homehub-backup-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = directory / f"homehub-{stamp}-{os.urandom(4).hex()}.sqlite3"
    try:
        with _BACKUP_LOCK:
            source = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True,
                                     timeout=30)
            target = sqlite3.connect(temporary, timeout=30)
            try:
                source.backup(target)
                target.commit()
                _check_database(target)
            finally:
                target.close()
                source.close()
            _sync_file(temporary)
            temporary.replace(final)
    except (OSError, sqlite3.Error, BackupError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("The database backup could not be created and verified.") from exc
    return final


def list_backups(directory: Path, limit: int = 365) -> list[dict[str, object]]:
    if not directory.is_dir():
        return []
    found = []
    for path in directory.iterdir():
        if path.is_file() and not path.is_symlink() and _BACKUP_NAME.fullmatch(path.name):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append({"name": path.name, "size": stat.st_size,
                          "created_at": datetime.fromtimestamp(stat.st_mtime,
                                                               timezone.utc)})
    return sorted(found, key=lambda item: item["created_at"], reverse=True)[:limit]


def _resolve_backup(directory: Path, name: str) -> Path:
    name = backup_filename(name)
    root = directory.resolve()
    path = root / name
    try:
        if path.is_symlink() or path.resolve().parent != root or not path.is_file():
            raise BackupError("That backup file is unavailable.")
    except OSError as exc:
        raise BackupError("That backup file is unavailable.") from exc
    return path


def delete_backup(directory: Path, name: str) -> None:
    with _BACKUP_LOCK:
        path = _resolve_backup(directory, name)
        try:
            path.unlink()
        except OSError as exc:
            raise BackupError("The backup file could not be deleted.") from exc


def restore_backup(database: Path, directory: Path, name: str,
                   initialize_database, schema_version: int) -> None:
    """Verify and migrate a copy, revoke sessions, then atomically replace DB."""
    source_path = _resolve_backup(directory, name)
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(database.parent, f".{database.name}-restore-")
    try:
        with _BACKUP_LOCK:
            source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True,
                                     timeout=30)
            target = sqlite3.connect(temporary, timeout=30)
            try:
                source.backup(target)
                target.commit()
                _check_database(target)
            finally:
                target.close()
                source.close()

            initialize_database(temporary)
            for sidecar in database.parent.glob(
                    f"{temporary.stem}.pre-v*-*{temporary.suffix}"):
                sidecar.unlink(missing_ok=True)
            migrated = sqlite3.connect(temporary, timeout=30)
            try:
                _check_database(migrated)
                version = migrated.execute("PRAGMA user_version").fetchone()[0]
                if version != schema_version:
                    raise BackupError("The selected backup could not be upgraded safely.")
                migrated.execute("UPDATE users SET session_version=session_version+1")
                migrated.commit()
                _check_database(migrated)
            finally:
                migrated.close()
            _sync_file(temporary)
            os.replace(temporary, database)
    except (OSError, sqlite3.Error, RuntimeError, BackupError) as exc:
        temporary.unlink(missing_ok=True)
        for sidecar in database.parent.glob(
                f"{temporary.stem}.pre-v*-*{temporary.suffix}"):
            sidecar.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("The selected backup could not be restored safely.") from exc


def prune_backups(directory: Path, retention: int) -> None:
    """Keep the newest verified snapshots, pruning only after a new one exists."""
    backups = list_backups(directory)
    for backup in backups[retention:]:
        delete_backup(directory, str(backup["name"]))
