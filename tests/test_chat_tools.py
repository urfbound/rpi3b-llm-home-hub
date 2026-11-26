from datetime import date, datetime, timedelta
import json
import sqlite3

import pytest

from homehub.app import PH, create_app
from homehub.chat_tools import ChatDataTools, ChatToolError
from homehub.constants import (
    CHAT_HISTORY_MAXIMUM,
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


def add_chat(db, owner_id, title, updated_at):
    return db.execute(
        'INSERT INTO chat_conversations(owner_user_id,title,created_at,updated_at) '
        'VALUES(?,?,?,?)',
        (owner_id, title, updated_at, updated_at),
    ).lastrowid


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


def test_list_tool_includes_chore_definitions_for_the_home_and_lists_tabs(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO chores(weekday,text,created_by,assignee_id) "
                   "VALUES(0,'Put clothes away',1,2)")
        db.execute("INSERT INTO chores(weekday,text,created_by,assigned_everyone) "
                   "VALUES(6,'Tidy the living room',1,1)")

    result = tools_for(app, 'alice').call('get_household_lists', {})

    assert result['chores'] == [
        {'weekday': 0, 'text': 'Put clothes away', 'assigned_to': 'Alice'},
        {'weekday': 6, 'text': 'Tidy the living room', 'assigned_to': 'Everyone'},
    ]


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
        'get_household_lists', 'get_calendar_events', 'get_household_recipes',
        'get_weather_forecast', 'get_my_chat_history', 'get_home_hub_datetime',
    }


def test_host_datetime_tool_reports_the_server_local_time(app):
    before = datetime.now().astimezone()
    result = tools_for(app, 'alice').call('get_home_hub_datetime', {})
    after = datetime.now().astimezone()

    reported = datetime.fromisoformat(result['local_datetime'])
    assert before <= reported <= after
    assert reported.utcoffset() == before.utcoffset()
    assert result['timezone'] == before.tzname()


def test_tool_catalog_covers_requested_apps_and_chat_history(app):
    names = {tool['function']['name'] for tool in tools_for(app, 'alice').definitions()}

    assert names == {
        'get_household_lists', 'get_calendar_events', 'get_household_recipes',
        'get_weather_forecast', 'get_my_chat_history', 'get_home_hub_datetime',
    }
    assert all('game' not in name.casefold() for name in names)


