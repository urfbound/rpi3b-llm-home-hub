from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH, create_app
from homehub.rss_store import set_article_state, store_feed_articles


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-article-page-test',
                      'DATABASE': str(tmp_path / 'rss-page.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin password'), 'admin', 'person'),
             ('reader', 'Reader', PH.hash('reader password'), 'user', 'person'),
             ('guest', 'Guest', PH.hash('guest password'), 'user', 'house')],
        )
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,created_by) "
                   "VALUES('https://house.example/rss','House feed','household',1)")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
                   "VALUES('https://private.example/rss','Private feed','personal',2,2)")
        db.row_factory = sqlite3.Row
        store_feed_articles(db, 1, [{
            'title': 'House story', 'url': 'https://news.example/house',
            'summary': 'Shared summary', 'published': '2026-10-24',
        }], fetched_at='2026-10-24T12:00:00+00:00')
        store_feed_articles(db, 2, [{
            'title': 'Private story', 'url': 'https://news.example/private',
            'summary': 'Private summary', 'published': '2026-10-24',
        }], fetched_at='2026-10-24T12:01:00+00:00')
    return app


def signed_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    client.post('/login', data={'username': username, 'password': password,
                                '_csrf': token})
    return client


def test_rss_page_groups_articles_by_feed_and_opening_marks_read(app):
    client = signed_in(app, 'reader', 'reader password')

    page = client.get('/rss')
    with sqlite3.connect(app.config['DATABASE']) as db:
        article_id = db.execute(
            "SELECT id FROM rss_articles WHERE title='House story'").fetchone()[0]

    opened = client.get(f'/rss/articles/{article_id}/open')

    assert page.status_code == HTTPStatus.OK
    assert b'House feed' in page.data
    assert b'House story' in page.data
    assert b'Shared summary' in page.data
    assert opened.status_code == HTTPStatus.FOUND
    assert opened.headers['Location'] == 'https://news.example/house'
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT is_read FROM rss_article_states '
                          'WHERE article_id=? AND user_id=2', (article_id,)).fetchone() == (1,)


def test_house_wide_account_never_sees_personal_articles(app):
    client = signed_in(app, 'guest', 'guest password')

    page = client.get('/rss')

    assert b'House story' in page.data
    assert b'Private story' not in page.data
    assert b'Private feed' not in page.data


def test_favourites_appear_in_a_dedicated_card_at_the_bottom(app):
    client = signed_in(app, 'reader', 'reader password')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        article_id = db.execute("SELECT id FROM rss_articles WHERE title='House story'").fetchone()['id']
        set_article_state(db, 2, article_id, is_favorite=True)

    page = client.get('/rss').data
    favourites = page.index(b'id="rss-favourites"')
    manager = page.index(b'id="rss-manage"')

    assert favourites > manager
    assert b'Favourites' in page[favourites:]
    assert b'House story' in page[favourites:]

