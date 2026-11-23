import json

import pytest

from homehub.chat import (ChatProviderError, OllamaProvider, OpenAICompatibleProvider,
                          list_ollama_models, list_openai_models, validate_chat_endpoint)
from homehub.constants import CHAT_CONTEXT_TOKENS_DEFAULT


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


def test_ollama_provider_sends_nonstreaming_chat_only_to_loopback(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return FakeResponse({'message': {'content': 'Hello from local Ollama'}})

    monkeypatch.setattr('homehub.chat.urlopen', fake_urlopen)

    response = OllamaProvider().complete(
        'gemma3:270m', [{'role': 'user', 'content': 'Hello'}])

    assert response == 'Hello from local Ollama'
    assert calls == [(
        'http://127.0.0.1:11434/api/chat',
        {'model': 'gemma3:270m', 'messages': [{'role': 'user', 'content': 'Hello'}],
         'stream': False, 'keep_alive': 0,
         'options': {'num_ctx': CHAT_CONTEXT_TOKENS_DEFAULT}},
        900,
    )]


def test_ollama_provider_rejects_non_loopback_before_sending(monkeypatch):
    monkeypatch.setattr('homehub.chat.urlopen', lambda *_args, **_kwargs: pytest.fail(
        'provider must not contact a non-loopback Ollama host'))

    with pytest.raises(ChatProviderError):
        OllamaProvider('http://192.168.1.9:11434').complete(
            'model', [{'role': 'user', 'content': 'Hi'}])


def test_ollama_provider_sends_only_supplied_tool_schemas(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return FakeResponse({'message': {'content': '', 'tool_calls': []}})

    monkeypatch.setattr('homehub.chat.urlopen', fake_urlopen)
    tools = [{'type': 'function', 'function': {'name': 'get_household_lists'}}]

    response = OllamaProvider().complete_message(
        'gemma3:270m', [{'role': 'user', 'content': 'What is on my list?'}], tools)

    assert response == {'content': '', 'tool_calls': []}
    assert calls[0]['tools'] == tools
    assert calls[0]['options'] == {'num_ctx': CHAT_CONTEXT_TOKENS_DEFAULT}


@pytest.mark.parametrize('endpoint', [
    'http://user:secret@192.168.1.7:8000',
    'http://192.168.1.7:8000/other',
    'http://192.168.1.7:8000?token=secret',
    'file:///tmp/agent',
    'http://example.com:8000',
    'http://127.0.0.1:0',
])
def test_remote_endpoint_rejects_unsafe_addresses(endpoint):
    with pytest.raises(ChatProviderError):
        validate_chat_endpoint(endpoint)


def test_remote_endpoint_accepts_private_ip_and_https_hostname():
    assert validate_chat_endpoint('http://192.168.1.7:8000') == 'http://192.168.1.7:8000'
    assert validate_chat_endpoint('https://agent.example.com:8443') == 'https://agent.example.com:8443'


def test_remote_ollama_discovers_models_when_explicitly_allowed(monkeypatch):
    calls = []
    monkeypatch.setattr('homehub.chat._open_remote', lambda url, timeout: (
        calls.append(url) or FakeResponse({'models': [{'name': 'remote:latest'}]})
    ))
    assert list_ollama_models('http://192.168.1.7:11434', allow_remote=True) == ['remote:latest']
    assert calls == ['http://192.168.1.7:11434/api/tags']


def test_remote_ollama_streams_without_forcing_local_memory_limits(monkeypatch):
    calls = []

    class StreamingResponse(FakeResponse):
        def __iter__(self):
            return iter([
                b'{"message":{"content":"Hello "},"done":false}\n',
                b'{"message":{"content":"agent"},"done":false}\n',
                b'{"done":true}\n',
            ])

    def fake_open(request, timeout):
        calls.append(json.loads(request.data))
        return StreamingResponse(None)

    monkeypatch.setattr('homehub.chat._open_remote', fake_open)
    chunks = []
    response = OllamaProvider('http://192.168.1.7:11434', allow_remote=True).complete(
        'agent', [{'role': 'user', 'content': 'Hi'}], on_chunk=chunks.append)
    assert response == 'Hello agent'
    assert chunks[-1] == 'Hello agent'
    assert calls[0]['stream'] is True
    assert 'keep_alive' not in calls[0]
    assert 'options' not in calls[0]


def test_openai_compatible_discovers_models_and_streams_reply(monkeypatch):
    calls = []

    class StreamingResponse(FakeResponse):
        def __iter__(self):
            return iter([
                b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n',
                b'data: {"choices":[{"delta":{"content":"world"}}]}\n',
                b'data: [DONE]\n',
            ])

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, dict(request.header_items()),
                      json.loads(request.data) if request.data else None))
        if request.full_url.endswith('/models'):
            return FakeResponse({'data': [{'id': 'agent-b'}, {'id': 'agent-a'}]})
        return StreamingResponse(None)

    monkeypatch.setattr('homehub.chat._open_remote', fake_urlopen)
    endpoint = 'https://agent.example.com:8443'
    assert list_openai_models(endpoint, api_key='test-token') == ['agent-a', 'agent-b']
    chunks = []
    result = OpenAICompatibleProvider(endpoint, api_key='test-token').complete(
        'agent-a', [{'role': 'user', 'content': 'Hi'}], on_chunk=chunks.append)
    assert result == 'Hello world'
    assert chunks == ['Hello world']
    assert calls[0][0] == 'https://agent.example.com:8443/v1/models'
    assert calls[1][0] == 'https://agent.example.com:8443/v1/chat/completions'
    assert calls[1][2]['stream'] is True
    assert calls[1][2]['messages'] == [{'role': 'user', 'content': 'Hi'}]
    assert calls[1][1]['Authorization'] == 'Bearer test-token'


