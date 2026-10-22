import sqlite3

import pytest
from homehub.app import create_app, PH

@pytest.fixture
def client(tmp_path):
    app=create_app({'TESTING':True,'SECRET_KEY':'test-only-secret','DATABASE':str(tmp_path/'homehub.db')})
    with app.app_context():
        from flask import g
        g.app_db_path=app.config['DATABASE']
        from homehub.app import get_db
        db=get_db()
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",('alice',PH.hash('a long test password'),'admin'))
        db.commit()
    return app.test_client()

def sign_in(client):
    client.get('/login')
    with client.session_transaction() as sess: token=sess['csrf_token']
    response=client.post('/login',data={'username':'alice','password':'a long test password','_csrf':token})
    assert response.status_code==302

def csrf_token(client):
    client.get('/login')
    with client.session_transaction() as sess:
        return sess['csrf_token']

def sign_in_as(client, username, password):
    token = csrf_token(client)
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': token,
    })
    assert response.status_code == 302

def test_requires_login(client):
    assert client.get('/').status_code==302

def test_login_and_ics(client):
    sign_in(client)
    assert b'A little home for everything' in client.get('/').data
    with client.session_transaction() as sess: token=sess['csrf_token']
    client.post('/events',data={'title':'Pasta night','event_date':'2026-10-21','_csrf':token})
    calendar=client.get('/calendar.ics')
    assert calendar.status_code==200
    assert b'SUMMARY:Pasta night' in calendar.data
    assert b'DTSTART;VALUE=DATE:20261021' in calendar.data

def test_csrf_required(client):
    sign_in(client)
    assert client.post('/shopping',data={'text':'milk'}).status_code==400

def test_secure_cookie_can_be_enabled_for_https_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv('HOMEHUB_COOKIE_SECURE', 'true')
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret',
                      'DATABASE': str(tmp_path / 'secure-cookie.db')})
    assert app.config['SESSION_COOKIE_SECURE'] is True
    response = app.test_client().get('/login')
    set_cookie = response.headers['Set-Cookie']
    assert 'Secure' in set_cookie
    assert 'HttpOnly' in set_cookie
    assert 'SameSite=Lax' in set_cookie

def test_secure_cookie_stays_off_for_local_http_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv('HOMEHUB_COOKIE_SECURE', raising=False)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret',
                      'DATABASE': str(tmp_path / 'http-cookie.db')})
    assert app.config['SESSION_COOKIE_SECURE'] is False

def test_shopping(client):
    sign_in(client)
    with client.session_transaction() as sess: token=sess['csrf_token']
    client.post('/shopping',data={'text':'Milk','_csrf':token})
    assert b'[ ] Milk' in client.get('/shopping.txt').data
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        list_id = db.execute('SELECT list_id FROM shopping_items WHERE text="Milk"').fetchone()[0]
        assert db.execute('SELECT title,kind FROM household_lists WHERE id=?',
                          (list_id,)).fetchone() == ('Groceries', 'shopping')

def test_private_list_creation_selection_and_allowlist_access(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('carol','Carol',?,'user')", (PH.hash('carol test password'),))
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        carol_id = db.execute("SELECT id FROM users WHERE username='carol'").fetchone()[0]

    token = csrf_token(client)
    response = client.post('/lists', data={
        'kind': 'shopping', 'title': 'Holiday gifts', 'category': 'Gifts',
        'visibility': 'private', 'allowed_user_ids': str(bob_id), '_csrf': token,
    })
    assert response.status_code == 302
    assert 'list_id=' in response.headers['Location']
    list_id = int(response.headers['Location'].split('list_id=')[1])

    token = csrf_token(client)
    added = client.post('/shopping', data={
        'text': 'Wrapping paper', 'list_id': str(list_id), '_csrf': token,
    })
    assert added.status_code == 302
    assert b'Holiday gifts' in client.get(f'/?list_id={list_id}').data
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT text FROM shopping_items WHERE list_id=?',
                          (list_id,)).fetchone() == ('Wrapping paper',)
        assert db.execute('SELECT user_id FROM list_access WHERE list_id=?',
                          (list_id,)).fetchone() == (bob_id,)
        item_id = db.execute('SELECT id FROM shopping_items WHERE list_id=?',
                             (list_id,)).fetchone()[0]

    bob = client.application.test_client()
    sign_in_as(bob, 'bob', 'bob test password')
    assert bob.get(f'/?list_id={list_id}').status_code == 200
    assert b'Wrapping paper' in bob.get(f'/shopping.txt?list_id={list_id}').data

    carol = client.application.test_client()
    sign_in_as(carol, 'carol', 'carol test password')
    denied = carol.get(f'/?list_id={list_id}')
    assert denied.status_code == 404
    assert b'Wrapping paper' not in denied.data
    assert carol.get(f'/shopping.txt?list_id={list_id}').status_code == 404
    token = csrf_token(carol)
    assert carol.post(f'/shopping/{item_id}/toggle', data={'_csrf': token}).status_code == 404

    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT done FROM shopping_items WHERE id=?',
                          (item_id,)).fetchone() == (0,)

