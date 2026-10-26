from http import HTTPStatus
import sqlite3
import time
from threading import Event

import pytest

from homehub.app import PH, create_app
from homehub.constants import (
    CHAT_PAIRS_PER_CHAT_DEFAULT,
    CHAT_PROMPT_MAXIMUM,
    CHAT_TOOL_RESULT_BYTES_MAXIMUM,
)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'chat-requests-test-secret',
        'DATABASE': str(tmp_path / 'chat-requests.sqlite3'),
        'CHAT_COMPLETION': lambda _model, _messages: 'Local answer',
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
        db.executemany(
            'INSERT INTO app_settings(key,value) VALUES(?,?)',
            [('chat_enabled', '1'), ('chat_model', 'gemma3:270m'),
             ('chat_max_queue_depth', '8'), ('chat_pairs_per_chat', '16')],
        )
        db.execute(
            "INSERT INTO chat_conversations(owner_user_id,title,created_at,updated_at) "
            "VALUES(2,'New chat','2026-10-24T10:00:00+00:00','2026-10-24T10:00:00+00:00')"
        )
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


def submit(client, prompt, **extra):
    return client.post(
        '/chat/1/send', data={'prompt': prompt, '_csrf': csrf(client), **extra},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )


def wait_for_request(client, request_id, wanted='complete'):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f'/chat/requests/{request_id}')
        if response.status_code == HTTPStatus.OK:
            data = response.get_json()
            if data['status'] == wanted:
                return data
        time.sleep(0.01)
    pytest.fail(f'Chat request {request_id} did not reach {wanted}')


def test_chat_request_saves_prompt_answer_model_and_updates_title(app):
    alice = sign_in(app, 'alice', 'alice chat password')

    queued = submit(alice, 'How is dinner going?')

    assert queued.status_code == HTTPStatus.ACCEPTED
    request_id = queued.get_json()['request_id']
    assert wait_for_request(alice, request_id)['queue_position'] is None
    page = alice.get('/chat/1')
    assert b'How is dinner going?' in page.data
    assert b'Local answer' in page.data
    assert b'Local model' in page.data
    assert b'gemma3:270m' in page.data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM chat_conversations WHERE id=1').fetchone() == (
            'How is dinner going?',)
        assert db.execute(
            'SELECT prompt,response,model FROM chat_pairs WHERE conversation_id=1'
        ).fetchone() == ('How is dinner going?', 'Local answer', 'gemma3:270m')


def test_chat_conversation_includes_busy_status_and_disable_controls(app):
    alice = sign_in(app, 'alice', 'alice chat password')

    page = alice.get('/chat/1')

    assert b'id="chat-prompt"' in page.data
    assert b'id="chat-send-button"' in page.data
    assert b'id="chat-request-status-text"' in page.data
    assert b'id="chat-request-ellipsis"' in page.data
    assert b'.chat-working-ellipsis[hidden]{display:none}' in page.data
    assert b'chat-ui.js' in page.data
    assert b'HomeHubChatUI.setBusy' in page.data
    assert b'HomeHubChatUI.setWorkingStatus' in page.data


@pytest.mark.parametrize('prompt', ['', 'x' * (CHAT_PROMPT_MAXIMUM + 1)])
def test_chat_rejects_blank_or_overlong_prompts(app, prompt):
    alice = sign_in(app, 'alice', 'alice chat password')

    response = submit(alice, prompt)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert app.extensions['homehub_chat_queue'].wait_idle(1)
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM chat_pairs').fetchone()[0] == 0


def test_history_limit_requires_confirmation_and_drops_oldest_only_after_success(app):
    alice = sign_in(app, 'alice', 'alice chat password')
    with sqlite3.connect(app.config['DATABASE']) as db:
        for index in range(CHAT_PAIRS_PER_CHAT_DEFAULT):
            db.execute(
                'INSERT INTO chat_pairs(conversation_id,position,prompt,response,model,created_at) '
                'VALUES(1,?,?,?,?,?)',
                (index, f'old prompt {index}', f'old answer {index}', 'gemma3:270m',
                 f'2026-10-24T10:{index:02d}:00+00:00'),
            )

    rejected = submit(alice, 'Next prompt')
    assert rejected.status_code == HTTPStatus.FOUND
    assert b'oldest prompt-and-response pair' in alice.get('/chat/1').data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM chat_pairs').fetchone()[0] == (
            CHAT_PAIRS_PER_CHAT_DEFAULT)

    queued = submit(alice, 'Next prompt', confirm_drop_oldest='1')
    assert queued.status_code == HTTPStatus.ACCEPTED
    wait_for_request(alice, queued.get_json()['request_id'])
    with sqlite3.connect(app.config['DATABASE']) as db:
        prompts = db.execute(
            'SELECT prompt FROM chat_pairs WHERE conversation_id=1 ORDER BY position'
        ).fetchall()
    assert len(prompts) == CHAT_PAIRS_PER_CHAT_DEFAULT
    assert prompts[0][0] == 'old prompt 1'
    assert prompts[-1][0] == 'Next prompt'


