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


def test_recurring_event_materializes_daily_weekly_monthly_and_yearly_dates(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    cases = [
        ('daily', '2026-10-26', ['2026-10-24', '2026-10-25', '2026-10-26']),
        ('weekly', '2026-11-07', ['2026-10-24', '2026-10-31', '2026-11-07']),
        ('monthly', '2026-12-24', ['2026-10-24', '2026-11-24', '2026-12-24']),
        ('yearly', '2028-10-24', ['2026-10-24', '2027-10-24', '2028-10-24']),
    ]

    for frequency, until, expected_dates in cases:
        response = bob.post('/events', data=event_form(
            bob, title=f'{frequency} event', recurrence_frequency=frequency,
            recurrence_until=until,
        ))
        assert response.status_code == 302
        with sqlite3.connect(app.config['DATABASE']) as db:
            dates = [row[0] for row in db.execute(
                'SELECT event_date FROM events WHERE title=? ORDER BY event_date',
                (f'{frequency} event',),
            )]
        assert dates == expected_dates


def test_recurrence_end_defaults_to_one_year_and_invalid_ranges_are_rejected(app):
    bob = signed_in(app, 'bob', 'bob calendar password')

    response = bob.post('/events', data=event_form(
        bob, recurrence_frequency='weekly',
    ))

    assert response.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        rows = db.execute(
            'SELECT event_date,recurrence_frequency,recurrence_until,series_id '
            'FROM events ORDER BY event_date'
        ).fetchall()
    assert len(rows) == 53
    assert rows[0] == ('2026-10-24', 'weekly', '2027-10-24', rows[0][3])
    assert rows[-1][0] == '2027-10-23'
    assert len({row[3] for row in rows}) == 1

    invalid = bob.post('/events', data=event_form(
        bob, recurrence_frequency='monthly', recurrence_until='2026-10-23',
    ))
    assert invalid.status_code == 400
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 53


def test_monthly_recurrence_clamps_short_months_without_shifting_later_dates(app):
    bob = signed_in(app, 'bob', 'bob calendar password')

    bob.post('/events', data=event_form(
        bob, event_date='2026-01-31', recurrence_frequency='monthly',
        recurrence_until='2026-04-30',
    ))

    with sqlite3.connect(app.config['DATABASE']) as db:
        dates = [row[0] for row in db.execute(
            'SELECT event_date FROM events ORDER BY event_date'
        )]
    assert dates == ['2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30']


def test_single_occurrence_edit_and_delete_leave_the_rest_of_series_intact(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    bob.post('/events', data=event_form(
        bob, recurrence_frequency='weekly', recurrence_until='2026-11-07',
    ))

    edited = bob.post('/events/2/edit', data=event_form(
        bob, title='One changed date', event_date='2026-10-31', edit_scope='occurrence',
    ))
    assert edited.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT title,series_id,recurrence_frequency FROM events WHERE id=2'
        ).fetchone() == ('One changed date', None, None)
        assert db.execute(
            'SELECT event_date,title FROM events WHERE series_id=1 ORDER BY event_date'
        ).fetchall() == [('2026-10-24', 'Pasta night'), ('2026-11-07', 'Pasta night')]

    deleted = bob.post('/events/3/delete', data={
        '_csrf': csrf(bob), 'delete_scope': 'occurrence',
    })
    assert deleted.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT event_date FROM events WHERE series_id=1 ORDER BY event_date'
        ).fetchall() == [('2026-10-24',)]


