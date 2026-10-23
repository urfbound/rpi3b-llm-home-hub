import socket

import pytest

from homehub.rss import (
    RSSFeedError, normalize_feed_url, parse_feed, validate_feed_url,
)


def public_resolver(host, _port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]


def test_feed_urls_normalize_scheme_host_default_port_and_fragment():
    assert normalize_feed_url(' HTTPS://Example.COM:443/news#top ') == 'https://example.com/news'
    assert normalize_feed_url('http://Example.com:80/feed/') == 'http://example.com/feed'


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'ftp://example.com/feed.xml',
    'https://user:secret@example.com/feed', 'http://localhost/feed',
    'http://homehub.local/feed', 'http://127.0.0.1/feed',
    'http://192.168.1.2/feed', 'http://[::1]/feed',
])
def test_feed_url_validation_rejects_non_http_credentials_and_local_hosts(url):
    with pytest.raises(RSSFeedError):
        validate_feed_url(url, resolver=public_resolver)


def test_feed_url_validation_rejects_dns_results_that_include_private_addresses():
    def mixed_resolver(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.4', 443)),
        ]

    with pytest.raises(RSSFeedError, match='public'):
        validate_feed_url('https://feed.example/news', resolver=mixed_resolver)


def test_feed_url_validation_accepts_public_host_without_resolving_other_hosts():
    assert validate_feed_url('https://feed.example/rss', resolver=public_resolver) == (
        'https://feed.example/rss'
    )


def test_feed_url_validation_allows_private_lan_only_when_explicitly_approved():
    def private_resolver(host, port, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.25', port))]

    with pytest.raises(RSSFeedError):
        validate_feed_url('http://printer.home/feed', resolver=private_resolver)

    assert validate_feed_url('http://printer.home/feed', resolver=private_resolver,
                             allow_lan=True) == 'http://printer.home/feed'


def test_rss_feed_parsing_extracts_articles_as_plain_text_and_resolves_relative_links():
    source = b'''<?xml version="1.0"?><rss version="2.0"><channel>
      <title>House news</title><item><title>First story</title>
      <link>/stories/one</link><description><![CDATA[<p>Warm <b>bread</b> today</p>]]></description>
      <pubDate>Fri, 23 Oct 2026 12:00:00 GMT</pubDate></item>
      <item><title>Second story</title><link>javascript:alert(1)</link></item>
    </channel></rss>'''

    parsed = parse_feed(source, 'https://feed.example/rss.xml')

    assert parsed['title'] == 'House news'
    assert parsed['articles'][0] == {
        'title': 'First story', 'url': 'https://feed.example/stories/one',
        'summary': 'Warm bread today', 'published': 'Fri, 23 Oct 2026 12:00:00 GMT',
    }
    assert parsed['articles'][1]['url'] is None


def test_atom_feed_parsing_handles_namespaces_and_alternate_links():
    source = b'''<feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom home</title><entry><title>Atom story</title>
      <link rel="self" href="https://feed.example/self" />
      <link rel="alternate" href="https://feed.example/story" />
      <summary>Short text</summary><updated>2026-10-23T12:00:00Z</updated></entry>
    </feed>'''

    parsed = parse_feed(source, 'https://feed.example/atom.xml')

    assert parsed['title'] == 'Atom home'
    assert parsed['articles'][0] == {
        'title': 'Atom story', 'url': 'https://feed.example/story',
        'summary': 'Short text', 'published': '2026-10-23T12:00:00Z',
    }


def test_feed_parser_rejects_invalid_xml_and_caps_article_count():
    with pytest.raises(RSSFeedError):
        parse_feed(b'<not-closed>', 'https://feed.example/rss')
    xml = '<rss><channel><title>Lots</title>' + ''.join(
        f'<item><title>Story {i}</title></item>' for i in range(150)
    ) + '</channel></rss>'

    assert len(parse_feed(xml.encode(), 'https://feed.example/rss')['articles']) == 100
