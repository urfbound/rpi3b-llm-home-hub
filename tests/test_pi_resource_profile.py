from scripts.profile_pi import (
    classify_process,
    parse_mem_available,
    parse_cpu_counters,
    cpu_usage_percent,
    read_sample,
    summarize_samples,
)


def test_mem_available_parser_reads_linux_meminfo_kibibytes():
    assert parse_mem_available(
        'MemTotal: 900000 kB\nMemFree: 300000 kB\nMemAvailable: 700000 kB\n'
    ) == 700000 * 1024


def test_process_classifier_separates_app_and_ollama_runner():
    assert classify_process('python /opt/homehub/.venv/bin/homehub') == 'app'
    assert classify_process('/usr/local/bin/ollama serve') == 'ollama'
    assert classify_process('/usr/local/lib/ollama/llama-server --model model.gguf') == 'model_runner'
    assert classify_process('systemd --user') is None


def test_cpu_sampling_normalizes_all_cores_to_zero_to_one_hundred_percent():
    before = parse_cpu_counters('cpu 500 0 500 500 0\n')
    after = parse_cpu_counters('cpu 600 0 500 600 0\n')

    assert before == (1500, 500)
    assert cpu_usage_percent(before, after) == 50.0
    assert parse_cpu_counters('cpu0 1 2 3 4\n') is None


def test_summary_reports_peaks_and_low_available_memory():
    summary = summarize_samples([
        {'available_bytes': 700 * 1024**2, 'app_rss_bytes': 100 * 1024**2,
         'ollama_rss_bytes': 20 * 1024**2, 'runner_rss_bytes': 0,
         'cpu_percent': 10},
        {'available_bytes': 400 * 1024**2, 'app_rss_bytes': 120 * 1024**2,
         'ollama_rss_bytes': 22 * 1024**2, 'runner_rss_bytes': 360 * 1024**2,
         'cpu_percent': 70},
    ])

    assert summary == {
        'samples': 2,
        'minimum_available_mib': 400,
        'peak_app_rss_mib': 120,
        'peak_ollama_rss_mib': 22,
        'peak_runner_rss_mib': 360,
        'peak_combined_llm_rss_mib': 382,
        'peak_cpu_percent': 70,
    }


def test_summary_handles_empty_capture():
    assert summarize_samples([]) is None


def test_proc_sampler_separates_app_server_and_model_runner_rss(tmp_path):
    (tmp_path / 'meminfo').write_text('MemTotal: 900000 kB\nMemAvailable: 700000 kB\n')
    (tmp_path / 'stat').write_text('cpu 100 0 50 800 20 10 10 0 0 0\n')
    processes = {
        '10': (b'/opt/homehub/.venv/bin/python\0/opt/homehub/.venv/bin/homehub\0', 10),
        '11': (b'/usr/local/bin/ollama\0serve\0', 5),
        '12': (b'/usr/local/lib/ollama/llama-server\0--model\0m.gguf\0', 20),
    }
    for pid, (command, resident_pages) in processes.items():
        process = tmp_path / pid
        process.mkdir()
        (process / 'cmdline').write_bytes(command)
        (process / 'statm').write_text(f'100 {resident_pages} 0 0 0 0 0\n')

    sample = read_sample(tmp_path, page_size=4096)

    assert sample['available_bytes'] == 700000 * 1024
    assert sample['app_rss_bytes'] == 10 * 4096
    assert sample['ollama_rss_bytes'] == 5 * 4096
    assert sample['runner_rss_bytes'] == 20 * 4096
