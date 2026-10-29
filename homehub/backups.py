"""Verified SQLite backup operations used by the admin interface."""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

_BACKUP_NAME = re.compile(r"^homehub-\d{8}T\d{6}Z-[0-9a-f]{8}\.sqlite3$")
_CALENDAR_ARCHIVE_NAME = re.compile(r"^homehub-calendar-\d{4}\.ics$")
_CALENDAR_ARCHIVE_TOTAL_MAXIMUM = 128 * 1024 * 1024
_CALENDAR_ARCHIVE_FILE_MAXIMUM = 16 * 1024 * 1024
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


def calendar_bundle_path(backup_path: Path) -> Path:
    """Return the companion archive bundle path for a SQLite snapshot."""
    return backup_path.with_name(backup_path.name + ".calendar.zip")


def _snapshot_calendar_names(snapshot: Path) -> list[str]:
    db = sqlite3.connect(snapshot)
    try:
        try:
            rows = db.execute('SELECT file_name FROM calendar_archives ORDER BY year').fetchall()
        except sqlite3.OperationalError as exc:
            raise BackupError('The database calendar archive metadata is unavailable.') from exc
    finally:
        db.close()
    names = [row[0] for row in rows]
    if any(not isinstance(name, str) or not _CALENDAR_ARCHIVE_NAME.fullmatch(name)
           for name in names) or len(set(names)) != len(names):
        raise BackupError('The database contains invalid calendar archive names.')
    return names


def _write_calendar_bundle(snapshot: Path, archive_directory: Path,
                           bundle_temporary: Path) -> None:
    names = _snapshot_calendar_names(snapshot)
    try:
        with zipfile.ZipFile(bundle_temporary, 'w', compression=zipfile.ZIP_STORED) as bundle:
            for name in names:
                source = archive_directory / name
                if source.is_symlink() or not source.is_file():
                    raise BackupError(f'Calendar archive {name} is missing or unavailable.')
                if source.stat().st_size > _CALENDAR_ARCHIVE_FILE_MAXIMUM:
                    raise BackupError(f'Calendar archive {name} exceeds the backup size limit.')
                bundle.write(source, arcname=name)
        if bundle_temporary.stat().st_size > _CALENDAR_ARCHIVE_TOTAL_MAXIMUM:
            raise BackupError('The calendar archive bundle exceeds the backup size limit.')
        _sync_file(bundle_temporary)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError('The calendar archive bundle could not be created.') from exc


def _stage_calendar_bundle(bundle_path: Path, names: list[str], parent: Path) -> Path:
    stage = Path(tempfile.mkdtemp(prefix='.homehub-calendar-restore-', dir=parent))
    stage.chmod(0o700)
    try:
        if bundle_path.exists():
            if bundle_path.is_symlink() or not bundle_path.is_file():
                raise BackupError('The calendar archive bundle is unavailable.')
            with zipfile.ZipFile(bundle_path) as bundle:
                entries = bundle.infolist()
                entry_names = [entry.filename for entry in entries]
                if (len(entry_names) != len(set(entry_names))
                        or set(entry_names) != set(names)
                        or any(not _CALENDAR_ARCHIVE_NAME.fullmatch(name)
                               for name in entry_names)
                        or sum(entry.file_size for entry in entries)
                        > _CALENDAR_ARCHIVE_TOTAL_MAXIMUM
                        or any(entry.file_size > _CALENDAR_ARCHIVE_FILE_MAXIMUM
                               for entry in entries)):
                    raise BackupError('The calendar archive bundle contents are invalid.')
                for entry in entries:
                    output = stage / entry.filename
                    written = 0
                    with bundle.open(entry) as source, output.open('xb') as target:
                        while chunk := source.read(65536):
                            written += len(chunk)
                            if written > _CALENDAR_ARCHIVE_FILE_MAXIMUM:
                                raise BackupError('A calendar archive exceeds the restore size limit.')
                            target.write(chunk)
        elif names:
            raise BackupError('This backup refers to calendar archives but has no archive bundle.')
        return stage
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError('The calendar archive bundle could not be restored safely.') from exc
    except BackupError:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _replace_directory(staged: Path, destination: Path) -> Path | None:
    if destination.is_symlink():
        raise BackupError('The calendar archive folder cannot be a symbolic link.')
    previous = destination.with_name(f'.{destination.name}-previous-{os.urandom(4).hex()}')
    had_previous = destination.exists()
    if had_previous:
        os.replace(destination, previous)
    try:
        os.replace(staged, destination)
    except OSError:
        if had_previous:
            os.replace(previous, destination)
        raise
    return previous if had_previous else None


def create_backup(database: Path, directory: Path) -> Path:
    """Create a verified database snapshot and its calendar archive bundle."""
    directory = validate_backup_directory(directory)
    temporary = _temporary_path(directory, ".homehub-backup-")
    bundle_temporary = _temporary_path(directory, ".homehub-calendar-backup-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = directory / f"homehub-{stamp}-{os.urandom(4).hex()}.sqlite3"
    bundle_final = calendar_bundle_path(final)
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
            _write_calendar_bundle(
                temporary, database.resolve().parent / 'calendar-archives', bundle_temporary)
            bundle_temporary.replace(bundle_final)
            temporary.replace(final)
    except (OSError, sqlite3.Error, BackupError) as exc:
        temporary.unlink(missing_ok=True)
        bundle_temporary.unlink(missing_ok=True)
        bundle_final.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("The database backup could not be created and verified.") from exc
    return final


def list_backups(directory: Path, limit: int | None = 365) -> list[dict[str, object]]:
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
    ordered = sorted(found, key=lambda item: item["created_at"], reverse=True)
    return ordered if limit is None else ordered[:limit]


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
            bundle = calendar_bundle_path(path)
            if bundle.is_file() and not bundle.is_symlink():
                bundle.unlink()
        except OSError as exc:
            raise BackupError("The backup file could not be deleted.") from exc


def restore_backup(database: Path, directory: Path, name: str,
                   initialize_database, schema_version: int) -> None:
    """Verify and migrate a copy, revoke sessions, then atomically replace DB."""
    source_path = _resolve_backup(directory, name)
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(database.parent, f".{database.name}-restore-")
    archive_stage = None
    previous_archives = None
    archive_directory = database.resolve().parent / 'calendar-archives'
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
                names = _snapshot_calendar_names(temporary)
            finally:
                migrated.close()
            _sync_file(temporary)
            archive_stage = _stage_calendar_bundle(
                calendar_bundle_path(source_path), names, database.parent)
            previous_archives = _replace_directory(archive_stage, archive_directory)
            archive_stage = None
            try:
                os.replace(temporary, database)
            except OSError:
                if previous_archives is not None:
                    shutil.rmtree(archive_directory, ignore_errors=True)
                    os.replace(previous_archives, archive_directory)
                    previous_archives = None
                raise
            if previous_archives is not None:
                shutil.rmtree(previous_archives, ignore_errors=True)
    except (OSError, sqlite3.Error, RuntimeError, BackupError) as exc:
        temporary.unlink(missing_ok=True)
        if archive_stage is not None:
            shutil.rmtree(archive_stage, ignore_errors=True)
        for sidecar in database.parent.glob(
                f"{temporary.stem}.pre-v*-*{temporary.suffix}"):
            sidecar.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("The selected backup could not be restored safely.") from exc


def prune_backups(directory: Path, retention: int) -> None:
    """Keep the newest verified snapshots, pruning only after a new one exists."""
    backups = list_backups(directory, limit=None)
    for backup in backups[retention:]:
        delete_backup(directory, str(backup["name"]))
