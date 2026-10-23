import sqlite3

import pytest

from homehub.app import PH, create_app, get_db
from flask import g


@pytest.fixture
def client(tmp_path):
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'test-only-secret',
        'DATABASE': str(tmp_path / 'homehub.db'),
    })
    with app.app_context():
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                   ('alice', PH.hash('a long test password'), 'admin'))
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                   ('bob', PH.hash('another long password'), 'user'))
        db.execute("INSERT INTO household_lists(kind,title,visibility,created_by) "
                   "VALUES('shopping','Groceries','shared',1)")
        list_id = db.execute("SELECT id FROM household_lists WHERE title='Groceries'").fetchone()[0]
        db.execute("INSERT INTO shopping_items(text,done,created_by,list_id,updated_at) "
                   "VALUES('Milk',0,1,?,'2026-10-22 10:00:00')", (list_id,))
        item_id = db.execute("SELECT id FROM shopping_items WHERE text='Milk'").fetchone()[0]
        db.commit()
    app.config['TEST_LIST_ID'] = list_id
    app.config['TEST_ITEM_ID'] = item_id
    return app.test_client()


def login(client, username='alice', password='a long test password'):
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post('/login', data={
        'username': username, 'password': password, '_csrf': token,
    }).status_code == 302
    with client.session_transaction() as session:
        return session['csrf_token']


def test_away_mode_snapshot_contains_only_items_visible_to_user(client):
    login(client)
    response = client.get(f"/shopping/sync?list_id={client.application.config['TEST_LIST_ID']}")

    assert response.status_code == 200
    data = response.get_json()
    assert data['list']['title'] == 'Groceries'
    assert data['items'] == [{
        'id': client.application.config['TEST_ITEM_ID'],
        'text': 'Milk',
        'done': False,
        'updated_at': '2026-10-22 10:00:00',
    }]


def test_shopping_page_exposes_away_mode_state_for_cached_items(client):
    login(client)
    response = client.get('/')

    assert response.status_code == 200
    assert b'data-away-list-id="1"' in response.data
    assert b'data-away-item' in response.data
    assert b'data-away-updated-at="2026-10-22 10:00:00"' in response.data
    assert b'homehub-user-id' in response.data
    assert b'data-away-login' in response.data


def test_away_mode_sync_applies_checkbox_state_and_returns_fresh_snapshot(client):
    token = login(client)
    item_id = client.application.config['TEST_ITEM_ID']
    list_id = client.application.config['TEST_LIST_ID']

    response = client.post('/shopping/sync', json={
        'list_id': list_id,
        'changes': [{'id': item_id, 'done': True, 'base_done': False,
                     'base_updated_at': '2026-10-22 10:00:00'}],
    }, headers={'X-CSRF-Token': token})

    assert response.status_code == 200
    assert response.get_json()['results'] == [{'id': item_id, 'status': 'updated', 'done': True}]
    assert response.get_json()['items'][0]['done'] is True


def test_away_mode_sync_reports_conflict_without_overwriting_other_users_change(client):
    token = login(client)
    item_id = client.application.config['TEST_ITEM_ID']
    list_id = client.application.config['TEST_LIST_ID']
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute("UPDATE shopping_items SET done=1,updated_at='2026-10-22 10:00:00' WHERE id=?",
                   (item_id,))

    response = client.post('/shopping/sync', json={
        'list_id': list_id,
        'changes': [{'id': item_id, 'done': False, 'base_done': False,
                     'base_updated_at': '2026-10-22 10:00:00'}],
    }, headers={'X-CSRF-Token': token})

    assert response.status_code == 200
    assert response.get_json()['results'] == [{
        'id': item_id, 'status': 'conflict', 'server_done': True, 'requested_done': False,
    }]
    assert response.get_json()['items'][0]['done'] is True


def test_away_mode_sync_identifies_deleted_items(client):
    token = login(client)
    item_id = client.application.config['TEST_ITEM_ID']
    list_id = client.application.config['TEST_LIST_ID']
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute('DELETE FROM shopping_items WHERE id=?', (item_id,))

    response = client.post('/shopping/sync', json={
        'list_id': list_id,
        'changes': [{'id': item_id, 'done': True, 'base_done': False,
                     'base_updated_at': '2026-10-22 10:00:00'}],
    }, headers={'X-CSRF-Token': token})

    assert response.status_code == 200
    assert response.get_json()['results'] == [{'id': item_id, 'status': 'deleted'}]


def test_away_mode_sync_rechecks_list_access_and_requires_login(client):
    list_id = client.application.config['TEST_LIST_ID']
    assert client.get(f'/shopping/sync?list_id={list_id}').status_code == 302
    login(client, 'bob', 'another long password')
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute("UPDATE household_lists SET visibility='private' WHERE id=?", (list_id,))
    assert client.get(f'/shopping/sync?list_id={list_id}').status_code == 404


def test_away_mode_sync_rejects_invalid_payload_and_house_account(client):
    token = login(client)
    assert client.post('/shopping/sync', json={'list_id': 1, 'changes': 'bad'},
                       headers={'X-CSRF-Token': token}).status_code == 400
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute("INSERT INTO users(username,password_hash,role,account_kind) "
                   "VALUES('house',?,'user','house')", (PH.hash('house account password'),))
    house = client.application.test_client()
    house_token = login(house, 'house', 'house account password')
    assert house.post('/shopping/sync', json={'list_id': 1, 'changes': []},
                      headers={'X-CSRF-Token': house_token}).status_code == 403


def test_away_mode_offline_shell_and_service_worker_are_available(client):
    offline = client.get('/offline.html')
    worker = client.get('/service-worker.js')

    assert offline.status_code == 200
    assert b'away-mode.js' in offline.data
    assert worker.status_code == 200
    assert worker.headers['Service-Worker-Allowed'] == '/'
    assert b'offline.html' in worker.data


def test_away_mode_snapshot_respects_private_allowlist(client):
    list_id = client.application.config['TEST_LIST_ID']
    bob = client.application.test_client()
    login(bob, 'bob', 'another long password')
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute("UPDATE household_lists SET visibility='private' WHERE id=?", (list_id,))
        db.execute('INSERT INTO list_access(list_id,user_id) VALUES(?,2)', (list_id,))
    assert bob.get(f'/shopping/sync?list_id={list_id}').status_code == 200


def test_away_mode_sync_requires_csrf_header(client):
    login(client)
    response = client.post('/shopping/sync', json={
        'list_id': client.application.config['TEST_LIST_ID'], 'changes': [],
    })
    assert response.status_code == 400
