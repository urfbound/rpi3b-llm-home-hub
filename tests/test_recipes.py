import sqlite3

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'recipe-test-secret',
        'DATABASE': str(tmp_path / 'recipes.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('alex','Alex',?,'admin')", (PH.hash('recipe test password'),))
        db.commit()
    return app.test_client()


def csrf(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(client):
    response = client.post('/login', data={
        'username': 'alex', 'password': 'recipe test password', '_csrf': csrf(client),
    })
    assert response.status_code == 302


def recipe_form(client, **overrides):
    data = {
        'title': 'Lemon pasta', 'description': 'Quick weeknight meal',
        'default_servings': '4',
        'ingredient_quantity': ['2', '1/2'],
        'ingredient_unit': ['cups', 'tsp'],
        'ingredient_name': ['flour', 'salt'],
        'ingredient_note': ['', 'to taste'],
        'step': ['Mix the flour.', 'Add salt and stir.'],
        '_csrf': csrf(client),
    }
    data.update(overrides)
    return data


def test_create_structured_recipe_and_render_detail(client):
    sign_in(client)

    response = client.post('/recipes', data=recipe_form(client))

    assert response.status_code == 302
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        recipe = db.execute('SELECT title,description,default_servings FROM recipes').fetchone()
        ingredients = db.execute('SELECT quantity,unit,name,note,position FROM recipe_ingredients '
                                 'ORDER BY position').fetchall()
        steps = db.execute('SELECT text,position FROM recipe_steps ORDER BY position').fetchall()
    assert recipe == ('Lemon pasta', 'Quick weeknight meal', 4)
    assert ingredients == [('2', 'cups', 'flour', '', 0), ('1/2', 'tsp', 'salt', 'to taste', 1)]
    assert steps == [('Mix the flour.', 0), ('Add salt and stir.', 1)]

    page = client.get('/recipes/1')
    assert page.status_code == 200
    assert b'Quick weeknight meal' in page.data
    assert b'2 cups' in page.data
    assert b'flour' in page.data
    assert b'Add salt and stir.' in page.data


@pytest.mark.parametrize('overrides', [
    {'title': 'x' * 129},
    {'description': 'x' * 257},
    {'default_servings': '0'},
    {'ingredient_quantity': ['x' * 33], 'ingredient_unit': [''],
     'ingredient_name': ['flour'], 'ingredient_note': ['']},
    {'ingredient_quantity': [''], 'ingredient_unit': ['x' * 17],
     'ingredient_name': ['flour'], 'ingredient_note': ['']},
    {'ingredient_quantity': [''], 'ingredient_unit': [''],
     'ingredient_name': ['x' * 129], 'ingredient_note': ['']},
    {'ingredient_quantity': [''], 'ingredient_unit': [''],
     'ingredient_name': ['flour'], 'ingredient_note': ['x' * 129]},
    {'step': ['x' * 257]},
    {'ingredient_quantity': ['1'] * 65, 'ingredient_unit': [''] * 65,
     'ingredient_name': ['item'] * 65, 'ingredient_note': [''] * 65},
    {'step': ['step'] * 65},
])
def test_recipe_limits_reject_invalid_content_without_partial_save(client, overrides):
    sign_in(client)

    response = client.post('/recipes', data=recipe_form(client, **overrides))

    assert response.status_code == 400
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM recipe_ingredients').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM recipe_steps').fetchone()[0] == 0


def test_recipe_book_limit_is_per_user_and_rejects_129th(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.executemany('INSERT INTO recipes(title,created_by) VALUES(?,1)',
                       [(f'Recipe {n}',) for n in range(128)])
        db.commit()

    response = client.post('/recipes', data=recipe_form(client))

    assert response.status_code == 409
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM recipes WHERE created_by=1').fetchone()[0] == 128


def test_recipe_create_rejects_missing_or_blank_structured_rows(client):
    sign_in(client)
    data = recipe_form(client, ingredient_quantity=[''], ingredient_unit=[''],
                       ingredient_name=['   '], ingredient_note=[''], step=['   '])

    response = client.post('/recipes', data=data)

    assert response.status_code == 400
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0] == 0


def test_recipe_accepts_all_documented_unicode_field_limits(client):
    sign_in(client)
    data = recipe_form(
        client, title='🍲' * 32, description='🍲' * 64,
        ingredient_quantity=['🍲' * 8] * 64,
        ingredient_unit=['🍲' * 4] * 64,
        ingredient_name=['🍲' * 32] * 64,
        ingredient_note=['🍲' * 32] * 64,
        step=['🍲' * 64] * 64,
    )

    response = client.post('/recipes', data=data)

    assert response.status_code == 302
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM recipe_ingredients').fetchone()[0] == 64
        assert db.execute('SELECT COUNT(*) FROM recipe_steps').fetchone()[0] == 64
