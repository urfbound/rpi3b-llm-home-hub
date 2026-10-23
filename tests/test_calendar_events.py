import sqlite3

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'calendar-event-test-secret',
        'DATABASE': str(tmp_path / 'calendar-events.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('alex', 'Alex', PH.hash('alex calendar password'), 'admin', 'person'),
             ('bob', 'Bob', PH.hash('bob calendar password'), 'user', 'person'),
             ('carol', 'Carol', PH.hash('carol calendar password'), 'user', 'person'),
             ('house', 'House', PH.hash('house calendar password'), 'user', 'house')],
        )
        db.commit()
    return app


def signed_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': csrf,
    })
    assert response.status_code == 302
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def event_form(client, **overrides):
    data = {
        '_csrf': csrf(client), 'title': 'Pasta night', 'event_date': '2026-10-24',
        'description': 'Dinner together', 'all_day': '1',
        'start_time': '', 'end_time': '',
    }
    data.update(overrides)
    return data


def test_create_all_day_event_and_detail_page_has_description_budget(app):
    bob = signed_in(app, 'bob', 'bob calendar password')

    response = bob.post('/events', data=event_form(bob))

    assert response.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        event = db.execute(
            'SELECT title,event_date,description,all_day,start_time,end_time,created_by '
            'FROM events'
        ).fetchone()
    assert event == ('Pasta night', '2026-10-24', 'Dinner together', 1, None, None, 2)
    detail = bob.get('/events/1')
    assert detail.status_code == 200
    assert b'Dinner together' in detail.data
    assert b'Edit event' in detail.data
    assert b'characters remaining (256 maximum)' in bob.get('/events/1/edit').data


def test_create_timed_event_requires_start_and_accepts_optional_later_end(app):
    bob = signed_in(app, 'bob', 'bob calendar password')

    response = bob.post('/events', data=event_form(
        bob, all_day='', start_time='18:15', end_time='19:45',
    ))

    assert response.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT all_day,start_time,end_time FROM events'
        ).fetchone() == (0, '18:15', '19:45')
    assert b'6:15 PM' in bob.get('/events/1').data
    assert b'7:45 PM' in bob.get('/events/1').data


def test_timed_event_can_have_no_end_time_and_appears_with_time_in_month_grid(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    bob.post('/events', data=event_form(
        bob, all_day='', start_time='18:15', end_time='',
    ))

    response = bob.get('/?month=10&year=2026')

    assert b'Pasta night' in response.data
    assert b'6:15 PM' in response.data


@pytest.mark.parametrize('overrides', [
    {'title': 'x' * 129},
    {'description': 'x' * 257},
    {'event_date': '2026-02-30'},
    {'all_day': '', 'start_time': '', 'end_time': ''},
    {'all_day': '', 'start_time': '25:00', 'end_time': ''},
    {'all_day': '', 'start_time': '18:00', 'end_time': '18:00'},
    {'all_day': '', 'start_time': '19:00', 'end_time': '18:00'},
])
def test_invalid_event_details_are_rejected_without_partial_event(app, overrides):
    bob = signed_in(app, 'bob', 'bob calendar password')

    response = bob.post('/events', data=event_form(bob, **overrides))

    assert response.status_code == 400
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_creator_can_edit_event_but_other_user_cannot_and_admin_can_delete(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    carol = signed_in(app, 'carol', 'carol calendar password')
    admin = signed_in(app, 'alex', 'alex calendar password')
    bob.post('/events', data=event_form(bob))

    assert b'Edit event' in bob.get('/events/1').data
    assert b'Edit event' not in carol.get('/events/1').data
    denied = carol.post('/events/1/edit', data=event_form(carol, title='Changed'))
    assert denied.status_code == 403
    edited = bob.post('/events/1/edit', data=event_form(bob, title='Changed'))
    assert edited.status_code == 302
    deleted = admin.post('/events/1/delete', data={'_csrf': csrf(admin)})
    assert deleted.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_house_wide_account_cannot_create_event(app):
    house = signed_in(app, 'house', 'house calendar password')

    response = house.post('/events', data=event_form(house))

    assert response.status_code == 403
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_timed_event_exports_local_floating_ics_datetime(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    bob.post('/events', data=event_form(
        bob, all_day='', start_time='18:15', end_time='19:45',
    ))

    response = bob.get('/calendar.ics')

    assert b'DTSTART:20261024T181500' in response.data
    assert b'DTEND:20261024T194500' in response.data


def test_v6_upgrade_preserves_events_and_adds_all_day_defaults(tmp_path):
    path = tmp_path / 'v6.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, event_date TEXT NOT NULL,
                recipe_id INTEGER, created_by INTEGER NOT NULL
            );
            INSERT INTO users VALUES(1,'alex','hash','admin',1,0);
            INSERT INTO events VALUES(23,'Dinner','2026-10-24',NULL,1);
            PRAGMA user_version=6;
        ''')

    create_app({'TESTING': True, 'SECRET_KEY': 'calendar-migrate-secret',
                'DATABASE': str(path)})

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 7
        assert db.execute(
            'SELECT id,title,event_date,description,all_day,start_time,end_time FROM events'
        ).fetchone() == (23, 'Dinner', '2026-10-24', '', 1, None, None)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('v6.pre-v7-*.sqlite3'))
    assert len(backups) == 1