def test_recipe_tool_obeys_private_recipe_visibility_and_returns_structure(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        shared_id = db.execute(
            "INSERT INTO recipes(title,description,created_by,visibility) "
            "VALUES('Soup','Weeknight meal',1,'shared')"
        ).lastrowid
        db.execute(
            "INSERT INTO recipes(title,description,created_by,visibility) "
            "VALUES('Secret recipe','private details',3,'private')"
        ).lastrowid
        allowed_id = db.execute(
            "INSERT INTO recipes(title,description,created_by,visibility) "
            "VALUES('Shared privately','Allowed detail',3,'private')"
        ).lastrowid
        db.execute('INSERT INTO recipe_access(recipe_id,user_id) VALUES(?,2)', (allowed_id,))
        db.execute("INSERT INTO recipe_ingredients(recipe_id,position,name) VALUES(?,0,'Flour')",
                   (shared_id,))
        db.execute("INSERT INTO recipe_steps(recipe_id,position,text) VALUES(?,0,'Mix gently')",
                   (shared_id,))
        db.execute('INSERT INTO recipe_notes(recipe_id,user_id,note) VALUES(?,3,?)',
                   (shared_id, 'Household note'))

    result = tools_for(app, 'alice').call('get_household_recipes', {})
    recipes = {recipe['title']: recipe for recipe in result['recipes']}

    assert set(recipes) == {'Soup', 'Shared privately'}
    assert recipes['Soup']['ingredients'][0]['name'] == 'Flour'
    assert recipes['Soup']['steps'] == ['Mix gently']
    assert recipes['Soup']['notes'][0]['note'] == 'Household note'
    assert 'Secret recipe' not in json.dumps(result)


def test_weather_tool_returns_only_the_configured_cached_forecast(app):
    forecast = {
        'current': {'temperature_2m': 12, 'apparent_temperature': 10,
                    'weather_code': 1, 'pressure_msl': 1012},
        'hourly': {'time': ['2026-10-25T12:00'], 'temperature_2m': [12]},
        'daily': {'time': ['2026-10-25', '2026-10-26'],
                  'temperature_2m_max': [15, 14]},
    }
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany('INSERT INTO app_settings(key,value) VALUES(?,?)', [
            ('weather_latitude', '44.0'), ('weather_longitude', '-63.0'),
            ('weather_timezone', 'America/Halifax'), ('weather_forecast_days', '5'),
            ('weather_units', 'metric'),
        ])
        db.execute('INSERT INTO weather_cache(cache_key,payload,fetched_at) VALUES(?,?,?)', (
            '[44.0,-63.0,"America/Halifax",5,"metric"]',
            json.dumps(forecast), '2026-10-25T12:00:00+00:00',
        ))

    result = tools_for(app, 'alice').call('get_weather_forecast', {})

    assert result['configured'] is True
    assert result['current']['temperature_2m'] == 12
    assert result['daily']['time'] == ['2026-10-25', '2026-10-26']
    assert result['hourly']['time'] == ['2026-10-25T12:00']


@pytest.mark.parametrize('username', ['alice', 'admin'])
@pytest.mark.parametrize('name', [
    'get_saved_articles', 'get_system_status', 'get_admin_overview',
])
def test_chat_data_tools_exclude_unrequested_apps(app, username, name):
    with pytest.raises(ChatToolError, match='not available'):
        tools_for(app, username).call(name, {})


def test_chat_history_tool_is_limited_to_the_requester_even_for_admins(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        alice_chat = add_chat(db, 2, 'Alice plans', '2026-10-25T10:00:00+00:00')
        bob_chat = add_chat(db, 3, 'Bob private', '2026-10-25T11:00:00+00:00')
        admin_chat = add_chat(db, 1, 'Admin notes', '2026-10-25T12:00:00+00:00')
        for conversation_id, prompt, response in (
            (alice_chat, 'Alice earlier question', 'Alice earlier answer'),
            (bob_chat, 'Bob secret question', 'Bob secret answer'),
            (admin_chat, 'Admin earlier question', 'Admin earlier answer'),
        ):
            db.execute(
                'INSERT INTO chat_pairs(conversation_id,position,prompt,response,model,created_at) '
                'VALUES(?,0,?,?,?,?)',
                (conversation_id, prompt, response, 'test-model', '2026-10-25T12:00:00+00:00'),
            )

    alice_history = tools_for(app, 'alice').call('get_my_chat_history', {})
    admin_history = tools_for(app, 'admin').call('get_my_chat_history', {})

    assert [chat['title'] for chat in alice_history['conversations']] == ['Alice plans']
    assert alice_history['conversations'][0]['pairs'][0]['prompt'] == 'Alice earlier question'
    assert [chat['title'] for chat in admin_history['conversations']] == ['Admin notes']
    assert 'Bob private' not in json.dumps(alice_history)
    assert 'Bob secret' not in json.dumps(alice_history)
    assert 'Alice plans' not in json.dumps(admin_history)
    assert 'Alice earlier' not in json.dumps(admin_history)


def test_chat_history_tool_rejects_other_user_arguments(app):
    with pytest.raises(ChatToolError, match='does not accept arguments'):
        tools_for(app, 'alice').call('get_my_chat_history', {'user_id': 3})


@pytest.mark.parametrize(('account_kind', 'active'), [('house', 1), ('person', 0)])
def test_chat_history_tool_rejects_house_and_inactive_accounts(app, account_kind, active):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind,active) '
            'VALUES(?,?,?,?,?,?)',
            ('restricted', 'Restricted', PH.hash('restricted chat password'),
             'user', account_kind, active),
        )

    with pytest.raises(ChatToolError, match='cannot use Chat data tools'):
        tools_for(app, 'restricted').call('get_my_chat_history', {})


def test_chat_history_tool_caps_conversations_rows_and_result_bytes(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        for chat_index in range(CHAT_HISTORY_MAXIMUM + 4):
            conversation_id = add_chat(
                db, 2, f'Chat {chat_index}',
                f'2026-10-25T{chat_index:02d}:00:00+00:00',
            )
            for pair_index in range(8):
                db.execute(
                    'INSERT INTO chat_pairs(conversation_id,position,prompt,response,model,created_at) '
                    'VALUES(?,?,?,?,?,?)',
                    (conversation_id, pair_index, f'Question {pair_index}', 'Answer',
                     'test-model', '2026-10-25T12:00:00+00:00'),
                )

    row_limited = tools_for(app, 'alice').call('get_my_chat_history', {})

    assert len(row_limited['conversations']) <= CHAT_HISTORY_MAXIMUM
    assert row_limited['row_count'] <= CHAT_TOOL_RESULT_ROWS_MAXIMUM
    assert row_limited['truncated'] is True

    with sqlite3.connect(app.config['DATABASE']) as db:
        large_chat = add_chat(db, 2, 'Large chat', '2027-01-01T00:00:00+00:00')
        db.executemany(
            'INSERT INTO chat_pairs(conversation_id,position,prompt,response,model,created_at) '
            'VALUES(?,?,?,?,?,?)',
            [(large_chat, index, f'Question {index}', 'x' * 3000, 'test-model',
              '2027-01-01T00:00:00+00:00') for index in range(10)],
        )

    byte_limited = tools_for(app, 'alice').call('get_my_chat_history', {})

    assert byte_limited['truncated'] is True
    assert byte_limited['row_count'] <= CHAT_TOOL_RESULT_ROWS_MAXIMUM
    assert len(json.dumps(byte_limited, ensure_ascii=False).encode('utf-8')) <= (
        CHAT_TOOL_RESULT_BYTES_MAXIMUM)
    assert byte_limited['conversations'][0]['title'] == 'Large chat'
    assert byte_limited['conversations'][0]['pairs'][-1]['prompt'] == 'Question 9'
