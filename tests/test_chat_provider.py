import json

import pytest

from homehub.chat import ChatProviderError, list_ollama_models


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _maximum):
        return json.dumps(self.payload).encode()


def test_model_discovery_uses_tags_api_and_returns_unique_sorted_names(monkeypatch):
    calls = []
    monkeypatch.setattr('homehub.chat.urlopen', lambda url, timeout: (
        calls.append((url, timeout)) or FakeResponse({'models': [
            {'name': 'qwen2.5:0.5b'}, {'name': 'gemma3:270m'},
            {'name': 'qwen2.5:0.5b'}, {'not_a_model': True},
        ]})
    ))

    result = list_ollama_models()

    assert result == ['gemma3:270m', 'qwen2.5:0.5b']
    assert calls == [('http://127.0.0.1:11434/api/tags', 2)]


@pytest.mark.parametrize('base_url', [
    'https://127.0.0.1:11434',
    'http://192.168.1.5:11434',
    'http://example.com:11434',
    'http://user:password@127.0.0.1:11434',
])
def test_model_discovery_rejects_non_loopback_or_unsafe_urls(monkeypatch, base_url):
    monkeypatch.setattr('homehub.chat.urlopen', lambda *_args, **_kwargs: pytest.fail(
        'unsafe Ollama URL must not be contacted'))

    with pytest.raises(ChatProviderError):
        list_ollama_models(base_url)


def test_model_discovery_rejects_malformed_ollama_payload(monkeypatch):
    monkeypatch.setattr('homehub.chat.urlopen', lambda *_args, **_kwargs: FakeResponse(
        {'items': []}))

    with pytest.raises(ChatProviderError):
        list_ollama_models()
