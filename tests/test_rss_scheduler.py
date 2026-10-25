from datetime import datetime, timezone
import sqlite3

from homehub.app import create_app
from homehub.rss import RSSFeedError
from homehub.rss_scheduler import refresh_due_feeds
from homehub.rss_store import store_feed_articles


FEED_BODY = b'''<rss><channel><title>Example</title><item>
<title>Story</title><link>https://news.example/story</link>
<description>Summary</description><pubDate>2026-10-24</pubDate>
</item></channel></rss>'''


def make_app(path, *, outbound='1', interval='30'):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-scheduler-test',
                      'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('reader','hash','user')")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
                   "VALUES('https://news.example/rss','News','personal',1,1)")
        db.executemany('INSERT INTO app_settings(key,value) VALUES(?,?)', [
            ('rss_outbound_enabled', outbound), ('rss_refresh_minutes', interval),
        ])
    return app


def test_refresh_obeys_outbound_toggle_before_any_network_request(tmp_path):
    app = make_app(tmp_path / 'rss.sqlite3', outbound='0')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.row_factory = sqlite3.Row
        store_feed_articles(db, 1, [{
            'title': 'Expired story', 'url': 'https://news.example/expired',
            'summary': '', 'published': '',
        }], fetched_at='2026-07-20T12:00:00+00:00')
        result = refresh_due_feeds(
            db, now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
            fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('outbound fetch must be disabled')),
        )
        remaining = db.execute('SELECT COUNT(*) FROM rss_articles').fetchone()[0]

    assert result == {'disabled': True, 'checked': 0, 'refreshed': 0, 'failed': 0}
    assert remaining == 0


def test_refresh_fetches_due_feed_once_and_respects_configured_interval(tmp_path):
    app = make_app(tmp_path / 'rss.sqlite3')
    calls = []

    def fetcher(url, *, allow_lan=False):
        calls.append((url, allow_lan))
        return FEED_BODY, url

    with sqlite3.connect(app.config['DATABASE']) as db:
        first = refresh_due_feeds(
            db, now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc), fetcher=fetcher)
        early = refresh_due_feeds(
            db, now=datetime(2026, 10, 24, 12, 29, tzinfo=timezone.utc), fetcher=fetcher)
        due = refresh_due_feeds(
            db, now=datetime(2026, 10, 24, 12, 30, tzinfo=timezone.utc), fetcher=fetcher)
        article_count = db.execute('SELECT COUNT(*) FROM rss_articles').fetchone()[0]
        attempts = db.execute('SELECT COUNT(*) FROM rss_feed_refresh').fetchone()[0]

    assert first == {'disabled': False, 'checked': 1, 'refreshed': 1, 'failed': 0}
    assert early == {'disabled': False, 'checked': 0, 'refreshed': 0, 'failed': 0}
    assert due == {'disabled': False, 'checked': 1, 'refreshed': 1, 'failed': 0}
    assert calls == [('https://news.example/rss', False)] * 2
    assert article_count == attempts == 1

def test_refresh_updates_feed_title_from_feed_metadata(tmp_path):
    app = make_app(tmp_path / 'rss-title.sqlite3')
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("UPDATE rss_feeds SET title='Old title' WHERE id=1")
        refresh_due_feeds(
            db, now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
            fetcher=lambda url, **_kwargs: (FEED_BODY, url),
        )
        title = db.execute('SELECT title FROM rss_feeds WHERE id=1').fetchone()[0]

    assert title == 'Example'


def test_refresh_records_feed_failure_and_retries_after_interval(tmp_path):
    app = make_app(tmp_path / 'rss.sqlite3')
    with sqlite3.connect(app.config['DATABASE']) as db:
        result = refresh_due_feeds(
            db, now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
            fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RSSFeedError('feed is unavailable')),
        )
        status = db.execute('SELECT attempted_at,succeeded_at,last_error '
                            'FROM rss_feed_refresh').fetchone()

    assert result == {'disabled': False, 'checked': 1, 'refreshed': 0, 'failed': 1}
    assert status == ('2026-10-24T12:00:00+00:00', None, 'feed is unavailable')
