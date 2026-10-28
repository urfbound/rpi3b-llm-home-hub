from datetime import datetime, timedelta, timezone
from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH, create_app
from homehub.constants import (
    CHAT_HISTORY_MAXIMUM,
    CHAT_PINNED_MAXIMUM,
    LATEST_SCHEMA_VERSION,
)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'chat-history-test-secret',
        'DATABASE': str(tmp_path / 'chat-history.sqlite3'),
    })
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin chat password'), 'admin', 'person'),
             ('alice', 'Alice', PH.hash('alice chat password'), 'user', 'person'),
             ('bob', 'Bob', PH.hash('bob chat password'), 'user', 'person'),
             ('house', 'House', PH.hash('house chat password'), 'user', 'house')],
        )
        db.execute("INSERT INTO app_settings(key,value) VALUES('chat_enabled','1')")
    return app


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': csrf(client),
    })
    assert response.status_code == HTTPStatus.FOUND
    return client


def post(client, path, **data):
    return client.post(path, data={**data, '_csrf': csrf(client)})


def make_chats(app, username, count, *, pinned=0):
    with sqlite3.connect(app.config['DATABASE']) as db:
        user_id = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()[0]
        now = datetime.now(timezone.utc)
        for index in range(count):
            db.execute(
                'INSERT INTO chat_conversations(owner_user_id,title,is_pinned,created_at,updated_at) '
                'VALUES(?,?,?,?,?)',
                (user_id, f'{username} conversation {index}', int(index < pinned),
                 (now + timedelta(seconds=index)).isoformat(),
                 (now + timedelta(seconds=index)).isoformat()),
            )
        return user_id


def test_chat_page_is_hidden_when_disabled_and_house_account_is_forbidden(app):
    alice = sign_in(app, 'alice', 'alice chat password')
    house = sign_in(app, 'house', 'house chat password')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE app_settings SET value='0' WHERE key='chat_enabled'")

    assert alice.get('/chat').status_code == HTTPStatus.NOT_FOUND
    assert b'>Chat</a>' not in alice.get('/').data
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE app_settings SET value='1' WHERE key='chat_enabled'")
    assert house.get('/chat').status_code == HTTPStatus.FORBIDDEN
    assert post(house, '/chat/new').status_code == HTTPStatus.FORBIDDEN
    assert b'>Chat</a>' not in house.get('/').data
    assert b'>Chat</a>' in alice.get('/').data


def test_users_can_only_view_and_delete_their_own_conversations(app):
    alice_id = make_chats(app, 'alice', 1)
    make_chats(app, 'bob', 1)
    alice = sign_in(app, 'alice', 'alice chat password')

    listing = alice.get('/chat')
    assert b'alice conversation 0' in listing.data
    assert b'bob conversation 0' not in listing.data
    assert b'alice conversation 0' in alice.get('/chat/1').data
    assert alice.get('/chat/2').status_code == HTTPStatus.NOT_FOUND
    assert post(alice, '/chat/2/delete').status_code == HTTPStatus.NOT_FOUND
    assert post(alice, '/chat/1/delete').status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM chat_conversations WHERE owner_user_id=?',
                          (alice_id,)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM chat_conversations').fetchone()[0] == 1


def test_chat_list_shows_a_private_last_prompt_preview(app):
    make_chats(app, 'alice', 1)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute(
            'INSERT INTO chat_pairs(conversation_id,position,prompt,response,model,created_at) '
            'VALUES(1,0,?,?,?,?)',
            ('What can I cook with lentils?', 'Try soup.', 'local-test',
             datetime.now(timezone.utc).isoformat()),
        )
    alice = sign_in(app, 'alice', 'alice chat password')
    page = alice.get('/chat').data
    listing = page.split(b'<ul class="chat-list">', 1)[1].split(b'</ul>', 1)[0]
    assert b'What can I cook with lentils?' in listing