def test_list_creation_rejects_unknown_kind_or_shopping_category(client):
    sign_in(client)
    token = csrf_token(client)
    for kind, category in (('chores', 'Other'), ('shopping', 'Arbitrary')):
        response = client.post('/lists', data={
            'kind': kind, 'title': 'Invalid list', 'category': category,
            'visibility': 'shared', '_csrf': token,
        })
        assert response.status_code == 400
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM household_lists WHERE title='Invalid list'").fetchone()[0] == 0

def test_shared_todo_list_accepts_tasks_and_is_visible_to_household(client):
    sign_in(client)
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))

    token = csrf_token(client)
    response = client.post('/lists', data={
        'kind': 'todo', 'title': 'Weekend jobs', 'visibility': 'shared', '_csrf': token,
    })
    assert response.status_code == 302
    list_id = int(response.headers['Location'].split('list_id=')[1])
    token = csrf_token(client)
    assert client.post('/todo', data={
        'text': 'Water the plants', 'list_id': str(list_id), '_csrf': token,
    }).status_code == 302

    bob = client.application.test_client()
    sign_in_as(bob, 'bob', 'bob test password')
    page = bob.get(f'/?kind=todo&list_id={list_id}')
    assert page.status_code == 200
    assert b'Water the plants' in page.data

def test_item_delete_is_limited_to_creator_or_admin(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        list_id = db.execute("SELECT id FROM household_lists WHERE is_default=1").fetchone()[0]
        db.execute('INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
                   ('Milk', 1, list_id))
        item_id = db.execute('SELECT id FROM shopping_items WHERE text=\'Milk\'').fetchone()[0]

    bob = client.application.test_client()
    sign_in_as(bob, 'bob', 'bob test password')
    token = csrf_token(bob)
    assert bob.post(f'/shopping/{item_id}/delete', data={'_csrf': token}).status_code == 403
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE id=?',
                          (item_id,)).fetchone()[0] == 1

    token = csrf_token(client)
    assert client.post(f'/shopping/{item_id}/delete', data={'_csrf': token}).status_code == 302
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE id=?',
                          (item_id,)).fetchone()[0] == 0

def test_only_list_creator_or_admin_can_delete_list_and_items_cascade(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        db.execute("INSERT INTO household_lists(kind,title,category,visibility,created_by) "
                   "VALUES('shopping','Bob list','Gifts','shared',?)", (bob_id,))
        list_id = db.execute("SELECT id FROM household_lists WHERE title='Bob list'").fetchone()[0]
        db.execute('INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
                   ('Toy', bob_id, list_id))
        item_id = db.execute('SELECT id FROM shopping_items WHERE list_id=?',
                             (list_id,)).fetchone()[0]

    bob = client.application.test_client()
    sign_in_as(bob, 'bob', 'bob test password')
    token = csrf_token(bob)
    assert bob.post(f'/lists/{list_id}/delete', data={'_csrf': token}).status_code == 302
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM household_lists WHERE id=?',
                          (list_id,)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE id=?',
                          (item_id,)).fetchone()[0] == 0

    token = csrf_token(client)
    assert client.post(f'/lists/{list_id}/delete', data={'_csrf': token}).status_code == 404
