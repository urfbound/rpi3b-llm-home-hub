from http import HTTPStatus
import sqlite3
import re

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
    assert response.status_code==HTTPStatus.FOUND

def csrf_token(client):
    client.get('/login')
    with client.session_transaction() as sess:
        return sess['csrf_token']

def sign_in_as(client, username, password):
    token = csrf_token(client)
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': token,
    })
    assert response.status_code == HTTPStatus.FOUND

def test_requires_login(client):
    assert client.get('/').status_code==HTTPStatus.FOUND

def test_login_and_ics(client):
    sign_in(client)
    assert b'A little home for everything' in client.get('/').data
    with client.session_transaction() as sess: token=sess['csrf_token']
    client.post('/events',data={'title':'Pasta night','event_date':'2026-10-21','_csrf':token})
    calendar=client.get('/calendar.ics')
    assert calendar.status_code==HTTPStatus.OK
    assert b'SUMMARY:Pasta night' in calendar.data
    assert b'DTSTART;VALUE=DATE:20261021' in calendar.data

def test_csrf_required(client):
    sign_in(client)
    assert client.post('/shopping',data={'text':'milk'}).status_code==HTTPStatus.BAD_REQUEST

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
    export = client.get('/shopping.txt')
    assert b'[ ] Milk' in export.data
    assert export.headers['Cache-Control'] == 'no-store'
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        list_id = db.execute('SELECT list_id FROM shopping_items WHERE text="Milk"').fetchone()[0]
        assert db.execute('SELECT title,kind FROM household_lists WHERE id=?',
                          (list_id,)).fetchone() == ('Groceries', 'shopping')

def test_paste_list_merges_checkmarks_and_adds_items_owned_by_importer(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        list_id = db.execute("SELECT id FROM household_lists WHERE is_default=1").fetchone()[0]
        db.executemany('INSERT INTO shopping_items(text,done,created_by,list_id) VALUES(?,?,?,?)', [
            ('Milk', 0, bob_id, list_id), ('Bread', 1, bob_id, list_id),
        ])
        milk_id = db.execute("SELECT id FROM shopping_items WHERE text='Milk'").fetchone()[0]

    token = csrf_token(client)
    pasted_text = 'List copied away from home\n\n[*]  milk  \n[ ] Bread\n[*] Eggs\n[ ] Tea\n[ ] eggs'
    response = client.post(f'/lists/{list_id}/paste', data={
        '_csrf': token,
        'text': pasted_text,
    })
    assert response.status_code == 200
    assert b'Review list changes' in response.data
    assert response.headers['Cache-Control'] == 'no-store'
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT text,done FROM shopping_items WHERE list_id=? ORDER BY id',
                          (list_id,)).fetchall() == [('Milk', 0), ('Bread', 1)]
    review_token = re.search(rb'name="review_token" value="([^"]+)"',
                             response.data).group(1).decode()
    response = client.post(f'/lists/{list_id}/paste/apply', data={
        '_csrf': csrf_token(client), 'text': pasted_text, 'review_token': review_token,
    })
    assert response.status_code == 302
    with sqlite3.connect(db_path) as db:
        rows = db.execute('SELECT text,done,created_by FROM shopping_items '
                          'WHERE list_id=? ORDER BY id', (list_id,)).fetchall()
    assert rows == [('Milk', 1, bob_id), ('Bread', 1, bob_id),
                    ('Eggs', 1, 1), ('Tea', 0, 1)]
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT done FROM shopping_items WHERE id=?',
                          (milk_id,)).fetchone() == (1,)