def test_series_edit_and_delete_apply_to_all_remaining_occurrences(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    bob.post('/events', data=event_form(
        bob, recurrence_frequency='weekly', recurrence_until='2026-11-07',
    ))

    edited = bob.post('/events/1/edit', data=event_form(
        bob, title='Updated series', recurrence_frequency='weekly',
        recurrence_until='2026-11-14', edit_scope='series',
    ))
    assert edited.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT event_date,title FROM events WHERE series_id=1 ORDER BY event_date'
        ).fetchall() == [
            ('2026-10-24', 'Updated series'), ('2026-10-31', 'Updated series'),
            ('2026-11-07', 'Updated series'), ('2026-11-14', 'Updated series'),
        ]

    deleted = bob.post('/events/1/delete', data={
        '_csrf': csrf(bob), 'delete_scope': 'series',
    })
    assert deleted.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_recurring_events_appear_on_calendar_and_offer_occurrence_or_series_actions(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    bob.post('/events', data=event_form(
        bob, recurrence_frequency='weekly', recurrence_until='2026-11-07',
    ))

    calendar = bob.get('/?month=10&year=2026')
    detail = bob.get('/events/2')
    edit = bob.get('/events/2/edit')

    assert calendar.data.count(b'Pasta night') == 2
    assert b'Only this occurrence' in detail.data
    assert b'Entire series' in detail.data
    assert b'name="edit_scope"' in edit.data
    assert b'name="recurrence_frequency"' in edit.data


def test_other_user_cannot_edit_or_cancel_recurring_series(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    carol = signed_in(app, 'carol', 'carol calendar password')
    bob.post('/events', data=event_form(
        bob, recurrence_frequency='weekly', recurrence_until='2026-11-07',
    ))

    edited = carol.post('/events/1/edit', data=event_form(
        carol, title='Unauthorized', edit_scope='series',
    ))
    deleted = carol.post('/events/1/delete', data={
        '_csrf': csrf(carol), 'delete_scope': 'series',
    })

    assert edited.status_code == 403
    assert deleted.status_code == 403
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 3


def test_v7_upgrade_adds_recurrence_columns_without_losing_event(tmp_path):
    path = tmp_path / 'v7.sqlite3'
    create_app({'TESTING': True, 'SECRET_KEY': 'calendar-migrate-secret',
                'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        for column in ('recurrence_until', 'recurrence_frequency', 'series_id'):
            db.execute(f'ALTER TABLE events DROP COLUMN {column}')
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('bob','hash','user')")
        db.execute("INSERT INTO events(title,event_date,created_by) VALUES('Dinner','2026-10-24',1)")
        db.execute('PRAGMA user_version=7')

    create_app({'TESTING': True, 'SECRET_KEY': 'calendar-migrate-secret',
                'DATABASE': str(path)})

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        assert db.execute(
            'SELECT id,title,event_date,series_id,recurrence_frequency,recurrence_until '
            'FROM events'
        ).fetchone() == (1, 'Dinner', '2026-10-24', None, None, None)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_private_event_hides_details_from_calendar_detail_and_ics_unless_invited(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    carol = signed_in(app, 'carol', 'carol calendar password')
    admin = signed_in(app, 'alex', 'alex calendar password')
    bob.post('/events', data=event_form(
        bob, title='Secret anniversary', description='Surprise party plans',
        visibility='private',
    ))

    calendar = carol.get('/?month=10&year=2026')
    detail = carol.get('/events/1')
    export = carol.get('/calendar.ics')
    only_required = carol.get('/calendar.ics?only_required=1')
    admin_detail = admin.get('/events/1')

    assert b'Busy' in calendar.data
    assert b'Secret anniversary' not in calendar.data
    assert b'Secret anniversary' not in detail.data
    assert b'Surprise party plans' not in detail.data
    assert b'Busy' in detail.data
    assert b'SUMMARY:Busy' in export.data
    assert b'Secret anniversary' not in export.data
    assert b'Surprise party plans' not in export.data
    assert b'BEGIN:VEVENT' not in only_required.data
    assert b'Secret anniversary' in admin_detail.data


def test_invited_user_sees_private_details_but_cannot_edit_event(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    carol = signed_in(app, 'carol', 'carol calendar password')
    bob.post('/events', data=event_form(
        bob, title='Secret anniversary', description='Surprise party plans',
        visibility='private', participant_ids=['3'],
    ))

    detail = carol.get('/events/1')
    denied = carol.post('/events/1/edit', data=event_form(
        carol, title='Changed', visibility='private',
    ))
    export = carol.get('/calendar.ics?only_required=1')

    assert b'Secret anniversary' in detail.data
    assert b'Surprise party plans' in detail.data
    assert b'Edit event' not in detail.data
    assert denied.status_code == 403
    assert b'SUMMARY:Secret anniversary' in export.data


def test_creator_can_manage_event_participants_and_house_account_is_explicitly_invitable(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    house = signed_in(app, 'house', 'house calendar password')
    created = bob.post('/events', data=event_form(
        bob, visibility='private', participant_ids=['3', '4'],
    ))
    assert created.status_code == 302

    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT visibility FROM events WHERE id=1'
        ).fetchone() == ('private',)
        assert db.execute(
            'SELECT user_id FROM event_participants WHERE event_id=1 ORDER BY user_id'
        ).fetchall() == [(3,), (4,)]

    assert b'Pasta night' in house.get('/events/1').data
    updated = bob.post('/events/1/edit', data=event_form(
        bob, visibility='private', participant_ids=['3'],
    ))
    assert updated.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT user_id FROM event_participants WHERE event_id=1'
        ).fetchall() == [(3,)]


def test_recurring_event_copies_participants_to_every_occurrence(app):
    bob = signed_in(app, 'bob', 'bob calendar password')
    bob.post('/events', data=event_form(
        bob, visibility='private', participant_ids=['3'],
        recurrence_frequency='weekly', recurrence_until='2026-11-07',
    ))

    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT events.event_date,event_participants.user_id FROM events '
            'JOIN event_participants ON event_participants.event_id=events.id '
            'ORDER BY events.event_date'
        ).fetchall() == [
            ('2026-10-24', 3), ('2026-10-31', 3), ('2026-11-07', 3),
        ]


def test_v8_upgrade_adds_private_event_access_without_losing_existing_event(tmp_path):
    path = tmp_path / 'v8.sqlite3'
    create_app({'TESTING': True, 'SECRET_KEY': 'calendar-migrate-secret',
                'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE IF EXISTS event_participants')
        if 'visibility' in {row[1] for row in db.execute('PRAGMA table_info(events)')}:
            db.execute('ALTER TABLE events DROP COLUMN visibility')
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('bob','hash','user')")
        db.execute("INSERT INTO events(title,event_date,created_by) VALUES('Dinner','2026-10-24',1)")
        db.execute('PRAGMA user_version=8')

    create_app({'TESTING': True, 'SECRET_KEY': 'calendar-migrate-secret',
                'DATABASE': str(path)})

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        assert db.execute(
            'SELECT id,title,event_date,visibility FROM events'
        ).fetchone() == (1, 'Dinner', '2026-10-24', 'shared')
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


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
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        assert db.execute(
            'SELECT id,title,event_date,description,all_day,start_time,end_time FROM events'
        ).fetchone() == (23, 'Dinner', '2026-10-24', '', 1, None, None)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('v6.pre-v9-*.sqlite3'))
    assert len(backups) == 1
