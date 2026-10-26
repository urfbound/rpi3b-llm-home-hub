"""Short-lived, interval-aware RSS refresh work for a systemd timer."""

from datetime import datetime, timedelta, timezone

from . import constants as limits
from .rss import (DEFAULT_RSS_REFRESH_MINUTES, RSSFeedError, parse_feed)
from .rss_fetch import fetch_feed
from .rss_store import prune_articles, store_feed_articles


def _setting(db, key, default):
    row = db.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def _utc(value):
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value):
    return value.isoformat(timespec='seconds')


def refresh_feed_now(db, feed_id, *, now=None, fetcher=None):
    """Refresh one newly added feed immediately and record its first result."""
    instant = _utc(now)
    if _setting(db, 'rss_outbound_enabled', '1') != '1':
        return {'status': 'disabled', 'articles_added': 0}
    feed = db.execute(
        'SELECT id,normalized_url,allow_lan FROM rss_feeds WHERE id=?', (feed_id,)
    ).fetchone()
    if feed is None:
        return {'status': 'missing', 'articles_added': 0}
    if fetcher is None:
        fetcher = fetch_feed
    stamp = _stamp(instant)
    try:
        document, final_url = fetcher(feed['normalized_url'], allow_lan=bool(feed['allow_lan']))
        parsed = parse_feed(document, final_url)
        if parsed['title']:
            db.execute('UPDATE rss_feeds SET title=? WHERE id=?',
                       (parsed['title'], feed['id']))
        added = store_feed_articles(db, feed['id'], parsed['articles'], fetched_at=stamp)
        db.execute(
            'INSERT INTO rss_feed_refresh(feed_id,attempted_at,succeeded_at,last_error,articles_added) '
            'VALUES(?,?,?,NULL,?) ON CONFLICT(feed_id) DO UPDATE SET '
            'attempted_at=excluded.attempted_at,succeeded_at=excluded.succeeded_at,'
            'last_error=NULL,articles_added=excluded.articles_added',
            (feed['id'], stamp, stamp, added),
        )
        return {'status': 'refreshed', 'articles_added': added}
    except (RSSFeedError, OSError, TimeoutError, ValueError) as exc:
        message = str(exc)[:limits.ERROR_TEXT_MAXIMUM]
        db.execute(
            'INSERT INTO rss_feed_refresh(feed_id,attempted_at,succeeded_at,last_error,articles_added) '
            'VALUES(?,?,NULL,?,0) ON CONFLICT(feed_id) DO UPDATE SET '
            'attempted_at=excluded.attempted_at,last_error=excluded.last_error,'
            'articles_added=0',
            (feed['id'], stamp, message),
        )
        return {'status': 'failed', 'articles_added': 0, 'error': message}


def refresh_due_feeds(db, *, now=None, fetcher=fetch_feed):
    """Refresh due feeds sequentially, respecting the household network toggle.

    The caller owns the SQLite transaction. Network access is done only for
    feeds whose last attempt is older than the configured refresh interval.
    """
    instant = _utc(now)
    prune_articles(db, now=instant)
    if _setting(db, 'rss_outbound_enabled', '1') != '1':
        return {'disabled': True, 'checked': 0, 'refreshed': 0, 'failed': 0}

    try:
        interval = int(_setting(
            db, 'rss_refresh_minutes', str(DEFAULT_RSS_REFRESH_MINUTES)))
    except (TypeError, ValueError):
        interval = DEFAULT_RSS_REFRESH_MINUTES
    if not limits.RSS_REFRESH_MINIMUM_MINUTES <= interval <= limits.RSS_REFRESH_MAXIMUM_MINUTES:
        interval = DEFAULT_RSS_REFRESH_MINUTES

    cutoff = instant - timedelta(minutes=interval)
    feeds = db.execute(
        'SELECT f.id,f.normalized_url,f.allow_lan,s.attempted_at '
        'FROM rss_feeds f LEFT JOIN rss_feed_refresh s ON s.feed_id=f.id '
        'ORDER BY f.id'
    ).fetchall()
    result = {'disabled': False, 'checked': 0, 'refreshed': 0, 'failed': 0}
    for feed in feeds:
        attempted_at = datetime.fromisoformat(feed[3]) if feed[3] else None
        if attempted_at is not None and _utc(attempted_at) > cutoff:
            continue
        result['checked'] += 1
        stamp = _stamp(instant)
        try:
            document, final_url = fetcher(feed[1], allow_lan=bool(feed[2]))
            parsed = parse_feed(document, final_url)
            if parsed['title']:
                db.execute('UPDATE rss_feeds SET title=? WHERE id=?',
                           (parsed['title'], feed[0]))
            added = store_feed_articles(
                db, feed[0], parsed['articles'], fetched_at=stamp)
            db.execute(
                'INSERT INTO rss_feed_refresh(feed_id,attempted_at,succeeded_at,last_error,articles_added) '
                'VALUES(?,?,?,NULL,?) ON CONFLICT(feed_id) DO UPDATE SET '
                'attempted_at=excluded.attempted_at,succeeded_at=excluded.succeeded_at,'
                'last_error=NULL,articles_added=excluded.articles_added',
                (feed[0], stamp, stamp, added),
            )
            result['refreshed'] += 1
        except (RSSFeedError, OSError, TimeoutError, ValueError) as exc:
            message = str(exc)[:limits.ERROR_TEXT_MAXIMUM]
            db.execute(
                'INSERT INTO rss_feed_refresh(feed_id,attempted_at,succeeded_at,last_error,articles_added) '
                'VALUES(?,?,NULL,?,0) ON CONFLICT(feed_id) DO UPDATE SET '
                'attempted_at=excluded.attempted_at,last_error=excluded.last_error',
                (feed[0], stamp, message),
            )
            result['failed'] += 1
    return result


def main():
    """Entry point for the short-lived systemd service."""
    from flask import g
    from .app import create_app, get_db

    app = create_app()
    with app.app_context():
        # The request hook normally supplies this path, but a systemd
        # scheduler runs in an app context without an HTTP request.
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        result = refresh_due_feeds(db)
        db.commit()
    print('RSS refresh: {checked} checked, {refreshed} refreshed, '
          '{failed} failed.'.format(**result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
