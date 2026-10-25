import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from homehub.system_status import (
    cpu_usage_percent, disk_usage, process_rss_bytes, read_cpu_counters,
    read_memory, read_temperature_c, record_metric_sample, cpu_bar_class,
)


def test_memory_usage_uses_available_ram_instead_of_only_free_ram(tmp_path):
    path = tmp_path / 'meminfo'
    path.write_text('MemTotal: 1000000 kB\nMemFree: 100000 kB\nMemAvailable: 400000 kB\n')

    memory = read_memory(path)

    assert memory == {
        'ram_total_bytes': 1024000000,
        'ram_used_bytes': 614400000,
        'ram_percent': 60.0,
    }


def test_cpu_usage_is_normalized_to_zero_to_one_hundred_percent():
    before = (1000, 500)
    after = (1200, 600)

    assert cpu_usage_percent(before, after) == 50.0
    assert cpu_usage_percent(after, before) is None
    assert cpu_usage_percent((0, 0), (0, 0)) is None


def test_cpu_counters_include_iowait_as_idle(tmp_path):
    path = tmp_path / 'stat'
    path.write_text('cpu 100 2 30 500 20 5 3 0 4 1\ncpu0 40 1 10 250 10 2 1 0 2 0\n')

    total, idle = read_cpu_counters(path)

    assert total == 665
    assert idle == 520


def test_temperature_reads_pi_thermal_sensor_and_handles_missing_sensor(tmp_path):
    path = tmp_path / 'temperature'
    path.write_text('44750\n')
    assert read_temperature_c(path) == 44.8
    assert read_temperature_c(tmp_path / 'missing') is None


def test_disk_usage_reports_the_partition_for_the_application_path():
    usage = disk_usage('app.py', usage_fn=lambda _path: (1000, 700, 300))

    assert usage == {
        'disk_total_bytes': 1000,
        'disk_used_bytes': 700,
        'disk_percent': 70.0,
    }


def test_process_rss_uses_resident_pages(tmp_path):
    path = tmp_path / 'statm'
    path.write_text('100 3 0 0 0 0 0\n')

    assert process_rss_bytes(path, page_size=4096) == 12288


def test_metric_history_samples_at_most_once_per_minute_and_prunes_old_rows():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE system_metrics(sampled_at TEXT PRIMARY KEY, cpu_percent REAL, '
               'ram_percent REAL, temperature_c REAL, disk_used_bytes INTEGER, '
               'disk_total_bytes INTEGER, app_rss_bytes INTEGER)')
    first = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    sample = {'cpu_percent': 40, 'ram_percent': 60, 'temperature_c': 44.75,
              'disk_used_bytes': 700, 'disk_total_bytes': 1000, 'app_rss_bytes': 4096}

    assert record_metric_sample(db, sample, first)
    assert not record_metric_sample(db, sample, first + timedelta(seconds=59))
    assert record_metric_sample(db, sample, first + timedelta(minutes=1))
    assert record_metric_sample(db, sample, first + timedelta(hours=2))

    rows = db.execute('SELECT sampled_at FROM system_metrics ORDER BY sampled_at').fetchall()
    assert len(rows) == 1
    assert rows[0][0] == (first + timedelta(hours=2)).isoformat()


def test_app_database_initializes_system_metrics_table(tmp_path):
    from homehub.app import create_app

    path = tmp_path / 'system-metrics-schema.sqlite3'
    create_app({'TESTING': True, 'SECRET_KEY': 'system-schema-secret', 'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_metrics'"
        ).fetchone() == ('system_metrics',)


@pytest.mark.parametrize(('percent', 'expected'), [
    (0, 'normal'), (59.9, 'normal'), (60, 'warning'), (80, 'warning'),
    (80.1, 'critical'), (100, 'critical'),
])
def test_cpu_history_color_uses_normalized_percentage_thresholds(percent, expected):
    assert cpu_bar_class(percent) == expected
