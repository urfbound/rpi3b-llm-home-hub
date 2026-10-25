from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALL = (ROOT / 'deploy' / 'install.sh').read_text(encoding='utf-8')
UNINSTALL = (ROOT / 'deploy' / 'uninstall.sh').read_text(encoding='utf-8')


def test_reinstall_repairs_service_read_and_traverse_permissions():
    assert 'homehub_replace_runtime_tree "$REPO_ROOT/homehub" "$INSTALL_ROOT/homehub" sudo' in INSTALL
    assert 'homehub_make_venv_service_accessible "$INSTALL_ROOT/.venv" sudo' in INSTALL
    assert 'if [[ ! -x $INSTALL_ROOT/.venv/bin/python ]]' in INSTALL
    assert 'sudo rm -rf -- "$INSTALL_ROOT"' not in INSTALL


def test_uninstall_backs_up_before_stopping_and_removing_scheduled_work():
    backup = UNINSTALL.index('homehub-backup" --force')
    stop = UNINSTALL.index('systemctl disable --now homehub-backup.timer')
    remove_units = UNINSTALL.index('/etc/systemd/system/homehub-backup.timer')

    assert backup < stop < remove_units
    assert 'sudo rm -rf -- "$DATA_ROOT"' not in UNINSTALL
    assert 'homehub_remove_installed_files "$INSTALL_ROOT" "$ENV_FILE" "$CONFIG_ROOT" sudo' in UNINSTALL
    assert 'homehub-backup' in UNINSTALL and 'backup_scheduler' in UNINSTALL


def test_install_verifies_existing_database_backup_before_service_activation():
    backup = INSTALL.index('homehub-backup" --force')
    activate_services = INSTALL.index('sudo systemctl enable --now homehub.service')

    assert 'if [[ -f $database_path ]]' in INSTALL
    assert backup < activate_services
    assert 'set -Eeuo pipefail' in INSTALL


def test_installer_keeps_backup_schedule_in_one_systemd_timer():
    assert 'homehub_filter_legacy_backup_crontab' in INSTALL
    assert 'sudo rm -f -- /etc/cron.d/homehub-backup' in INSTALL
    assert 'homehub-backup.timer.example' in INSTALL
    assert 'sudo systemctl enable --now homehub.service homehub-backup.timer' in INSTALL


def test_deployment_shell_scripts_use_unix_line_endings_for_pi_scp():
    shell_scripts = sorted((ROOT / 'deploy').glob('*.sh'))
    attributes = (ROOT / '.gitattributes').read_text(encoding='utf-8')

    assert shell_scripts
    assert '*.sh text eol=lf' in attributes
    assert all(b'\r' not in path.read_bytes() for path in shell_scripts)
