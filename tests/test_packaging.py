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
