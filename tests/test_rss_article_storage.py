import sqlite3

import pytest

from homehub.app import create_app
from homehub.rss_store import (RSSArticleError, articles_for_user,
                               set_article_state, store_feed_articles)


def make_app(path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-articles-test',
                      'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('reader-one','hash','user')")
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('reader-two','hash','user')")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,created_by) "
                   "VALUES('https://house.example/rss','House feed','household',1)")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
                   "VALUES('https://personal.example/rss','Personal feed','personal',1,1)")
    return app


def test_refresh_deduplicates_articles_and_keeps_read_state_per_user(tmp_path):
    app = make_app(tmp_path / 'rss.sqlite3')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        assert store_feed_articles(db, 1, [{
            'title': 'First title', 'url': 'https://news.example/story/1',
            'summary': 'First summary', 'published': '2026-10-24',
        }], fetched_at='2026-10-24T12:00:00Z') == 1
        article_id = db.execute('SELECT id FROM rss_articles').fetchone()['id']
        set_article_state(db, 1, article_id, is_read=True,
                          read_at='2026-10-24T12:01:00Z')
        set_article_state(db, 1, article_id, is_read=False)
        assert store_feed_articles(db, 1, [{
            'title': 'First title', 'url': 'https://news.example/story/1',
            'summary': 'First summary', 'published': '2026-10-24',
        }], fetched_at='2026-10-24T12:30:00Z') == 0

        first, second = articles_for_user(db, 1), articles_for_user(db, 2)

    assert len(first) == len(second) == 1
    assert first[0]['id'] == second[0]['id'] == article_id
    assert first[0]['is_read'] is True
    assert second[0]['is_read'] is False


def test_personal_articles_are_visible_only_to_the_feed_owner(tmp_path):
    app = make_app(tmp_path / 'rss.sqlite3')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        store_feed_articles(db, 2, [{
            'title': 'Private feed story', 'url': 'https://personal.example/story',
            'summary': '', 'published': '',
        }], fetched_at='2026-10-24T12:00:00Z')

        owner_articles = articles_for_user(db, 1)
        other_articles = articles_for_user(db, 2)
        with pytest.raises(RSSArticleError, match='unavailable'):
            set_article_state(db, 2, owner_articles[0]['id'], is_read=True)

    assert [article['title'] for article in owner_articles] == ['Private feed story']
    assert other_articles == []
