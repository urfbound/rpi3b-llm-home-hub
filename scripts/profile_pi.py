#!/usr/bin/env python3
"""Sample Home Hub and Ollama memory use during a Pi chat request."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


MIB = 1024 * 1024


def parse_mem_available(meminfo: str) -> int:
    for line in meminfo.splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise ValueError('MemAvailable is missing from /proc/meminfo')


def parse_cpu_counters(stat: str):
    line = next((line for line in stat.splitlines() if line.startswith('cpu ')), None)
    if line is None:
        return None
    values = [int(value) for value in line.split()[1:]]
    if len(values) < 4:
        return None
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def cpu_usage_percent(before, after):
    if before is None or after is None:
        return None
    total_delta = after[0] - before[0]
    idle_delta = after[1] - before[1]
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        return None
    return round((total_delta - idle_delta) * 100 / total_delta, 1)


def classify_process(command: str):
    tokens = command.split()
    names = [Path(token).name for token in tokens]
    if 'homehub' in names:
        return 'app'
    if 'llama-server' in names:
        return 'model_runner'
    if 'ollama' in names:
        return 'ollama'
    return None


def summarize_samples(samples: list[dict[str, int]]):
    if not samples:
        return None
    peak_app = max(row['app_rss_bytes'] for row in samples)
    peak_ollama = max(row['ollama_rss_bytes'] for row in samples)
    peak_runner = max(row['runner_rss_bytes'] for row in samples)
    cpu_values = [row['cpu_percent'] for row in samples if row.get('cpu_percent') is not None]
    return {
        'samples': len(samples),
        'minimum_available_mib': min(row['available_bytes'] for row in samples) // MIB,
        'peak_app_rss_mib': peak_app // MIB,
        'peak_ollama_rss_mib': peak_ollama // MIB,
        'peak_runner_rss_mib': peak_runner // MIB,
        'peak_combined_llm_rss_mib': (peak_ollama + peak_runner) // MIB,
        'peak_cpu_percent': max(cpu_values) if cpu_values else None,
    }


def read_sample(proc_root: Path = Path('/proc'), page_size: int | None = None) -> dict[str, int]:
    if page_size is None:
        page_size = os.sysconf('SC_PAGE_SIZE')
    app_rss = ollama_rss = runner_rss = 0
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(
                errors='replace'
            ).strip()
            statm = (entry / 'statm').read_text().split()
            rss_bytes = int(statm[1]) * page_size
        except (OSError, IndexError, ValueError):
            continue
        category = classify_process(command)
        if category:
            key = {'app': 'app_rss_bytes', 'ollama': 'ollama_rss_bytes',
                   'model_runner': 'runner_rss_bytes'}[category]
            if key == 'app_rss_bytes':
                app_rss += rss_bytes
            elif key == 'ollama_rss_bytes':
                ollama_rss += rss_bytes
            else:
                runner_rss += rss_bytes
    available = parse_mem_available((proc_root / 'meminfo').read_text())
    return {
        'available_bytes': available,
        'app_rss_bytes': app_rss,
        'ollama_rss_bytes': ollama_rss,
        'runner_rss_bytes': runner_rss,
        'cpu_counters': parse_cpu_counters((proc_root / 'stat').read_text()),
    }


def capture(seconds: int, interval: float, proc_root: Path = Path('/proc')):
    samples = []
    previous_cpu = None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sample = read_sample(proc_root)
        current_cpu = sample.pop('cpu_counters')
        sample['cpu_percent'] = cpu_usage_percent(previous_cpu, current_cpu)
        previous_cpu = current_cpu
        samples.append(sample)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    return samples


def format_report(title: str, samples: list[dict[str, int]]) -> str:
    summary = summarize_samples(samples)
    if summary is None:
        return f'{title}: no samples captured'
    return (f"{title} ({summary['samples']} samples)\n"
            f"  minimum MemAvailable: {summary['minimum_available_mib']} MiB\n"
            f"  peak Home Hub RSS: {summary['peak_app_rss_mib']} MiB\n"
            f"  peak Ollama server RSS: {summary['peak_ollama_rss_mib']} MiB\n"
            f"  peak model runner RSS: {summary['peak_runner_rss_mib']} MiB\n"
            f"  peak Ollama + runner RSS: {summary['peak_combined_llm_rss_mib']} MiB\n"
            f"  peak whole-system CPU: {summary['peak_cpu_percent']}%")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-seconds', type=int, default=15)
    parser.add_argument('--measure-seconds', type=int, default=180)
    parser.add_argument('--interval', type=float, default=0.5)
    args = parser.parse_args(argv)
    if args.baseline_seconds < 1 or args.measure_seconds < 1 or args.interval <= 0:
        parser.error('durations and interval must be positive')
    if not Path('/proc/meminfo').exists():
        parser.error('this profiler requires Linux /proc (run it on the Raspberry Pi)')

    print(f'Capturing a {args.baseline_seconds}-second idle baseline...')
    baseline = capture(args.baseline_seconds, args.interval)
    print(format_report('Idle baseline', baseline))
    input('Open Chat, then press Enter and immediately send a representative prompt: ')
    print(f'Capturing for {args.measure_seconds} seconds; send the prompt now.')
    loaded = capture(args.measure_seconds, args.interval)
    print(format_report('Chat capture', loaded))
    if not any(row['app_rss_bytes'] for row in loaded):
        print('Warning: Home Hub process was not detected by its command name.', file=sys.stderr)
    if not any(row['runner_rss_bytes'] for row in loaded):
        print('Warning: no llama-server runner was observed; check that a model ran.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
