from http import HTTPStatus
import sqlite3

from homehub.app import PH, create_app
import pytest


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-favorites-test',
                      'DATABASE': str(tmp_path / 'rss-favorites.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) "
                   "VALUES('rss_outbound_enabled','0')")
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
            [('reader', 'Reader', PH.hash('reader password'), 'user'),
             ('other', 'Other', PH.hash('other password'), 'user')],
        )
        reader_id = db.execute("SELECT id FROM users WHERE username='reader'").fetchone()[0]
        other_id = db.execute("SELECT id FROM users WHERE username='other'").fetchone()[0]
        feed_id = db.execute(
            'INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
            'VALUES(?,?,?,?,?)',
            ('https://news.example/feed', 'News feed', 'personal', reader_id, reader_id),
        ).lastrowid
        db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,created_by) '
                   'VALUES(?,?,?,?)', ('https://house.example/feed', 'House feed', 'household', other_id))
        db.executemany(
            'INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
            'VALUES(?,?,?,?,?,?)',
            [(feed_id, f'Story {index}', f'https://news.example/{index}', '', '',
              f'2026-10-{(index % 23) + 1:02d}T00:00:00+00:00') for index in range(1, 35)],
        )
        db.execute(
            'INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
            'SELECT id,?,?,?,?,? FROM rss_feeds WHERE scope=?',
            ('House story', 'https://house.example/story', '', '', '2026-10-23T00:00:00+00:00', 'household'),
        )
    return app


def sign_in(app, username='reader', password='reader password'):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post('/login', data={'username': username, 'password': password,
                                       '_csrf': token}).status_code == HTTPStatus.FOUND
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def article_id(app, title):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return db.execute('SELECT id FROM rss_articles WHERE title=?', (title,)).fetchone()[0]


def favourites(app, username='reader'):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return db.execute(
            'SELECT a.title FROM rss_article_states s JOIN rss_articles a ON a.id=s.article_id '
            'JOIN users u ON u.id=s.user_id WHERE u.username=? AND s.is_favourite=1 '
            'ORDER BY a.title', (username,),
        ).fetchall()


def test_user_can_favourite_and_unfavourite_article_without_affecting_another_user(app):
    reader = sign_in(app)
    other = sign_in(app, 'other', 'other password')
    story = article_id(app, 'House story')

    added = reader.post(f'/rss/articles/{story}/favourite', data={'_csrf': csrf(reader)})
    other_page = other.get('/rss')

    assert added.status_code == HTTPStatus.FOUND
    assert favourites(app) == [('House story',)]
    assert favourites(app, 'other') == []
    assert b'aria-pressed="false"' in other_page.data
    removed = reader.post(f'/rss/articles/{story}/favourite', data={'_csrf': csrf(reader)})
    assert removed.status_code == HTTPStatus.FOUND
    assert favourites(app) == []


def test_favourites_card_lists_saved_articles_after_feed_groups(app):
    reader = sign_in(app)
    story = article_id(app, 'Story 1')
    reader.post(f'/rss/articles/{story}/favourite', data={'_csrf': csrf(reader)})

    page = reader.get('/rss')

    assert b'id="favourites-card"' in page.data
    assert b'Story 1' in page.data
    assert page.data.index(b'id="favourites-card"') > page.data.index(b'Articles from News feed')


def test_favourites_are_capped_at_32_per_user(app):
    reader = sign_in(app)
    with sqlite3.connect(app.config['DATABASE']) as db:
        article_ids = [row[0] for row in db.execute(
            'SELECT id FROM rss_articles ORDER BY id LIMIT 33'
        )]
    for story_id in article_ids[:32]:
        assert reader.post(f'/rss/articles/{story_id}/favourite',
                           data={'_csrf': csrf(reader)}).status_code == HTTPStatus.FOUND

    response = reader.post(f'/rss/articles/{article_ids[32]}/favourite',
                           data={'_csrf': csrf(reader)})

    assert response.status_code == HTTPStatus.FOUND
    assert len(favourites(app)) == 32
    assert b'up to 32 favourite articles' in reader.get('/rss').data


def test_deleting_a_favourite_requires_confirmation(app):
    reader = sign_in(app)
    story = article_id(app, 'Story 1')
    reader.post(f'/rss/articles/{story}/favourite', data={'_csrf': csrf(reader)})

    blocked = reader.post(f'/rss/articles/{story}/delete', data={'_csrf': csrf(reader)})

    assert blocked.status_code == HTTPStatus.CONFLICT
    assert favourites(app) == [('Story 1',)]
    assert b'id="delete-favourite-dialog"' in reader.get('/rss').data
    confirmed = reader.post(f'/rss/articles/{story}/delete', data={
        '_csrf': csrf(reader), 'confirm_favourite_delete': '1',
    })
    assert confirmed.status_code == HTTPStatus.FOUND
    assert favourites(app) == []
    assert f'/rss/articles/{story}/open'.encode() not in reader.get('/rss').data
