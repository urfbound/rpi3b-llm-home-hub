# Home Hub — Phase 1 starter

A lightweight local-first family dashboard for Raspberry Pi 3B. Includes login, a household shopping list, recipes, local event creation, `.ics` calendar export and a switchable cozy light/dark theme. **This is a prototype, not a production-hardened household service.**

The [draft product plan](docs/PROJECT_PLAN.md) records the agreed direction
for the full household app and decisions still to settle.
See the [upgrade guide](docs/UPGRADES.md) before updating an existing install.

## Run on Windows (PowerShell)

Install Python 3.11 or newer, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
$env:HOMEHUB_SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:HOMEHUB_DB = "$PWD\homehub.sqlite3"
flask --app homehub.app:create_app create-admin
homehub
```

Browse to http://127.0.0.1:8080. **Keep your secret key persistent** across restarts (e.g. in a protected OS environment file). The generated example above is temporary and sessions will reset when it changes. Use `python -m pytest` to run tests.

## Run on the Pi

Install a minimal Raspberry Pi OS (Python 3.11+), create a Python virtual environment, install the app using `pip install .`, and configure a systemd service with `HOMEHUB_SECRET_KEY`, `HOMEHUB_DB` and `HOMEHUB_BIND=<Pi LAN IP>`. See `deploy/homehub.service.example`; edit paths and user before installation. Do not open a router port to this service. Restrict the allowed LAN subnet with your firewall. If you use an HTTP connection for the initial prototype, passwords and cookies travel unencrypted over the LAN; arrange HTTPS before household use.

## Important limitations / follow-up work

- Admins can now create and remove accounts, reset passwords, and configure session limits. Account deletion can transfer or remove owned prototype content.
- Recipe creation and viewing exist, but editing, ingredient normalization, linking recipes to dates, deletion, and shopping-list aggregation remain to build.
- Calendar `.ics` downloads are a one-way snapshot, not two-way synchronization. The basic export covers all-day events only.
- Weather, RSS, games, outbound email, and LLM chat are not wired up yet.
- Login rate limits, TLS, routine backup/restore, audit logging, and reliable timezone rules must be added before production. Initial schema versioning and pre-upgrade backups are implemented; later releases still need their own migrations and upgrade tests.
- Do not store sensitive information until further security hardening. For now, run on localhost for development.
