from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALL = (ROOT / 'deploy' / 'install.sh').read_text(encoding='utf-8')
UNINSTALL = (ROOT / 'deploy' / 'uninstall.sh').read_text(encoding='utf-8')


def test_reinstall_repairs_service_read_and_traverse_permissions():
    assert 'sudo chmod -R a+rX -- "$INSTALL_ROOT/homehub"' in INSTALL
    assert 'sudo chmod -R a+rX -- "$INSTALL_ROOT/.venv"' in INSTALL
    assert 'if [[ ! -x $INSTALL_ROOT/.venv/bin/python ]]' in INSTALL
    assert 'sudo rm -rf -- "$INSTALL_ROOT/homehub"' in INSTALL
    assert 'sudo rm -rf -- "$INSTALL_ROOT"' not in INSTALL


def test_uninstall_backs_up_before_stopping_and_removing_scheduled_work():
    backup = UNINSTALL.index('homehub-backup" --force')
    stop = UNINSTALL.index('systemctl disable --now homehub-backup.timer')
    remove_units = UNINSTALL.index('/etc/systemd/system/homehub-backup.timer')

    assert backup < stop < remove_units
    assert 'sudo rm -rf -- "$DATA_ROOT"' not in UNINSTALL
    assert 'sudo rm -rf -- "$INSTALL_ROOT"' in UNINSTALL
    assert 'homehub-backup' in UNINSTALL and 'backup_scheduler' in UNINSTALL
