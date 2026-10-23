import sqlite3
import re

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'recipe-sharing-test-secret',
        'DATABASE': str(tmp_path / 'recipes.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        for username, display, role, kind in (
            ('alex', 'Alex', 'admin', 'person'),
            ('bob', 'Bob', 'user', 'person'),
            ('carol', 'Carol', 'user', 'person'),
            ('house', 'House', 'user', 'house'),
        ):
            db.execute('INSERT INTO users(username,display_name,password_hash,role,account_kind) '
                       'VALUES(?,?,?,?,?)', (username, display, PH.hash(f'{username} password'), role, kind))
        db.commit()
    return app


def csrf(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(app, username):
    client = app.test_client()
    response = client.post('/login', data={
        'username': username, 'password': f'{username} password', '_csrf': csrf(client),
    })
    assert response.status_code == 302
    return client


def seed_recipe(app, visibility='shared', owner=1):
    with sqlite3.connect(app.config['DATABASE']) as db:
        cursor = db.execute('INSERT INTO recipes(title,description,default_servings,created_by,visibility) '
                            'VALUES(?,?,?,?,?)', ('House soup', 'Warm and easy', 4, owner, visibility))
        recipe_id = cursor.lastrowid
        db.execute('INSERT INTO recipe_ingredients(recipe_id,position,name) VALUES(?,?,?)',
                   (recipe_id, 0, 'Beans'))
        db.execute('INSERT INTO recipe_steps(recipe_id,position,text) VALUES(?,?,?)',
                   (recipe_id, 0, 'Simmer.'))
        db.commit()
    return recipe_id


def recipe_edit_payload(client, **overrides):
    payload = {
        '_csrf': csrf(client), 'title': 'House soup', 'description': 'Warm and easy',
        'default_servings': '4', 'ingredient_quantity': [''], 'ingredient_unit': [''],
        'ingredient_name': ['Beans'], 'ingredient_note': [''], 'step': ['Simmer.'],
        'visibility': 'private',
    }
    payload.update(overrides)
    return payload


def test_private_recipe_is_visible_only_to_owner_allowlist_and_admin(app):
    recipe_id = seed_recipe(app, visibility='private')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO recipe_access(recipe_id,user_id) VALUES(?,2)', (recipe_id,))
        db.commit()
    bob = sign_in(app, 'bob')
    carol = sign_in(app, 'carol')
    house = sign_in(app, 'house')
    admin = sign_in(app, 'alex')

    assert bob.get(f'/recipes/{recipe_id}').status_code == 200
    assert b'House soup' in bob.get('/').data
    assert carol.get(f'/recipes/{recipe_id}').status_code == 404
    assert carol.get(f'/recipes/{recipe_id}?servings=8').status_code == 404
    assert b'House soup' not in carol.get('/').data
    assert carol.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'Should not persist', '_csrf': csrf(carol),
    }).status_code == 404
    assert house.get(f'/recipes/{recipe_id}').status_code == 404
    assert admin.get(f'/recipes/{recipe_id}').status_code == 200


def test_recipe_creation_can_be_private_with_an_allowlist(app):
    alex = sign_in(app, 'alex')
    response = alex.post('/recipes', data={
        'title': 'Private pie', 'description': '', 'default_servings': '4',
        'ingredient_quantity': ['1'], 'ingredient_unit': ['cup'],
        'ingredient_name': ['berries'], 'ingredient_note': [''], 'step': ['Bake.'],
        'visibility': 'private', 'allowed_user_ids': ['2'], '_csrf': csrf(alex),
    })

    assert response.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        recipe_id, visibility = db.execute('SELECT id,visibility FROM recipes WHERE title=?',
                                           ('Private pie',)).fetchone()
        allowed = db.execute('SELECT user_id FROM recipe_access WHERE recipe_id=?',
                             (recipe_id,)).fetchall()
    assert visibility == 'private'
    assert allowed == [(2,)]


