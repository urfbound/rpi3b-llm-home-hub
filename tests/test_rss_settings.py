from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH, create_app
from homehub.rss import (
    DEFAULT_RSS_REFRESH_MINUTES, MAX_RSS_REFRESH_MINUTES,
    MIN_RSS_REFRESH_MINUTES,
)


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-settings-test',
                      'DATABASE': str(tmp_path / 'rss-settings.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin password'), 'admin'),
             ('reader', 'Reader', PH.hash('reader password'), 'user')],
        )
    return app


def sign_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post('/login', data={
        'username': username, 'password': password, '_csrf': token,
    }).status_code == HTTPStatus.FOUND
    return client


def values(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return dict(db.execute("SELECT key,value FROM app_settings WHERE key LIKE 'rss_%'"))


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def test_admin_can_configure_rss_refresh_and_outbound_access(app):
    admin = sign_in(app, 'admin', 'admin password')

    page = admin.get('/admin/accounts')

    assert b'RSS settings' in page.data
    control = (f'name="refresh_minutes" min="{MIN_RSS_REFRESH_MINUTES}" '
               f'max="{MAX_RSS_REFRESH_MINUTES}" '
               f'value="{DEFAULT_RSS_REFRESH_MINUTES}"').encode()
    assert control in page.data
    assert b'name="outbound_enabled" value="1" checked' in page.data

    response = admin.post('/admin/rss/settings', data={
        '_csrf': csrf(admin), 'refresh_minutes': '30', 'outbound_enabled': '1',
    })
    assert response.status_code == HTTPStatus.FOUND
    assert values(app) == {'rss_refresh_minutes': '30', 'rss_outbound_enabled': '1'}


def test_rss_settings_validate_interval_and_save_outbound_toggle(app):
    admin = sign_in(app, 'admin', 'admin password')
    with admin.session_transaction() as session:
        token = session['csrf_token']

    for interval in (str(MIN_RSS_REFRESH_MINUTES - 1),
                     str(MAX_RSS_REFRESH_MINUTES + 1), 'abc'):
        assert admin.post('/admin/rss/settings', data={
            '_csrf': token, 'refresh_minutes': interval, 'outbound_enabled': '1',
        }).status_code == HTTPStatus.BAD_REQUEST
    assert values(app) == {}

    response = admin.post('/admin/rss/settings', data={
        '_csrf': token, 'refresh_minutes': str(MAX_RSS_REFRESH_MINUTES),
    })

    assert response.status_code == HTTPStatus.FOUND
    assert response.headers['Location'].endswith('#rss-settings-section')
    assert values(app) == {
        'rss_refresh_minutes': str(MAX_RSS_REFRESH_MINUTES), 'rss_outbound_enabled': '0',
    }


def test_rss_refresh_accepts_the_configured_minimum(app):
    admin = sign_in(app, 'admin', 'admin password')

    response = admin.post('/admin/rss/settings', data={
        '_csrf': csrf(admin), 'refresh_minutes': str(MIN_RSS_REFRESH_MINUTES),
        'outbound_enabled': '1',
    })

    assert response.status_code == HTTPStatus.FOUND
    assert values(app)['rss_refresh_minutes'] == str(MIN_RSS_REFRESH_MINUTES)


def test_only_admins_can_change_rss_settings(app):
    reader = sign_in(app, 'reader', 'reader password')
    with reader.session_transaction() as session:
        token = session['csrf_token']

    response = reader.post('/admin/rss/settings', data={
        '_csrf': token, 'refresh_minutes': '60', 'outbound_enabled': '1',
    })

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert values(app) == {}
