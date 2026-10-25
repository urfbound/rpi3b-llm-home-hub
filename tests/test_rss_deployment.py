from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALL = (ROOT / 'deploy' / 'install.sh').read_text(encoding='utf-8')
UNINSTALL = (ROOT / 'deploy' / 'uninstall.sh').read_text(encoding='utf-8')
SERVICE_PATH = ROOT / 'deploy' / 'homehub-rss-refresh.service.example'
TIMER_PATH = ROOT / 'deploy' / 'homehub-rss-refresh.timer.example'
SERVICE = SERVICE_PATH.read_text(encoding='utf-8') if SERVICE_PATH.exists() else ''
TIMER = TIMER_PATH.read_text(encoding='utf-8') if TIMER_PATH.exists() else ''


def test_install_and_uninstall_manage_the_rss_timer_units():
    assert '"$SCRIPT_DIR/homehub-rss-refresh.service.example"' in INSTALL
    assert '"$SCRIPT_DIR/homehub-rss-refresh.timer.example"' in INSTALL
    assert '/etc/systemd/system/homehub-rss-refresh.service' in INSTALL
    assert '/etc/systemd/system/homehub-rss-refresh.timer' in INSTALL
    assert 'homehub-rss-refresh.timer' in UNINSTALL
    assert 'homehub-rss-refresh.service' in UNINSTALL


def test_rss_timer_is_short_lived_and_uses_the_dedicated_service_account():
    assert 'Type=oneshot' in SERVICE
    assert 'User=homehub' in SERVICE
    assert 'homehub-rss-refresh' in SERVICE
    assert 'OnUnitActiveSec=10min' in TIMER
    assert 'Persistent=true' in TIMER
