"""Safe, dependency-free URL checks and RSS/Atom parsing."""

from html.parser import HTMLParser
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET


MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ARTICLES = 100


class RSSFeedError(Exception):
    """Feed URL or document could not be safely processed."""


def normalize_feed_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise RSSFeedError('Enter a feed URL no longer than 2048 characters.')
    try:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RSSFeedError('Enter a valid feed URL.') from exc
    if scheme not in ('http', 'https') or not host or parsed.username or parsed.password:
        raise RSSFeedError('Feed URLs must use HTTP or HTTPS and cannot contain credentials.')
    try:
        host = host.encode('idna').decode('ascii').lower()
    except UnicodeError as exc:
        raise RSSFeedError('The feed host name is invalid.') from exc
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    default_port = 80 if scheme == 'http' else 443
    netloc = host if port in (None, default_port) else f'{host}:{port}'
    path = parsed.path.rstrip('/')
    return urlunsplit((scheme, netloc, path, parsed.query, ''))


def resolve_feed_destination(value, *, resolver=socket.getaddrinfo, allow_lan=False):
    normalized = normalize_feed_url(value)
    parsed = urlsplit(normalized)
    host = parsed.hostname
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError as exc:
        raise RSSFeedError('The feed port is invalid.') from exc
    if port not in (80, 443):
        raise RSSFeedError('Feed URLs must use the standard HTTP or HTTPS port.')
    local_suffixes = ('.localhost', '.local', '.lan', '.internal', '.home')
    if host == 'localhost' or (not allow_lan and host.endswith(local_suffixes)):
        raise RSSFeedError('Feed URLs must point to a public internet host.')
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_allowed_destination(literal, allow_lan):
            raise RSSFeedError('Feed URLs must point to a public internet host.')
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        sockaddr = (str(literal), port, 0, 0) if literal.version == 6 else (str(literal), port)
        return normalized, [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr)]
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RSSFeedError('The feed host could not be resolved.') from exc
    safe_addresses = []
    for result in addresses:
        try:
            resolved_ip = ipaddress.ip_address(result[4][0].split('%', 1)[0])
        except (ValueError, IndexError, TypeError, AttributeError):
            continue
        if not _is_allowed_destination(resolved_ip, allow_lan):
            raise RSSFeedError('Feed URLs must resolve only to public internet addresses.')
        safe_addresses.append(result)
    if not safe_addresses:
        raise RSSFeedError('Feed URLs must resolve only to public internet addresses.')
    return normalized, safe_addresses


def _is_allowed_destination(address, allow_lan):
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        return False
    return address.is_global or (allow_lan and address.is_private and not address.is_reserved)


def validate_feed_url(value, *, resolver=socket.getaddrinfo, allow_lan=False):
    normalized, _destinations = resolve_feed_destination(
        value, resolver=resolver, allow_lan=allow_lan
    )
    return normalized


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._blocked = 0

    def handle_starttag(self, tag, _attrs):
        if tag.lower() in ('script', 'style'):
            self._blocked += 1

    def handle_endtag(self, tag):
        if tag.lower() in ('script', 'style') and self._blocked:
            self._blocked -= 1

    def handle_data(self, data):
        if not self._blocked:
            self.parts.append(data)


def _local_name(element):
    return element.tag.rsplit('}', 1)[-1].split(':')[-1].lower()


def _child(element, name):
    return next((node for node in list(element) if _local_name(node) == name), None)


def _child_text(element, names):
    for child in list(element):
        if _local_name(child) in names:
            return ''.join(child.itertext()).strip()
    return ''


def _plain_text(value):
    if not value:
        return ''
    extractor = _TextExtractor()
    try:
        extractor.feed(value)
        extractor.close()
        return ' '.join(' '.join(extractor.parts).split())
    except (ValueError, AssertionError):
        return ' '.join(value.split())


def _article_url(entry, feed_url, atom):
    if atom:
        links = [node for node in list(entry) if _local_name(node) == 'link']
        link = next((node.get('href', '') for node in links
                     if node.get('rel', 'alternate') == 'alternate'), '')
    else:
        link = _child_text(entry, {'link'})
    if not link:
        return None
    absolute = urljoin(feed_url, link.strip())
    try:
        parsed = urlsplit(absolute)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return absolute


def parse_feed(document, feed_url):
    if isinstance(document, str):
        document = document.encode('utf-8')
    if not isinstance(document, bytes) or len(document) > MAX_FEED_BYTES:
        raise RSSFeedError('The feed document is too large or invalid.')
    try:
        root = ET.fromstring(document)
    except (ET.ParseError, ValueError) as exc:
        raise RSSFeedError('The feed document is not valid XML.') from exc
    root_name = _local_name(root)
    atom = root_name == 'feed'
    if root_name == 'rss':
        channel = _child(root, 'channel')
        if channel is None:
            channel = root
    elif atom:
        channel = root
    else:
        raise RSSFeedError('The document is not an RSS or Atom feed.')
    title = _child_text(channel, {'title'})
    item_name = 'entry' if atom else 'item'
    entries = [node for node in list(channel) if _local_name(node) == item_name]
    articles = []
    for entry in entries[:MAX_ARTICLES]:
        article_title = _child_text(entry, {'title'})
        summary = _child_text(entry, {'summary', 'description', 'encoded', 'content'})
        published = _child_text(entry, {'pubdate', 'published', 'updated', 'date'})
        articles.append({
            'title': _plain_text(article_title)[:512],
            'url': _article_url(entry, feed_url, atom),
            'summary': _plain_text(summary)[:4000],
            'published': _plain_text(published)[:128],
        })
    return {'title': _plain_text(title)[:256], 'articles': articles}
