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


def test_legacy_backup_cron_cleanup_is_repeatable_and_preserves_other_jobs(tmp_path):
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
