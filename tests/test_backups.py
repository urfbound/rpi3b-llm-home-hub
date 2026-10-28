from http import HTTPStatus
import logging
import sqlite3
import time
from contextlib import closing

from homehub.constants import BACKUP_INTERVAL_DAYS_MAXIMUM, BACKUP_RETENTION_DEFAULT
from homehub.backup_scheduler import main as backup_main, run_backup_cycle
from homehub.backups import create_backup
from homehub.app import PH, create_app


def make_admin_client(tmp_path):
    database = tmp_path / 'homehub.sqlite3'
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-backup-key',
                      'DATABASE': str(database)})
    with closing(sqlite3.connect(database)) as db:
        with db:
            db.execute('INSERT INTO users(username,display_name,password_hash,role) '
                       'VALUES(?,?,?,?)', ('admin', 'Admin', PH.hash('correct horse battery'), 'admin'))
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    response = client.post('/login', data={'username': 'admin',
                                          'password': 'correct horse battery',
                                          '_csrf': csrf})
    assert response.status_code == HTTPStatus.FOUND
    return app, client, database


def token(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def test_admin_can_create_download_and_delete_verified_backup(tmp_path):
    app, client, database = make_admin_client(tmp_path)
    client.get('/admin/accounts')
    response = client.post('/admin/backups/create', data={'_csrf': token(client)},
                           follow_redirects=True)
    assert b'Backup created and verified' in response.data

    backup_dir = tmp_path / 'backups'
    backups = list(backup_dir.glob('homehub-*.sqlite3'))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as backup:
        assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert backup.execute('SELECT username FROM users').fetchone() == ('admin',)

    download = client.get(f'/admin/backups/{backups[0].name}/download')
    assert download.status_code == HTTPStatus.OK
    assert download.headers['Content-Disposition'].startswith('attachment;')
    download.get_data()
    download.close()

    deleted = client.post(f'/admin/backups/{backups[0].name}/delete',
                          data={'_csrf': token(client)})
    assert deleted.status_code == HTTPStatus.FOUND, deleted.data
    assert not backups[0].exists()


def test_restore_replaces_data_and_revokes_existing_sessions(tmp_path):
    app, client, database = make_admin_client(tmp_path)
    client.get('/admin/accounts')
    created = client.post('/admin/backups/create', data={'_csrf': token(client)},
                          follow_redirects=True)
    assert b'Backup created and verified' in created.data
    with closing(sqlite3.connect(database)) as db:
        with db:
            db.execute("INSERT INTO shopping_items(text,created_by) VALUES('After backup',1)")

    backup = next((tmp_path / 'backups').glob('homehub-*.sqlite3'))
    response = client.post(f'/admin/backups/{backup.name}/restore',
                           data={'_csrf': token(client)}, follow_redirects=True)
    assert response.status_code == HTTPStatus.OK
    assert b'Backup restored and verified' in response.data
    assert client.get('/').headers['Location'].endswith('/login')
    with closing(sqlite3.connect(database)) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items').fetchone()[0] == 0
        assert db.execute('SELECT session_version FROM users WHERE username="admin"').fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_restore_rejects_corrupt_copy_without_changing_live_database(tmp_path):
    app, client, database = make_admin_client(tmp_path)
    client.get('/admin/accounts')
    backup_dir = tmp_path / 'backups'
    backup_dir.mkdir(exist_ok=True)
    corrupt = backup_dir / 'homehub-20261022T000000Z-deadbeef.sqlite3'
    corrupt.write_bytes(b'not a SQLite database')

    response = client.post(f'/admin/backups/{corrupt.name}/restore',
                           data={'_csrf': token(client)}, follow_redirects=True)
    assert b'could not be restored safely' in response.data
    assert client.get('/admin/accounts').status_code == HTTPStatus.OK
    with closing(sqlite3.connect(database)) as db:
        assert db.execute('SELECT username FROM users').fetchone() == ('admin',)
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_backup_settings_validate_and_allow_turning_automatic_backups_off(tmp_path):
    app, client, database = make_admin_client(tmp_path)
    client.get('/admin/accounts')
    folder = tmp_path / 'custom backups'
    response = client.post('/admin/backups/settings', data={
        '_csrf': token(client), 'backups_off': 'on', 'backup_folder': str(folder),
    })
    assert response.status_code == HTTPStatus.FOUND
    assert response.headers['Location'].endswith('/admin/accounts#backup-restore-section')
    with closing(sqlite3.connect(database)) as db:
        settings = dict(db.execute('SELECT key,value FROM app_settings'))
    assert settings['backup_folder'] == str(folder)
    assert settings['backup_interval_days'] == '0'
    assert settings['backup_retention'] == str(BACKUP_RETENTION_DEFAULT)

    response = client.post('/admin/backups/settings', data={
        '_csrf': token(client), 'backup_folder': str(folder),
        'backup_frequency': str(BACKUP_INTERVAL_DAYS_MAXIMUM + 1),
        'backup_retention': str(BACKUP_RETENTION_DEFAULT),
    })
    assert response.status_code == HTTPStatus.FOUND
    with closing(sqlite3.connect(database)) as db:
        assert db.execute('SELECT value FROM app_settings WHERE key="backup_interval_days"').fetchone()[0] == '0'


def test_backup_routes_require_admin_and_csrf(tmp_path):
    app, admin_client, database = make_admin_client(tmp_path)
    response = admin_client.post('/admin/backups/create')
    assert response.status_code == HTTPStatus.BAD_REQUEST

    with closing(sqlite3.connect(database)) as db:
        with db:
            db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                       "VALUES('member','Member',?,'user')", (PH.hash('another long password'),))
    member = app.test_client()
    member.get('/login')
    with member.session_transaction() as session:
        csrf = session['csrf_token']
    member.post('/login', data={'username': 'member', 'password': 'another long password',
                                '_csrf': csrf})
    assert member.get('/admin/accounts').status_code == HTTPStatus.FORBIDDEN
    assert member.post('/admin/backups/create', data={'_csrf': token(member)}).status_code == HTTPStatus.FORBIDDEN