def test_paste_list_rechecks_private_list_access(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('carol','Carol',?,'user')", (PH.hash('carol test password'),))
        db.execute("INSERT INTO household_lists(kind,title,category,visibility,created_by) "
                   "VALUES('shopping','Private','Gifts','private',1)")
        list_id = db.execute("SELECT id FROM household_lists WHERE title='Private'").fetchone()[0]

    carol = client.application.test_client()
    sign_in_as(carol, 'carol', 'carol test password')
    response = carol.post(f'/lists/{list_id}/paste', data={
        '_csrf': csrf_token(carol), 'text': 'Private\n\n[x] Secret item',
    })
    assert response.status_code == 404
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE list_id=?',
                          (list_id,)).fetchone() == (0,)

def test_list_page_offers_copy_and_paste_controls(client):
    sign_in(client)
    page = client.get('/')
    assert b'Copy list' in page.data
    assert b'Paste list' in page.data
    assert b'Clipboard access unavailable' in page.data

def test_calendar_month_control_selects_the_month_shown_in_its_heading(client):
    sign_in(client)
    response = client.get('/?month=10&year=2026')

    assert b'October 2026' in response.data
    assert b'<option value="10" selected>October</option>' in response.data
    assert b'<option value="9" selected>September</option>' not in response.data

def test_calendar_download_links_render_on_separate_lines(client):
    sign_in(client)
    response = client.get('/?month=10&year=2026')

    assert b'</a></p>\n    <p><a href="/calendar.ics?only_required=1">' in response.data

def test_theme_switch_uses_approved_theme_label(client):
    sign_in(client)

    assert '◐ Theme'.encode() in client.get('/').data

def test_list_picker_groups_shopping_lists_by_category(client):
    sign_in(client)
    response = client.get('/')

    assert b'<optgroup label="Groceries">' in response.data

def test_calendar_weekdays_include_responsive_abbreviations(client):
    sign_in(client)
    response = client.get('/?month=10&year=2026')

    assert b'<span class="weekday-full">Sunday</span>' in response.data
    assert b'<span class="weekday-short">Sun</span>' in response.data
    assert b'<span class="weekday-narrow">S</span>' in response.data

def test_list_and_recipe_remove_controls_have_inline_layout_hooks(client):
    sign_in(client)
    response = client.get('/')

    assert b'class="checks list-items"' in response.data
    assert b'class="recipe-ingredient-row"' in response.data
    assert b'class="recipe-step-row"' in response.data
def test_top_navigation_links_to_dedicated_lists_and_calendar_pages(client):
    sign_in(client)
    page = client.get('/')

    assert b'href="/lists"' in page.data
    assert b'href="/calendar"' in page.data

def test_homepage_cards_link_to_dedicated_pages(client):
    sign_in(client)
    page = client.get('/')

    assert b'href="/lists?kind=chores"' in page.data
    assert b'href="/calendar"' in page.data

def test_lists_and_calendar_routes_show_their_own_sections(client):
    sign_in(client)

    lists_page = client.get('/lists')
    calendar_page = client.get('/calendar')

    assert lists_page.status_code == HTTPStatus.OK
    assert b'id="shopping"' in lists_page.data
    assert b'id="calendar"' not in lists_page.data
    assert calendar_page.status_code == HTTPStatus.OK
    assert b'id="calendar"' in calendar_page.data
    assert b'id="shopping"' not in calendar_page.data

def test_list_text_exports_the_selected_todo_list(client):
    sign_in(client)
    token = csrf_token(client)
    created = client.post('/lists', data={
        'kind': 'todo', 'title': 'Weekend jobs', 'visibility': 'shared', '_csrf': token,
    })
    list_id = int(created.headers['Location'].split('list_id=')[1])
    token = csrf_token(client)
    client.post('/todo', data={
        'text': 'Water plants', 'list_id': str(list_id), '_csrf': token,
    })
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        db.execute('UPDATE shopping_items SET done=1 WHERE list_id=?', (list_id,))
    response = client.get(f'/lists/{list_id}/text')
    assert response.status_code == 200
    assert response.mimetype == 'text/plain'
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.get_data(as_text=True) == 'Weekend jobs\n\n[x] Water plants\n'

