# Home Hub engineering guidelines

Goal: a cozy local-first multiuser household app on a systemd-based Ubuntu Proxmox LXC, with optional Chat using an agent API on another trusted node. Local inference is not planned for the current deployment; the earlier Raspberry Pi 3B profiles remain historical reference. Budget: under 256 MB steady-state application RSS if practical; under 10 GB for app, dependencies and application data (OS and model files excluded). Avoid heavy JS toolchains and unnecessary always-on services. Python Flask, SQLite, server-rendered HTML, small vanilla JS, pytest. Desktop development on Windows. LAN-only; no public access. Calendar local-first, with downloadable ICS; cloud integration deferred. Theme must support light/dark. Target 32 users, at most 4 admins. Secure login, session management, CSRF and user admin planned. Prefer small feature increments with tests and explicit profiling. Keep remote agent APIs on a trusted private network or behind HTTPS; never expose an unauthenticated model runtime publicly. Keep recipes, shopping list, calendar, weather, RSS, games separate modules as project grows.

## Test-first development rule

All implementation work in this project must be test-driven. For each behavior
change, first add or update tests that define the expected behavior, run the
focused tests, and confirm that they fail for the missing behavior. Then write
the smallest implementation that makes those tests pass, rerun the focused
tests, and run the full suite before considering the change complete. Include
authorization, privacy, validation, and data-migration boundaries in tests
where relevant. Do not write tests that only mirror implementation details.
If a behavior cannot be meaningfully automated, state why and define the
manual verification needed.

Current priorities and the planned work across every tab are tracked in
[`docs/IMPLEMENTATION_AGENDA.md`](docs/IMPLEMENTATION_AGENDA.md). Keep that
agenda aligned with user-approved scope and actual implementation status.
