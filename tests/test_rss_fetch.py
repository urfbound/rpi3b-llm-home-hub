import socket

import pytest

from homehub.rss import RSSFeedError
from homehub.rss_fetch import fetch_feed


def public_resolver(host, port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]


def response(status=200, headers=None, body=b'<rss><channel/></rss>'):
    return status, headers or {}, body


def test_fetch_uses_resolved_public_destination_and_returns_final_url():
    requests = []

    def requester(url, address, timeout, max_bytes):
        requests.append((url, address[4][0], timeout, max_bytes))
        return response()

    body, final_url = fetch_feed('https://feeds.example/rss', resolver=public_resolver,
                                 requester=requester)

    assert body == b'<rss><channel/></rss>'
    assert final_url == 'https://feeds.example/rss'
    assert requests[0][0:2] == ('https://feeds.example/rss', '93.184.216.34')


def test_fetch_revalidates_redirect_target_before_requesting_it():
    requested = []

    def resolver(host, port, **_kwargs):
        address = '93.184.216.34' if host == 'feeds.example' else '127.0.0.1'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]

    def requester(url, _address, _timeout, _max_bytes):
        requested.append(url)
        return response(302, {'location': 'http://127.0.0.1/admin'})

    with pytest.raises(RSSFeedError, match='public'):
        fetch_feed('https://feeds.example/rss', resolver=resolver, requester=requester)

    assert requested == ['https://feeds.example/rss']


def test_lan_approval_is_limited_to_the_feed_host_and_never_allows_loopback():
    def resolver(host, port, **_kwargs):
        address = {'printer.home': '192.168.1.25',
                   'other.home': '192.168.1.26',
                   'localhost': '127.0.0.1'}.get(host, '93.184.216.34')
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]

    with pytest.raises(RSSFeedError, match='public'):
        fetch_feed('http://localhost/feed', resolver=resolver, allow_lan=True,
                   requester=lambda *_args: response())

    with pytest.raises(RSSFeedError, match='public'):
        fetch_feed('http://printer.home/feed', resolver=resolver, allow_lan=True,
                   requester=lambda *_args: response(
                       302, {'location': 'http://other.home/secret'}))

    body, final_url = fetch_feed('http://printer.home/feed', resolver=resolver,
                                 allow_lan=True, requester=lambda *_args: response())
    assert body == b'<rss><channel/></rss>'
    assert final_url == 'http://printer.home/feed'


def test_fetch_follows_bounded_relative_redirects():
    calls = []

    def requester(url, _address, _timeout, _max_bytes):
        calls.append(url)
        if len(calls) == 1:
            return response(301, {'location': '/new/feed.xml'})
        return response(body=b'<rss><channel><title>ok</title></channel></rss>')

    body, final_url = fetch_feed('https://feeds.example/old', resolver=public_resolver,
                                 requester=requester)

    assert calls == ['https://feeds.example/old', 'https://feeds.example/new/feed.xml']
    assert final_url == 'https://feeds.example/new/feed.xml'
    assert b'<title>ok</title>' in body


def test_fetch_rejects_oversized_body_and_content_length():
    with pytest.raises(RSSFeedError, match='large'):
        fetch_feed('https://feeds.example/rss', resolver=public_resolver,
                   requester=lambda *_args: response(body=b'x' * (2 * 1024 * 1024 + 1)))
    with pytest.raises(RSSFeedError, match='large'):
        fetch_feed('https://feeds.example/rss', resolver=public_resolver,
                   requester=lambda *_args: response(headers={'content-length': '999999999'}))


def test_fetch_rejects_non_success_responses_and_redirect_loops():
    with pytest.raises(RSSFeedError, match='HTTP'):
        fetch_feed('https://feeds.example/rss', resolver=public_resolver,
                   requester=lambda *_args: response(503))
    with pytest.raises(RSSFeedError, match='redirect'):
        fetch_feed('https://feeds.example/rss', resolver=public_resolver,
                   requester=lambda *_args: response(302, {'location': '/again'}),
                   max_redirects=2)


def test_fetch_rejects_redirect_without_location():
    with pytest.raises(RSSFeedError, match='redirect'):
        fetch_feed('https://feeds.example/rss', resolver=public_resolver,
                   requester=lambda *_args: response(302))