def test_admin_can_view_and_delete_any_users_conversations(app):
    make_chats(app, 'alice', 1)
    admin = sign_in(app, 'admin', 'admin chat password')

    page = admin.get('/admin/chat/history')

    assert page.status_code == HTTPStatus.OK
    assert b'alice conversation 0' in page.data
    assert b'alice conversation 0' in admin.get('/chat/1').data
    assert post(admin, '/admin/chat/1/delete').status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM chat_conversations').fetchone()[0] == 0


def test_new_chat_evicts_oldest_unpinned_at_history_limit(app):
    make_chats(app, 'alice', CHAT_HISTORY_MAXIMUM, pinned=CHAT_PINNED_MAXIMUM)
    alice = sign_in(app, 'alice', 'alice chat password')

    response = post(alice, '/chat/new')

    assert response.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        rows = db.execute(
            'SELECT title,is_pinned FROM chat_conversations ORDER BY created_at'
        ).fetchall()
    assert len(rows) == CHAT_HISTORY_MAXIMUM
    assert rows[0][0] == 'alice conversation 0'
    assert all(row[0] != 'alice conversation 10' for row in rows)
    assert sum(row[1] for row in rows) == CHAT_PINNED_MAXIMUM


def test_new_chat_is_rejected_when_all_existing_chats_are_pinned(app):
    make_chats(app, 'alice', CHAT_HISTORY_MAXIMUM, pinned=CHAT_HISTORY_MAXIMUM)
    alice = sign_in(app, 'alice', 'alice chat password')

    response = post(alice, '/chat/new')

    assert response.status_code == HTTPStatus.FOUND
    assert b'All your chats are pinned' in alice.get('/chat').data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM chat_conversations').fetchone()[0] == (
            CHAT_HISTORY_MAXIMUM)


def test_user_can_pin_up_to_ten_chats_and_cannot_pin_another(app):
    make_chats(app, 'alice', CHAT_PINNED_MAXIMUM + 1)
    alice = sign_in(app, 'alice', 'alice chat password')

    for chat_id in range(1, CHAT_PINNED_MAXIMUM + 1):
        assert post(alice, f'/chat/{chat_id}/pin').status_code == HTTPStatus.FOUND
    blocked = post(alice, f'/chat/{CHAT_PINNED_MAXIMUM + 1}/pin')

    assert blocked.status_code == HTTPStatus.FOUND
    assert b'You can pin up to' in alice.get('/chat').data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM chat_conversations WHERE is_pinned=1').fetchone()[0] == (
            CHAT_PINNED_MAXIMUM)


def test_v15_database_upgrade_adds_chat_storage_without_losing_household_data(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('keep','hash','user')")
        db.execute("INSERT INTO recipes(title,ingredients,instructions,created_by) "
                   "VALUES('Keep recipe','','',1)")
        db.execute('DROP TABLE chat_pairs') if _table_exists(db, 'chat_pairs') else None
        db.execute('DROP TABLE chat_conversations') if _table_exists(db, 'chat_conversations') else None
        db.execute('PRAGMA user_version=15')
    from homehub.app import create_app

    create_app(app.config)

    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title FROM recipes').fetchone() == ('Keep recipe',)
        assert {'chat_conversations', 'chat_pairs'} <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_chat_history_admin_tools_are_not_available_to_regular_users(app):
    reader = sign_in(app, 'alice', 'alice chat password')

    assert reader.get('/admin/chat/history').status_code == HTTPStatus.FORBIDDEN
    assert post(reader, '/admin/chat/1/delete').status_code == HTTPStatus.FORBIDDEN


def test_admin_can_still_review_histories_when_chat_is_disabled(app):
    make_chats(app, 'alice', 1)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE app_settings SET value='0' WHERE key='chat_enabled'")
    admin = sign_in(app, 'admin', 'admin chat password')

    page = admin.get('/chat/1')

    assert page.status_code == HTTPStatus.OK
    assert b'alice conversation 0' in page.data
    assert b'This is a read-only admin view' in page.data
    assert b'name="prompt"' not in page.data


def _table_exists(db, name):
    return db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',
                      ('table', name)).fetchone() is not None
