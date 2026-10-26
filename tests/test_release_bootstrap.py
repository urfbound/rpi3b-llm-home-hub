from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_release_bootstrap_uses_latest_release_asset_and_checks_checksum():
    script = (ROOT / 'deploy' / 'release_bootstrap.sh').read_text(encoding='utf-8')

    assert 'releases/latest' in script
    assert 'sha256sum --check' in script
    assert 'mktemp -d' in script
    assert 'trap ' in script
    assert 'deploy/install.sh' in script


def test_install_and_upgrade_entrypoints_select_the_expected_mode():
    install = (ROOT / 'deploy' / 'install-release.sh').read_text(encoding='utf-8')
    upgrade = (ROOT / 'deploy' / 'upgrade.sh').read_text(encoding='utf-8')

    assert 'bash "$bootstrap" install' in install
    assert 'bash "$bootstrap" upgrade' in upgrade


def test_release_archive_workflow_publishes_a_checked_bundle():
    workflow = (ROOT / '.github' / 'workflows' / 'release.yml').read_text(encoding='utf-8')

    assert 'homehub-${RELEASE_TAG}.tar.gz' in workflow
    assert 'sha256sum' in workflow
    assert 'gh release create' in workflow
