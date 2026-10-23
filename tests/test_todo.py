from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH


@pytest.fixture
def client(tmp_path):
    from homehub.app import create_app, get_db
    from flask import g

    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'test-only-secret',
        'DATABASE': str(tmp_path / 'homehub.db'),
    })
    with app.app_context():
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                   ('alice', PH.hash('a long test password'), 'admin'))
        db.commit()
    return app.test_client()


def token_for(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(client, username='alice', password='a long test password'):
    token = token_for(client)
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': token,
    })
    assert response.status_code == HTTPStatus.FOUND


def setup_todo(client, visibility='shared'):
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        db.execute("INSERT INTO household_lists(kind,title,visibility,created_by) "
                   "VALUES('todo','Weekend jobs',?,1)", (visibility,))
        list_id = db.execute("SELECT id FROM household_lists WHERE title='Weekend jobs'").fetchone()[0]
        db.execute("INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)",
                   ('Water plants', 1, list_id))
        item_id = db.execute("SELECT id FROM shopping_items WHERE list_id=?", (list_id,)).fetchone()[0]
        db.commit()
    return db_path, bob_id, list_id, item_id


def test_todo_creation_saves_optional_due_date_and_assignee(client):
    sign_in(client)
    db_path, bob_id, list_id, _ = setup_todo(client)

    response = client.post('/todo', data={
        'text': 'Water plants', 'list_id': str(list_id),
        'due_date': '2026-10-25', 'assignee_id': str(bob_id),
        '_csrf': token_for(client),
    })

    assert response.status_code == HTTPStatus.FOUND
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT due_date,assignee_id FROM shopping_items "
                         "WHERE text='Water plants' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ('2026-10-25', bob_id)


@pytest.mark.parametrize('fields', [
    {'due_date': 'next Friday', 'assignee_id': ''},
    {'due_date': '', 'assignee_id': '99999'},
])
def test_todo_creation_rejects_invalid_due_date_or_assignee(client, fields):
    sign_in(client)
    db_path, _, list_id, _ = setup_todo(client)

    response = client.post('/todo', data={
        'text': 'Invalid task', 'list_id': str(list_id), **fields,
        '_csrf': token_for(client),
    })

    assert response.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM shopping_items WHERE text='Invalid task'").fetchone()[0] == 0


def test_any_member_with_shared_todo_access_can_edit_task(client):
    sign_in(client)
    db_path, bob_id, list_id, item_id = setup_todo(client)
    bob = client.application.test_client()
    sign_in(bob, 'bob', 'bob test password')

    response = bob.post(f'/todo/{item_id}/edit', data={
        'text': 'Water the seedlings', 'due_date': '2026-10-26',
        'assignee_id': str(bob_id), '_csrf': token_for(bob),
    })

    assert response.status_code == HTTPStatus.FOUND
    with sqlite3.connect(db_path) as db:
        row = db.execute('SELECT text,due_date,assignee_id FROM shopping_items WHERE id=?',
                         (item_id,)).fetchone()
    assert row == ('Water the seedlings', '2026-10-26', bob_id)


def test_todo_edit_requires_list_visibility_and_does_not_edit_shopping_items(client):
    sign_in(client)
    db_path, bob_id, list_id, item_id = setup_todo(client, visibility='private')
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO household_lists(kind,title,visibility,created_by) "
                   "VALUES('shopping','Shopping','shared',1)")
        shopping_list_id = db.execute("SELECT id FROM household_lists WHERE title='Shopping'").fetchone()[0]
        db.execute("INSERT INTO shopping_items(text,created_by,list_id) VALUES('Milk',1,?)",
                   (shopping_list_id,))
        shopping_item_id = db.execute("SELECT id FROM shopping_items WHERE list_id=?",
                                      (shopping_list_id,)).fetchone()[0]
        db.commit()
    bob = client.application.test_client()
    sign_in(bob, 'bob', 'bob test password')
    payload = {'text': 'Changed', 'due_date': '', 'assignee_id': '', '_csrf': token_for(bob)}

    assert bob.post(f'/todo/{item_id}/edit', data=payload).status_code == HTTPStatus.NOT_FOUND
    assert bob.post(f'/todo/{shopping_item_id}/edit', data=payload).status_code == HTTPStatus.NOT_FOUND
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT text FROM shopping_items WHERE id=?', (item_id,)).fetchone()[0] == 'Water plants'
        assert db.execute('SELECT text FROM shopping_items WHERE id=?', (shopping_item_id,)).fetchone()[0] == 'Milk'


def test_read_only_house_account_cannot_edit_todo(client):
    sign_in(client)
    db_path, _, _, item_id = setup_todo(client)
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,password_hash,role,account_kind) "
                   "VALUES('house',?,'user','house')", (PH.hash('house test password'),))
        db.commit()
    house = client.application.test_client()
    sign_in(house, 'house', 'house test password')

    response = house.post(f'/todo/{item_id}/edit', data={
        'text': 'Changed', 'due_date': '', 'assignee_id': '', '_csrf': token_for(house),
    })

    assert response.status_code == HTTPStatus.FORBIDDEN
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT text FROM shopping_items WHERE id=?', (item_id,)).fetchone()[0] == 'Water plants'


def test_todo_list_shows_assignment_due_date_and_edit_control(client):
    sign_in(client)
    db_path, bob_id, list_id, item_id = setup_todo(client)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE shopping_items SET due_date='2026-10-25',assignee_id=? WHERE id=?",
                   (bob_id, item_id))
        db.commit()

    page = client.get(f'/?kind=todo&list_id={list_id}')

    assert b'Assigned to Bob' in page.data
    assert b'2026-10-25' in page.data
    assert b'Edit task' in page.data


def test_private_todo_assignment_is_limited_to_list_members(client):
    sign_in(client)
    db_path, bob_id, list_id, _ = setup_todo(client, visibility='private')
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('carol','Carol',?,'user')", (PH.hash('carol test password'),))
        carol_id = db.execute("SELECT id FROM users WHERE username='carol'").fetchone()[0]
        db.execute('INSERT INTO list_access(list_id,user_id) VALUES(?,?)', (list_id, bob_id))
        db.commit()
    bob = client.application.test_client()
    sign_in(bob, 'bob', 'bob test password')

    page = bob.get(f'/?kind=todo&list_id={list_id}')
    assignment_options = page.data.split(b'<select name="assignee_id">', 1)[1].split(b'</select>', 1)[0]
    assert b'Carol' not in assignment_options
    response = bob.post('/todo', data={
        'text': 'Private task', 'list_id': str(list_id), 'assignee_id': str(carol_id),
        '_csrf': token_for(bob),
    })

    assert response.status_code == HTTPStatus.BAD_REQUEST
    edit_response = bob.post(f'/todo/{_}/edit', data={
        'text': 'Private task', 'due_date': '', 'assignee_id': str(carol_id),
        '_csrf': token_for(bob),
    })
    assert edit_response.status_code == HTTPStatus.BAD_REQUEST
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM shopping_items WHERE text='Private task'").fetchone()[0] == 0
