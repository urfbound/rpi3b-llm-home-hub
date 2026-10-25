import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


def test_installer_repairs_virtualenv_permissions_for_service_account(tmp_path):
    if os.name == 'nt':
        pytest.skip('Windows does not preserve POSIX virtualenv permission bits')
    bash = shutil.which('bash')
    if bash is None:
        pytest.skip('Bash is required to exercise deployment permission helpers')

    helper = Path(__file__).parents[1] / 'deploy' / 'venv_permissions.sh'
    venv = tmp_path / 'venv'
    script = f'''\
set -euo pipefail
source {shlex.quote(str(helper))}
venv={shlex.quote(str(venv))}
umask 077
mkdir -p "$venv/bin" "$venv/lib/site-packages"
touch "$venv/bin/homehub" "$venv/lib/site-packages/module.py"
chmod 700 "$venv/bin/homehub"
chmod 600 "$venv/lib/site-packages/module.py"
homehub_make_venv_service_accessible "$venv" direct
[[ $(stat -c %a "$venv/bin") == 755 ]]
[[ $(stat -c %a "$venv/lib/site-packages") == 755 ]]
[[ $(stat -c %a "$venv/bin/homehub") == 755 ]]
[[ $(stat -c %a "$venv/lib/site-packages/module.py") == 644 ]]
'''

    result = subprocess.run([bash, '-c', script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
