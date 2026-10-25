from datetime import datetime, timezone
import sqlite3

from homehub import constants as limits
from homehub.app import create_app
from homehub.rss_store import prune_articles, set_article_state, store_feed_articles


def app_with_feeds(path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-retention-test',
                      'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('reader','hash','user')")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,created_by) "
                   "VALUES('https://house.example/rss','House','household',1)")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
                   "VALUES('https://personal.example/rss','Personal','personal',1,1)")
    return app


def insert_article(db, feed_id, number, first_seen, title='story'):
    return store_feed_articles(db, feed_id, [{
        'title': f'{title}-{number}', 'url': f'https://article.example/{number}',
        'summary': '', 'published': '',
    }], fetched_at=first_seen)


def test_retention_expires_old_household_and_personal_articles_but_protects_favourites(tmp_path):
    app = app_with_feeds(tmp_path / 'rss-retention.sqlite3')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        insert_article(db, 1, 1, '2026-10-20T12:00:00+00:00', title='house-old')
        insert_article(db, 1, 2, '2026-10-20T12:00:00+00:00', title='house-favourite')
        insert_article(db, 2, 3, '2026-07-24T11:00:00+00:00', title='personal-old')
        insert_article(db, 2, 4, '2026-07-24T11:00:00+00:00', title='personal-favourite')
        favourite_ids = [row['id'] for row in db.execute(
            "SELECT id FROM rss_articles WHERE title LIKE '%favourite%'")]
        for article_id in favourite_ids:
            set_article_state(db, 1, article_id, is_favorite=True)

        removed = prune_articles(
            db, now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
        remaining = {row['title'] for row in db.execute('SELECT title FROM rss_articles')}

    assert removed == {'household_age': 1, 'personal_age': 1, 'personal_quota': 0}
    assert remaining == {'house-favourite-2', 'personal-favourite-4'}


def test_personal_quota_evicts_oldest_unread_before_older_read_and_keeps_favourites(
        tmp_path, monkeypatch):
    app = app_with_feeds(tmp_path / 'rss-quota.sqlite3')
    monkeypatch.setattr(limits, 'RSS_PERSONAL_ARTICLE_STORAGE_BYTES_MAXIMUM', 64)
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        for number, timestamp in enumerate((
                '2026-10-23T10:00:00+00:00', '2026-10-23T11:00:00+00:00',
                '2026-10-23T12:00:00+00:00'), start=1):
            insert_article(db, 2, number, timestamp)
        rows = db.execute('SELECT id,content_bytes FROM rss_articles ORDER BY id').fetchall()
        byte_count = rows[0]['content_bytes']
        monkeypatch.setattr(limits, 'RSS_PERSONAL_ARTICLE_STORAGE_BYTES_MAXIMUM', byte_count * 2)
        set_article_state(db, 1, rows[0]['id'], is_read=True)
        set_article_state(db, 1, rows[2]['id'], is_favorite=True)

        removed = prune_articles(
            db, now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
        remaining = [row[0] for row in db.execute('SELECT id FROM rss_articles ORDER BY id')]

    assert removed == {'household_age': 0, 'personal_age': 0, 'personal_quota': 1}
    assert remaining == [rows[0]['id'], rows[2]['id']]
