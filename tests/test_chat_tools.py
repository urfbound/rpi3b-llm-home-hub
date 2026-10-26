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
        'get_weather_forecast', 'get_saved_articles', 'get_system_status',
        'get_admin_overview',
    }


def test_tool_catalog_covers_every_household_tab_except_games(app):
    names = {tool['function']['name'] for tool in tools_for(app, 'alice').definitions()}

    assert names == {
        'get_household_lists', 'get_calendar_events', 'get_household_recipes',
        'get_weather_forecast', 'get_saved_articles', 'get_system_status',
        'get_admin_overview',
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


def test_rss_tool_limits_articles_to_household_and_requester_feeds(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        household = db.execute(
            "INSERT INTO rss_feeds(normalized_url,title,scope,created_by) "
            "VALUES('https://house.example/rss','House feed','household',1)"
        ).lastrowid
        alice_feed = db.execute(
            "INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
            "VALUES('https://alice.example/rss','Alice feed','personal',2,2)"
        ).lastrowid
        bob_feed = db.execute(
            "INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
            "VALUES('https://bob.example/rss','Bob feed','personal',3,3)"
        ).lastrowid
        article_ids = []
        for index, (feed_id, title) in enumerate(
                ((household, 'House story'), (alice_feed, 'Alice story'),
                 (bob_feed, 'Bob story'))):
            article_ids.append(db.execute(
                'INSERT INTO rss_articles(feed_id,article_key,title,url,summary,published,'
                'first_seen_at,content_bytes) VALUES(?,?,?,?,?,?,?,0)',
                (feed_id, f'{index:064x}', title, None, 'Summary', '',
                 '2026-10-25T12:00:00+00:00'),
            ).lastrowid)
        db.execute('INSERT INTO rss_article_states(article_id,user_id,deleted_at) VALUES(?,?,?)',
                   (article_ids[0], 2, '2026-10-25T12:01:00+00:00'))

    result = tools_for(app, 'alice').call('get_saved_articles', {})
    titles = [article['title'] for article in result['articles']]

    assert titles == ['Alice story']
    assert 'Bob feed' not in json.dumps(result)
    assert 'House story' not in json.dumps(result)


def test_system_status_tool_applies_household_visibility_setting(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES('system_metrics_visible_to_all','0')")
        db.execute('INSERT INTO system_metrics(sampled_at,cpu_percent,ram_percent,disk_used_bytes,'
                   'disk_total_bytes,app_rss_bytes) VALUES(?,?,?,?,?,?)',
                   ('2026-10-25T12:00:00+00:00', 42.0, 55.0, 100, 500, 10))

    with pytest.raises(ChatToolError, match='not available'):
        tools_for(app, 'alice').call('get_system_status', {})

    result = tools_for(app, 'admin').call('get_system_status', {})
    assert result['cpu_percent'] == 42.0
    assert result['disk_used_bytes'] == 100


def test_admin_overview_is_admin_only_and_omits_credentials(app):
    with pytest.raises(ChatToolError, match='administrators'):
        tools_for(app, 'alice').call('get_admin_overview', {})

    result = tools_for(app, 'admin').call('get_admin_overview', {})

    assert result['accounts']['active'] == 3
    assert 'password_hash' not in json.dumps(result)
    assert 'alice chat password' not in json.dumps(result)