def test_paste_list_rejects_invalid_item_without_partial_changes(client):
    sign_in(client)
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        list_id = db.execute('SELECT id FROM household_lists WHERE is_default=1').fetchone()[0]
    token = csrf_token(client)
    response = client.post(f'/lists/{list_id}/paste', data={
        '_csrf': token, 'text': 'Groceries\n\n[ ] Apples\n[ ] ' + ('x' * 241),
    })
    assert response.status_code == 200
    assert b'need attention' in response.data
    assert b'Merge list' not in response.data
    with sqlite3.connect(client.application.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE list_id=?',
                          (list_id,)).fetchone() == (0,)

def test_paste_list_flags_ambiguous_existing_matches(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        list_id = db.execute('SELECT id FROM household_lists WHERE is_default=1').fetchone()[0]
        db.executemany('INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)', [
            ('Green apples', 1, list_id), (' green   apples ', 1, list_id),
        ])
    response = client.post(f'/lists/{list_id}/paste', data={
        '_csrf': csrf_token(client), 'text': 'Groceries\n\n[x] GREEN apples',
    })
    assert response.status_code == 200
    assert b'ambiguous match' in response.data
    assert b'Merge list' not in response.data

def test_paste_apply_requires_a_fresh_review_when_list_changes(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        list_id = db.execute('SELECT id FROM household_lists WHERE is_default=1').fetchone()[0]
    text = 'Groceries\n\n[ ] Apples'
    review = client.post(f'/lists/{list_id}/paste', data={
        '_csrf': csrf_token(client), 'text': text,
    })
    review_token = re.search(rb'name="review_token" value="([^"]+)"',
                             review.data).group(1).decode()
    with sqlite3.connect(db_path) as db:
        db.execute('INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
                   ('Apples', 1, list_id))
    response = client.post(f'/lists/{list_id}/paste/apply', data={
        '_csrf': csrf_token(client), 'text': text, 'review_token': review_token,
    })
    assert response.status_code == 409
    assert b'list changed after the preview' in response.data
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE list_id=?',
                          (list_id,)).fetchone() == (1,)

def test_paste_apply_rechecks_private_list_access(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        db.execute("INSERT INTO household_lists(kind,title,category,visibility,created_by) "
                   "VALUES('shopping','Bob private','Gifts','private',1)")
        list_id = db.execute("SELECT id FROM household_lists WHERE title='Bob private'").fetchone()[0]
        db.execute('INSERT INTO list_access(list_id,user_id) VALUES(?,?)', (list_id, bob_id))
    bob = client.application.test_client()
    sign_in_as(bob, 'bob', 'bob test password')
    text = 'Bob private\n\n[x] Secret'
    review = bob.post(f'/lists/{list_id}/paste', data={
        '_csrf': csrf_token(bob), 'text': text,
    })
    review_token = re.search(rb'name="review_token" value="([^"]+)"',
                             review.data).group(1).decode()
    with sqlite3.connect(db_path) as db:
        db.execute('DELETE FROM list_access WHERE list_id=? AND user_id=?',
                   (list_id, bob_id))
    response = bob.post(f'/lists/{list_id}/paste/apply', data={
        '_csrf': csrf_token(bob), 'text': text, 'review_token': review_token,
    })
    assert response.status_code == 404
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE list_id=?',
                          (list_id,)).fetchone() == (0,)

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
    assert response.status_code == HTTPStatus.FOUND
    assert 'list_id=' in response.headers['Location']
    list_id = int(response.headers['Location'].split('list_id=')[1])

    token = csrf_token(client)
    added = client.post('/shopping', data={
        'text': 'Wrapping paper', 'list_id': str(list_id), '_csrf': token,
    })
    assert added.status_code == HTTPStatus.FOUND
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
    assert bob.get(f'/?list_id={list_id}').status_code == HTTPStatus.OK
    assert b'Wrapping paper' in bob.get(f'/shopping.txt?list_id={list_id}').data

    carol = client.application.test_client()
    sign_in_as(carol, 'carol', 'carol test password')
    denied = carol.get(f'/?list_id={list_id}')
    assert denied.status_code == HTTPStatus.NOT_FOUND
    assert b'Wrapping paper' not in denied.data
    assert carol.get(f'/shopping.txt?list_id={list_id}').status_code == HTTPStatus.NOT_FOUND
    token = csrf_token(carol)
    assert carol.post(f'/shopping/{item_id}/toggle', data={'_csrf': token}).status_code == HTTPStatus.NOT_FOUND

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
        assert response.status_code == HTTPStatus.BAD_REQUEST
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
    assert response.status_code == HTTPStatus.FOUND
    list_id = int(response.headers['Location'].split('list_id=')[1])
    token = csrf_token(client)
    assert client.post('/todo', data={
        'text': 'Water the plants', 'list_id': str(list_id), '_csrf': token,
    }).status_code == HTTPStatus.FOUND

    bob = client.application.test_client()
    sign_in_as(bob, 'bob', 'bob test password')
    page = bob.get(f'/?kind=todo&list_id={list_id}')
    assert page.status_code == HTTPStatus.OK
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
    assert bob.post(f'/shopping/{item_id}/delete', data={'_csrf': token}).status_code == HTTPStatus.FORBIDDEN
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE id=?',
                          (item_id,)).fetchone()[0] == 1

    token = csrf_token(client)
    assert client.post(f'/shopping/{item_id}/delete', data={'_csrf': token}).status_code == HTTPStatus.FOUND
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
    assert bob.post(f'/lists/{list_id}/delete', data={'_csrf': token}).status_code == HTTPStatus.FOUND
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM household_lists WHERE id=?',
                          (list_id,)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM shopping_items WHERE id=?',
                          (item_id,)).fetchone()[0] == 0

    token = csrf_token(client)
    assert client.post(f'/lists/{list_id}/delete', data={'_csrf': token}).status_code == HTTPStatus.NOT_FOUND


