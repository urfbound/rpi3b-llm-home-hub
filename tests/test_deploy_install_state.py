import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


def _bash_path(path):
    value = str(path)
    if os.name == 'nt':
        drive, remainder = os.path.splitdrive(value)
        return f'/{drive[0].lower()}{remainder.replace(chr(92), "/")}'
    return value


def _bash():
    bash = shutil.which('bash')
    if bash is None and os.name == 'nt':
        candidate = Path(r'C:\Program Files\Git\bin\bash.exe')
        bash = str(candidate) if candidate.exists() else None
    if bash is None:
        pytest.skip('Bash is required to exercise deployment helpers')
    return bash


def test_repeat_install_preserves_environment_and_data(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    root = tmp_path / 'install'
    source = tmp_path / 'runtime'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
root={shlex.quote(_bash_path(root))}
source_tree={shlex.quote(_bash_path(source))}
mkdir -p "$root/homehub" "$root/data" "$root/backups" "$root/etc" "$source_tree"
printf 'original secret\\n' > "$root/etc/homehub.env"
printf 'household database\\n' > "$root/data/homehub.sqlite3"
printf 'existing backup\\n' > "$root/backups/backup.sqlite3"
printf 'version one\\n' > "$source_tree/version.txt"
before_umask=$(umask)
homehub_ensure_environment_file "$root/etc/homehub.env" "$root/data" \
  'replacement secret' homehub direct
homehub_replace_runtime_tree "$source_tree" "$root/homehub" direct
[[ $(cat "$root/homehub/version.txt") == 'version one' ]]
[[ $(cat "$root/etc/homehub.env") == 'original secret' ]]
[[ $(cat "$root/data/homehub.sqlite3") == 'household database' ]]
[[ $(cat "$root/backups/backup.sqlite3") == 'existing backup' ]]
[[ $(umask) == "$before_umask" ]]
printf 'version two\\n' > "$source_tree/version.txt"
homehub_ensure_environment_file "$root/etc/homehub.env" "$root/data" \
  'third secret' homehub direct
homehub_replace_runtime_tree "$source_tree" "$root/homehub" direct
[[ $(cat "$root/homehub/version.txt") == 'version two' ]]
[[ $(cat "$root/etc/homehub.env") == 'original secret' ]]
[[ $(cat "$root/data/homehub.sqlite3") == 'household database' ]]
[[ $(cat "$root/backups/backup.sqlite3") == 'existing backup' ]]
[[ $(umask) == "$before_umask" ]]
'''

    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr

def test_installer_parser_accepts_noninteractive_admin_options():
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
homehub_parse_install_args --no-prompt --admin-username household-admin --admin-password-stdin --install-ollama --install-caddy
[[ $HOMEHUB_NO_PROMPT == 1 ]]
[[ $HOMEHUB_ADMIN_USERNAME == household-admin ]]
[[ $HOMEHUB_ADMIN_PASSWORD_STDIN == 1 ]]
[[ $HOMEHUB_INSTALL_OLLAMA == 1 ]]
[[ $HOMEHUB_INSTALL_CADDY == 1 ]]
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_no_rss_flag_disables_scheduled_refresh_by_default_it_remains_enabled():
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
homehub_parse_install_args
[[ $HOMEHUB_NO_RSS == 0 ]]
homehub_parse_install_args --no-rss
[[ $HOMEHUB_NO_RSS == 1 ]]
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_interactive_installer_explains_optional_packages_before_begin_prompt():
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
homehub_parse_install_args
if homehub_prompt_install_choices < <(printf 'n\\n'); then exit 1; fi
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert '--install-ollama' in result.stdout
    assert '--install-caddy' in result.stdout
    assert result.stdout.index('--install-ollama') < result.stdout.index('Begin Home Hub installation?')


def test_interactive_installer_asks_about_scheduled_rss_refresh():
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
homehub_parse_install_args
homehub_prompt_install_choices < <(printf '\\nN\\n')
[[ $HOMEHUB_NO_RSS == 1 ]]
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert 'Enable scheduled RSS feed refresh?' in result.stdout


def test_rss_timer_helper_enables_or_disables_scheduled_refresh(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    log = tmp_path / 'sudo.log'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    sudo = fake_bin / 'sudo'
    sudo.write_text(f'''#!/bin/sh
printf '%s\\n' "$*" >> {shlex.quote(_bash_path(log))}
''')
    sudo.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
homehub_set_rss_refresh_timer 0
homehub_set_rss_refresh_timer 1
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        'systemctl enable --now homehub-rss-refresh.timer',
        'systemctl start homehub-rss-refresh.service',
        'systemctl disable --now homehub-rss-refresh.timer',
    ]


def test_installer_ollama_option_defaults_off_and_is_shown_in_help():
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
homehub_parse_install_args
[[ $HOMEHUB_INSTALL_OLLAMA == 0 ]]
[[ $HOMEHUB_INSTALL_CADDY == 0 ]]
homehub_parse_install_args --help
[[ $HOMEHUB_SHOW_HELP == 1 ]]
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert '--install-ollama' in result.stdout
    assert '--install-caddy' in result.stdout
    help_text = ' '.join(result.stdout.split())
    assert 'Linux x86_64 or ARM64' in help_text
    assert 'No model is selected or downloaded' in help_text
    assert 'already installed' in help_text
    assert 'does not configure Home Hub' in help_text
    assert 'local DNS' in help_text
    assert 'installation or service' in help_text


def test_caddy_installer_skips_existing_install_without_system_changes(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    caddy = fake_bin / 'caddy'
    caddy.write_text('#!/bin/sh\nexit 0\n')
    caddy.chmod(0o755)
    marker = tmp_path / 'sudo-called.txt'
    sudo = fake_bin / 'sudo'
    sudo.write_text(f'#!/bin/sh\nprintf called > {shlex.quote(_bash_path(marker))}\n')
    sudo.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
uname() {{ printf 'FreeBSD\\n'; }}
homehub_install_caddy_linux
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert 'already installed' in result.stdout
    assert not marker.exists()


def test_caddy_installer_skips_existing_systemd_service_outside_path(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    log = tmp_path / 'commands.txt'
    for name, body in {
        'systemctl': '#!/bin/sh\nexit 0\n',
        'apt-get': '#!/bin/sh\nexit 99\n',
    }.items():
        executable = fake_bin / name
        executable.write_text(body)
        executable.chmod(0o755)
    sudo = fake_bin / 'sudo'
    sudo.write_text(f'''#!/bin/sh
printf 'sudo %s\\n' "$*" >> {shlex.quote(_bash_path(log))}
if [ "$1" = systemctl ] && [ "$2" = cat ] && [ "$3" = caddy.service ]; then exit 0; fi
exit 99
''')
    sudo.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
command() {{ if [[ $1 == -v && $2 == caddy ]]; then return 1; fi; builtin command "$@"; }}
uname() {{ printf 'FreeBSD\\n'; }}
homehub_install_caddy_linux
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert 'service already exists' in result.stdout
    assert log.read_text().strip() == 'sudo systemctl cat caddy.service'


def test_caddy_installer_uses_official_package_repo_and_does_not_write_caddyfile(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    log = tmp_path / 'commands.txt'
    curl = fake_bin / 'curl'
    curl.write_text(f'''#!/bin/sh
printf 'curl %s\\n' "$*" >> {shlex.quote(_bash_path(log))}
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then printf test-data > "$2"; exit 0; fi
  shift
done
exit 2
''')
    curl.chmod(0o755)
    gpg = fake_bin / 'gpg'
    gpg.write_text(f'''#!/bin/sh
printf 'gpg %s\\n' "$*" >> {shlex.quote(_bash_path(log))}
''')
    gpg.chmod(0o755)
    apt_get = fake_bin / 'apt-get'
    apt_get.write_text('#!/bin/sh\nexit 0\n')
    apt_get.chmod(0o755)
    systemctl = fake_bin / 'systemctl'
    systemctl.write_text(f'''#!/bin/sh
printf 'systemctl %s\\n' "$*" >> {shlex.quote(_bash_path(log))}
''')
    systemctl.chmod(0o755)
    sudo = fake_bin / 'sudo'
    sudo.write_text(f'''#!/bin/sh
printf 'sudo %s\\n' "$*" >> {shlex.quote(_bash_path(log))}
if [ "$1" = systemctl ] && [ "$2" = cat ] && [ "$3" = caddy.service ]; then exit 1; fi
''')
    sudo.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
command() {{ if [[ $1 == -v && $2 == caddy ]]; then return 1; fi; builtin command "$@"; }}
uname() {{ printf 'Linux\\n'; }}
homehub_install_caddy_linux
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' in commands
    assert 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' in commands
    assert 'apt-get update' in commands
    assert 'apt-get install --yes caddy' in commands
    assert 'sudo systemctl enable --now caddy' in commands
    assert '/etc/caddy/Caddyfile' not in commands


def test_ollama_installer_supports_official_linux_architectures():
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
homehub_ollama_platform_supported Linux x86_64
homehub_ollama_platform_supported Linux aarch64
homehub_ollama_platform_supported Linux arm64
! homehub_ollama_platform_supported Linux armv7l
! homehub_ollama_platform_supported Windows x86_64
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_ollama_installer_fetches_official_script_without_pulling_models(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    marker = tmp_path / 'installer-ran.txt'
    ollama_calls = tmp_path / 'ollama-calls.txt'
    systemctl_calls = tmp_path / 'systemctl-calls.txt'
    curl = fake_bin / 'curl'
    curl.write_text('''#!/bin/sh
set -eu
[ "$1" = "-fsSL" ]
[ "$2" = "https://ollama.com/install.sh" ]
[ "$3" = "-o" ]
cat > "$4" <<'INSTALLER'
#!/bin/sh
printf 'official installer ran' > "$OLLAMA_INSTALL_TEST_MARKER"
cat > "$OLLAMA_FAKE_BIN/ollama" <<'OLLAMA'
#!/bin/sh
printf '%s\\n' "$*" >> "$OLLAMA_TEST_CALL_LOG"
OLLAMA
chmod +x "$OLLAMA_FAKE_BIN/ollama"
INSTALLER
''')
    curl.chmod(0o755)
    systemctl = fake_bin / 'systemctl'
    systemctl.write_text('''#!/bin/sh
if [ "$1" = "cat" ] && [ "$2" = "ollama.service" ]; then exit 0; fi
printf '%s\\n' "$*" >> "$OLLAMA_SYSTEMCTL_CALL_LOG"
''')
    systemctl.chmod(0o755)
    sudo = fake_bin / 'sudo'
    sudo.write_text('''#!/bin/sh
exec "$@"
''')
    sudo.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
export OLLAMA_INSTALL_TEST_MARKER={shlex.quote(_bash_path(marker))}
export OLLAMA_TEST_CALL_LOG={shlex.quote(_bash_path(ollama_calls))}
export OLLAMA_FAKE_BIN={shlex.quote(_bash_path(fake_bin))}
export OLLAMA_SYSTEMCTL_CALL_LOG={shlex.quote(_bash_path(systemctl_calls))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
uname() {{ case $1 in -s) printf 'Linux\\n' ;; -m) printf 'x86_64\\n' ;; esac; }}
command() {{ if [[ $1 == -v && $2 == ollama ]]; then return 1; fi; builtin command "$@"; }}
homehub_install_ollama_linux
'''

    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == 'official installer ran'
    assert not ollama_calls.exists()
    assert systemctl_calls.read_text().strip() == 'disable --now ollama.service'


def test_ollama_installer_rejects_unsupported_architecture_before_download(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    download_marker = tmp_path / 'download-attempted.txt'
    curl = fake_bin / 'curl'
    curl.write_text(f'''#!/bin/sh
printf called > {shlex.quote(_bash_path(download_marker))}
''')
    curl.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
command() {{ if [[ $1 == -v && $2 == ollama ]]; then return 1; fi; builtin command "$@"; }}
uname() {{ case $1 in -s) printf 'Linux\\n' ;; -m) printf 'armv7l\\n' ;; esac; }}
if homehub_install_ollama_linux; then exit 1; fi
'''

    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert 'Linux/armv7l' in result.stderr
    assert not download_marker.exists()


def test_ollama_installer_preserves_existing_installation_and_service(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    curl_marker = tmp_path / 'curl-called.txt'
    service_marker = tmp_path / 'systemctl-called.txt'
    for name, marker in (('curl', curl_marker), ('systemctl', service_marker)):
        executable = fake_bin / name
        executable.write_text(f'#!/bin/sh\nprintf called > {shlex.quote(_bash_path(marker))}\n')
        executable.chmod(0o755)
    ollama = fake_bin / 'ollama'
    ollama.write_text('#!/bin/sh\nexit 0\n')
    ollama.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
uname() {{ case $1 in -s) printf 'Linux\n' ;; -m) printf 'armv7l\n' ;; esac; }}
homehub_install_ollama_linux
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert 'already installed' in result.stdout
    assert not curl_marker.exists()
    assert not service_marker.exists()


def test_ollama_download_failure_has_actionable_context(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    curl = fake_bin / 'curl'
    curl.write_text("#!/bin/sh\nprintf 'network unavailable\\n' >&2\nexit 7\n")
    curl.chmod(0o755)
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
PATH={shlex.quote(_bash_path(fake_bin))}:$PATH
command() {{ if [[ $1 == -v && $2 == ollama ]]; then return 1; fi; builtin command "$@"; }}
uname() {{ case $1 in -s) printf 'Linux\n' ;; -m) printf 'aarch64\n' ;; esac; }}
if homehub_install_ollama_linux; then exit 1; fi
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert 'network unavailable' in result.stderr
    assert 'Could not download the official Ollama installer' in result.stderr


def test_legacy_backup_cron_cleanup_is_repeatable_and_preserves_other_jobs():
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
crontab=''' + "'''" + '''\
0 4 * * * /opt/homehub/.venv/bin/homehub-backup
15 2 * * * /usr/local/bin/other-backup
*/10 * * * * /opt/homehub/.venv/bin/python -m homehub.backup_scheduler
30 5 * * * /usr/local/bin/other-maintenance
''' + "'''" + '''
once=$(printf '%s' "$crontab" | homehub_filter_legacy_backup_crontab)
twice=$(printf '%s' "$once" | homehub_filter_legacy_backup_crontab)
[[ "$once" == "$twice" ]]
[[ "$once" == $'15 2 * * * /usr/local/bin/other-backup\\n30 5 * * * /usr/local/bin/other-maintenance' ]]
'''
    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name == 'nt', reason='Windows does not preserve POSIX modes')
def test_new_environment_file_has_private_mode(tmp_path):
    helper = Path(__file__).parents[1] / 'deploy' / 'install_helpers.sh'
    env_file = tmp_path / 'homehub.env'
    script = f'''\
set -euo pipefail
source {shlex.quote(str(helper))}
homehub_ensure_environment_file {shlex.quote(str(env_file))} \
  {shlex.quote(str(tmp_path))} secret homehub direct
[[ $(stat -c %a {shlex.quote(str(env_file))}) == 640 ]]
'''

    result = subprocess.run([_bash(), '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
