from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'recipe-list-test-secret',
        'DATABASE': str(tmp_path / 'recipe-list.sqlite3'),
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


def signed_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': csrf,
    })
    assert response.status_code == HTTPStatus.FOUND
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def add_recipe(app, owner_id=2, visibility='shared', ingredients=None):
    with sqlite3.connect(app.config['DATABASE']) as db:
        cur = db.execute(
            'INSERT INTO recipes(title,created_by,visibility,default_servings) '
            'VALUES(?,?,?,4)', ('Family stew', owner_id, visibility),
        )
        recipe_id = cur.lastrowid
        db.executemany(
            'INSERT INTO recipe_ingredients(recipe_id,position,quantity,unit,name,note) '
            'VALUES(?,?,?,?,?,?)',
            [(recipe_id, n, *ingredient) for n, ingredient in enumerate(
                ingredients or [('1', 'cup', 'beans', '')]
            )],
        )
        return recipe_id


def test_recipe_detail_offers_selected_visible_shopping_list_and_keeps_servings(app):
    recipe_id = add_recipe(app)
    with sqlite3.connect(app.config['DATABASE']) as db:
        list_id = db.execute(
            "INSERT INTO household_lists(kind,title,visibility,created_by) "
            "VALUES('shopping','Market','shared',2) RETURNING id"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO household_lists(kind,title,visibility,created_by) "
            "VALUES('shopping','Carol only','private',3)"
        )
    bob = signed_in(app, 'bob', 'bob recipe password')

    response = bob.get(f'/recipes/{recipe_id}?servings=8')

    assert response.status_code == HTTPStatus.OK
    assert b'Add ingredients to list' in response.data
    assert f'value="{list_id}"'.encode() in response.data
    assert b'name="servings" value="8"' in response.data
    assert b'Carol only' not in response.data


def test_add_to_list_scales_combines_convertible_units_and_keeps_uncertain_items_separate(app):
    recipe_id = add_recipe(app, ingredients=[
        ('1', 'cup', 'beans', 'rinsed'), ('2', 'tbsp', 'Beans', 'rinsed'),
        ('to taste', '', 'salt', ''), ('1', 'handful', 'cilantro', ''),
        ('2', 'handful', 'cilantro', ''), ('1', 'kg', 'carrots', ''),
        ('500', 'g', 'Carrots', ''), ('1', 'bunch', 'spinach', ''),
        ('2', 'leaves', 'spinach', ''),
    ])
    with sqlite3.connect(app.config['DATABASE']) as db:
        list_id = db.execute("SELECT id FROM household_lists WHERE is_default=1").fetchone()[0]
    bob = signed_in(app, 'bob', 'bob recipe password')

    response = bob.post(f'/recipes/{recipe_id}/shopping', data={
        '_csrf': csrf(bob), 'list_id': str(list_id), 'servings': '8',
    })

    assert response.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        rows = db.execute(
            'SELECT text,created_by,list_id FROM shopping_items ORDER BY id'
        ).fetchall()
    assert rows == [
        ('2 1/4 cup beans (rinsed)', 2, list_id),
        ('to taste salt', 2, list_id),
        ('6 handful cilantro', 2, list_id),
        ('3 kg carrots', 2, list_id),
        ('2 bunch spinach', 2, list_id),
        ('4 leaves spinach', 2, list_id),
    ]


def test_add_to_list_copies_editable_snapshot_that_recipe_edits_do_not_change(app):
    recipe_id = add_recipe(app)
    with sqlite3.connect(app.config['DATABASE']) as db:
        list_id = db.execute("SELECT id FROM household_lists WHERE is_default=1").fetchone()[0]
    bob = signed_in(app, 'bob', 'bob recipe password')
    bob.post(f'/recipes/{recipe_id}/shopping', data={
        '_csrf': csrf(bob), 'list_id': str(list_id), 'servings': '4',
    })
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE recipe_ingredients SET name='lentils' WHERE recipe_id=?", (recipe_id,))
        snapshot = db.execute(
            'SELECT text,created_by FROM shopping_items WHERE list_id=?', (list_id,)
        ).fetchone()
    assert snapshot == ('1 cup beans', 2)


def test_add_to_list_rejects_inaccessible_private_recipe_and_private_list(app):
    recipe_id = add_recipe(app, visibility='private')
    with sqlite3.connect(app.config['DATABASE']) as db:
        list_id = db.execute(
            "INSERT INTO household_lists(kind,title,visibility,created_by) "
            "VALUES('shopping','Private','private',2) RETURNING id"
        ).fetchone()[0]
        todo_id = db.execute(
            "INSERT INTO household_lists(kind,title,visibility,created_by) "
            "VALUES('todo','Tasks','shared',2) RETURNING id"
        ).fetchone()[0]
    carol = signed_in(app, 'carol', 'carol recipe password')

    hidden_recipe = carol.post(f'/recipes/{recipe_id}/shopping', data={
        '_csrf': csrf(carol), 'list_id': str(list_id), 'servings': '4',
    })
    assert hidden_recipe.status_code == HTTPStatus.NOT_FOUND

    shared_recipe_id = add_recipe(app)
    hidden_list = carol.post(f'/recipes/{shared_recipe_id}/shopping', data={
        '_csrf': csrf(carol), 'list_id': str(list_id), 'servings': '4',
    })
    wrong_kind = carol.post(f'/recipes/{shared_recipe_id}/shopping', data={
        '_csrf': csrf(carol), 'list_id': str(todo_id), 'servings': '4',
    })
    assert hidden_list.status_code == HTTPStatus.NOT_FOUND
    assert wrong_kind.status_code == HTTPStatus.NOT_FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items').fetchone()[0] == 0


def test_house_wide_account_cannot_copy_recipe_to_shopping_list(app):
    recipe_id = add_recipe(app)
    with sqlite3.connect(app.config['DATABASE']) as db:
        list_id = db.execute("SELECT id FROM household_lists WHERE is_default=1").fetchone()[0]
    house = signed_in(app, 'house', 'house recipe password')

    response = house.post(f'/recipes/{recipe_id}/shopping', data={
        '_csrf': csrf(house), 'list_id': str(list_id), 'servings': '4',
    })

    assert response.status_code == HTTPStatus.FORBIDDEN
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items').fetchone()[0] == 0


def test_add_to_list_rejects_invalid_servings_and_oversized_snapshot_atomically(app):
    recipe_id = add_recipe(app, ingredients=[('1', 'cup', 'x' * 128, 'y' * 128)])
    with sqlite3.connect(app.config['DATABASE']) as db:
        list_id = db.execute("SELECT id FROM household_lists WHERE is_default=1").fetchone()[0]
    bob = signed_in(app, 'bob', 'bob recipe password')

    invalid = bob.post(f'/recipes/{recipe_id}/shopping', data={
        '_csrf': csrf(bob), 'list_id': str(list_id), 'servings': '100',
    })
    too_long = bob.post(f'/recipes/{recipe_id}/shopping', data={
        '_csrf': csrf(bob), 'list_id': str(list_id), 'servings': '4',
    })

    assert invalid.status_code == HTTPStatus.BAD_REQUEST
    assert too_long.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items').fetchone()[0] == 0
