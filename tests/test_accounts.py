import re
import sqlite3
import time

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret',
                      'DATABASE': str(tmp_path / 'hub.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
                   ('alex', 'Alex', PH.hash('correct horse battery'), 'admin'))
        db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
                   ('sam', 'Sam', PH.hash('another long password'), 'user'))
    return app


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(client, username='alex', password='correct horse battery'):
    client.get('/login')
    return client.post('/login', data={'username': username, 'password': password,
                                       '_csrf': csrf(client)})


def post(client, path, **data):
    return client.post(path, data={**data, '_csrf': csrf(client)})


def db_rows(app, query, parameters=()):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return db.execute(query, parameters).fetchall()


def test_accounts_are_admin_only(app):
    client = app.test_client()
    assert client.get('/admin/accounts').status_code == 302
    assert sign_in(client, 'sam', 'another long password').status_code == 302
    assert client.get('/admin/accounts').status_code == 403
    assert post(client, '/admin/accounts', username='new', display_name='New').status_code == 403


def test_create_and_force_password_change(app):
    admin = app.test_client()
    sign_in(admin)
    response = post(admin, '/admin/accounts', username='morgan', display_name='Morgan', role='user')
    assert response.status_code == 200
    assert b'Temporary password' in response.data
    password = re.search(rb'<code>([^<]+)</code>', response.data).group(1).decode()
    assert db_rows(app, 'SELECT must_change_password FROM users WHERE username=?', ('morgan',)) == [(1,)]
    member = app.test_client()
    assert sign_in(member, 'morgan', password).headers['Location'].endswith('/account/password')
    assert member.get('/').headers['Location'].endswith('/account/password')
    changed = post(member, '/account/password', current_password=password,
                   new_password='a replacement password', confirm_password='a replacement password')
    assert changed.status_code == 302
    assert sign_in(member, 'morgan', 'a replacement password').headers['Location'].endswith('/')


def test_reset_revokes_sessions(app):
    member = app.test_client()
    sign_in(member, 'sam', 'another long password')
    admin = app.test_client()
    sign_in(admin)
    user_id = db_rows(app, 'SELECT id FROM users WHERE username=?', ('sam',))[0][0]
    response = post(admin, f'/admin/accounts/{user_id}/reset-password')
    assert response.status_code == 200
    assert member.get('/').status_code == 302
    assert db_rows(app, 'SELECT must_change_password FROM users WHERE id=?', (user_id,)) == [(1,)]


@pytest.mark.parametrize('choice', ['transfer', 'delete'])
def test_removal_handles_owned_content(app, choice):
    with sqlite3.connect(app.config['DATABASE']) as db:
        sam = db.execute("SELECT id FROM users WHERE username='sam'").fetchone()[0]
        alex = db.execute("SELECT id FROM users WHERE username='alex'").fetchone()[0]
        db.execute('INSERT INTO shopping_items(text,created_by) VALUES(?,?)', ('Milk', sam))
        db.execute('INSERT INTO recipes(title,created_by) VALUES(?,?)', ('Soup', sam))
        db.execute('INSERT INTO events(title,event_date,created_by) VALUES(?,?,?)',
                   ('Dinner', '2026-10-21', sam))
    client = app.test_client()
    sign_in(client)
    assert post(client, f'/admin/accounts/{sam}/delete', content_choice=choice,
                transfer_to=alex, confirm_username='wrong').status_code == 400
    response = post(client, f'/admin/accounts/{sam}/delete', content_choice=choice,
                    transfer_to=alex, confirm_username='sam')
    assert response.status_code == 302
    assert not db_rows(app, 'SELECT id FROM users WHERE id=?', (sam,))
    for table in ('shopping_items', 'recipes', 'events'):
        rows = db_rows(app, f'SELECT created_by FROM {table}')
        assert rows == ([(alex,)] if choice == 'transfer' else [])


def test_house_account_is_read_only(app):
    admin = app.test_client()
    sign_in(admin)
    response = post(admin, '/admin/accounts', username='house', display_name='House-wide',
                    role='user', account_kind='house')
    password = re.search(rb'<code>([^<]+)</code>', response.data).group(1).decode()
    house = app.test_client()
    sign_in(house, 'house', password)
    assert house.get('/').status_code == 200
    assert post(house, '/shopping', text='No').status_code == 403


def test_removal_requires_valid_transfer_and_keeps_admin(app):
    client = app.test_client()
    sign_in(client)
    alex = db_rows(app, 'SELECT id FROM users WHERE username=?', ('alex',))[0][0]
    sam = db_rows(app, 'SELECT id FROM users WHERE username=?', ('sam',))[0][0]
    assert post(client, f'/admin/accounts/{sam}/delete', content_choice='transfer',
                transfer_to=sam, confirm_username='sam').status_code == 400
    assert post(client, f'/admin/accounts/{alex}/delete', content_choice='delete',
                confirm_username='alex').status_code == 400
    assert db_rows(app, 'SELECT COUNT(*) FROM users WHERE role=?', ('admin',)) == [(1,)]


def test_session_expiry_and_settings(app):
    client = app.test_client()
    sign_in(client)
    assert post(client, '/admin/session-settings', idle_hours='0', absolute_days='7').status_code == 400
    assert post(client, '/admin/session-settings', idle_hours='2', absolute_days='3').status_code == 302
    with client.session_transaction() as session:
        session['last_seen'] = int(time.time()) - 3 * 3600
    assert client.get('/admin/accounts').status_code == 302


def test_migrates_existing_user_table(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, "
                   "role TEXT, active INTEGER DEFAULT 1, session_version INTEGER DEFAULT 0)")
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                   ('legacy', PH.hash('long legacy password'), 'admin'))
    create_app({'TESTING': True, 'SECRET_KEY': 'test-secret', 'DATABASE': str(path)})
    assert db_rows(type('Config', (), {'config': {'DATABASE': str(path)}}),
                   'SELECT display_name,must_change_password,account_kind FROM users') == [
                       ('legacy', 0, 'person')]
