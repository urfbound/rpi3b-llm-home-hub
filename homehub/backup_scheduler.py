"""Small in-process scheduler for routine database snapshots."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from .backups import (BackupError, create_backup, default_backup_directory,
                      list_backups, prune_backups, validate_backup_directory)


def run_backup_cycle(database: Path, logger, now: float | None = None) -> bool:
    """Create a verified backup when the configured interval has elapsed."""
    import time

    now = time.time() if now is None else now
    try:
        with closing(sqlite3.connect(database, timeout=5)) as db:
            settings = dict(db.execute('SELECT key,value FROM app_settings'))
        interval = int(settings.get('backup_interval_days', '1'))
        if interval == 0:
            return False
        retention = int(settings.get('backup_retention', '14'))
        folder = (Path(settings['backup_folder']) if 'backup_folder' in settings
                  else default_backup_directory(database))
        folder = validate_backup_directory(folder)
        backups = list_backups(folder)
        if backups and now - backups[0]['created_at'].timestamp() < interval * 86400:
            return False
        created = create_backup(database, folder)
        prune_backups(folder, retention)
        logger.info('Created and verified scheduled backup %s', created.name)
        return True
    except (OSError, ValueError, sqlite3.Error, BackupError):
        logger.exception('Scheduled Home Hub backup failed')
        return False


def start_backup_scheduler(app):
    """Start one low-frequency daemon that checks settings every 15 minutes."""
    if app.testing:
        return None
    stop = threading.Event()

    def run():
        database = Path(app.config['DATABASE'])
        while not stop.is_set():
            run_backup_cycle(database, app.logger)
            stop.wait(15 * 60)

    thread = threading.Thread(target=run, name='homehub-backup-scheduler', daemon=True)
    thread.start()
    return stop, thread
