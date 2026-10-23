from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'calendar-budget-test-secret',
        'DATABASE': str(tmp_path / 'calendar-budgets.sqlite3'),
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
             ('carol', 'Carol', PH.hash('carol calendar password'), 'user', 'person')],
        )
        db.commit()
    return app


def signed_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': token,
    })
    assert response.status_code == HTTPStatus.FOUND
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def event_form(client, **overrides):
    form = {
        '_csrf': csrf(client), 'title': 'Dinner', 'event_date': '2026-10-24',
        'description': '', 'all_day': '1', 'start_time': '', 'end_time': '',
    }
    form.update(overrides)
    return form


def store_limit(app, key, value):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute(
            'INSERT INTO app_settings(key,value) VALUES(?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, str(value)),
        )


def add_existing_event(app, user_id, event_date):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO events(title,event_date,created_by) VALUES(?,?,?)',
                   ('Existing', event_date, user_id))


def test_calendar_limit_settings_show_approved_defaults_and_can_be_changed_by_admin(app):
    admin = signed_in(app, 'alex', 'alex calendar password')
    page = admin.get('/admin/accounts')

    assert b'name="events_per_day"' in page.data
    assert b'value="8"' in page.data
    assert b'name="events_per_month"' in page.data
    assert b'value="128"' in page.data

    saved = admin.post('/admin/calendar/limits', data={
        '_csrf': csrf(admin), 'events_per_day': '32', 'events_per_month': '512',
    })

    assert saved.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            "SELECT key,value FROM app_settings WHERE key IN "
            "('calendar_events_per_day','calendar_events_per_month') ORDER BY key"
        ).fetchall() == [
            ('calendar_events_per_day', '32'), ('calendar_events_per_month', '512'),
        ]


@pytest.mark.parametrize(('daily', 'monthly'), [
    ('0', '128'), ('8', '6'), ('8', '513'), ('bad', '128'),
])
def test_admin_rejects_calendar_budget_values_outside_configured_ranges(app, daily, monthly):
    admin = signed_in(app, 'alex', 'alex calendar password')

    response = admin.post('/admin/calendar/limits', data={
        '_csrf': csrf(admin), 'events_per_day': daily, 'events_per_month': monthly,
    })

    assert response.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM app_settings WHERE key LIKE "calendar_events_%"').fetchone()[0] == 0


def test_only_admin_can_change_calendar_budgets(app):
    bob = signed_in(app, 'bob', 'bob calendar password')

    response = bob.post('/admin/calendar/limits', data={
        '_csrf': csrf(bob), 'events_per_day': '12', 'events_per_month': '140',
    })

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_daily_budget_is_per_creator_and_counts_recurring_occurrences(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    carol = signed_in(app, 'carol', 'carol calendar password')
    for _ in range(8):
        add_existing_event(app, 2, '2026-10-24')

    denied = bob.post('/events', data=event_form(bob))
    allowed = carol.post('/events', data=event_form(carol))

    assert denied.status_code == HTTPStatus.BAD_REQUEST
    assert allowed.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events WHERE created_by=2').fetchone()[0] == 8
        assert db.execute('SELECT COUNT(*) FROM events WHERE created_by=3').fetchone()[0] == 1


def test_monthly_budget_counts_occurrences_and_rejects_without_partial_series(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    store_limit(app, 'calendar_events_per_day', 128)
    store_limit(app, 'calendar_events_per_month', 128)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO events(title,event_date,created_by) VALUES(?,?,2)',
            [('Existing', f'2026-10-{day:02}',) for day in range(1, 17) for _ in range(8)],
        )

    denied = bob.post('/events', data=event_form(
        bob, recurrence_frequency='daily', recurrence_until='2026-10-26',
    ))

    assert denied.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events WHERE created_by=2').fetchone()[0] == 128


def test_daily_budget_is_checked_for_every_date_in_a_recurrence_atomically(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    store_limit(app, 'calendar_events_per_day', 1)
    add_existing_event(app, 2, '2026-10-25')

    denied = bob.post('/events', data=event_form(
        bob, recurrence_frequency='daily', recurrence_until='2026-10-26',
    ))

    assert denied.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1


def test_series_edit_cannot_move_occurrences_over_daily_budget(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    store_limit(app, 'calendar_events_per_day', 1)
    bob.post('/events', data=event_form(
        bob, recurrence_frequency='weekly', recurrence_until='2026-11-07',
    ))
    add_existing_event(app, 2, '2026-11-07')

    response = bob.post('/events/1/edit', data=event_form(
        bob, edit_scope='series', recurrence_frequency='weekly',
        recurrence_until='2026-11-07',
    ))

    assert response.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT event_date FROM events WHERE series_id=1 ORDER BY event_date'
        ).fetchall() == [('2026-10-24',), ('2026-10-31',), ('2026-11-07',)]


def test_single_occurrence_edit_cannot_move_event_over_daily_budget(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    store_limit(app, 'calendar_events_per_day', 1)
    bob.post('/events', data=event_form(bob))
    add_existing_event(app, 2, '2026-10-25')

    response = bob.post('/events/1/edit', data=event_form(
        bob, event_date='2026-10-25', edit_scope='occurrence',
    ))

    assert response.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT event_date FROM events WHERE id=1').fetchone() == (
            '2026-10-24',
        )
