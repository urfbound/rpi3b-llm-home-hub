from http import HTTPStatus
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import homehub.app as app_module
from homehub.app import PH, create_app


SAMPLE = {
    'cpu_percent': 81.0, 'cpu_counters': (1000, 100),
    'ram_total_bytes': 1024 * 1024 * 1024,
    'ram_used_bytes': 640 * 1024 * 1024, 'ram_percent': 62.5,
    'temperature_c': 44.8, 'disk_total_bytes': 16 * 1024 ** 3,
    'disk_used_bytes': 8 * 1024 ** 3, 'disk_percent': 50.0,
    'app_rss_bytes': 32 * 1024 ** 2,
}


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'system-dashboard-test-secret',
        'DATABASE': str(tmp_path / 'system-dashboard.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin system password'), 'admin', 'person'),
             ('bob', 'Bob', PH.hash('bob system password'), 'user', 'person'),
             ('house', 'House', PH.hash('house system password'), 'user', 'house')],
        )
        db.commit()
    return app


def sign_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': csrf,
    })
    assert response.status_code == HTTPStatus.FOUND
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def set_metrics_provider(monkeypatch):
    monkeypatch.setattr(app_module, 'collect_system_snapshot',
                        lambda **_kwargs: dict(SAMPLE))


def test_home_system_card_shows_disk_before_ram_cpu_temperature_and_app_rss(app, monkeypatch):
    set_metrics_provider(monkeypatch)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO system_metrics(sampled_at,cpu_percent,ram_percent,temperature_c,'
            'disk_used_bytes,disk_total_bytes,app_rss_bytes) VALUES(?,?,?,?,?,?,?)',
            [( (now - timedelta(minutes=4-i)).isoformat(), value, 60, 45,
               100, 200, 1000) for i, value in enumerate((55, 60, 80, 81))],
        )
    bob = sign_in(app, 'bob', 'bob system password')

    page = bob.get('/')

    assert b'System Status' in page.data
    assert b'Disk usage' in page.data
    assert b'RAM used' in page.data
    assert b'CPU usage' in page.data
    assert b'System temperature' in page.data
    assert b'Application memory' in page.data
    assert page.data.index(b'Disk usage') < page.data.index(b'RAM used')
    assert b'class="cpu-bar normal"' in page.data
    assert b'class="cpu-bar warning"' in page.data
    assert b'class="cpu-bar critical"' in page.data
    assert b'81.0%' in page.data


def test_admin_can_hide_metrics_from_household_and_optionally_allow_house_account(app, monkeypatch):
    set_metrics_provider(monkeypatch)
    admin = sign_in(app, 'admin', 'admin system password')
    bob = sign_in(app, 'bob', 'bob system password')
    house = sign_in(app, 'house', 'house system password')

    saved = admin.post('/admin/system-status', data={
        '_csrf': csrf(admin), 'house_account_enabled': '1',
    })
    bob_page = bob.get('/')
    house_page = house.get('/')
    admin_page = admin.get('/')

    assert saved.status_code == HTTPStatus.FOUND
    assert b'System Status' not in bob_page.data
    assert b'System Status' in house_page.data
    assert b'System Status' in admin_page.data


def test_metrics_are_visible_to_everyone_by_default(app, monkeypatch):
    set_metrics_provider(monkeypatch)
    bob = sign_in(app, 'bob', 'bob system password')

    assert b'System Status' in bob.get('/').data


def test_only_admin_can_change_metrics_visibility(app):
    bob = sign_in(app, 'bob', 'bob system password')

    response = bob.post('/admin/system-status', data={
        '_csrf': csrf(bob), 'house_account_enabled': '1',
    })

    assert response.status_code == HTTPStatus.FORBIDDEN
