from datetime import datetime, timedelta, timezone
from http import HTTPStatus
import sqlite3

import homehub.app as app_module
from homehub.app import PH, create_app


FEED_DOCUMENT = b'''<rss><channel><title>Garden News</title>
<item><title>Spring planting</title><link>https://news.example/spring</link>
<description>How to plant peas</description><pubDate>2026-10-22</pubDate></item>
</channel></rss>'''


def create_test_app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-refresh-test',
                      'DATABASE': str(tmp_path / 'rss-refresh.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin password'), 'admin'),
             ('reader', 'Reader', PH.hash('reader password'), 'user')],
        )
        reader_id = db.execute("SELECT id FROM users WHERE username='reader'").fetchone()[0]
        db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
                   'VALUES(?,?,?,?,?)',
                   ('https://news.example/feed', 'news.example', 'personal', reader_id, reader_id))
    return app


def sign_in(app, username='reader', password='reader password'):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post('/login', data={'username': username, 'password': password,
                                       '_csrf': token}).status_code == HTTPStatus.FOUND
    return client


def set_settings(app, *, outbound='1', interval='30'):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany('INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)', [
            ('rss_outbound_enabled', outbound), ('rss_refresh_minutes', interval),
        ])


def add_household_feed(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        admin_id = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
                   'VALUES(?,?,?,?,?)',
                   ('https://family.example/feed', 'family.example', 'household', None, admin_id))


def test_rss_page_fetches_due_personal_and_household_feeds_and_stores_articles(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    add_household_feed(app)
    set_settings(app)
    requests = []

    def fetch(url, *, allow_lan=False):
        requests.append((url, allow_lan))
        return FEED_DOCUMENT, url

    monkeypatch.setattr(app_module, 'fetch_feed', fetch)

    response = sign_in(app).get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert requests == [
        ('https://news.example/feed', False),
        ('https://family.example/feed', False),
    ]
    assert b'Spring planting' in response.data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM rss_articles ORDER BY id').fetchall() == [
            ('Spring planting',), ('Spring planting',),
        ]
        assert db.execute("SELECT count(*) FROM rss_feeds WHERE last_fetched_at IS NOT NULL").fetchone()[0] == 2


def test_disabled_outbound_access_serves_cached_articles_without_fetching(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    set_settings(app, outbound='0')
    with sqlite3.connect(app.config['DATABASE']) as db:
        feed_id = db.execute('SELECT id FROM rss_feeds').fetchone()[0]
        db.execute('INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
                   'VALUES(?,?,?,?,?,?)',
                   (feed_id, 'Cached story', 'https://news.example/cached', 'Saved copy', '',
                    '2026-10-22T00:00:00+00:00'))
    monkeypatch.setattr(app_module, 'fetch_feed', lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('outbound RSS was disabled')))

    response = sign_in(app).get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert b'Cached story' in response.data
    assert b'Saved copy' in response.data


def test_recently_refreshed_feed_is_not_requested_again(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    set_settings(app, interval='30')
    fetched_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('UPDATE rss_feeds SET last_attempt_at=?,last_fetched_at=?',
                   (fetched_at, fetched_at))
    requests = []
    monkeypatch.setattr(app_module, 'fetch_feed', lambda url, **kwargs: requests.append(url))

    response = sign_in(app).get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert requests == []


def test_failed_refresh_keeps_cached_articles_and_records_error(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    set_settings(app)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with sqlite3.connect(app.config['DATABASE']) as db:
        feed_id = db.execute('SELECT id FROM rss_feeds').fetchone()[0]
        db.execute('UPDATE rss_feeds SET last_attempt_at=?,last_fetched_at=?', (old, old))
        db.execute('INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
                   'VALUES(?,?,?,?,?,?)',
                   (feed_id, 'Old story', 'https://news.example/old', 'Still available', '', old))

    def fail(*_args, **_kwargs):
        raise app_module.RSSFeedError('Feed timed out.')

    monkeypatch.setattr(app_module, 'fetch_feed', fail)

    response = sign_in(app).get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert b'Old story' in response.data
    assert b'Feed timed out.' in response.data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM rss_articles').fetchone() == ('Old story',)


def test_expired_articles_are_pruned_even_while_outbound_access_is_disabled(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    add_household_feed(app)
    set_settings(app, outbound='0')
    now = datetime.now(timezone.utc)
    old_personal = (now - timedelta(days=91)).isoformat()
    old_household = (now - timedelta(days=4)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    with sqlite3.connect(app.config['DATABASE']) as db:
        personal_id = db.execute("SELECT id FROM rss_feeds WHERE scope='personal'").fetchone()[0]
        household_id = db.execute("SELECT id FROM rss_feeds WHERE scope='household'").fetchone()[0]
        db.executemany(
            'INSERT INTO rss_articles(feed_id,title,url,fetched_at) VALUES(?,?,?,?)',
            [(personal_id, 'Expired personal', 'https://news.example/expired', old_personal),
             (household_id, 'Expired household', 'https://family.example/expired', old_household),
             (personal_id, 'Recent', 'https://news.example/recent', recent),
             (household_id, 'Recent household', 'https://family.example/recent', recent)],
        )
    monkeypatch.setattr(app_module, 'fetch_feed', lambda *_args, **_kwargs: pytest.fail(
        'outbound RSS was disabled'))

    response = sign_in(app).get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert b'Expired personal' not in response.data
    assert b'Expired household' not in response.data
    assert b'Recent household' in response.data
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT title FROM rss_articles ORDER BY title').fetchall() == [
            ('Recent',), ('Recent household',),
        ]

