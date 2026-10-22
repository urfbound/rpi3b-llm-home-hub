import sqlite3
from datetime import date

import pytest

import homehub.app as app_module
from homehub.app import PH, create_app


@pytest.fixture
def household(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'chores-test-secret',
        'DATABASE': str(tmp_path / 'homehub.sqlite3'),
    })
    with app.app_context():
        from flask import g
        g.app_db_path = app.config['DATABASE']
        from homehub.app import get_db
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [
                ('alice', 'Alice', PH.hash('alice test password'), 'admin', 'person'),
                ('bob', 'Bob', PH.hash('bob test password'), 'user', 'person'),
                ('house', 'House', PH.hash('house test password'), 'user', 'house'),
            ],
        )
        db.commit()
    client = app.test_client()
    sign_in(client, 'alice', 'alice test password')
    return app, client


def csrf_token(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(client, username, password):
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': csrf_token(client),
    })
    assert response.status_code == 302


def create_chore(client, *, text='Put clothes away', weekday=0, assignee='everyone'):
    return client.post('/chores', data={
        'text': text, 'weekday': str(weekday), 'assignee': assignee,
        '_csrf': csrf_token(client),
    })


def test_chores_tab_shows_seven_days_and_everyone_controls(household):
    _, client = household
    page = client.get('/?kind=chores')
    assert page.status_code == 200
    for day in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'):
        assert day.encode() in page.data
    assert page.data.count(b'>Everyone</option>') == 2


def test_home_chore_card_groups_today_and_filters_to_signed_in_user(household, monkeypatch):
    app, client = household
    monkeypatch.setattr(app_module, 'household_today', lambda: date(2026, 10, 26))
    with sqlite3.connect(app.config['DATABASE']) as db:
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
    create_chore(client, text='Put clothes away', weekday=0)
    create_chore(client, text='Feed dog', weekday=0, assignee=str(bob_id))

    page = client.get('/')
    assert page.data.index(b'id="home-chores"') < page.data.index(b'id="calendar"')
    assert b'Put clothes away</span>' in page.data
    assert b'Feed dog</span>' in page.data
    assert b'Just my chores' in page.data

    filtered = client.get('/?just_my_chores=1')
    assert b'Put clothes away</span>' in filtered.data
    assert b'Feed dog</span>' not in filtered.data
    assert b'name="just_my_chores" value="1" checked' in filtered.data


def test_everyone_chore_is_grouped_per_person_and_excludes_house_account(household):
    app, client = household
    response = create_chore(client)
    assert response.status_code == 302
    page = client.get('/?kind=chores&weekday=0')
    assert b'Alice' in page.data
    assert b'Bob' in page.data
    assert b'House' not in page.data
    assert page.data.count(b'Put clothes away</span>') == 2
    with sqlite3.connect(app.config['DATABASE']) as db:
        chore = db.execute('SELECT id,weekday,text,assignee_id,assigned_everyone,created_by '
                           'FROM chores').fetchone()
    assert chore[1:] == (0, 'Put clothes away', None, 1, 1)
    assert page.data.count(f'/chores/{chore[0]}/toggle'.encode()) == 1


def test_person_assignment_and_filter_show_only_that_users_chore(household):
    app, client = household
    with sqlite3.connect(app.config['DATABASE']) as db:
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        alice_id = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    assert create_chore(client, text='Feed dog', assignee=str(bob_id)).status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        chore_id = db.execute("SELECT id FROM chores WHERE text='Feed dog'").fetchone()[0]
    assert b'Feed dog</span>' in client.get('/?kind=chores&weekday=0').data
    assert b'Feed dog</span>' not in client.get(
        f'/?kind=chores&weekday=0&assignee_id={alice_id}').data
    assert b'Feed dog</span>' in client.get(
        f'/?kind=chores&weekday=0&assignee_id={bob_id}').data
    assert client.post(f'/chores/{chore_id}/toggle',
                       data={'_csrf': csrf_token(client)}).status_code == 403

    bob = app.test_client()
    sign_in(bob, 'bob', 'bob test password')
    assert bob.post(f'/chores/{chore_id}/toggle',
                    data={'_csrf': csrf_token(bob)}).status_code == 302


