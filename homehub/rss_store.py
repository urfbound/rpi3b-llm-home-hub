"""Persistence and per-user state for fetched RSS articles."""

from datetime import datetime, timezone
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

from . import constants as limits


class RSSArticleError(ValueError):
    """Article state cannot be applied to the requested account or article."""


def _timestamp(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec='seconds')
    if not isinstance(value, str) or not value:
        raise ValueError('Timestamp must be a non-empty string or datetime.')
    return value


def _article_key(article):
    url = article.get('url')
    if url:
        parsed = urlsplit(url)
        canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                                parsed.path, parsed.query, ''))
        identity = 'url:' + canonical
    else:
        identity = 'content:' + json.dumps(
            [article.get('title', ''), article.get('published', ''),
             article.get('summary', '')], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def store_feed_articles(db, feed_id, articles, *, fetched_at=None):
    """Insert a fetched snapshot once; repeated feed items do not duplicate it."""
    fetched_at = _timestamp(fetched_at)
    feed = db.execute('SELECT id FROM rss_feeds WHERE id=?', (feed_id,)).fetchone()
    if feed is None:
        raise RSSArticleError('The feed no longer exists.')
    inserted = 0
    for article in articles:
        title = str(article.get('title') or '')[:limits.RSS_ARTICLE_TITLE_MAXIMUM]
        url = article.get('url')
        if url is not None:
            url = str(url)[:limits.RSS_ARTICLE_URL_MAXIMUM]
        summary = str(article.get('summary') or '')[:limits.RSS_ARTICLE_BODY_MAXIMUM]
        published = str(article.get('published') or '')[:limits.RSS_ARTICLE_DATE_MAXIMUM]
        content_bytes = sum(len(value.encode('utf-8')) for value in
                            (title, url or '', summary, published))
        cursor = db.execute(
            'INSERT OR IGNORE INTO rss_articles '
            '(feed_id,article_key,title,url,summary,published,first_seen_at,content_bytes) '
            'VALUES(?,?,?,?,?,?,?,?)',
            (feed_id, _article_key({'title': title, 'url': url,
                                    'summary': summary, 'published': published}),
             title, url, summary, published, fetched_at, content_bytes),
        )
        inserted += cursor.rowcount
    return inserted


def _user_can_view(db, user_id, article_id):
    return db.execute(
        'SELECT a.id FROM rss_articles a JOIN rss_feeds f ON f.id=a.feed_id '
        'WHERE a.id=? AND (f.scope="household" OR '
        '(f.scope="personal" AND f.owner_user_id=?))',
        (article_id, user_id),
    ).fetchone() is not None


def set_article_state(db, user_id, article_id, *, is_read=None,
                      is_favorite=None, is_deleted=False, read_at=None,
                      changed_at=None):
    """Set read/favourite/delete state only for a visible article.

    Read state is monotonic: an article cannot be returned to unread.
    """
    if not _user_can_view(db, user_id, article_id):
        raise RSSArticleError('The article is unavailable.')
    if is_favorite is True:
        existing = db.execute(
            'SELECT is_favorite FROM rss_article_states WHERE user_id=? AND article_id=?',
            (user_id, article_id),
        ).fetchone()
        count = db.execute(
            'SELECT COUNT(*) FROM rss_article_states WHERE user_id=? AND is_favorite=1',
            (user_id,),
        ).fetchone()[0]
        if not (existing and existing[0]) and count >= limits.RSS_FAVORITES_MAXIMUM:
            raise RSSArticleError('You have reached the maximum number of favourite articles.')
    current = db.execute(
        'SELECT is_read,is_favorite,deleted_at,read_at FROM rss_article_states '
        'WHERE user_id=? AND article_id=?', (user_id, article_id),
    ).fetchone()
    was_read = bool(current[0]) if current else False
    was_favorite = bool(current[1]) if current else False
    was_deleted = current[2] if current else None
    new_read = was_read or is_read is True
    new_favorite = was_favorite if is_favorite is None else bool(is_favorite)
    new_deleted = was_deleted or (_timestamp(changed_at) if is_deleted else None)
    new_read_at = ((current and current[3]) or _timestamp(read_at or changed_at)
                   if is_read is True else (current and current[3]))
    db.execute(
        'INSERT INTO rss_article_states(article_id,user_id,is_read,read_at,is_favorite,deleted_at) '
        'VALUES(?,?,?,?,?,?) ON CONFLICT(article_id,user_id) DO UPDATE SET '
        'is_read=excluded.is_read, read_at=COALESCE(rss_article_states.read_at,excluded.read_at), '
        'is_favorite=excluded.is_favorite, deleted_at=COALESCE(rss_article_states.deleted_at,excluded.deleted_at)',
        (article_id, user_id, int(new_read), new_read_at,
         int(new_favorite), new_deleted),
    )


def articles_for_user(db, user_id):
    """List household and owned personal articles with isolated state."""
    articles = [dict(row) for row in db.execute(
        'SELECT a.id,a.feed_id,a.title,a.url,a.summary,a.published,a.first_seen_at,'
        'f.title AS feed_title,f.scope,COALESCE(s.is_read,0) AS is_read,'
        'COALESCE(s.is_favorite,0) AS is_favorite '
        'FROM rss_articles a JOIN rss_feeds f ON f.id=a.feed_id '
        'LEFT JOIN rss_article_states s ON s.article_id=a.id AND s.user_id=? '
        'WHERE s.deleted_at IS NULL AND (f.scope="household" OR '
        '(f.scope="personal" AND f.owner_user_id=?)) '
        'ORDER BY f.sort_order,f.id,a.first_seen_at DESC,a.id DESC',
        (user_id, user_id),
    ).fetchall()]
    for article in articles:
        article['is_read'] = bool(article['is_read'])
        article['is_favorite'] = bool(article['is_favorite'])
    return articles
