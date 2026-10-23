from datetime import datetime, timedelta, timezone
from http import HTTPStatus
import sqlite3

import homehub.app as app_module
from homehub.app import PH, create_app


def make_app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-storage-test',
                      'DATABASE': str(tmp_path / 'rss-storage.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES('rss_outbound_enabled','0')")
        user_id = db.execute(
            'INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
            ('reader', 'Reader', PH.hash('reader password'), 'user'),
        ).lastrowid
        feed_id = db.execute(
            'INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
            'VALUES(?,?,?,?,?)',
            ('https://news.example/feed', 'News', 'personal', user_id, user_id),
        ).lastrowid
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    client.post('/login', data={'username': 'reader', 'password': 'reader password',
                                '_csrf': csrf})
    return app, client, feed_id, user_id


def add_article(db, feed_id, title, fetched_at, *, user_id=None, read=False, favourite=False):
    article_id = db.execute(
        'INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
        'VALUES(?,?,?,?,?,?)',
        (feed_id, title, f'https://news.example/{title}', 'summary', '', fetched_at),
    ).lastrowid
    if user_id and (read or favourite):
        db.execute(
            'INSERT INTO rss_article_states(article_id,user_id,read_at,is_favourite) VALUES(?,?,?,?)',
            (article_id, user_id, fetched_at if read else None, int(favourite)),
        )
    return article_id


def test_storage_cap_evicts_oldest_unread_before_read_articles(tmp_path, monkeypatch):
    app, client, feed_id, user_id = make_app(tmp_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(app.config['DATABASE']) as db:
        add_article(db, feed_id, 'old-read', (now - timedelta(days=3)).isoformat(),
                    user_id=user_id, read=True)
        add_article(db, feed_id, 'old-unread', (now - timedelta(days=2)).isoformat())
        add_article(db, feed_id, 'new-unread', (now - timedelta(days=1)).isoformat())
    monkeypatch.setattr(app_module, 'MAX_RSS_STORAGE_BYTES', 500)

    response = client.get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert b'old-unread' not in response.data
    assert b'old-read' in response.data
    assert b'new-unread' in response.data


def test_storage_cap_falls_back_to_oldest_read_when_no_unread_remain(tmp_path, monkeypatch):
    app, client, feed_id, user_id = make_app(tmp_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(app.config['DATABASE']) as db:
        add_article(db, feed_id, 'old-read', (now - timedelta(days=2)).isoformat(),
                    user_id=user_id, read=True)
        add_article(db, feed_id, 'new-read', (now - timedelta(days=1)).isoformat(),
                    user_id=user_id, read=True)
    monkeypatch.setattr(app_module, 'MAX_RSS_STORAGE_BYTES', 250)

    response = client.get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert b'old-read' not in response.data
    assert b'new-read' in response.data


def test_favourites_are_exempt_from_storage_eviction_and_usage_is_shown(tmp_path, monkeypatch):
    app, client, feed_id, user_id = make_app(tmp_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(app.config['DATABASE']) as db:
        add_article(db, feed_id, 'saved-favourite', (now - timedelta(days=1)).isoformat(),
                    user_id=user_id, favourite=True)
        add_article(db, feed_id, 'ordinary-story', now.isoformat())
    monkeypatch.setattr(app_module, 'MAX_RSS_STORAGE_BYTES', 1)

    response = client.get('/rss')

    assert response.status_code == HTTPStatus.OK
    assert b'saved-favourite' in response.data
    assert b'ordinary-story' not in response.data
    assert b'Saved for your account' in response.data
