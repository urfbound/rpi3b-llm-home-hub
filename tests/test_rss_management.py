from http import HTTPStatus
import sqlite3

import pytest

from homehub.constants import HOUSEHOLD_FEEDS_MAXIMUM
import homehub.app as app_module
from homehub.app import PH, create_app
from homehub.rss import RSSFeedError


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-management-test',
                      'DATABASE': str(tmp_path / 'rss.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin password'), 'admin', 'person'),
             ('reader', 'Reader', PH.hash('reader password'), 'user', 'person'),
             ('house', 'House', PH.hash('house password'), 'user', 'house')],
        )
    return app


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def sign_in(client, username, password):
    client.get('/login')
    return client.post('/login', data={'username': username, 'password': password,
                                       '_csrf': csrf(client)})


def post(client, path, **data):
    return client.post(path, data={**data, '_csrf': csrf(client)})


def add_feed(db, url, title, scope, owner, creator):
    return db.execute(
        'INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
        'VALUES(?,?,?,?,?)', (url, title, scope, owner, creator),
    ).lastrowid


def ids(app, *names):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return dict(db.execute(
            f"SELECT username,id FROM users WHERE username IN ({','.join('?' for _ in names)})",
            names,
        ))


def test_rss_requires_login(app):
    assert app.test_client().get('/rss').status_code == HTTPStatus.FOUND


def test_personal_feed_add_normalizes_url_and_rejects_household_duplicate(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url',
                        lambda url, **_kwargs: url.lower().rstrip('/'))
    user_ids = ids(app, 'admin', 'reader')
    with sqlite3.connect(app.config['DATABASE']) as db:
        add_feed(db, 'https://news.example/rss', 'News', 'household', None, user_ids['admin'])
    client = app.test_client()
    sign_in(client, 'reader', 'reader password')

    duplicate = post(client, '/rss/feeds', scope='personal', feed_url='https://NEWS.example/rss/')
    assert duplicate.status_code == HTTPStatus.FOUND
    assert b'already available in the household' in client.get('/rss').data
    assert sqlite3.connect(app.config['DATABASE']).execute(
        'SELECT count(*) FROM rss_feeds'
    ).fetchone()[0] == 1

    added = post(client, '/rss/feeds', scope='personal', feed_url='https://garden.example/feed')
    assert added.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT normalized_url,scope,owner_user_id FROM rss_feeds '
                          'WHERE normalized_url LIKE ?', ('%garden.example%',)).fetchone() == (
                              'https://garden.example/feed', 'personal', user_ids['reader'])


