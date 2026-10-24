"""Small synchronous client for the loopback Ollama API."""
import ipaddress
import json
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


OLLAMA_DEFAULT_URL = 'http://127.0.0.1:11434'
OLLAMA_REQUEST_TIMEOUT_SECONDS = 2


class ChatProviderError(Exception):
    """Raised when the local Ollama service is unavailable or malformed."""


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