def test_personal_notes_are_private_to_each_author_but_visible_to_recipe_viewers(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')
    carol = sign_in(app, 'carol')
    house = sign_in(app, 'house')

    assert bob.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'Add lemon zest', '_csrf': csrf(bob),
    }).status_code == 302
    assert bob.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'Use fresh lemon zest', '_csrf': csrf(bob),
    }).status_code == 302
    assert carol.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'Serve with salad', '_csrf': csrf(carol),
    }).status_code == 302

    bob_page = bob.get(f'/recipes/{recipe_id}')
    house_page = house.get(f'/recipes/{recipe_id}')
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT user_id,note FROM recipe_notes WHERE recipe_id=? ORDER BY user_id',
                          (recipe_id,)).fetchall() == [(2, 'Use fresh lemon zest'), (3, 'Serve with salad')]
    assert b'Use fresh lemon zest' in bob_page.data
    assert b'Serve with salad' in bob_page.data
    assert b'>Edit</button>' in bob_page.data
    assert re.search(r'<form id="edit-note-\d+"[^>]* hidden>', bob_page.data.decode())
    assert b'aria-label="Delete your note"' in bob_page.data
    assert b'Use fresh lemon zest' in house_page.data
    assert b'Edit note' not in house_page.data


def test_recipe_note_length_is_128_and_house_account_cannot_add_notes(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')
    house = sign_in(app, 'house')

    too_long = bob.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'x' * 129, '_csrf': csrf(bob),
    })
    house_attempt = house.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'A note', '_csrf': csrf(house),
    })

    assert too_long.status_code == 400
    assert house_attempt.status_code == 403
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipe_notes').fetchone()[0] == 0


def test_recipe_note_accepts_exactly_128_characters(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')

    response = bob.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'x' * 128, '_csrf': csrf(bob),
    })

    assert response.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT length(note) FROM recipe_notes WHERE recipe_id=?',
                          (recipe_id,)).fetchone()[0] == 128


def test_recipe_owner_must_confirm_before_removing_notes_of_revoked_users(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')
    carol = sign_in(app, 'carol')
    alex = sign_in(app, 'alex')
    for member, note in ((bob, 'Keep this'), (carol, 'Remove this')):
        assert member.post(f'/recipes/{recipe_id}/notes', data={
            'text': note, '_csrf': csrf(member),
        }).status_code == 302

    first = alex.post(f'/recipes/{recipe_id}/edit', data=recipe_edit_payload(
        alex, allowed_user_ids=['2'],
    ))

    assert first.status_code == 200
    assert b'Carol' in first.data
    assert b"Carol&#39;s note" in first.data or b"Carol's note" in first.data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT visibility FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 'shared'
        assert db.execute('SELECT COUNT(*) FROM recipe_notes WHERE recipe_id=?', (recipe_id,)).fetchone()[0] == 2

    confirmed = alex.post(f'/recipes/{recipe_id}/edit', data=recipe_edit_payload(
        alex, allowed_user_ids=['2'], confirm_note_removal='yes',
    ))

    assert confirmed.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT visibility FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 'private'
        assert db.execute('SELECT user_id,note FROM recipe_notes WHERE recipe_id=?',
                          (recipe_id,)).fetchall() == [(2, 'Keep this')]
        assert db.execute('SELECT user_id FROM recipe_access WHERE recipe_id=?',
                          (recipe_id,)).fetchall() == [(2,)]


def test_recipe_note_can_be_deleted_only_by_its_author(app):
    recipe_id = seed_recipe(app)
    bob = sign_in(app, 'bob')
    carol = sign_in(app, 'carol')
    assert bob.post(f'/recipes/{recipe_id}/notes', data={
        'text': 'Add pepper', '_csrf': csrf(bob),
    }).status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        note_id = db.execute('SELECT id FROM recipe_notes WHERE recipe_id=?', (recipe_id,)).fetchone()[0]

    denied = carol.post(f'/recipes/{recipe_id}/notes/{note_id}/delete', data={'_csrf': csrf(carol)})
    allowed = bob.post(f'/recipes/{recipe_id}/notes/{note_id}/delete', data={'_csrf': csrf(bob)})

    assert denied.status_code == 403
    assert allowed.status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipe_notes WHERE id=?', (note_id,)).fetchone()[0] == 0
