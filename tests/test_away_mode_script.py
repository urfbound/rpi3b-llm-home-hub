import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is unavailable')
def test_away_mode_client_unit_suite():
    suite = Path(__file__).with_name('away_mode.test.cjs')
    result = subprocess.run(
        ['node', '--test', str(suite)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
