from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'recipe-calendar-test-secret',
        'DATABASE': str(tmp_path / 'recipe-calendar.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('alex', 'Alex', PH.hash('alex recipe password'), 'admin', 'person'),
             ('bob', 'Bob', PH.hash('bob recipe password'), 'user', 'person'),
             ('carol', 'Carol', PH.hash('carol recipe password'), 'user', 'person'),
             ('house', 'House', PH.hash('house recipe password'), 'user', 'house')],
        )
        db.commit()
    return app


def sign_in(app, username):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    response = client.post('/login', data={
        'username': username, 'password': f'{username} recipe password', '_csrf': token,
    })
    assert response.status_code == HTTPStatus.FOUND
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def seed_recipe(app, visibility='shared'):
    with sqlite3.connect(app.config['DATABASE']) as db:
        recipe_id = db.execute(
            'INSERT INTO recipes(title,description,default_servings,created_by,visibility) '
            'VALUES(?,?,?,?,?)', ('Lemon pasta', 'Quick supper', 4, 2, visibility),
        ).lastrowid
        db.execute('INSERT INTO recipe_ingredients(recipe_id,position,name) VALUES(?,?,?)',
                   (recipe_id, 0, 'Pasta'))
        db.execute('INSERT INTO recipe_steps(recipe_id,position,text) VALUES(?,?,?)',
                   (recipe_id, 0, 'Cook pasta.'))
    return recipe_id


def event_form(client, **overrides):
    data = {
        '_csrf': csrf(client), 'title': 'Lemon pasta', 'event_date': '2026-10-24',
        'description': '', 'all_day': '1', 'start_time': '', 'end_time': '',
        'visibility': 'shared',
    }
    data.update(overrides)
    return data


def test_recipe_detail_add_to_calendar_link_preserves_selected_servings(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')

    page = bob.get(f'/recipes/{recipe_id}?servings=6')

    assert b'Add to calendar' in page.data
    assert f'/events/new?recipe_id={recipe_id}&amp;servings=6&amp;meal_prep=1'.encode() in page.data


def test_add_to_calendar_prefills_recipe_event_and_saves_servings_and_meal_slot(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')
    prefill = bob.get(f'/events/new?recipe_id={recipe_id}&servings=6&meal_prep=1')

    assert b'value="Lemon pasta"' in prefill.data
    assert b'value="6"' in prefill.data
    assert b'name="meal_slot"' in prefill.data

    saved = bob.post('/events', data=event_form(
        bob, recipe_id=str(recipe_id), recipe_servings='6', recipe_title_auto='1',
        meal_prep='1', meal_slot='dinner',
    ))

    assert saved.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        event = db.execute(
            'SELECT recipe_id,recipe_servings,meal_prep,meal_slot,recipe_title_auto FROM events'
        ).fetchone()
    assert event == (recipe_id, 6, 1, 'dinner', 1)
    detail = bob.get('/events/1')
    assert f'/recipes/{recipe_id}?servings=6'.encode() in detail.data
    assert b'Lemon pasta' in detail.data


def test_private_recipe_uses_meal_prep_default_and_does_not_leak_to_event_view_or_ics(app):
    recipe_id = seed_recipe(app, visibility='private')
    bob = sign_in(app, 'bob')
    carol = sign_in(app, 'carol')

    prefill = bob.get(f'/events/new?recipe_id={recipe_id}&servings=3&meal_prep=1')
    assert b'value="Meal prep"' in prefill.data
    bob.post('/events', data=event_form(
        bob, title='Meal prep', recipe_id=str(recipe_id), recipe_servings='3',
        recipe_title_auto='1', meal_prep='1', meal_slot='lunch',
    ))

    detail = carol.get('/events/1')
    export = carol.get('/calendar.ics')

    assert b'Meal prep' in detail.data
    assert b'Private recipe' in detail.data
    assert b'Lemon pasta' not in detail.data
    assert b'SUMMARY:Meal prep' in export.data
    assert b'Lemon pasta' not in export.data


def test_recipe_access_is_required_to_link_it_and_event_invitation_does_not_grant_recipe_access(app):
    recipe_id = seed_recipe(app, visibility='private')
    bob = sign_in(app, 'bob')
    carol = sign_in(app, 'carol')
    denied = carol.post('/events', data=event_form(
        carol, title='Meal prep', recipe_id=str(recipe_id), recipe_servings='4',
        meal_prep='1', meal_slot='dinner', participant_ids=['2'],
    ))
    assert denied.status_code == HTTPStatus.NOT_FOUND

    bob.post('/events', data=event_form(
        bob, title='Meal prep', recipe_id=str(recipe_id), recipe_servings='4',
        recipe_title_auto='1', meal_prep='1', meal_slot='dinner',
        participant_ids=['3'],
    ))
    participant_detail = carol.get('/events/1')

    assert b'Private recipe' in participant_detail.data
    assert b'Lemon pasta' not in participant_detail.data


def test_recipe_privacy_change_rewrites_only_autogenerated_linked_event_titles(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')
    bob.post('/events', data=event_form(
        bob, title='Lemon pasta', recipe_id=str(recipe_id), recipe_servings='4',
        recipe_title_auto='1',
    ))
    bob.post('/events', data=event_form(
        bob, title='Family dinner', recipe_id=str(recipe_id), recipe_servings='4',
    ))

    response = bob.post(f'/recipes/{recipe_id}/edit', data={
        '_csrf': csrf(bob), 'title': 'Lemon pasta', 'description': 'Quick supper',
        'default_servings': '4', 'ingredient_quantity': [''], 'ingredient_unit': [''],
        'ingredient_name': ['Pasta'], 'ingredient_note': [''], 'step': ['Cook pasta.'],
        'visibility': 'private',
    })

    assert response.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute(
            'SELECT title FROM events ORDER BY id'
        ).fetchall() == [('Meal prep',), ('Family dinner',)]


@pytest.mark.parametrize(('recipe_id', 'servings', 'meal_slot'), [
    ('999', '4', 'dinner'), ('1', '0', 'dinner'), ('1', '100', 'dinner'),
    ('1', '4', 'brunch'), ('', '4', 'dinner'),
])
def test_invalid_recipe_event_link_or_meal_prep_is_rejected_without_event(app, recipe_id, servings, meal_slot):
    seed_recipe(app)
    bob = sign_in(app, 'bob')

    response = bob.post('/events', data=event_form(
        bob, title='Meal prep', recipe_id=recipe_id, recipe_servings=servings,
        meal_prep='1', meal_slot=meal_slot,
    ))

    assert response.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_v9_upgrade_adds_recipe_event_fields_without_losing_events(tmp_path):
    path = tmp_path / 'v9.sqlite3'
    create_app({'TESTING': True, 'SECRET_KEY': 'recipe-calendar-migration-secret',
                'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        for column in ('recipe_servings', 'meal_prep', 'meal_slot', 'recipe_title_auto'):
            columns = {row[1] for row in db.execute('PRAGMA table_info(events)')}
            if column in columns:
                db.execute(f'ALTER TABLE events DROP COLUMN {column}')
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('bob','hash','user')")
        db.execute("INSERT INTO events(title,event_date,created_by) VALUES('Dinner','2026-10-24',1)")
        db.execute('PRAGMA user_version=9')

    create_app({'TESTING': True, 'SECRET_KEY': 'recipe-calendar-migration-secret',
                'DATABASE': str(path)})

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 17
        assert db.execute(
            'SELECT title,recipe_id,recipe_servings,meal_prep,meal_slot,recipe_title_auto FROM events'
        ).fetchone() == ('Dinner', None, None, 0, None, 0)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
