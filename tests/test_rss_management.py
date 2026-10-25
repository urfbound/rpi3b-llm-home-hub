from http import HTTPStatus
from datetime import datetime, timedelta
import sqlite3

import pytest

from homehub.constants import HOUSEHOLD_FEEDS_MAXIMUM
import homehub.app as app_module
from homehub.app import PH, create_app
from homehub.rss import RSSFeedError
from homehub import rss_scheduler as rss_scheduler_module
from homehub.rss_scheduler import refresh_due_feeds


FIRST_FETCH_BODY = b'''<rss><channel><title>Example</title><item>
<title>First article</title><link>https://garden.example/story</link>
<description>Welcome article</description><pubDate>2026-10-25</pubDate>
</item></channel></rss>'''


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


@pytest.fixture(autouse=True)
def mock_first_feed_refresh(monkeypatch):
    monkeypatch.setattr(
        rss_scheduler_module, 'fetch_feed',
        lambda url, **_kwargs: (FIRST_FETCH_BODY, url),
    )


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
    assert b'Feed added and refreshed' in client.get('/rss').data
    assert b'First article' in client.get('/rss').data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT normalized_url,scope,owner_user_id FROM rss_feeds '
                          'WHERE normalized_url LIKE ?', ('%garden.example%',)).fetchone() == (
                              'https://garden.example/feed', 'personal', user_ids['reader'])
        refresh = db.execute(
            'SELECT succeeded_at,articles_added,last_error FROM rss_feed_refresh'
        ).fetchone()
        assert refresh[0] is not None
        assert refresh[1:] == (1, None)


def test_feed_add_keeps_feed_and_notifies_user_when_first_refresh_fails(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url', lambda url, **_kwargs: url)
    monkeypatch.setattr(
        rss_scheduler_module, 'fetch_feed',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RSSFeedError('The feed could not be reached.')),
    )
    client = app.test_client()
    sign_in(client, 'reader', 'reader password')

    response = post(client, '/rss/feeds', scope='personal',
                    feed_url='https://garden.example/feed')

    assert response.status_code == HTTPStatus.FOUND
    page = client.get('/rss').data
    assert b'Feed was saved, but its first refresh failed' in page
    assert b'It will retry after the configured refresh interval' in page
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM rss_feeds').fetchone()[0] == 1
        assert db.execute('SELECT succeeded_at,last_error FROM rss_feed_refresh').fetchone() == (
            None, 'The feed could not be reached.')


def test_scheduler_retries_failed_first_pull_after_configured_interval(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url', lambda url, **_kwargs: url)
    fetches = []

    def fail_once(url, **_kwargs):
        fetches.append(url)
        if len(fetches) == 1:
            raise RSSFeedError('The feed could not be reached.')
        return FIRST_FETCH_BODY, url

    monkeypatch.setattr(rss_scheduler_module, 'fetch_feed', fail_once)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES('rss_refresh_minutes','10')")
    client = app.test_client()
    sign_in(client, 'reader', 'reader password')
    post(client, '/rss/feeds', scope='personal', feed_url='https://garden.example/feed')

    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        attempted_at = datetime.fromisoformat(db.execute(
            'SELECT attempted_at FROM rss_feed_refresh'
        ).fetchone()['attempted_at'])
        early = refresh_due_feeds(
            db, now=attempted_at + timedelta(minutes=9), fetcher=fail_once)
        due = refresh_due_feeds(
            db, now=attempted_at + timedelta(minutes=10), fetcher=fail_once)
        article_count = db.execute('SELECT COUNT(*) FROM rss_articles').fetchone()[0]

    assert early['checked'] == 0
    assert due['refreshed'] == 1
    assert fetches == ['https://garden.example/feed'] * 2
    assert article_count == 1


def test_feed_add_skips_immediate_refresh_when_outbound_access_is_disabled(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url', lambda url, **_kwargs: url)
    fetches = []
    monkeypatch.setattr(rss_scheduler_module, 'fetch_feed',
                        lambda *args, **kwargs: fetches.append((args, kwargs)))
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES('rss_outbound_enabled','0')")
    client = app.test_client()
    sign_in(client, 'reader', 'reader password')

    post(client, '/rss/feeds', scope='personal', feed_url='https://garden.example/feed')
    page = client.get('/rss').data

    assert b'Feed added. First refresh skipped because outbound access is disabled' in page
    assert fetches == []
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM rss_feeds').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM rss_feed_refresh').fetchone()[0] == 0


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


def test_feed_management_actions_are_collected_in_manage_feeds_panel(app, monkeypatch):
    monkeypatch.setattr(app_module, 'validate_feed_url', lambda value, **_kwargs: value)
    admin = app.test_client()
    sign_in(admin, 'admin', 'admin password')
    post(admin, '/rss/feeds', scope='personal', feed_url='https://personal.example/rss')
    post(admin, '/rss/feeds', scope='household', feed_url='https://house.example/rss')

    page = admin.get('/rss').data
    panel = page.index(b'id="rss-manage"')

    assert b'Manage feeds' in page
    assert b'href="#rss-manage"' in page
    assert page.index(b'Move personal.example up') > panel
    assert page.index(b'Move house.example up') > panel
    assert page.index(b'Remove') > panel
    assert b'aria-label="Move personal.example up"' not in page[:panel]
