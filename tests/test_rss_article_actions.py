from http import HTTPStatus
import sqlite3

import pytest

from homehub.app import PH, create_app
from homehub.rss_store import store_feed_articles, set_article_state


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-article-actions-test',
                      'DATABASE': str(tmp_path / 'rss-actions.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('reader','Reader',?,'user')", (PH.hash('reader password'),))
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,created_by) "
                   "VALUES('https://news.example/rss','News','household',1)")
        db.row_factory = sqlite3.Row
        store_feed_articles(db, 1, [
            {'title': f'Story {index}', 'url': f'https://news.example/{index}',
             'summary': '', 'published': ''} for index in range(1, 4)
        ], fetched_at='2026-10-24T12:00:00+00:00')
    return app


def signed_in(app):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    client.post('/login', data={'username': 'reader', 'password': 'reader password',
                                '_csrf': token})
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def test_done_reading_requires_confirmation_then_honors_dont_ask_preference(app):
    client = signed_in(app)
    with sqlite3.connect(app.config['DATABASE']) as db:
        article_ids = [row[0] for row in db.execute('SELECT id FROM rss_articles ORDER BY id')]

    prompt = client.post(f'/rss/articles/{article_ids[0]}/done-reading',
                         data={'_csrf': csrf(client)})
    assert prompt.status_code == HTTPStatus.CONFLICT

    first = client.post(f'/rss/articles/{article_ids[0]}/done-reading', data={
        '_csrf': csrf(client), 'confirmed': '1', 'choice': 'delete',
        'dont_ask_again': '1',
    })
    second = client.post(f'/rss/articles/{article_ids[1]}/done-reading',
                         data={'_csrf': csrf(client)})

    assert first.status_code == second.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT is_read,deleted_at IS NOT NULL FROM rss_article_states '
                          'WHERE article_id=? AND user_id=1', (article_ids[0],)).fetchone() == (1, 1)
        assert db.execute('SELECT is_read,deleted_at IS NOT NULL FROM rss_article_states '
                          'WHERE article_id=? AND user_id=1', (article_ids[1],)).fetchone() == (1, 1)
        assert db.execute('SELECT ask_before_done_delete FROM rss_user_preferences '
                          'WHERE user_id=1').fetchone() == (0,)


def test_user_can_turn_done_reading_prompt_back_on_from_feed_management(app):
    client = signed_in(app)
    client.post('/rss/preferences/done-reading', data={
        '_csrf': csrf(client), 'ask_before_done_delete': '1',
    })

    page = client.get('/rss')

    assert page.status_code == HTTPStatus.OK
    assert b'Ask before deleting after Done reading' in page.data
    assert b'name="ask_before_done_delete" value="1" checked' in page.data


def test_deleting_a_favourite_requires_confirmation(app):
    client = signed_in(app)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        article_id = db.execute('SELECT id FROM rss_articles ORDER BY id LIMIT 1').fetchone()['id']
        set_article_state(db, 1, article_id, is_favorite=True)

    prompt = client.post(f'/rss/articles/{article_id}/delete',
                         data={'_csrf': csrf(client)})
    deleted = client.post(f'/rss/articles/{article_id}/delete', data={
        '_csrf': csrf(client), 'confirmed': '1',
    })

    assert prompt.status_code == HTTPStatus.CONFLICT
    assert deleted.status_code == HTTPStatus.FOUND
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT deleted_at IS NOT NULL FROM rss_article_states '
                          'WHERE article_id=? AND user_id=1', (article_id,)).fetchone() == (1,)
