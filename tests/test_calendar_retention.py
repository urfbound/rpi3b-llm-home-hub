from datetime import date, datetime, timezone
from http import HTTPStatus
import sqlite3

import homehub.app as app_module
from homehub.app import PH, create_app, get_db, household_today


def make_app(tmp_path):
    database = tmp_path / 'calendar.sqlite3'
    app = create_app({'TESTING': True, 'SECRET_KEY': 'calendar-retention-test',
                      'DATABASE': str(database)})
    with sqlite3.connect(database) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin password'), 'admin'),
             ('member', 'Member', PH.hash('member password'), 'user')],
        )
    return app


def sign_in(app, username='admin', password='admin password'):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    client.post('/login', data={'username': username, 'password': password,
                                '_csrf': csrf})
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def add_event(app, event_date, title):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO events(title,event_date,created_by) VALUES(?,?,1)',
                   (title, event_date))


def test_admin_sees_calendar_retention_controls_and_resolved_archive_folder(tmp_path):
    app = make_app(tmp_path)

    response = sign_in(app).get('/admin/accounts')

    assert response.status_code == HTTPStatus.OK
    assert b'id="calendar-retention-section"' in response.data
    assert b'Previous years to keep' in response.data
    assert b'value="3" selected' in response.data
    assert b'Archive to ICS first' in response.data
    assert str((tmp_path / 'calendar-archives').resolve()).encode() in response.data


def test_reducing_calendar_retention_archives_each_newly_excluded_year_before_deleting(
        tmp_path):
    app = make_app(tmp_path)
    current_year = date.today().year
    excluded_year = current_year - 2
    add_event(app, f'{excluded_year}-06-12', 'Old family picnic')
    client = sign_in(app)

    response = client.post('/admin/calendar/retention', data={
        '_csrf': csrf(client), 'previous_years': '1', 'expiry_action': 'archive',
    })

    assert response.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events WHERE event_date LIKE ?',
                          (f'{excluded_year}-%',)).fetchone()[0] == 0
        archive = db.execute(
            'SELECT year,file_name,event_count FROM calendar_archives'
        ).fetchone()
        assert archive == (excluded_year, f'homehub-calendar-{excluded_year}.ics', 1)
        assert db.execute(
            "SELECT value FROM app_settings WHERE key='calendar_retention_years'"
        ).fetchone() == ('1',)
    archive_path = tmp_path / 'calendar-archives' / f'homehub-calendar-{excluded_year}.ics'
    assert b'SUMMARY:Old family picnic' in archive_path.read_bytes()
    download = client.get(f'/admin/calendar/archives/{archive_path.name}')
    assert download.status_code == HTTPStatus.OK
    assert download.mimetype == 'text/calendar'
    member = sign_in(app, 'member', 'member password')
    assert member.get(f'/admin/calendar/archives/{archive_path.name}').status_code == HTTPStatus.FORBIDDEN


def test_archive_failure_keeps_events_and_reports_failure(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    excluded_year = date.today().year - 2
    add_event(app, f'{excluded_year}-06-12', 'Keep this event')
    client = sign_in(app)

    def fail_archive(*_args, **_kwargs):
        raise OSError('disk is full')

    monkeypatch.setattr(app_module, 'write_calendar_archive', fail_archive)
    response = client.post('/admin/calendar/retention', data={
        '_csrf': csrf(client), 'previous_years': '1', 'expiry_action': 'delete',
    }, follow_redirects=True)

    assert response.status_code == HTTPStatus.OK
    assert b'could not be archived' in response.data.lower()
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM events').fetchone() == ('Keep this event',)
        assert db.execute('SELECT COUNT(*) FROM calendar_archives').fetchone()[0] == 0
        assert db.execute('SELECT year FROM calendar_archive_failures').fetchone() == (excluded_year,)
    client.get('/')
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM events').fetchone() == ('Keep this event',)
    admin_page = client.get('/admin/accounts')
    assert b'was not archived' in admin_page.data


def test_deliberate_retention_reduction_archives_even_if_automatic_cleanup_is_delete(tmp_path):
    app = make_app(tmp_path)
    excluded_year = date.today().year - 2
    add_event(app, f'{excluded_year}-06-12', 'Discard this event')
    client = sign_in(app)

    response = client.post('/admin/calendar/retention', data={
        '_csrf': csrf(client), 'previous_years': '1', 'expiry_action': 'delete',
    })

    assert response.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
        assert db.execute('SELECT year FROM calendar_archives').fetchone() == (excluded_year,)
        assert db.execute(
            "SELECT value FROM app_settings WHERE key='calendar_expiry_action'"
        ).fetchone() == ('delete',)


def test_retention_input_is_bounded_and_admin_only(tmp_path):
    app = make_app(tmp_path)
    admin = sign_in(app)
    bad = admin.post('/admin/calendar/retention', data={
        '_csrf': csrf(admin), 'previous_years': '11', 'expiry_action': 'archive',
    })
    member = sign_in(app, 'member', 'member password')
    forbidden = member.post('/admin/calendar/retention', data={
        '_csrf': csrf(member), 'previous_years': '1', 'expiry_action': 'archive',
    })

    assert bad.status_code == HTTPStatus.BAD_REQUEST
    assert forbidden.status_code == HTTPStatus.FORBIDDEN


def test_calendar_uses_timezone_from_the_admin_selected_household_location(tmp_path):
    app = make_app(tmp_path)
    with app.app_context():
        from flask import g
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.execute("INSERT INTO app_settings(key,value) VALUES('weather_timezone','America/St_Johns')")
        now = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)

        local_day = household_today(db, now=now)

    assert local_day == date(2025, 12, 31)


def test_calendar_automatically_archives_years_that_age_out_of_the_window(tmp_path):
    app = make_app(tmp_path)
    expired_year = date.today().year - 4
    add_event(app, f'{expired_year}-06-12', 'Aged out event')

    response = sign_in(app).get('/')

    assert response.status_code == HTTPStatus.OK
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
        assert db.execute('SELECT year FROM calendar_archives').fetchone() == (expired_year,)


def test_automatic_age_cleanup_can_delete_without_archive(tmp_path):
    app = make_app(tmp_path)
    expired_year = date.today().year - 4
    add_event(app, f'{expired_year}-06-12', 'Aged out event')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO app_settings(key,value) "
                   "VALUES('calendar_expiry_action','delete')")

    response = sign_in(app).get('/')

    assert response.status_code == HTTPStatus.OK
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM calendar_archives').fetchone()[0] == 0
