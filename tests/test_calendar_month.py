import sqlite3
from datetime import date

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'calendar-month-test-secret',
        'DATABASE': str(tmp_path / 'calendar.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                   ('alex', PH.hash('alex calendar password'), 'admin'))
        db.commit()
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    client.post('/login', data={
        'username': 'alex', 'password': 'alex calendar password', '_csrf': csrf,
    })
    return client


def add_event(client, title, event_date):
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute('INSERT INTO events(title,event_date,created_by) VALUES(?,?,1)',
                   (title, event_date))


def test_calendar_shows_selected_month_grid_and_only_its_events(client):
    add_event(client, 'Autumn dinner', '2026-10-09')
    add_event(client, 'November plans', '2026-11-02')

    response = client.get('/?month=10&year=2026')

    assert response.status_code == 200
    assert b'October 2026' in response.data
    assert b'Autumn dinner' in response.data
    assert b'November plans' not in response.data
    assert b'calendar-month-grid' in response.data
    assert b'Sunday' in response.data and b'Saturday' in response.data


def test_calendar_controls_navigate_month_jump_year_and_return_to_today(client):
    response = client.get('/?month=10&year=2026')
    body = response.data
    today = date.today()

    assert b'aria-label="Previous month"' in body
    assert b'aria-label="Next month"' in body
    assert b'name="month"' in body
    assert b'name="year"' in body
    assert b'Today' in body
    assert f'value="{today.year}" selected'.encode() in client.get(
        f'/?month={today.month}&year={today.year}'
    ).data


def test_calendar_year_range_respects_retention_setting_and_disables_boundary_navigation(client):
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES('calendar_retention_years','0')")
    previous_year = date.today().year - 1

    assert client.get(f'/?month=1&year={previous_year}').status_code == 400
    response = client.get(f'/?month=1&year={date.today().year}')
    assert response.status_code == 200
    assert b'No earlier retained month' in response.data


@pytest.mark.parametrize(('query', 'expected'), [
    ('month=0&year=2026', 400),
    ('month=13&year=2026', 400),
    ('month=abc&year=2026', 400),
    ('month=10&year=1899', 400),
])
def test_calendar_rejects_invalid_month_or_year(client, query, expected):
    assert client.get(f'/?{query}').status_code == expected