def test_queued_request_uses_pair_limit_seen_at_submission(app):
    started = Event()
    release = Event()

    def slow_completion(_model, messages):
        prompt = messages[-1]['content']
        if prompt == 'First blocking prompt':
            started.set()
            assert release.wait(2)
        return 'Completed answer'

    app.config['CHAT_COMPLETION'] = slow_completion
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute(
            'INSERT INTO chat_pairs(conversation_id,position,prompt,response,model,created_at) '
            "VALUES(2,0,'Keep this prompt','Keep this answer','gemma3:270m','2026-10-24T10:00:00+00:00')"
        )
        db.execute(
            "INSERT INTO chat_conversations(owner_user_id,title,created_at,updated_at) "
            "VALUES(2,'Queued chat','2026-10-24T10:00:00+00:00','2026-10-24T10:00:00+00:00')"
        )
    alice = sign_in(app, 'alice', 'alice chat password')

    running = submit(alice, 'First blocking prompt')
    assert running.status_code == HTTPStatus.ACCEPTED
    assert started.wait(2)
    queued = alice.post('/chat/2/send', data={
        'prompt': 'Second prompt', '_csrf': csrf(alice),
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert queued.status_code == HTTPStatus.ACCEPTED
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE app_settings SET value='1' WHERE key='chat_pairs_per_chat'")
    release.set()
    wait_for_request(alice, running.get_json()['request_id'])
    wait_for_request(alice, queued.get_json()['request_id'])

    with sqlite3.connect(app.config['DATABASE']) as db:
        prompts = db.execute(
            'SELECT prompt FROM chat_pairs WHERE conversation_id=2 ORDER BY position'
        ).fetchall()
    assert prompts == [('Keep this prompt',), ('Second prompt',)]


def test_request_status_and_response_are_private_to_the_chat_owner(app):
    alice = sign_in(app, 'alice', 'alice chat password')
    bob = sign_in(app, 'bob', 'bob chat password')

    queued = submit(alice, 'Private question')
    request_id = queued.get_json()['request_id']

    assert wait_for_request(alice, request_id)['status'] == 'complete'
    assert bob.get(f'/chat/requests/{request_id}').status_code == HTTPStatus.NOT_FOUND
    assert bob.get('/chat/1').status_code == HTTPStatus.NOT_FOUND


def test_chat_rejects_requests_when_waiting_queue_is_full(app):
    first_started = Event()
    release_first = Event()

    def slow_completion(_model, _messages):
        first_started.set()
        assert release_first.wait(2)
        return 'Done'

    app.config['CHAT_COMPLETION'] = slow_completion
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE app_settings SET value='2' WHERE key='chat_max_queue_depth'")
        db.execute(
            "INSERT INTO chat_conversations(owner_user_id,title,created_at,updated_at) "
            "VALUES(2,'Second chat','2026-10-24T10:00:00+00:00','2026-10-24T10:00:00+00:00')"
        )
        db.execute(
            "INSERT INTO chat_conversations(owner_user_id,title,created_at,updated_at) "
            "VALUES(1,'Admin chat','2026-10-24T10:00:00+00:00','2026-10-24T10:00:00+00:00')"
        )
        db.execute(
            "INSERT INTO chat_conversations(owner_user_id,title,created_at,updated_at) "
            "VALUES(2,'Third chat','2026-10-24T10:00:00+00:00','2026-10-24T10:00:00+00:00')"
        )
    alice = sign_in(app, 'alice', 'alice chat password')
    admin = sign_in(app, 'admin', 'admin chat password')
    first = alice.post('/chat/1/send', data={
        'prompt': 'First', '_csrf': csrf(alice),
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert first.status_code == HTTPStatus.ACCEPTED
    assert first_started.wait(2)
    waiting = alice.post('/chat/2/send', data={
        'prompt': 'Waiting', '_csrf': csrf(alice),
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    admin_request = admin.post('/chat/3/send', data={
        'prompt': 'Please prioritize me', '_csrf': csrf(admin),
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    waiting_status = alice.get(
        f"/chat/requests/{waiting.get_json()['request_id']}"
    ).get_json()
    admin_status = admin.get(
        f"/chat/requests/{admin_request.get_json()['request_id']}"
    ).get_json()
    full = alice.post('/chat/4/send', data={
        'prompt': 'Queue is full', '_csrf': csrf(alice),
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    release_first.set()

    assert waiting.status_code == HTTPStatus.ACCEPTED
    assert admin_request.status_code == HTTPStatus.ACCEPTED
    assert waiting_status == {
        'status': 'queued', 'queue_position': 2, 'error': '', 'conversation_id': 2,
    }
    assert admin_status == {
        'status': 'queued', 'queue_position': 1, 'error': '', 'conversation_id': 3,
    }
    assert full.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert full.get_json()['error'] == 'The Chat waiting queue is full. Try again shortly.'
    assert app.extensions['homehub_chat_queue'].wait_idle(2)


def test_chat_requires_enabled_feature_and_a_selected_model(app):
    alice = sign_in(app, 'alice', 'alice chat password')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE app_settings SET value='0' WHERE key='chat_enabled'")
    assert submit(alice, 'Hidden').status_code == HTTPStatus.NOT_FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE app_settings SET value='1' WHERE key='chat_enabled'")
        db.execute("UPDATE app_settings SET value='' WHERE key='chat_model'")
    assert submit(alice, 'No model').status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_enabled_chat_data_tools_are_scoped_and_model_receives_bounded_result(app):
    calls = []

    def completion_with_tools(_model, messages, tools):
        calls.append((messages, tools))
        if len(calls) == 1:
            return {'content': '', 'tool_calls': [{
                'function': {'name': 'get_household_lists', 'arguments': {}},
            }]}
        return {'content': 'Your shared list has bread.', 'tool_calls': []}

    app.config['CHAT_COMPLETION_WITH_TOOLS'] = completion_with_tools
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute(
            "INSERT INTO app_settings(key,value) VALUES('chat_data_tools_enabled','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )
        shared = db.execute(
            "INSERT INTO household_lists(kind,title,category,visibility,created_by) "
            "VALUES('shopping','Groceries','Other','shared',1)"
        ).lastrowid
        hidden = db.execute(
            "INSERT INTO household_lists(kind,title,category,visibility,created_by) "
            "VALUES('shopping','Bob private','Other','private',3)"
        ).lastrowid
        db.execute("INSERT INTO shopping_items(text,created_by,list_id) VALUES('Bread',1,?)", (shared,))
        db.execute("INSERT INTO shopping_items(text,created_by,list_id) VALUES('Secret',3,?)", (hidden,))
    alice = sign_in(app, 'alice', 'alice chat password')

    queued = submit(alice, 'What do I need?')
    assert queued.status_code == HTTPStatus.ACCEPTED
    wait_for_request(alice, queued.get_json()['request_id'])

    assert {tool['function']['name'] for tool in calls[0][1]} == {
        'get_household_lists', 'get_calendar_events', 'get_household_recipes',
        'get_weather_forecast', 'get_saved_articles', 'get_system_status',
        'get_admin_overview',
    }
    serialized_tool_result = calls[1][0][-1]['content']
    assert 'Groceries' in serialized_tool_result and 'Bread' in serialized_tool_result
    assert 'Bob private' not in serialized_tool_result and 'Secret' not in serialized_tool_result
    assert len(serialized_tool_result.encode()) <= CHAT_TOOL_RESULT_BYTES_MAXIMUM


def test_chat_data_tools_are_omitted_when_admin_setting_is_off(app):
    calls = []
    app.config['CHAT_COMPLETION_WITH_TOOLS'] = lambda _model, messages, tools: (
        calls.append(tools) or {'content': 'No tools attached.', 'tool_calls': []})
    alice = sign_in(app, 'alice', 'alice chat password')

    queued = submit(alice, 'Hello')
    assert queued.status_code == HTTPStatus.ACCEPTED
    wait_for_request(alice, queued.get_json()['request_id'])

    assert calls == []
