"""Bounded HTTP fetching for RSS feeds with DNS-pinned public destinations."""

from http import HTTPStatus
import http.client
import socket
import ssl
from urllib.parse import urljoin, urlsplit, urlunsplit
from . import constants as limits

from .rss import (MAX_FEED_BYTES, RSSFeedError, normalize_feed_url,
                  resolve_feed_destination)


REDIRECT_CODES = {HTTPStatus.MOVED_PERMANENTLY, HTTPStatus.FOUND,
                  HTTPStatus.SEE_OTHER, HTTPStatus.TEMPORARY_REDIRECT,
                  HTTPStatus.PERMANENT_REDIRECT}
MAX_REDIRECTS = limits.RSS_REDIRECTS_MAXIMUM
REQUEST_TIMEOUT_SECONDS = limits.RSS_REQUEST_TIMEOUT_SECONDS


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, destination, timeout):
        super().__init__(host, port, timeout=timeout)
        self.destination = destination

    def connect(self):
        family, socktype, proto, _canonname, sockaddr = self.destination
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(self.timeout)
        try:
            sock.connect(sockaddr)
        except Exception:
            sock.close()
            raise
        self.sock = sock


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, destination, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.destination = destination

    def connect(self):
        family, socktype, proto, _canonname, sockaddr = self.destination
        raw = socket.socket(family, socktype, proto)
        raw.settimeout(self.timeout)
        try:
            raw.connect(sockaddr)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _http_request(url, destination, timeout, max_bytes):
    parsed = urlsplit(url)
    port = parsed.port or (limits.HTTPS_DEFAULT_PORT if parsed.scheme == 'https' else limits.HTTP_DEFAULT_PORT)
    connection_type = _PinnedHTTPSConnection if parsed.scheme == 'https' else _PinnedHTTPConnection
    connection = connection_type(parsed.hostname, port, destination, timeout)
    path = urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
    try:
        connection.request('GET', path, headers={
            'Accept': 'application/atom+xml, application/rss+xml, application/xml, text/xml, */*',
            'Accept-Encoding': 'identity',
            'User-Agent': 'HomeHub RSS reader',
            'Connection': 'close',
        })
        result = connection.getresponse()
        headers = {name.lower(): value for name, value in result.getheaders()}
        if result.status in REDIRECT_CODES or not HTTPStatus.OK <= result.status < HTTPStatus.MULTIPLE_CHOICES:
            return result.status, headers, b''
        content_length = headers.get('content-length')
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise RSSFeedError('The feed response is too large.')
            except ValueError as exc:
                raise RSSFeedError('The feed response has an invalid content length.') from exc
        body = result.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise RSSFeedError('The feed response is too large.')
        return result.status, headers, body
    finally:
        connection.close()


def fetch_feed(url, *, resolver=socket.getaddrinfo, requester=_http_request,
               timeout=REQUEST_TIMEOUT_SECONDS, max_redirects=MAX_REDIRECTS,
               max_bytes=MAX_FEED_BYTES, allow_lan=False):
    """Fetch a feed, pinning each request to a checked DNS result.

    Redirect targets are resolved and checked independently. ``allow_lan`` is
    reserved for feeds explicitly approved by an administrator.
    """
    if (not 0 < timeout <= limits.RSS_REQUEST_TIMEOUT_MAXIMUM_SECONDS
            or not 0 <= max_redirects <= limits.RSS_REDIRECTS_MAXIMUM_ALLOWED):
        raise ValueError('Invalid feed fetch limits.')
    if not 1 <= max_bytes <= MAX_FEED_BYTES:
        raise ValueError('Invalid maximum feed size.')
    current_url = normalize_feed_url(url)
    approved_lan_host = urlsplit(current_url).hostname
    redirects = 0
    while True:
        current_host = urlsplit(current_url).hostname
        hop_allow_lan = allow_lan and current_host == approved_lan_host
        current_url, destinations = resolve_feed_destination(
            current_url, resolver=resolver, allow_lan=hop_allow_lan
        )
        try:
            status, headers, body = requester(current_url, destinations[0], timeout, max_bytes)
        except RSSFeedError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError) as exc:
            raise RSSFeedError('The feed could not be reached.') from exc
        headers = {str(key).lower(): value for key, value in headers.items()}
        if status in REDIRECT_CODES:
            location = headers.get('location')
            if not location or redirects >= max_redirects:
                raise RSSFeedError('The feed returned an invalid or excessive redirect.')
            current_url = urljoin(current_url, location)
            redirects += 1
            continue
        if not HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
            raise RSSFeedError(f'The feed server returned HTTP {status}.')
        content_length = headers.get('content-length')
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise RSSFeedError('The feed response is too large.')
            except ValueError as exc:
                raise RSSFeedError('The feed response has an invalid content length.') from exc
        if not isinstance(body, bytes) or len(body) > max_bytes:
            raise RSSFeedError('The feed response is too large or invalid.')
        return body, current_url
