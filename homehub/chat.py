"""Small synchronous client for the loopback Ollama API."""
import ipaddress
import json
from threading import Condition, Thread
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .constants import CHAT_CONTEXT_TOKENS_DEFAULT


OLLAMA_DEFAULT_URL = 'http://127.0.0.1:11434'
OLLAMA_REQUEST_TIMEOUT_SECONDS = 2
OLLAMA_CHAT_TIMEOUT_SECONDS = 900


class ChatProviderError(Exception):
    """Raised when the local Ollama service is unavailable or malformed."""


class ChatQueueFull(Exception):
    """Raised when the configured waiting-request limit has been reached."""


def _validated_base_url(base_url):
    try:
        parsed = urlsplit(base_url)
        address = ipaddress.ip_address(parsed.hostname or '')
        is_loopback = address.is_loopback
    except (ValueError, TypeError):
        parsed = urlsplit(base_url)
        is_loopback = (parsed.hostname or '').lower() == 'localhost'
    if parsed.scheme != 'http' or not is_loopback or parsed.username or parsed.password:
        raise ChatProviderError('Ollama must use an HTTP loopback address.')
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ChatProviderError('Ollama base URL must be an origin only.')
    return f'{parsed.scheme}://{parsed.netloc}'


def list_ollama_models(base_url=OLLAMA_DEFAULT_URL):
    """Return installed model names without allowing a remote Ollama URL."""
    url = _validated_base_url(base_url) + '/api/tags'
    try:
        with urlopen(url, timeout=OLLAMA_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(1024 * 1024))
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        raise ChatProviderError('Could not reach local Ollama.') from exc
    models = payload.get('models') if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ChatProviderError('Local Ollama returned an invalid model list.')
    names = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get('name'), str):
            name = model['name'].strip()
            if name and len(name) <= 128 and name not in names:
                names.append(name)
    return sorted(names, key=str.casefold)


class ChatProvider:
    """Interface for a model provider; remote implementations stay disabled."""

    def complete(self, model, messages):
        raise NotImplementedError


class OllamaProvider(ChatProvider):
    """Non-streaming chat completion against the loopback Ollama API."""

    def __init__(self, base_url=OLLAMA_DEFAULT_URL):
        self.base_url = _validated_base_url(base_url)

    def _request(self, model, messages, tools=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise ChatProviderError('Choose a valid installed model.')
        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'keep_alive': 0,
            'options': {'num_ctx': CHAT_CONTEXT_TOKENS_DEFAULT},
        }
        if tools is not None:
            payload['tools'] = tools
        request = Request(
            self.base_url + '/api/chat', data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'}, method='POST',
        )
        try:
            with urlopen(request, timeout=OLLAMA_CHAT_TIMEOUT_SECONDS) as response:
                result = json.loads(response.read(1024 * 1024))
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            raise ChatProviderError('Local Ollama could not complete the request.') from exc
        message = result.get('message') if isinstance(result, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            raise ChatProviderError('Local Ollama returned an invalid chat response.')
        tool_calls = message.get('tool_calls', [])
        if not isinstance(tool_calls, list):
            raise ChatProviderError('Local Ollama returned invalid tool calls.')
        return message

    def complete(self, model, messages):
        return self._request(model, messages)['content'].strip()

    def complete_message(self, model, messages, tools=None):
        """Return a validated assistant message, optionally with tool calls."""
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
                job['error'] = str(exc)[:256] or 'The local model request failed.'
            finally:
                with self._condition:
                    self._active = None
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
            }

    def position(self, job_id):
        details = self.status(job_id)
        return details['queue_position'] if details else None

    def wait_idle(self, timeout):
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._pending and self._active is None, timeout=timeout)
