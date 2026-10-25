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


def test_uninstall_removes_only_installed_files_and_can_be_repeated(tmp_path):
    bash = shutil.which('bash')
    if bash is None and os.name == 'nt':
        candidate = Path(r'C:\Program Files\Git\bin\bash.exe')
        bash = str(candidate) if candidate.exists() else None
    if bash is None:
        pytest.skip('Bash is required to exercise uninstall helpers')

    helper = Path(__file__).parents[1] / 'deploy' / 'uninstall_helpers.sh'
    repository = tmp_path / 'repository'
    installed = tmp_path / 'opt' / 'homehub'
    config = tmp_path / 'etc' / 'homehub'
    data = tmp_path / 'var' / 'lib' / 'homehub'
    script = f'''\
set -euo pipefail
source {shlex.quote(_bash_path(helper))}
repository={shlex.quote(_bash_path(repository))}
installed={shlex.quote(_bash_path(installed))}
config={shlex.quote(_bash_path(config))}
data={shlex.quote(_bash_path(data))}
mkdir -p "$repository/.git" "$installed/.venv" "$config" "$data/backups" "$data/calendar-archives"
printf 'checkout\\n' > "$repository/.git/keep"
printf 'application\\n' > "$installed/app.py"
printf 'secret\\n' > "$config/homehub.env"
printf 'unrelated\\n' > "$config/other.conf"
printf 'database\\n' > "$data/homehub.sqlite3"
printf 'backup\\n' > "$data/backups/homehub-backup.sqlite3"
printf 'archive\\n' > "$data/calendar-archives/homehub-calendar-2026.ics"
homehub_remove_installed_files "$installed" "$config/homehub.env" "$config" direct
homehub_remove_installed_files "$installed" "$config/homehub.env" "$config" direct
[[ ! -e "$installed" ]]
[[ ! -e "$config/homehub.env" ]]
[[ $(cat "$config/other.conf") == 'unrelated' ]]
[[ $(cat "$repository/.git/keep") == 'checkout' ]]
[[ $(cat "$data/homehub.sqlite3") == 'database' ]]
[[ $(cat "$data/backups/homehub-backup.sqlite3") == 'backup' ]]
[[ $(cat "$data/calendar-archives/homehub-calendar-2026.ics") == 'archive' ]]
'''

    result = subprocess.run([bash, '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
