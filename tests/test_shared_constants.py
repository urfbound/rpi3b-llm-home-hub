from homehub.constants import (
    APP_DEFAULT_HOST,
    APP_DEFAULT_PORT,
    LATEST_SCHEMA_VERSION,
    OLLAMA_DEFAULT_HOST,
    OLLAMA_DEFAULT_PORT,
    RECIPE_MAXIMUM,
    USER_MAXIMUM,
)
from homehub.app import SCHEMA_VERSION


def test_latest_schema_version_is_the_app_version():
    assert SCHEMA_VERSION == LATEST_SCHEMA_VERSION


def test_system_ports_are_named_and_match_deployment_configuration():
    from pathlib import Path

    caddy = Path('deploy/Caddyfile.example').read_text()
    readme = Path('README.md').read_text()
    assert f'{APP_DEFAULT_HOST}:{APP_DEFAULT_PORT}' in caddy
    assert f'http://{APP_DEFAULT_HOST}:{APP_DEFAULT_PORT}' in readme
    assert APP_DEFAULT_HOST
    assert OLLAMA_DEFAULT_HOST
    assert OLLAMA_DEFAULT_PORT > 0


def test_named_limits_match_application_policy():
    assert USER_MAXIMUM > 0
    assert RECIPE_MAXIMUM > 0
