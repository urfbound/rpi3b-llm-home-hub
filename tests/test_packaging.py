import fnmatch
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


def test_distribution_declares_all_templates_and_static_assets():
    config = tomllib.loads((PROJECT_ROOT / 'pyproject.toml').read_text())
    package_data = config['tool']['setuptools']['package-data']['homehub']
    package_root = PROJECT_ROOT / 'homehub'
    required_files = [
        path.relative_to(package_root).as_posix()
        for folder in ('templates', 'static')
        for path in (package_root / folder).rglob('*')
        if path.is_file()
    ]

    assert required_files
    assert all(
        any(fnmatch.fnmatchcase(filename, pattern) for pattern in package_data)
        for filename in required_files
    )


def test_distribution_includes_third_party_notices():
    config = tomllib.loads((PROJECT_ROOT / 'pyproject.toml').read_text())
    package_data = config['tool']['setuptools']['package-data']['homehub']
    notice_path = PROJECT_ROOT / 'homehub' / 'THIRD_PARTY_NOTICES.md'
    notice = notice_path.read_text()
    dependencies = config['project']['dependencies']

    assert any(
        fnmatch.fnmatchcase('THIRD_PARTY_NOTICES.md', pattern)
        for pattern in package_data
    )
    assert config['project']['license'] == 'GPL-3.0-only'
    assert 'Flask' in notice
    assert '| Click | CLI option parsing for `create-admin` | BSD-3-Clause |' in notice
    assert 'pytest' in notice
    assert 'SCOWL' in notice
    assert 'wordfreq' in notice
    assert 'CC BY-SA 4.0' in notice
    assert 'Ollama runtime' in notice
    assert 'select, recommend, supply, or download models' in notice
    assert 'Caddy is licensed under Apache-2.0' in notice
    assert 'Home Hub does not bundle' in notice
    assert any(dependency.startswith('click>=') for dependency in dependencies)
