"""Refresh visible RSS feeds on demand and retain their last good articles."""

from datetime import datetime, timedelta, timezone

from .rss import RSSFeedError, parse_feed


PERSONAL_ARTICLE_RETENTION_DAYS = 90
HOUSEHOLD_ARTICLE_RETENTION_DAYS = 3
MAX_RSS_STORAGE_BYTES = 64 * 1024 * 1024
MAX_ORDINARY_ARTICLES_PER_FEED = 30
ARTICLE_STORAGE_OVERHEAD_BYTES = 128


def _as_utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def article_storage_bytes(row):
    """Estimate one cached article's database footprint conservatively."""
    fields = ('title', 'url', 'summary', 'published')
    return ARTICLE_STORAGE_OVERHEAD_BYTES + sum(
        len((row[field] or '').encode('utf-8')) for field in fields
    )


def enforce_rss_user_storage(db, user_id, *, max_bytes=MAX_RSS_STORAGE_BYTES):
    """Hide the oldest unstarred articles for one user until within their budget."""
    articles = db.execute(
        'SELECT a.id,a.title,a.url,a.summary,a.published,a.fetched_at,'
        's.read_at,COALESCE(s.is_favourite,0) AS is_favourite,s.deleted_at,f.scope '
        'FROM rss_articles a JOIN rss_feeds f ON f.id=a.feed_id '
        'LEFT JOIN rss_article_states s ON s.article_id=a.id AND s.user_id=? '
        'WHERE (f.scope=\'household\' OR (f.scope=\'personal\' AND f.owner_user_id=?)) '
        'AND (s.deleted_at IS NULL OR s.article_id IS NULL)', (user_id, user_id)
    ).fetchall()
    used = sum(article_storage_bytes(article) for article in articles)
    candidates = sorted(
        (article for article in articles if not article['is_favourite']),
        key=lambda article: (article['read_at'] is not None,
                             article['fetched_at'], article['id']),
    )
    for article in candidates:
        if used <= max_bytes:
            break
        db.execute(
            'INSERT INTO rss_article_states(article_id,user_id,deleted_at,is_favourite) '
            'VALUES(?,?,?,0) ON CONFLICT(article_id,user_id) DO UPDATE SET '
            'deleted_at=excluded.deleted_at',
            (article['id'], user_id, datetime.now(timezone.utc).isoformat()),
        )
        used -= article_storage_bytes(article)
    db.commit()
    return used


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
        "(SELECT id FROM rss_feeds WHERE scope='personal') AND NOT EXISTS "
        '(SELECT 1 FROM rss_article_states s WHERE s.article_id=rss_articles.id '
        'AND s.is_favourite=1)', (personal_cutoff,)
    )
    db.execute(
        'DELETE FROM rss_articles WHERE fetched_at<? AND feed_id IN '
        "(SELECT id FROM rss_feeds WHERE scope='household') AND NOT EXISTS "
        '(SELECT 1 FROM rss_article_states s WHERE s.article_id=rss_articles.id '
        'AND s.is_favourite=1)', (household_cutoff,)
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
        for position, article in enumerate(parsed['articles']):
            if not article['url']:
                continue
            db.execute(
                'INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at,position) '
                'VALUES(?,?,?,?,?,?,?) ON CONFLICT(feed_id,url) DO UPDATE SET '
                'title=excluded.title,summary=excluded.summary,published=excluded.published,'
                'fetched_at=excluded.fetched_at,position=excluded.position',
                (feed['id'], article['title'], article['url'], article['summary'],
                 article['published'], now_text, position),
            )
        db.execute(
            'DELETE FROM rss_articles WHERE feed_id=? AND id IN ('
            'SELECT a.id FROM rss_articles a WHERE a.feed_id=? AND NOT EXISTS '
            '(SELECT 1 FROM rss_article_states s WHERE s.article_id=a.id AND s.is_favourite=1) '
            'ORDER BY a.position,a.fetched_at DESC,a.id DESC LIMIT -1 OFFSET ?)',
            (feed['id'], feed['id'], MAX_ORDINARY_ARTICLES_PER_FEED),
        )
        db.execute('UPDATE rss_feeds SET title=?,last_fetched_at=?,last_error=NULL WHERE id=?',
                   (title, now_text, feed['id']))
        db.commit()