def test_scheduler_respects_interval_and_off_setting(tmp_path):
    _, _, database = make_admin_client(tmp_path)
    folder = tmp_path / 'scheduled'
    with closing(sqlite3.connect(database)) as db:
        db.executemany('INSERT INTO app_settings(key,value) VALUES(?,?)', [
            ('backup_folder', str(folder)), ('backup_interval_days', '1'),
            ('backup_retention', '1'),
        ])
        db.commit()

    now = time.time() + 5
    logger = logging.getLogger('test-backup-scheduler')
    assert run_backup_cycle(database, logger, now=now)
    assert len(list(folder.glob('homehub-*.sqlite3'))) == 1
    assert not run_backup_cycle(database, logger, now=now + 60)
    with closing(sqlite3.connect(database)) as db:
        db.execute("UPDATE app_settings SET value='0' WHERE key='backup_interval_days'")
        db.commit()
    assert not run_backup_cycle(database, logger, now=now + 86400)
    assert len(list(folder.glob('homehub-*.sqlite3'))) == 1


def test_scheduler_uses_current_settings_after_pause_and_folder_change(tmp_path):
    _, client, database = make_admin_client(tmp_path)
    first_folder = tmp_path / 'scheduled-first'
    next_folder = tmp_path / 'scheduled-next'
    client.get('/admin/accounts')
    response = client.post('/admin/backups/settings', data={
        '_csrf': token(client), 'backup_folder': str(first_folder),
        'backup_frequency': '1', 'backup_retention': '14',
    })
    assert response.status_code == HTTPStatus.FOUND

    now = time.time() + 5
    logger = logging.getLogger('test-backup-settings-reload')
    assert run_backup_cycle(database, logger, now=now)
    assert len(list(first_folder.glob('homehub-*.sqlite3'))) == 1

    response = client.post('/admin/backups/settings', data={
        '_csrf': token(client), 'backups_off': 'on',
        'backup_folder': str(next_folder), 'backup_retention': '14',
    })
    assert response.status_code == HTTPStatus.FOUND
    assert not run_backup_cycle(database, logger, now=now + 86400)
    assert not list(next_folder.glob('homehub-*.sqlite3'))

    response = client.post('/admin/backups/settings', data={
        '_csrf': token(client), 'backup_folder': str(next_folder),
        'backup_frequency': '1', 'backup_retention': '1',
    })
    assert response.status_code == HTTPStatus.FOUND
    assert run_backup_cycle(database, logger, now=now + 86400)
    assert len(list(first_folder.glob('homehub-*.sqlite3'))) == 1
    assert len(list(next_folder.glob('homehub-*.sqlite3'))) == 1
    assert run_backup_cycle(database, logger, now=now + 2 * 86400)
    assert len(list(next_folder.glob('homehub-*.sqlite3'))) == 1


def test_backup_command_uses_configured_database_and_reports_failures(tmp_path, monkeypatch):
    _, _, database = make_admin_client(tmp_path)
    with closing(sqlite3.connect(database)) as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES('backup_interval_days','0')")
        db.commit()

    monkeypatch.setenv('HOMEHUB_DB', str(database))
    monkeypatch.setattr('sys.argv', ['homehub-backup'])
    assert backup_main() == 0

    monkeypatch.setenv('HOMEHUB_DB', str(tmp_path / 'missing.sqlite3'))
    assert backup_main() == 1


def test_forced_backup_ignores_pause_and_does_not_prune_existing_backups(tmp_path):
    _, _, database = make_admin_client(tmp_path)
    folder = tmp_path / 'preserved backups'
    folder.mkdir()
    with closing(sqlite3.connect(database)) as db:
        db.executemany('INSERT INTO app_settings(key,value) VALUES(?,?)', [
            ('backup_folder', str(folder)), ('backup_interval_days', '0'),
            ('backup_retention', '1'),
        ])
        db.commit()
    create_backup(database, folder)
    create_backup(database, folder)

    result = run_backup_cycle(
        database, logging.getLogger('test-forced-backup'), now=time.time(), force=True)

    assert result is True
    assert len(list(folder.glob('homehub-*.sqlite3'))) == 3


def test_backup_command_force_makes_immediate_verified_copy_when_disabled(
        tmp_path, monkeypatch):
    _, _, database = make_admin_client(tmp_path)
    with closing(sqlite3.connect(database)) as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES('backup_interval_days','0')")
        db.commit()
    monkeypatch.setenv('HOMEHUB_DB', str(database))
    monkeypatch.setattr('sys.argv', ['homehub-backup', '--force'])

    assert backup_main() == 0
    assert len(list((tmp_path / 'backups').glob('homehub-*.sqlite3'))) == 1
