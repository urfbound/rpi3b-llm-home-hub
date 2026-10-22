"""One-shot backup runner invoked by the systemd timer."""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .backups import (BackupError, create_backup, default_backup_directory,
                      list_backups, prune_backups, validate_backup_directory)


def run_backup_cycle(database: Path, logger, now: float | None = None,
                     raise_on_error: bool = False) -> bool:
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
        if raise_on_error:
            raise
        return False


def main() -> int:
    """Run one backup check; systemd owns the recurring schedule."""
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    database = Path(os.environ.get(
        'HOMEHUB_DB', str(Path.home() / '.local/share/homehub/homehub.sqlite3')))
    try:
        run_backup_cycle(database, logging.getLogger('homehub.backup'), raise_on_error=True)
    except (OSError, ValueError, sqlite3.Error, BackupError):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