def test_chore_completion_is_independent_for_each_assigned_user(household):
    app, client = household
    create_chore(client)
    with sqlite3.connect(app.config['DATABASE']) as db:
        chore_id = db.execute('SELECT id FROM chores').fetchone()[0]
        alice_id = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
    assert client.post(f'/chores/{chore_id}/toggle',
                       data={'_csrf': csrf_token(client)}).status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        completed = db.execute('SELECT user_id,completed_week FROM chore_completions '
                               'WHERE chore_id=?', (chore_id,)).fetchall()
    assert len(completed) == 1
    assert completed[0][0] == alice_id
    assert completed[0][1] == app_module.week_start_for(app_module.household_today()).isoformat()

    bob = app.test_client()
    sign_in(bob, 'bob', 'bob test password')
    bob_page = bob.get('/?kind=chores&weekday=0&assignee_id=' + str(bob_id))
    assert b'class="done"' not in bob_page.data
    assert bob.post(f'/chores/{chore_id}/toggle',
                    data={'_csrf': csrf_token(bob)}).status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        completed_users = {row[0] for row in db.execute(
            'SELECT user_id FROM chore_completions WHERE chore_id=?', (chore_id,))}
    assert completed_users == {alice_id, bob_id}


def test_house_account_can_view_but_cannot_change_chores(household):
    app, client = household
    create_chore(client)
    with sqlite3.connect(app.config['DATABASE']) as db:
        chore_id = db.execute('SELECT id FROM chores').fetchone()[0]
    house = app.test_client()
    sign_in(house, 'house', 'house test password')
    page = house.get('/?kind=chores&weekday=0')
    assert b'Put clothes away' in page.data
    assert b'/chores/' not in page.data
    assert house.post(f'/chores/{chore_id}/toggle',
                      data={'_csrf': csrf_token(house)}).status_code == 403


def test_chore_completion_resets_at_next_sunday_without_deleting_chore(household, monkeypatch):
    app, client = household
    create_chore(client)
    with sqlite3.connect(app.config['DATABASE']) as db:
        chore_id = db.execute('SELECT id FROM chores').fetchone()[0]
    monkeypatch.setattr(app_module, 'household_today', lambda: date(2026, 10, 25))
    client.post(f'/chores/{chore_id}/toggle', data={'_csrf': csrf_token(client)})
    assert b'class="done"' in client.get('/?kind=chores&weekday=0').data

    monkeypatch.setattr(app_module, 'household_today', lambda: date(2026, 10, 31))
    assert b'class="done"' in client.get('/?kind=chores&weekday=0').data

    monkeypatch.setattr(app_module, 'household_today', lambda: date(2026, 11, 1))
    page = client.get('/?kind=chores&weekday=0')
    assert b'class="done"' not in page.data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM chores WHERE id=?',
                          (chore_id,)).fetchone() == (1,)


def test_only_creator_or_admin_can_delete_a_chore(household):
    app, client = household
    bob = app.test_client()
    sign_in(bob, 'bob', 'bob test password')
    with sqlite3.connect(app.config['DATABASE']) as db:
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
    response = create_chore(bob, text='Bob task', weekday=1, assignee=str(bob_id))
    assert response.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        chore_id = db.execute("SELECT id FROM chores WHERE text='Bob task'").fetchone()[0]
    assert client.post(f'/chores/{chore_id}/delete',
                       data={'_csrf': csrf_token(client)}).status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM chores WHERE id=?',
                          (chore_id,)).fetchone() == (0,)

    response = create_chore(client, text='Owner task', weekday=1, assignee='everyone')
    assert response.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        chore_id = db.execute("SELECT id FROM chores WHERE text='Owner task'").fetchone()[0]
    assert bob.post(f'/chores/{chore_id}/delete',
                    data={'_csrf': csrf_token(bob)}).status_code == 403
    assert client.post(f'/chores/{chore_id}/delete',
                       data={'_csrf': csrf_token(client)}).status_code == 302


def test_chore_creation_rejects_house_assignment_and_invalid_values(household):
    app, client = household
    with sqlite3.connect(app.config['DATABASE']) as db:
        house_id = db.execute("SELECT id FROM users WHERE username='house'").fetchone()[0]
    for values in (
        {'text': 'x', 'weekday': '7', 'assignee': 'everyone'},
        {'text': 'x', 'weekday': '1', 'assignee': str(house_id)},
        {'text': ' ' * 5, 'weekday': '1', 'assignee': 'everyone'},
    ):
        assert client.post('/chores', data={
            **values, '_csrf': csrf_token(client),
        }).status_code == 400
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM chores').fetchone() == (0,)
