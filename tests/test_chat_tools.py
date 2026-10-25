from datetime import date, timedelta
import json
import sqlite3

import pytest

from homehub.app import PH, create_app
from homehub.chat_tools import ChatDataTools, ChatToolError
from homehub.constants import (
    CHAT_TOOL_RESULT_BYTES_MAXIMUM,
    CHAT_TOOL_RESULT_ROWS_MAXIMUM,
)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'chat-tools-test-secret',
        'DATABASE': str(tmp_path / 'chat-tools.sqlite3'),
    })
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin chat password'), 'admin', 'person'),
             ('alice', 'Alice', PH.hash('alice chat password'), 'user', 'person'),
             ('bob', 'Bob', PH.hash('bob chat password'), 'user', 'person')],
        )
        db.execute('DELETE FROM household_lists')
    return app


def add_list(db, title, visibility, owner, default=0):
    return db.execute(
        'INSERT INTO household_lists(kind,title,category,visibility,created_by,is_default) '
        'VALUES(?,?,?,?,?,?)',
        ('shopping', title, 'Other', visibility, owner, default),
    ).lastrowid


def add_item(db, list_id, text, owner):
    db.execute('INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
               (text, owner, list_id))


def tools_for(app, username):
    with sqlite3.connect(app.config['DATABASE']) as db:
        user = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
    return ChatDataTools(app.config['DATABASE'], user[0])


def test_list_tool_returns_only_lists_and_items_the_requester_can_access(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        shared = add_list(db, 'Groceries', 'shared', 1, 1)
        own_private = add_list(db, 'Alice private', 'private', 2)
        allowed_private = add_list(db, 'Shared with Alice', 'private', 3)
        hidden_private = add_list(db, 'Bob private', 'private', 3)
        db.execute('INSERT INTO list_access(list_id,user_id) VALUES(?,2)', (allowed_private,))
        add_item(db, shared, 'Bread', 1)
        add_item(db, own_private, 'Alice journal', 2)
        add_item(db, allowed_private, 'Shared flour', 3)
        add_item(db, hidden_private, 'Bob journal', 3)

    result = tools_for(app, 'alice').call('get_household_lists', {})
    titles = [item['title'] for item in result['lists']]
    item_text = [item['text'] for row in result['lists'] for item in row['items']]

    assert titles == ['Groceries', 'Alice private', 'Shared with Alice']
    assert item_text == ['Bread', 'Alice journal', 'Shared flour']
    assert 'Bob private' not in json.dumps(result)
    assert 'Bob journal' not in json.dumps(result)


def test_list_tool_caps_rows_and_serialized_result_bytes(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        shared = add_list(db, 'Shared', 'shared', 1)
        for index in range(200):
            add_item(db, shared, f'item {index} ' + 'x' * 180, 1)

    result = tools_for(app, 'alice').call('get_household_lists', {})

    assert result['row_count'] <= CHAT_TOOL_RESULT_ROWS_MAXIMUM
    assert result['truncated'] is True
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= CHAT_TOOL_RESULT_BYTES_MAXIMUM


def test_calendar_tool_obeys_shared_private_and_participant_permissions(app):
    today = date.today()
    with sqlite3.connect(app.config['DATABASE']) as db:
        for title, visibility, creator, offset in (
            ('Shared dinner', 'shared', 1, 1),
            ('Alice private', 'private', 2, 2),
            ('Bob private', 'private', 3, 3),
            ('Secret shared with Bob', 'private', 1, 4),
        ):
            cursor = db.execute(
                'INSERT INTO events(title,event_date,created_by,visibility) VALUES(?,?,?,?)',
                (title, (today + timedelta(days=offset)).isoformat(), creator, visibility),
            )
            if title == 'Secret shared with Bob':
                db.execute('INSERT INTO event_participants(event_id,user_id) VALUES(?,3)',
                           (cursor.lastrowid,))

    alice = tools_for(app, 'alice').call('get_calendar_events', {
        'start_date': today.isoformat(), 'end_date': (today + timedelta(days=7)).isoformat(),
    })
    bob = tools_for(app, 'bob').call('get_calendar_events', {
        'start_date': today.isoformat(), 'end_date': (today + timedelta(days=7)).isoformat(),
    })
    admin = tools_for(app, 'admin').call('get_calendar_events', {
        'start_date': today.isoformat(), 'end_date': (today + timedelta(days=7)).isoformat(),
    })

    assert {row['title'] for row in alice['events']} == {'Shared dinner', 'Alice private'}
    assert {row['title'] for row in bob['events']} == {
        'Shared dinner', 'Secret shared with Bob', 'Bob private'}
    assert {row['title'] for row in admin['events']} == {
        'Shared dinner', 'Alice private', 'Bob private', 'Secret shared with Bob'}


@pytest.mark.parametrize('args', [
    {'start_date': 'not-a-date', 'end_date': '2026-10-20'},
    {'start_date': '2026-10-01', 'end_date': '2026-12-01'},
])
def test_calendar_tool_rejects_invalid_or_oversized_date_ranges(app, args):
    with pytest.raises(ChatToolError):
        tools_for(app, 'alice').call('get_calendar_events', args)


def test_chat_data_tool_interface_has_no_write_or_arbitrary_sql_methods(app):
    tools = tools_for(app, 'alice')

    with pytest.raises(ChatToolError):
        tools.call('delete_list', {'list_id': 1})
    assert not hasattr(tools, 'execute')
    assert {tool['function']['name'] for tool in tools.definitions()} == {
        'get_household_lists', 'get_calendar_events',
    }
