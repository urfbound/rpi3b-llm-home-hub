from http import HTTPStatus
import sqlite3

from homehub.app import PH, create_app


def make_app(path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'house-read-only-test',
        'DATABASE': str(path),
    })
    with sqlite3.connect(path) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin test password'), 'admin', 'person'),
             ('house', 'House', PH.hash('house test password'), 'user', 'house')],
        )
        admin_id, house_id = db.execute(
            "SELECT id FROM users WHERE username='admin' UNION ALL "
            "SELECT id FROM users WHERE username='house'"
        ).fetchall()
        db.execute(
            "INSERT INTO household_lists(kind,title,visibility,created_by) "
            "VALUES('shopping','Admin list','shared',?)", (admin_id[0],),
        )
        admin_list_id = db.execute(
            "SELECT id FROM household_lists WHERE title='Admin list'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO household_lists(kind,title,visibility,created_by) "
            "VALUES('shopping','Legacy house list','shared',?)", (house_id[0],),
        )
        house_list_id = db.execute(
            "SELECT id FROM household_lists WHERE title='Legacy house list'"
        ).fetchone()[0]
        db.execute(
            'INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
            ('Admin item', admin_id[0], admin_list_id),
        )
        db.execute(
            'INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
            ('Legacy house item', house_id[0], house_list_id),
        )
        db.execute(
            "INSERT INTO household_lists(kind,title,visibility,created_by) "
            "VALUES('todo','House tasks','shared',?)", (house_id[0],),
        )
        todo_list_id = db.execute(
            "SELECT id FROM household_lists WHERE title='House tasks'"
        ).fetchone()[0]
        db.execute(
            'INSERT INTO shopping_items(text,created_by,list_id) VALUES(?,?,?)',
            ('Legacy house task', house_id[0], todo_list_id),
        )
        db.execute(
            'INSERT INTO chores(weekday,text,created_by,assigned_everyone) VALUES(0,?,?,1)',
            ('Legacy house chore', house_id[0]),
        )
    return app, admin_list_id, house_list_id, todo_list_id


def token_for(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(client, username, password):
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': token_for(client),
    })
    assert response.status_code == HTTPStatus.FOUND


def post(client, url, **data):
    return client.post(url, data={**data, '_csrf': token_for(client)})


def test_house_account_cannot_create_or_change_lists_items_or_chores(tmp_path):
    app, admin_list_id, house_list_id, todo_list_id = make_app(tmp_path / 'hub.db')
    house = app.test_client()
    sign_in(house, 'house', 'house test password')
    with sqlite3.connect(app.config['DATABASE']) as db:
        admin_item_id = db.execute(
            "SELECT id FROM shopping_items WHERE text='Admin item'"
        ).fetchone()[0]
        house_item_id = db.execute(
            "SELECT id FROM shopping_items WHERE text='Legacy house item'"
        ).fetchone()[0]
        house_task_id = db.execute(
            "SELECT id FROM shopping_items WHERE text='Legacy house task'"
        ).fetchone()[0]
        house_chore_id = db.execute(
            "SELECT id FROM chores WHERE text='Legacy house chore'"
        ).fetchone()[0]

    responses = [
        post(house, '/lists', kind='shopping', title='New list', visibility='shared'),
        post(house, '/shopping', text='New item', list_id=str(admin_list_id)),
        post(house, '/todo', text='New task', list_id=str(todo_list_id)),
        post(house, f'/shopping/{admin_item_id}/toggle'),
        post(house, f'/shopping/{admin_item_id}/delete'),
        post(house, f'/shopping/{house_item_id}/delete'),
        post(house, f'/shopping/{house_task_id}/toggle'),
        post(house, f'/todo/{house_task_id}/edit', text='Changed', due_date='', assignee_id=''),
        post(house, f'/lists/{admin_list_id}/delete'),
        post(house, f'/lists/{house_list_id}/delete'),
        post(house, '/chores', text='New chore', weekday='0', assignee='everyone'),
        post(house, f'/chores/{house_chore_id}/toggle'),
        post(house, f'/chores/{house_chore_id}/delete'),
    ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.FORBIDDEN,
    ] * len(responses)
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM household_lists').fetchone()[0] == 4
        assert db.execute('SELECT COUNT(*) FROM shopping_items').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM chores').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM chore_completions').fetchone()[0] == 0
