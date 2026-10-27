import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
HELPER = ROOT / 'deploy' / 'install_helpers.sh'
ENABLE_HTTPS = ROOT / 'deploy' / 'enable_https.sh'


def _bash():
    bash = shutil.which('bash')
    if bash is None and os.name == 'nt':
        candidate = Path(r'C:\Program Files\Git\bin\bash.exe')
        bash = str(candidate) if candidate.exists() else None
    if bash is None:
        pytest.skip('Bash is required to exercise deployment helpers')
    return bash


def test_https_helpers_render_only_valid_hosts_and_loopback_upstream():
    script = f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_render_caddyfile homehub.home.arpa 8080
'''
    rendered = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert rendered.returncode == 0
    assert 'homehub.home.arpa {' in rendered.stdout
    assert 'tls internal' in rendered.stdout
    assert 'reverse_proxy 127.0.0.1:8080' in rendered.stdout

    for invalid_args in (
        "'homehub.home.arpa; reverse_proxy attacker.test' 8080",
        "homehub..home.arpa 8080",
        "homehub.home.arpa 70000",
    ):
        invalid = subprocess.run([_bash(), '-c', f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_render_caddyfile {invalid_args}
'''], capture_output=True, text=True)
        assert invalid.returncode != 0


def test_https_environment_update_preserves_secrets_and_unrelated_settings(tmp_path):
    env_file = tmp_path / 'homehub.env'
    env_file.write_text(
        'HOMEHUB_SECRET_KEY=keep-this-secret\n'
        'HOMEHUB_BIND=0.0.0.0\n'
        'HOMEHUB_BIND=localhost\n'
        'HOMEHUB_COOKIE_SECURE=0\n'
        'CUSTOM_SETTING=keep-this-too\n',
        encoding='utf-8',
    )
    script = f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_update_environment_file {shlex.quote(str(env_file))} homehub direct \\
  HOMEHUB_BIND=127.0.0.1 HOMEHUB_COOKIE_SECURE=1
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    lines = env_file.read_text(encoding='utf-8').splitlines()
    assert lines.count('HOMEHUB_BIND=127.0.0.1') == 1
    assert lines.count('HOMEHUB_COOKIE_SECURE=1') == 1
    assert 'HOMEHUB_SECRET_KEY=keep-this-secret' in lines
    assert 'CUSTOM_SETTING=keep-this-too' in lines
    assert not any(line.startswith('HOMEHUB_BIND=') and line != 'HOMEHUB_BIND=127.0.0.1'
                   for line in lines)

    invalid = subprocess.run([_bash(), '-c', f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_update_environment_file {shlex.quote(str(env_file))} homehub direct \\
  HOMEHUB_SECRET_KEY=overwritten
'''], capture_output=True, text=True)
    assert invalid.returncode != 0
    assert 'HOMEHUB_SECRET_KEY=keep-this-secret' in env_file.read_text(encoding='utf-8')


def test_existing_caddyfile_requires_explicit_replacement_opt_in(tmp_path):
    caddyfile = tmp_path / 'Caddyfile'
    original = 'other-service.home.arpa { reverse_proxy 127.0.0.1:9000 }\n'
    caddyfile.write_text(original, encoding='utf-8')

    refused = subprocess.run([_bash(), '-c', f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_require_caddyfile_replacement {shlex.quote(str(caddyfile))} 0
'''], capture_output=True, text=True)
    assert refused.returncode != 0
    assert caddyfile.read_text(encoding='utf-8') == original

    opted_in = subprocess.run([_bash(), '-c', f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_require_caddyfile_replacement {shlex.quote(str(caddyfile))} 1
'''], capture_output=True, text=True)
    assert opted_in.returncode == 0, opted_in.stderr
    assert caddyfile.read_text(encoding='utf-8') == original


def test_caddy_activation_failure_restores_prior_configuration(tmp_path):
    caddyfile = tmp_path / 'Caddyfile'
    backup = tmp_path / 'Caddyfile.backup'
    caddyfile.write_text('homehub.home.arpa { tls internal }\n', encoding='utf-8')
    previous_config = 'other-service.home.arpa { reverse_proxy 127.0.0.1:9000 }\n'
    backup.write_text(previous_config, encoding='utf-8')

    result = subprocess.run([_bash(), '-c', f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_restore_caddyfile_after_activation_failure \\
  {shlex.quote(str(caddyfile))} {shlex.quote(str(backup))} 1 direct
'''], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert caddyfile.read_text(encoding='utf-8') == previous_config


def test_caddy_activation_failure_removes_config_when_none_existed(tmp_path):
    caddyfile = tmp_path / 'Caddyfile'
    caddyfile.write_text('homehub.home.arpa { tls internal }\n', encoding='utf-8')

    result = subprocess.run([_bash(), '-c', f'''\
set -euo pipefail
source {shlex.quote(str(HELPER))}
homehub_restore_caddyfile_after_activation_failure \\
  {shlex.quote(str(caddyfile))} '' 0 direct
'''], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert not caddyfile.exists()


def test_https_setup_script_is_opt_in_and_keeps_service_upstream_private():
    source = ENABLE_HTTPS.read_text(encoding='utf-8')
    assert 'HOMEHUB_BIND=127.0.0.1' in source
    assert 'HOMEHUB_COOKIE_SECURE=1' in source
    assert 'systemctl restart homehub.service' in source
    assert 'systemctl enable caddy' in source
    assert 'homehub_restore_caddyfile_after_activation_failure' in source
    assert 'systemctl restart caddy' in source
    assert 'HOMEHUB_DB' not in source
    assert 'router' not in source.lower()
    assert '--replace-existing-caddyfile' in source