def test_non_owner_cannot_delete_a_list_or_cascade_its_items(client):
    sign_in(client)
    db_path = client.application.config['DATABASE']
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('bob','Bob',?,'user')", (PH.hash('bob test password'),))
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('carol','Carol',?,'user')", (PH.hash('carol test password'),))
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('dave','Dave',?,'user')", (PH.hash('dave test password'),))
        bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        carol_id = db.execute("SELECT id FROM users WHERE username='carol'").fetchone()[0]
        db.execute("INSERT INTO household_lists(kind,title,visibility,created_by) "
                   "VALUES('shopping','Bob private list','private',?)", (bob_id,))
        list_id = db.execute(
            "SELECT id FROM household_lists WHERE title='Bob private list'"
        ).fetchone()[0]
        db.execute('INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
                   ('Keep this item', bob_id, list_id))
        db.execute('INSERT INTO list_access(list_id,user_id) VALUES(?,?)',
                   (list_id, carol_id))
        item_id = db.execute('SELECT id FROM shopping_items WHERE list_id=?',
                             (list_id,)).fetchone()[0]

    dave = client.application.test_client()
    sign_in_as(dave, 'dave', 'dave test password')
    token = csrf_token(dave)
    hidden = dave.post(f'/lists/{list_id}/delete', data={'_csrf': token})
    assert hidden.status_code == HTTPStatus.NOT_FOUND

    carol = client.application.test_client()
    sign_in_as(carol, 'carol', 'carol test password')
    token = csrf_token(carol)
    response = carol.post(f'/lists/{list_id}/delete', data={'_csrf': token})

    assert response.status_code == HTTPStatus.FORBIDDEN
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT title FROM household_lists WHERE id=?',
                          (list_id,)).fetchone() == ('Bob private list',)
        assert db.execute('SELECT text FROM shopping_items WHERE id=?',
                          (item_id,)).fetchone() == ('Keep this item',)