def test_openai_tool_response_preserves_call_id_and_argument_json(monkeypatch):
    requests = []

    def fake_open(request, timeout):
        requests.append(json.loads(request.data))
        return FakeResponse({'choices': [{'message': {
            'content': None, 'tool_calls': [{'id': 'call-1', 'type': 'function',
                'function': {'name': 'get_household_lists', 'arguments': '{}'}}],
        }}]})

    monkeypatch.setattr('homehub.chat._open_remote', fake_open)
    result = OpenAICompatibleProvider('http://192.168.1.7:8000').complete_message(
        'agent', [{'role': 'user', 'content': 'My lists?'}],
        [{'type': 'function', 'function': {'name': 'get_household_lists'}}])
    assert result['tool_calls'][0]['id'] == 'call-1'
    assert result['tool_calls'][0]['function']['arguments'] == '{}'
    assert requests[0]['stream'] is False
    assert requests[0]['tools'][0]['function']['name'] == 'get_household_lists'


def test_openai_stream_accepts_standard_sse_data_and_empty_usage_event(monkeypatch):
    class StreamingResponse(FakeResponse):
        def __iter__(self):
            return iter([
                b': keepalive\n',
                b'data:{"choices":[{"delta":{"content":"Ready"}}]}\n',
                b'data:{"choices":[],"usage":{"total_tokens":2}}\n',
                b'data:[DONE]\n',
            ])

    monkeypatch.setattr('homehub.chat._open_remote',
                        lambda request, timeout: StreamingResponse(None))
    result = OpenAICompatibleProvider('http://192.168.1.7:8000').complete(
        'agent', [{'role': 'user', 'content': 'Hi'}], on_chunk=lambda text: None)
    assert result == 'Ready'


def test_remote_http_client_refuses_cross_host_redirect():
    from homehub.chat import _NoRedirectHandler
    assert _NoRedirectHandler().redirect_request(
        None, None, 302, 'Found', {}, 'http://unexpected.example.com'
    ) is None
