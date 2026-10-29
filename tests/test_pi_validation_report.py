from pathlib import Path


ROOT = Path(__file__).parents[1]
REPORT = ROOT / 'deploy' / 'pi_validation_report.sh'


def test_pi_report_script_is_read_only_and_documents_recordable_scenarios():
    script = REPORT.read_text(encoding='utf-8')

    assert 'install-keep' in script
    assert 'upgrade-preserves-data' in script
    assert 'uninstall-keep' in script
    assert 'uninstall-delete' in script
    assert '--record' in script
    assert 'HOMEHUB_SECRET_KEY' not in script or 'HOMEHUB_SECRET_KEY is set' in script
    assert 'systemctl is-active' in script
    assert 'bash -n' in script
    assert 'sudo ' not in script
    assert 'rm -rf' not in script


def test_pi_report_supports_output_file_without_exposing_environment_values():
    script = REPORT.read_text(encoding='utf-8')

    assert '--output' in script
    assert 'environment variable names only' in script
    assert 'chmod 600' in script
