"""Low-overhead Linux system readings for the Home dashboard."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil


def read_memory(path='/proc/meminfo'):
    values = {}
    try:
        for line in Path(path).read_text(encoding='ascii').splitlines():
            key, separator, raw = line.partition(':')
            if separator and key in ('MemTotal', 'MemAvailable'):
                values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    total = values.get('MemTotal')
    available = values.get('MemAvailable')
    if not total or available is None:
        return None
    used = max(0, total - available)
    return {
        'ram_total_bytes': total,
        'ram_used_bytes': used,
        'ram_percent': round(used * 100 / total, 1),
    }


def read_cpu_counters(path='/proc/stat'):
    try:
        first_line = Path(path).read_text(encoding='ascii').splitlines()[0].split()
        if first_line[0] != 'cpu':
            return None
        values = [int(value) for value in first_line[1:]]
    except (OSError, ValueError, IndexError):
        return None
    if len(values) < 4:
        return None
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def cpu_usage_percent(previous, current):
    if not previous or not current:
        return None
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0 or idle_delta < 0:
        return None
    busy = max(0, total_delta - idle_delta)
    return round(min(100.0, busy * 100 / total_delta), 1)


def cpu_bar_class(percent):
    if percent is None:
        return 'unavailable'
    if percent > 80:
        return 'critical'
    if percent >= 60:
        return 'warning'
    return 'normal'


def read_temperature_c(path='/sys/class/thermal/thermal_zone0/temp'):
    try:
        return round(int(Path(path).read_text(encoding='ascii').strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def disk_usage(path, *, usage_fn=shutil.disk_usage):
    try:
        total, used, _free = usage_fn(path)
    except OSError:
        return None
    if total <= 0:
        return None
    return {
        'disk_total_bytes': total,
        'disk_used_bytes': used,
        'disk_percent': round(used * 100 / total, 1),
    }


def process_rss_bytes(path='/proc/self/statm', *, page_size=None):
    if page_size is None:
        try:
            page_size = os.sysconf('SC_PAGE_SIZE')
        except (AttributeError, OSError, ValueError):
            page_size = 4096
    try:
        resident_pages = int(Path(path).read_text(encoding='ascii').split()[1])
        return resident_pages * page_size
    except (OSError, ValueError, IndexError):
        return None


def collect_system_snapshot(*, app_path, previous_cpu=None, proc_root='/proc',
                            thermal_path='/sys/class/thermal/thermal_zone0/temp',
                            disk_usage_fn=shutil.disk_usage):
    memory = read_memory(Path(proc_root) / 'meminfo')
    disk = disk_usage(app_path, usage_fn=disk_usage_fn)
    if memory is None or disk is None:
        return None
    current_cpu = read_cpu_counters(Path(proc_root) / 'stat')
    return {
        'cpu_percent': cpu_usage_percent(previous_cpu, current_cpu),
        'cpu_counters': current_cpu,
        **memory,
        **disk,
        'temperature_c': read_temperature_c(thermal_path),
        'app_rss_bytes': process_rss_bytes(Path(proc_root) / 'self' / 'statm'),
    }


def record_metric_sample(db, sample, sampled_at, *, minimum_interval_seconds=60):
    sampled_at = sampled_at.astimezone(timezone.utc)
    latest = db.execute('SELECT sampled_at FROM system_metrics ORDER BY sampled_at DESC LIMIT 1').fetchone()
    if latest:
        previous_time = datetime.fromisoformat(latest['sampled_at'])
        if (sampled_at - previous_time).total_seconds() < minimum_interval_seconds:
            return False
    db.execute(
        'INSERT OR REPLACE INTO system_metrics(sampled_at,cpu_percent,ram_percent,temperature_c,'
        'disk_used_bytes,disk_total_bytes,app_rss_bytes) VALUES(?,?,?,?,?,?,?)',
        (sampled_at.isoformat(), sample.get('cpu_percent'), sample['ram_percent'],
         sample.get('temperature_c'), sample['disk_used_bytes'], sample['disk_total_bytes'],
         sample.get('app_rss_bytes')),
    )
    cutoff = (sampled_at - timedelta(hours=1)).isoformat()
    db.execute('DELETE FROM system_metrics WHERE sampled_at<?', (cutoff,))
    db.commit()
    return True
