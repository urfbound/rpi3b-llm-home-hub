from http import HTTPStatus
import sqlite3

import pytest

from homehub.constants import (
    CHAT_MAX_QUEUE_DEPTH_DEFAULT,
    CHAT_MAX_QUEUE_DEPTH_MAXIMUM,
    CHAT_MAX_QUEUE_DEPTH_MINIMUM,
    CHAT_PAIRS_PER_CHAT_DEFAULT,
    CHAT_PAIRS_PER_CHAT_MAXIMUM,
    CHAT_PAIRS_PER_CHAT_MINIMUM,
)
import homehub.app as app_module
from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'list_ollama_models',
                        lambda *_args: ['gemma3:270m', 'qwen2.5:0.5b'],
                        raising=False)
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': 'chat-settings-test-secret',
        'DATABASE': str(tmp_path / 'chat-settings.sqlite3'),
    })
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin chat password'), 'admin', 'person'),
             ('reader', 'Reader', PH.hash('reader chat password'), 'user', 'person'),
             ('house', 'House', PH.hash('house chat password'), 'user', 'house')],
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


def post(client, **data):
    return client.post('/admin/chat/settings', data={**data, '_csrf': csrf(client)})


def setting_values(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return dict(db.execute(
            "SELECT key,value FROM app_settings WHERE key LIKE 'chat_%'"
        ))


def test_chat_is_hidden_by_default_and_admin_can_configure_installed_model(app):
    admin = sign_in(app, 'admin', 'admin chat password')

    page = admin.get('/admin/accounts')

    assert b'Chat settings' in page.data
    assert b'name="chat_enabled" value="1"' in page.data
    assert b'name="chat_enabled" value="1" checked' not in page.data
    assert b'Enable Chat</label>' in page.data
    assert b'value="gemma3:270m" selected' in page.data
    assert b'name="max_queue_depth"' in page.data
    assert f'value="{CHAT_MAX_QUEUE_DEPTH_DEFAULT}"'.encode() in page.data
    assert b'name="pairs_per_chat"' in page.data
    assert f'value="{CHAT_PAIRS_PER_CHAT_DEFAULT}"'.encode() in page.data
    assert b'name="chat_data_tools_enabled" value="1"' in page.data
    assert b'name="chat_data_tools_enabled" value="1" checked' not in page.data
    assert b'Prompts and any enabled household context are sent to the selected agent node.' in page.data
    response = post(admin, chat_enabled='1', model='qwen2.5:0.5b',
                    max_queue_depth='12', pairs_per_chat='24',
                    chat_data_tools_enabled='1')

    assert response.status_code == HTTPStatus.FOUND
    assert setting_values(app) == {
        'chat_enabled': '1', 'chat_model': 'qwen2.5:0.5b',
        'chat_max_queue_depth': '12', 'chat_pairs_per_chat': '24',
        'chat_data_tools_enabled': '1',
    }


@pytest.mark.parametrize('model', ['missing:model', ''])
def test_chat_cannot_be_enabled_without_an_installed_model(app, model):
    admin = sign_in(app, 'admin', 'admin chat password')

    response = post(admin, chat_enabled='1', model=model,
                    max_queue_depth='8', pairs_per_chat='16')

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert setting_values(app) == {}


@pytest.mark.parametrize(('queue_depth', 'pair_limit'), [
    (str(CHAT_MAX_QUEUE_DEPTH_MINIMUM - 1), str(CHAT_PAIRS_PER_CHAT_DEFAULT)),
    (str(CHAT_MAX_QUEUE_DEPTH_MAXIMUM + 1), str(CHAT_PAIRS_PER_CHAT_DEFAULT)),
    (str(CHAT_MAX_QUEUE_DEPTH_DEFAULT), str(CHAT_PAIRS_PER_CHAT_MINIMUM - 1)),
    (str(CHAT_MAX_QUEUE_DEPTH_DEFAULT), str(CHAT_PAIRS_PER_CHAT_MAXIMUM + 1)),
    ('invalid', str(CHAT_PAIRS_PER_CHAT_DEFAULT)),
    (str(CHAT_MAX_QUEUE_DEPTH_DEFAULT), 'invalid'),
])
def test_chat_settings_reject_out_of_range_limits(app, queue_depth, pair_limit):
    admin = sign_in(app, 'admin', 'admin chat password')

    response = post(admin, chat_enabled='1', model='gemma3:270m',
                    max_queue_depth=queue_depth, pairs_per_chat=pair_limit)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert setting_values(app) == {}


def test_disabling_chat_hides_it_without_erasing_saved_model(app):
    admin = sign_in(app, 'admin', 'admin chat password')
    post(admin, chat_enabled='1', model='gemma3:270m',
         max_queue_depth='8', pairs_per_chat='16')

    response = post(admin, model='qwen2.5:0.5b', max_queue_depth='8', pairs_per_chat='16')

    assert response.status_code == HTTPStatus.FOUND
    assert setting_values(app) == {
        'chat_enabled': '0', 'chat_model': 'qwen2.5:0.5b',
        'chat_max_queue_depth': '8', 'chat_pairs_per_chat': '16',
        'chat_data_tools_enabled': '0',
    }


def test_only_admins_can_change_chat_settings(app):
    reader = sign_in(app, 'reader', 'reader chat password')
    house = sign_in(app, 'house', 'house chat password')
    admin = sign_in(app, 'admin', 'admin chat password')
    post(admin, chat_enabled='1', model='gemma3:270m',
         max_queue_depth='8', pairs_per_chat='16')

    assert post(reader, chat_enabled='1', model='gemma3:270m',
                max_queue_depth='8', pairs_per_chat='16').status_code == HTTPStatus.FORBIDDEN
    assert post(house, chat_enabled='1', model='gemma3:270m',
                max_queue_depth='8', pairs_per_chat='16').status_code == HTTPStatus.FORBIDDEN


def test_chat_data_tools_setting_is_admin_controlled_and_defaults_off(app):
    admin = sign_in(app, 'admin', 'admin chat password')
    reader = sign_in(app, 'reader', 'reader chat password')

    assert setting_values(app).get('chat_data_tools_enabled') is None
    assert post(reader, chat_data_tools_enabled='1', chat_enabled='0',
                model='', max_queue_depth='8', pairs_per_chat='16').status_code == HTTPStatus.FORBIDDEN

    response = post(admin, chat_data_tools_enabled='1', chat_enabled='0',
                    model='', max_queue_depth='8', pairs_per_chat='16')

    assert response.status_code == HTTPStatus.FOUND
    assert setting_values(app)['chat_data_tools_enabled'] == '1'


def test_admin_can_select_remote_openai_endpoint_and_discovered_model(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, 'list_openai_models',
                        lambda endpoint, api_key='': calls.append((endpoint, api_key)) or ['agent-model'])
    admin = sign_in(app, 'admin', 'admin chat password')
    response = post(admin, chat_enabled='1', provider='openai_compatible',
                    endpoint='https://agent.example.com:8443', model='agent-model',
                    max_queue_depth='8', pairs_per_chat='16')
    assert response.status_code == HTTPStatus.FOUND
    assert calls == [('https://agent.example.com:8443', '')]
    assert setting_values(app)['chat_provider'] == 'openai_compatible'
    assert setting_values(app)['chat_endpoint'] == 'https://agent.example.com:8443'
    page = admin.get('/admin/accounts')
    assert b'https://agent.example.com:8443' in page.data
    assert b'agent-model' in page.data


def test_remote_chat_setting_rejects_invalid_endpoint_without_saving(app):
    admin = sign_in(app, 'admin', 'admin chat password')
    response = post(admin, chat_enabled='1', provider='openai_compatible',
                    endpoint='http://example.com:8000', model='agent-model',
                    max_queue_depth='8', pairs_per_chat='16')
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert setting_values(app) == {}


def test_model_discovery_is_admin_only_and_uses_requested_endpoint(app, monkeypatch):
    monkeypatch.setattr(app_module, 'list_openai_models',
                        lambda endpoint, api_key='': [endpoint.rsplit(':', 1)[-1]])
    admin = sign_in(app, 'admin', 'admin chat password')
    reader = sign_in(app, 'reader', 'reader chat password')
    path = ('/admin/chat/models?provider=openai_compatible&'
            'endpoint=https%3A%2F%2Fagent.example.com%3A8443')
    assert reader.get(path).status_code == HTTPStatus.FORBIDDEN
    assert admin.get(path).get_json() == {'models': ['8443']}
