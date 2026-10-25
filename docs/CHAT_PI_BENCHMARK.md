# Chat feasibility benchmark: Raspberry Pi 3B

This record captures the initial Ollama benchmarks on the minimum supported
hardware. The results support an optional Chat feature, but do not yet establish
memory headroom with Home Hub running or evaluate answer quality.

## Test host and conditions

- Raspberry Pi 3B, 64-bit Debian, Linux `6.18.50+rpt-rpi-v8`.
- `free -h` reported 905 MiB total memory. Idle available memory was 730 MiB
  in the first run and 758 MiB in a later check. Idle Ollama server RSS was
  about 18 MiB; `ollama ps` showed no resident models after testing.
- Temperature was 44 C at the first baseline. Swap was 904 MiB total, with
  37 MiB in use at the later idle check.
- The warm benchmark used a 2,048-token context, temperature zero, at most 64
  generated tokens, one warm-up request, and two or three measured requests.
  It kept one model loaded during its run, then unloaded it before the next.
- Memory sampling recorded system `MemAvailable` and process RSS every 0.1
  seconds. The model runner was identified as `llama-server`; the earlier
  sampler that matched only `ollama` process names missed this runner.

## Warm results

| Model | Warm response speed | Runner peak RSS | Minimum system available RAM |
| --- | ---: | ---: | ---: |
| `smollm2:135m` | 5.05–5.10 tokens/s | 343 MiB | 405 MiB |
| `gemma3:270m` | 5.01–5.10 tokens/s | 365 MiB | 377 MiB |
| `qwen2.5:0.5b` | 3.47–3.62 tokens/s | 469 MiB | 283 MiB |

The full 64-token responses took about 12–14 seconds for SmolLM2 and Gemma3.
At the measured Qwen2.5 rate, a 64-token response would take about 18 seconds.
Some measured replies naturally ended before the 64-token cap.

An earlier 2,048-context run reported a minimum of 23 MiB available while
`qwen3:0.6b` was generating. That run did not capture runner RSS, so treat the
available-memory low point as a warning and rerun with the corrected sampler
before considering Qwen3. `lfm2.5-thinking:latest` returned HTTP 500; its error
body was not captured. Other first-request cold-start tests ranged from about
24 seconds to 102 seconds total, with model loading taking about 16–71 seconds.

## Design implications and remaining gates

- Chat can be offered as an admin-enabled, optional feature on Pi 3B. Keep it
  hidden until enabled, use one selected model and one active request, and keep
  the approved admin-priority queue and limits.
- SmolLM2 135m and Gemma3 270m are the lighter, faster warm candidates for
  ordinary chat. The supplied Ollama model metadata lists tool capability for
  Qwen2.5 0.5b, which is the warm-tested candidate for the approved read-only
  database tools; it is slower and leaves 283 MiB available before Home Hub is
  included.
- These measurements do not include Home Hub's memory use. Profile the app and
  selected model together before enabling Chat on a running household Pi. Keep
  database tools disabled until their approved RAM and CPU budget is verified
  in that combined profile.
- The benchmark measures latency and memory only, not answer quality. Review
  representative responses before recommending a model to household users.
- Continue to use a bounded context and a response token cap. Recheck memory
  and latency if either changes. Do not expose Ollama beyond loopback.

## Combined Home Hub and model profile

The repository includes a standard-library-only sampler for the Pi. From the
project checkout on the Pi, run:

```sh
python3 scripts/profile_pi.py --baseline-seconds 15 --measure-seconds 180
```

It records an idle baseline, then prompts you to open Chat, submit a normal
prompt, and leave the page open while it samples every half-second. It reports
minimum system `MemAvailable`, peak Home Hub RSS, Ollama server RSS, model
runner RSS, their combined Ollama-plus-runner RSS, and peak whole-system CPU
use normalized across cores. Increase `--measure-seconds` if a response can
take longer than three minutes; `--interval` can adjust sample frequency.
Run one capture for the selected chat model and repeat after changing models.

Process RSS can count shared pages more than once, so use system
`MemAvailable` as the primary measure of actual memory headroom. The combined
Ollama and runner RSS is a useful per-process comparison, not a substitute for
system memory accounting. Include model, context size, response cap, ambient
temperature, and whether other household activity was occurring when sharing
results. The tool requires Linux `/proc` and must be run on the Pi; it does not
make requests or change server settings itself.