def test_only_admin_can_add_or_delete_household_feeds(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url', lambda url, **_kwargs: url)
    admin = app.test_client()
    sign_in(admin, 'admin', 'admin password')
    reader = app.test_client()
    sign_in(reader, 'reader', 'reader password')

    assert post(reader, '/rss/feeds', scope='household',
                feed_url='https://house.example/feed').status_code == HTTPStatus.FORBIDDEN
    assert post(admin, '/rss/feeds', scope='household',
                feed_url='https://house.example/feed').status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        feed_id = db.execute('SELECT id FROM rss_feeds').fetchone()[0]

    assert post(reader, f'/rss/feeds/{feed_id}/delete').status_code == HTTPStatus.FORBIDDEN
    assert post(admin, f'/rss/feeds/{feed_id}/delete').status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM rss_feeds').fetchone()[0] == 0


def test_household_feed_cap_and_private_feed_ownership(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url', lambda url, **_kwargs: url)
    user_ids = ids(app, 'admin', 'reader')
    with sqlite3.connect(app.config['DATABASE']) as db:
        for n in range(HOUSEHOLD_FEEDS_MAXIMUM):
            add_feed(db, f'https://house{n}.example/rss', 'House', 'household', None,
                     user_ids['admin'])
        personal_id = add_feed(db, 'https://reader.example/rss', 'Reader', 'personal',
                               user_ids['reader'], user_ids['reader'])

    admin = app.test_client()
    sign_in(admin, 'admin', 'admin password')
    capped = post(admin, '/rss/feeds', scope='household',
                  feed_url='https://house4.example/rss')
    assert capped.status_code == HTTPStatus.FOUND
    assert f'up to {HOUSEHOLD_FEEDS_MAXIMUM} house-wide feeds'.encode() in admin.get('/rss').data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute("SELECT count(*) FROM rss_feeds WHERE scope='household'").fetchone()[0] == HOUSEHOLD_FEEDS_MAXIMUM

    reader = app.test_client()
    sign_in(reader, 'reader', 'reader password')
    assert post(admin, f'/rss/feeds/{personal_id}/delete').status_code == HTTPStatus.FORBIDDEN
    assert post(reader, f'/rss/feeds/{personal_id}/delete').status_code == HTTPStatus.FOUND


def test_invalid_feed_url_is_reported_without_saving(app, monkeypatch):
    def invalid(_url, **_kwargs):
        raise RSSFeedError('Feed URLs must point to a public internet host.')

    monkeypatch.setattr(app_module, 'validate_feed_url', invalid)
    client = app.test_client()
    sign_in(client, 'reader', 'reader password')

    response = post(client, '/rss/feeds', scope='personal', feed_url='http://localhost/rss')

    assert response.status_code == HTTPStatus.FOUND
    assert b'public internet host' in client.get('/rss').data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT count(*) FROM rss_feeds').fetchone()[0] == 0


def test_house_account_can_view_but_cannot_manage_feeds(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url', lambda url, **_kwargs: url)
    user_ids = ids(app, 'admin')
    with sqlite3.connect(app.config['DATABASE']) as db:
        add_feed(db, 'https://bulletin.example/rss', 'Bulletin', 'household', None,
                 user_ids['admin'])
    house = app.test_client()
    sign_in(house, 'house', 'house password')

    page = house.get('/rss')

    assert page.status_code == HTTPStatus.OK
    assert b'Bulletin' in page.data
    assert post(house, '/rss/feeds', scope='personal',
                feed_url='https://other.example/rss').status_code == HTTPStatus.FORBIDDEN


def test_only_admin_can_explicitly_allow_lan_feed_destinations(app, monkeypatch):
    approvals = []

    def validator(url, *, allow_lan=False):
        approvals.append(allow_lan)
        return url

    monkeypatch.setattr(app_module, 'validate_feed_url', validator)
    admin = app.test_client()
    sign_in(admin, 'admin', 'admin password')
    response = post(admin, '/rss/feeds', scope='household',
                    feed_url='http://printer.home/rss', allow_lan='1')

    assert response.status_code == HTTPStatus.FOUND
    assert approvals == [True]
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT allow_lan FROM rss_feeds').fetchone() == (1,)

    reader = app.test_client()
    sign_in(reader, 'reader', 'reader password')
    assert post(reader, '/rss/feeds', scope='personal', feed_url='http://printer.home/rss',
                allow_lan='1').status_code == HTTPStatus.BAD_REQUEST


def test_reordering_is_limited_to_the_feed_owner_or_household_admin(app):
    user_ids = ids(app, 'admin', 'reader')
    with sqlite3.connect(app.config['DATABASE']) as db:
        personal_first = add_feed(db, 'https://one.example/rss', 'One', 'personal',
                                  user_ids['reader'], user_ids['reader'])
        add_feed(db, 'https://two.example/rss', 'Two', 'personal',
                 user_ids['reader'], user_ids['reader'])
        shared_first = add_feed(db, 'https://house-one.example/rss', 'House one',
                                'household', None, user_ids['admin'])
        add_feed(db, 'https://house-two.example/rss', 'House two', 'household', None,
                 user_ids['admin'])

    reader = app.test_client()
    sign_in(reader, 'reader', 'reader password')
    admin = app.test_client()
    sign_in(admin, 'admin', 'admin password')

    assert post(reader, f'/rss/feeds/{shared_first}/move', direction='down').status_code == HTTPStatus.FORBIDDEN
    assert post(reader, f'/rss/feeds/{personal_first}/move', direction='down').status_code == HTTPStatus.FOUND
    assert post(admin, f'/rss/feeds/{shared_first}/move', direction='down').status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute("SELECT normalized_url FROM rss_feeds WHERE scope='personal' "
                          'ORDER BY sort_order,id').fetchall() == [
                              ('https://two.example/rss',), ('https://one.example/rss',)]
        assert db.execute("SELECT normalized_url FROM rss_feeds WHERE scope='household' "
                          'ORDER BY sort_order,id').fetchall() == [
                              ('https://house-two.example/rss',), ('https://house-one.example/rss',)]
