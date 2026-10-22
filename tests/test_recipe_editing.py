import sqlite3

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def users(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'recipe-edit-test-secret',
        'DATABASE': str(tmp_path / 'recipes.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('alex','Alex',?,'admin')", (PH.hash('alex recipe password'),))
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob recipe password'),))
        db.commit()
    return app


def token(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf_token']


def signed_in(app, username, password):
    client = app.test_client()
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': token(client),
    })
    assert response.status_code == 302
    return client


def add_recipe(app, owner_id):
    with sqlite3.connect(app.config['DATABASE']) as db:
        cursor = db.execute('INSERT INTO recipes(title,description,default_servings,created_by) '
                            'VALUES(?,?,?,?)', ('Old soup', 'Old description', 4, owner_id))
        recipe_id = cursor.lastrowid
        db.execute('INSERT INTO recipe_ingredients(recipe_id,position,quantity,unit,name,note) '
                   'VALUES(?,?,?,?,?,?)', (recipe_id, 0, '1', 'cup', 'beans', '',))
        db.execute('INSERT INTO recipe_steps(recipe_id,position,text) VALUES(?,?,?)',
                   (recipe_id, 0, 'Rinse beans.'))
        db.commit()
    return recipe_id


def replacement_fields(client, **overrides):
    data = {
        'title': 'New soup', 'description': 'A brighter soup', 'default_servings': '6',
        'ingredient_quantity': ['2'], 'ingredient_unit': ['cups'],
        'ingredient_name': ['lentils'], 'ingredient_note': ['rinsed'],
        'step': ['Simmer gently.'], '_csrf': token(client),
    }
    data.update(overrides)
    return data


def test_recipe_owner_can_edit_structured_content(users):
    bob_id = 2
    recipe_id = add_recipe(users, bob_id)
    bob = signed_in(users, 'bob', 'bob recipe password')

    response = bob.post(f'/recipes/{recipe_id}/edit', data=replacement_fields(bob))

    assert response.status_code == 302
    with sqlite3.connect(users.config['DATABASE']) as db:
        assert db.execute('SELECT title,description,default_servings FROM recipes WHERE id=?',
                          (recipe_id,)).fetchone() == ('New soup', 'A brighter soup', 6)
        assert db.execute('SELECT quantity,unit,name,note FROM recipe_ingredients WHERE recipe_id=?',
                          (recipe_id,)).fetchall() == [('2', 'cups', 'lentils', 'rinsed')]
        assert db.execute('SELECT text FROM recipe_steps WHERE recipe_id=?',
                          (recipe_id,)).fetchall() == [('Simmer gently.',)]


def test_only_recipe_owner_can_edit_even_when_admin_views_it(users):
    recipe_id = add_recipe(users, owner_id=2)
    admin = signed_in(users, 'alex', 'alex recipe password')

    response = admin.post(f'/recipes/{recipe_id}/edit', data=replacement_fields(admin))

    assert response.status_code == 403
    with sqlite3.connect(users.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 'Old soup'


def test_recipe_owner_can_delete_and_children_are_removed(users):
    recipe_id = add_recipe(users, owner_id=2)
    bob = signed_in(users, 'bob', 'bob recipe password')

    response = bob.post(f'/recipes/{recipe_id}/delete', data={'_csrf': token(bob)})

    assert response.status_code == 302
    with sqlite3.connect(users.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM recipe_ingredients WHERE recipe_id=?', (recipe_id,)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM recipe_steps WHERE recipe_id=?', (recipe_id,)).fetchone()[0] == 0


def test_non_owner_cannot_delete_and_admin_can_delete_any_recipe(users):
    recipe_id = add_recipe(users, owner_id=1)
    bob = signed_in(users, 'bob', 'bob recipe password')

    denied = bob.post(f'/recipes/{recipe_id}/delete', data={'_csrf': token(bob)})

    assert denied.status_code == 403
    with sqlite3.connect(users.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 1

    admin = signed_in(users, 'alex', 'alex recipe password')
    allowed = admin.post(f'/recipes/{recipe_id}/delete', data={'_csrf': token(admin)})

    assert allowed.status_code == 302
    with sqlite3.connect(users.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 0


def test_invalid_recipe_edit_keeps_previous_content_atomic(users):
    recipe_id = add_recipe(users, owner_id=2)
    bob = signed_in(users, 'bob', 'bob recipe password')

    response = bob.post(f'/recipes/{recipe_id}/edit', data=replacement_fields(
        bob, title='x' * 129, ingredient_name=['must not save'],
    ))

    assert response.status_code == 400
    with sqlite3.connect(users.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 'Old soup'
        assert db.execute('SELECT name FROM recipe_ingredients WHERE recipe_id=?',
                          (recipe_id,)).fetchone()[0] == 'beans'


def test_recipe_detail_and_library_show_owner_controls(users):
    recipe_id = add_recipe(users, owner_id=2)
    bob = signed_in(users, 'bob', 'bob recipe password')

    detail = bob.get(f'/recipes/{recipe_id}')
    edit_page = bob.get(f'/recipes/{recipe_id}/edit')
    library = bob.get('/')

    assert b'Edit recipe' in detail.data
    assert b'Delete recipe' in detail.data
    assert b'name="ingredient_name"' in edit_page.data
    assert b'value="beans"' in edit_page.data
    assert b'Old soup' in library.data
    assert b'Delete this recipe?' in detail.data


def test_house_wide_read_only_account_cannot_edit_or_delete_recipe(users):
    recipe_id = add_recipe(users, owner_id=1)
    with sqlite3.connect(users.config['DATABASE']) as db:
        db.execute("INSERT INTO users(username,password_hash,role,account_kind) "
                   "VALUES('house',?,'user','house')", (PH.hash('house recipe password'),))
        db.commit()
    house = signed_in(users, 'house', 'house recipe password')

    edit = house.post(f'/recipes/{recipe_id}/edit', data=replacement_fields(house))
    delete = house.post(f'/recipes/{recipe_id}/delete', data={'_csrf': token(house)})

    assert edit.status_code == 403
    assert delete.status_code == 403
    with sqlite3.connect(users.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM recipes WHERE id=?', (recipe_id,)).fetchone()[0] == 'Old soup'
