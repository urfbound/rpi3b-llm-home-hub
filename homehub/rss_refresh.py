"""Refresh visible RSS feeds on demand and retain their last good articles."""

from datetime import datetime, timedelta, timezone

from .rss import RSSFeedError, parse_feed


PERSONAL_ARTICLE_RETENTION_DAYS = 90
HOUSEHOLD_ARTICLE_RETENTION_DAYS = 3


def _as_utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def refresh_due_feeds(db, feeds, *, outbound_enabled, interval_minutes,
                      fetcher, now=None):
    """Update due feed caches, leaving the previous cache intact on errors.

    The attempt timestamp is committed before network access so overlapping
    page requests do not fetch the same feed repeatedly.
    """
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    now_text = now.isoformat()
    cutoff = now - timedelta(minutes=interval_minutes)
    personal_cutoff = (now - timedelta(days=PERSONAL_ARTICLE_RETENTION_DAYS)).isoformat()
    household_cutoff = (now - timedelta(days=HOUSEHOLD_ARTICLE_RETENTION_DAYS)).isoformat()
    db.execute(
        'DELETE FROM rss_articles WHERE fetched_at<? AND feed_id IN '
        "(SELECT id FROM rss_feeds WHERE scope='personal')", (personal_cutoff,)
    )
    db.execute(
        'DELETE FROM rss_articles WHERE fetched_at<? AND feed_id IN '
        "(SELECT id FROM rss_feeds WHERE scope='household')", (household_cutoff,)
    )
    db.commit()
    if not outbound_enabled:
        return
    for feed in feeds:
        last_attempt = feed['last_attempt_at']
        if last_attempt:
            try:
                if _as_utc(last_attempt) > cutoff:
                    continue
            except (TypeError, ValueError):
                pass
        cursor = db.execute(
            'UPDATE rss_feeds SET last_attempt_at=? WHERE id=? AND last_attempt_at IS ?',
            (now_text, feed['id'], last_attempt),
        )
        if cursor.rowcount != 1:
            continue
        db.commit()
        try:
            document, _final_url = fetcher(
                feed['normalized_url'], allow_lan=bool(feed['allow_lan'])
            )
            parsed = parse_feed(document, feed['normalized_url'])
        except RSSFeedError as exc:
            db.execute('UPDATE rss_feeds SET last_error=? WHERE id=?',
                       (str(exc)[:256], feed['id']))
            db.commit()
            continue
        title = parsed['title'] or feed['title']
        for article in parsed['articles']:
            if not article['url']:
                continue
            db.execute(
                'INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
                'VALUES(?,?,?,?,?,?) ON CONFLICT(feed_id,url) DO UPDATE SET '
                'title=excluded.title,summary=excluded.summary,published=excluded.published,'
                'fetched_at=excluded.fetched_at',
                (feed['id'], article['title'], article['url'], article['summary'],
                 article['published'], now_text),
            )
        db.execute('UPDATE rss_feeds SET title=?,last_fetched_at=?,last_error=NULL WHERE id=?',
                   (title, now_text, feed['id']))
        db.commit()
