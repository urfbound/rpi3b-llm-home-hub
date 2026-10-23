import sqlite3
import re
from datetime import date

import pytest

import homehub.app as app_module
from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'household_today', lambda: date(2026, 10, 22))
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'home-weekend-test-secret',
        'DATABASE': str(tmp_path / 'home-weekend.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('alex', 'Alex', PH.hash('alex weekend password'), 'admin', 'person'),
             ('bob', 'Bob', PH.hash('bob weekend password'), 'user', 'person'),
             ('carol', 'Carol', PH.hash('carol weekend password'), 'user', 'person'),
             ('house', 'House', PH.hash('house weekend password'), 'user', 'house')],
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
    assert response.status_code == 302
    return client


def add_event(app, *, title, event_date, created_by=2, description='',
              visibility='shared', start_time=None, end_time=None, invitees=()):
    with sqlite3.connect(app.config['DATABASE']) as db:
        cursor = db.execute(
            'INSERT INTO events(title,event_date,created_by,description,visibility,all_day,'
            'start_time,end_time) VALUES(?,?,?,?,?,?,?,?)',
            (title, event_date, created_by, description, visibility,
             int(start_time is None), start_time, end_time),
        )
        event_id = cursor.lastrowid
        db.executemany(
            'INSERT INTO event_participants(event_id,user_id) VALUES(?,?)',
            [(event_id, user_id) for user_id in invitees],
        )
        return event_id


def test_weekend_card_covers_friday_through_sunday_with_event_context(app):
    add_event(app, title='Friday supper', event_date='2026-10-23',
              description='Bring the pie', start_time='18:00', end_time='19:30', invitees=(3,))
    add_event(app, title='Saturday walk', event_date='2026-10-24')
    add_event(app, title='Sunday brunch', event_date='2026-10-25')
    add_event(app, title='Thursday meeting', event_date='2026-10-22')
    add_event(app, title='Monday meeting', event_date='2026-10-26')
    bob = sign_in(app, 'bob', 'bob weekend password')

    page = bob.get('/')
    card = re.search(r'<section class="panel home-weekend".*?(?=<section class="panel" id="calendar")',
                     page.data.decode(), re.S).group()

    assert 'This weekend' in card
    assert 'Friday supper' in card
    assert 'Saturday walk' in card
    assert 'Sunday brunch' in card
    assert 'Thursday meeting' not in card
    assert 'Monday meeting' not in card
    assert 'Bring the pie' in card
    assert 'Carol' in card
    assert '6:00 PM' in card
    assert '7:30 PM' in card
    assert '/events/1' in card


def test_weekend_card_hides_private_details_and_just_my_events_filters(app):
    add_event(app, title='Bob party', event_date='2026-10-23', description='Bob event')
    add_event(app, title='Carol private', event_date='2026-10-24', created_by=3,
              description='Secret details', visibility='private', invitees=(3,))
    add_event(app, title='Bob invited', event_date='2026-10-25', created_by=3,
              description='Invitee details', invitees=(2,))
    bob = sign_in(app, 'bob', 'bob weekend password')

    all_events = bob.get('/')
    just_mine = bob.get('/?just_my_events=1')
    all_card = re.search(r'<section class="panel home-weekend".*?(?=<section class="panel" id="calendar")',
                         all_events.data.decode(), re.S).group()
    mine_card = re.search(r'<section class="panel home-weekend".*?(?=<section class="panel" id="calendar")',
                          just_mine.data.decode(), re.S).group()

    assert 'Bob party' in all_card
    assert 'Busy' in all_card
    assert 'Carol private' not in all_card
    assert 'Secret details' not in all_card
    assert 'Bob invited' in mine_card
    assert 'Bob party' in mine_card
    assert 'Carol private' not in mine_card
    assert 'Just my events' in all_card


def test_weekend_card_on_saturday_stops_at_sunday(app, monkeypatch):
    monkeypatch.setattr(app_module, 'household_today', lambda: date(2026, 10, 24))
    add_event(app, title='Friday already passed', event_date='2026-10-23')
    add_event(app, title='Saturday today', event_date='2026-10-24')
    add_event(app, title='Sunday tomorrow', event_date='2026-10-25')
    add_event(app, title='Monday next week', event_date='2026-10-26')
    bob = sign_in(app, 'bob', 'bob weekend password')

    page = bob.get('/')
    card = re.search(r'<section class="panel home-weekend".*?(?=<section class="panel" id="calendar")',
                     page.data.decode(), re.S).group()

    assert 'Friday already passed' not in card
    assert 'Saturday today' in card
    assert 'Sunday tomorrow' in card
    assert 'Monday next week' not in card
