"""Short-lived, interval-aware RSS refresh work for a systemd timer."""

from datetime import datetime, timedelta, timezone

from . import constants as limits
from .rss import (DEFAULT_RSS_REFRESH_MINUTES, RSSFeedError, parse_feed)
from .rss_fetch import fetch_feed
from .rss_store import store_feed_articles


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


def refresh_due_feeds(db, *, now=None, fetcher=fetch_feed):
    """Refresh due feeds sequentially, respecting the household network toggle.

    The caller owns the SQLite transaction. Network access is done only for
    feeds whose last attempt is older than the configured refresh interval.
    """
    if _setting(db, 'rss_outbound_enabled', '1') != '1':
        return {'disabled': True, 'checked': 0, 'refreshed': 0, 'failed': 0}

    try:
        interval = int(_setting(
            db, 'rss_refresh_minutes', str(DEFAULT_RSS_REFRESH_MINUTES)))
    except (TypeError, ValueError):
        interval = DEFAULT_RSS_REFRESH_MINUTES
    if not limits.RSS_REFRESH_MINIMUM_MINUTES <= interval <= limits.RSS_REFRESH_MAXIMUM_MINUTES:
        interval = DEFAULT_RSS_REFRESH_MINUTES

    instant = _utc(now)
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
