"""Small clients for local Ollama and trusted remote agent APIs."""
import ipaddress
import json
import time
from threading import Condition, Thread
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import (HTTPRedirectHandler, ProxyHandler, Request,
                            build_opener, urlopen)

from .constants import CHAT_CONTEXT_TOKENS_DEFAULT


OLLAMA_DEFAULT_URL = 'http://127.0.0.1:11434'
OLLAMA_REQUEST_TIMEOUT_SECONDS = 2
OLLAMA_CHAT_TIMEOUT_SECONDS = 900
CHAT_RESPONSE_BYTES_MAXIMUM = 1024 * 1024


class ChatProviderError(Exception):
    """Raised when a configured model service is unavailable or malformed."""


class ChatQueueFull(Exception):
    """Raised when the configured waiting-request limit has been reached."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _open_remote(request, timeout):
    """Keep prompts and bearer tokens at the configured endpoint."""
    return build_opener(ProxyHandler({}), _NoRedirectHandler()).open(request, timeout=timeout)


def validate_chat_endpoint(base_url, *, local_only=False):
    """Accept an origin with an explicit port; require TLS off the private LAN."""
    if not isinstance(base_url, str) or len(base_url) > 255:
        raise ChatProviderError('Enter a valid agent API address and port.')
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ''
        port = parsed.port
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        try:
            parsed = urlsplit(base_url)
            host = parsed.hostname or ''
            port = parsed.port
        except (ValueError, TypeError) as exc:
            raise ChatProviderError('Enter a valid agent API address and port.') from exc
        address = None
    if (parsed.scheme not in ('http', 'https') or not host or not port
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ChatProviderError('Enter an HTTP or HTTPS agent API origin with a port and no credentials or path.')
    is_loopback = (address.is_loopback if address else host.lower() == 'localhost')
    if local_only and (parsed.scheme != 'http' or not is_loopback):
        raise ChatProviderError('Local Ollama must use an HTTP loopback address.')
    lan_name = host.lower().endswith(('.home.arpa', '.lan', '.local', '.internal'))
    if parsed.scheme == 'http' and not (is_loopback or lan_name or
                                        address is not None and address.is_private):
        raise ChatProviderError('Use HTTPS for an agent API outside the private LAN.')
    return f'{parsed.scheme}://{parsed.netloc}'


def list_ollama_models(base_url=OLLAMA_DEFAULT_URL, *, allow_remote=False):
    """Return available Ollama models from a validated local or remote node."""
    url = validate_chat_endpoint(base_url, local_only=not allow_remote) + '/api/tags'
    try:
        opener = _open_remote if allow_remote else urlopen
        with opener(url, timeout=OLLAMA_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(1024 * 1024))
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        raise ChatProviderError('Could not reach Ollama.') from exc
    models = payload.get('models') if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ChatProviderError('Ollama returned an invalid model list.')
    names = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get('name'), str):
            name = model['name'].strip()
            if name and len(name) <= 128 and name not in names:
                names.append(name)
    return sorted(names, key=str.casefold)


def list_openai_models(base_url, api_key=''):
    """Read model IDs from an OpenAI-compatible agent node."""
    request = Request(validate_chat_endpoint(base_url) + '/v1/models',
                      headers=_api_headers(api_key))
    try:
        with _open_remote(request, timeout=OLLAMA_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(CHAT_RESPONSE_BYTES_MAXIMUM))
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        raise ChatProviderError('Could not reach the agent model API.') from exc
    models = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ChatProviderError('The agent API returned an invalid model list.')
    names = {model['id'].strip() for model in models if isinstance(model, dict)
             and isinstance(model.get('id'), str) and 0 < len(model['id'].strip()) <= 128}
    return sorted(names, key=str.casefold)


def _api_headers(api_key=''):
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    return headers


def _stream_messages(response, *, kind, on_chunk=None):
    parts = []
    used = 0
    finished = False
    last_notification = time.monotonic()
    last_preview = ''
    for raw in response:
        used += len(raw)
        if used > CHAT_RESPONSE_BYTES_MAXIMUM:
            raise ChatProviderError('The model response exceeded the size limit.')
        line = raw.strip()
        if kind == 'openai':
            if not line.startswith(b'data:'):
                continue
            line = line[5:].strip()
            if line == b'[DONE]':
                finished = True
                break
        elif not line:
            continue
        try:
            item = json.loads(line)
            if kind == 'openai':
                choices = item['choices']
                if not choices:
                    continue
                delta = choices[0].get('delta') or {}
                chunk = delta.get('content') or ''
            else:
                chunk = item.get('message', {}).get('content') or ''
                finished = bool(item.get('done'))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ChatProviderError('The model returned an invalid stream.') from exc
        if not isinstance(chunk, str):
            raise ChatProviderError('The model returned invalid text.')
        if chunk:
            parts.append(chunk)
            if on_chunk and time.monotonic() - last_notification >= 0.2:
                last_preview = ''.join(parts)[:65536]
                on_chunk(last_preview)
                last_notification = time.monotonic()
        if finished:
            break
    if not finished:
        raise ChatProviderError('The model stream ended early.')
    answer = ''.join(parts)
    if on_chunk and answer[:65536] != last_preview:
        on_chunk(answer[:65536])
    return answer.strip()


class ChatProvider:
    """Interface for a model provider; remote implementations stay disabled."""

    def complete(self, model, messages):
        raise NotImplementedError


class OllamaProvider(ChatProvider):
    """Native Ollama chat completion against a configured node."""

    def __init__(self, base_url=OLLAMA_DEFAULT_URL, *, allow_remote=False):
        self.base_url = validate_chat_endpoint(base_url, local_only=not allow_remote)
        self.allow_remote = allow_remote

    def _request(self, model, messages, tools=None, on_chunk=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise ChatProviderError('Choose a valid installed model.')
        payload = {
            'model': model,
            'messages': messages,
            'stream': on_chunk is not None,
        }
        if not self.allow_remote:
            payload['keep_alive'] = 0
            payload['options'] = {'num_ctx': CHAT_CONTEXT_TOKENS_DEFAULT}
        if tools is not None:
            payload['tools'] = tools
        request = Request(
            self.base_url + '/api/chat', data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'}, method='POST',
        )
        try:
            opener = _open_remote if self.allow_remote else urlopen
            with opener(request, timeout=OLLAMA_CHAT_TIMEOUT_SECONDS) as response:
                if on_chunk is not None:
                    return {'content': _stream_messages(response, kind='ollama', on_chunk=on_chunk),
                            'tool_calls': []}
                result = json.loads(response.read(CHAT_RESPONSE_BYTES_MAXIMUM))
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            raise ChatProviderError('Ollama could not complete the request.') from exc
        message = result.get('message') if isinstance(result, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            raise ChatProviderError('Ollama returned an invalid chat response.')
        tool_calls = message.get('tool_calls', [])
        if not isinstance(tool_calls, list):
            raise ChatProviderError('Ollama returned invalid tool calls.')
        return message

    def complete(self, model, messages, *, on_chunk=None):
        return self._request(model, messages, on_chunk=on_chunk)['content'].strip()

    def complete_message(self, model, messages, tools=None):
        """Return a validated assistant message, optionally with tool calls."""
        return self._request(model, messages, tools)


class OpenAICompatibleProvider(ChatProvider):
    """OpenAI-compatible chat API on a trusted agent node."""

    def __init__(self, base_url, *, api_key=''):
        self.base_url = validate_chat_endpoint(base_url)
        self.api_key = api_key

    def _request(self, model, messages, tools=None, on_chunk=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise ChatProviderError('Choose a valid available model.')
        payload = {'model': model, 'messages': messages, 'stream': on_chunk is not None}
        if tools is not None:
            payload['tools'] = tools
        request = Request(self.base_url + '/v1/chat/completions',
                          data=json.dumps(payload).encode(),
                          headers=_api_headers(self.api_key), method='POST')
        try:
            with _open_remote(request, timeout=OLLAMA_CHAT_TIMEOUT_SECONDS) as response:
                if on_chunk is not None:
                    return {'content': _stream_messages(response, kind='openai', on_chunk=on_chunk),
                            'tool_calls': []}
                result = json.loads(response.read(CHAT_RESPONSE_BYTES_MAXIMUM))
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            raise ChatProviderError('The agent API could not complete the request.') from exc
        try:
            message = result['choices'][0]['message']
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatProviderError('The agent API returned an invalid chat response.') from exc
        if (not isinstance(message, dict)
                or not isinstance(message.get('content'), (str, type(None)))
                or not isinstance(message.get('tool_calls', []), list)):
            raise ChatProviderError('The agent API returned an invalid chat response.')
        return {'content': message.get('content') or '',
                'tool_calls': message.get('tool_calls', [])}

    def complete(self, model, messages, *, on_chunk=None):
        return self._request(model, messages, on_chunk=on_chunk)['content'].strip()

    def complete_message(self, model, messages, tools=None):
        return self._request(model, messages, tools)


class ChatRequestQueue:
    """A lazy, single-worker in-process queue with admin-first FIFO ordering."""

    def __init__(self, processor):
        self._processor = processor
        self._condition = Condition()
        self._pending = []
        self._active = None
        self._tracked = {}
        self._completed = []
        self._sequence = 0
        self._worker = None

    def enqueue(self, job, *, max_waiting):
        with self._condition:
            if len(self._pending) >= max_waiting:
                raise ChatQueueFull
            self._sequence += 1
            queued = dict(job, status='queued', sequence=self._sequence, error='')
            job_id = queued['id']
            self._tracked[job_id] = queued
            self._pending.append(queued)
            if self._worker is None or not self._worker.is_alive():
                self._worker = Thread(target=self._run, name='homehub-chat-worker', daemon=True)
                self._worker.start()
            self._condition.notify_all()
            return job_id

    def _ordered_pending(self):
        return sorted(self._pending, key=lambda job: (not job['is_admin'], job['sequence']))

    def _run(self):
        while True:
            with self._condition:
                while not self._pending:
                    self._active = None
                    self._worker = None
                    self._condition.notify_all()
                    return
                job = self._ordered_pending()[0]
                self._pending.remove(job)
                self._active = job
                job['status'] = 'running'
                self._condition.notify_all()
            try:
                job['result'] = self._processor(job)
                job['status'] = 'complete'
            except Exception as exc:  # Keep one failed model call from stopping the queue.
                job['status'] = 'failed'
                job['error'] = str(exc)[:256] or 'The model request failed.'
            finally:
                with self._condition:
                    self._active = None
                    job.pop('partial_text', None)
                    self._completed.append(job['id'])
                    while len(self._completed) > 256:
                        expired_id = self._completed.pop(0)
                        self._tracked.pop(expired_id, None)
                    self._condition.notify_all()

    def status(self, job_id):
        with self._condition:
            job = self._tracked.get(job_id)
            if job is None:
                return None
            position = None
            if job['status'] == 'running':
                position = 0
            elif job['status'] == 'queued':
                position = next((index for index, queued in enumerate(
                    self._ordered_pending(), start=1) if queued['id'] == job_id), None)
            return {
                'id': job['id'], 'owner_id': job.get('owner_id'),
                'conversation_id': job.get('conversation_id'),
                'status': job['status'], 'queue_position': position,
                'result': job.get('result'), 'error': job.get('error', ''),
                'partial_text': job.get('partial_text', ''),
            }

    def update_partial(self, job_id, text):
        with self._condition:
            job = self._tracked.get(job_id)
            if job is not None and job['status'] == 'running':
                job['partial_text'] = text[:65536]

    def position(self, job_id):
        details = self.status(job_id)
        return details['queue_position'] if details else None

    def wait_idle(self, timeout):
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._pending and self._active is None, timeout=timeout)
