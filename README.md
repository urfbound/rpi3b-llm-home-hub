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

## Backups

Admins can manage backups from **Admin → Backup settings**. Verified SQLite
snapshots are created daily by default, with 14 copies retained. A systemd
timer checks every 15 minutes whether one is due. The interval can be set
from 1 to 31 days or disabled; the retention count can be set from 1 to 365.
Admins can also create, download, restore, and delete backups.
Restores are checked and upgraded before replacing the database, then all
users are signed out. The default backup folder is `backups` beside the
configured database; choose a protected folder with enough free space.
Backups contain account hashes and private household data.

## Install on Raspberry Pi OS

The supported installer targets a Raspberry Pi running 64-bit Raspberry Pi
OS with Python 3.11 or newer and `sudo` access. It installs the service under
`/opt/homehub`, keeps the database and calendar archives under
`/var/lib/homehub`, and creates a root-owned configuration file at
`/etc/homehub/homehub.env`. It creates a dedicated `homehub` service account,
installs the web service and systemd backup timer, and prompts to install
`python3-venv` if needed. Run it as your normal Pi login; do not prefix it
with `sudo`. The app listens on loopback only.

### Transfer files from Windows

Open PowerShell in the project folder on Windows. Replace `username` and
`hostname` in the commands with your Pi login and SSH host name or alias.
Windows OpenSSH's `ssh` and `scp` utilities are used.

```powershell
ssh username@hostname "mkdir -p ~/homehub-starter"
scp -r .\homehub .\deploy .\pyproject.toml .\README.md .\LICENSE username@hostname:~/homehub-starter/
ssh username@hostname "cd ~/homehub-starter && chmod +x deploy/install.sh deploy/uninstall.sh && ./deploy/install.sh"
```

If `scp` reports `Permission denied` for one existing destination file, copy
that file to a temporary name in your home directory, then install it over the
blocked file with the correct owner. For example, to transfer the venv
permission helper:

```powershell
scp .\deploy\venv_permissions.sh jake@brigus:~/venv_permissions.sh
ssh -t jake@brigus "sudo install -o jake -g jake -m 0644 /home/jake/venv_permissions.sh /home/jake/homehub-starter/deploy/venv_permissions.sh && rm /home/jake/venv_permissions.sh"
```

Then repeat the normal transfer command to ensure the rest of the files are
current, and run the installer as `jake` without `sudo`.

The transfer includes only runtime files and deployment examples, not your
Windows `.git` directory, virtual environment, database, or backups. The
installer copies the application into `/opt/homehub`; it leaves the transferred
repository in your Pi home directory. To update later, repeat the same `scp`
command and run `./deploy/install.sh` again from `~/homehub-starter`. An
existing database is backed up and verified before the new service starts.
Keep `/etc/homehub/homehub.env` private and persistent; it contains the
session-signing secret.

For first-time setup, the installer prompts you to create the first admin if
the database has no active admin. Create a tunnel from another PowerShell
window, leaving it open while using the app:

```powershell
ssh -N -L 8080:127.0.0.1:8080 username@hostname
```

Then open <http://127.0.0.1:8080> on the Windows PC. Stop the tunnel with
Ctrl+C. For access from other household devices, follow
[the TLS guide](docs/TLS_DEPLOYMENT.md) and keep access restricted to the
household LAN; do not expose the service through a router port.

## Uninstall from Raspberry Pi OS

Run the uninstaller as your regular Pi login from the transferred repository:

```powershell
ssh -t username@hostname "cd ~/homehub-starter && ./deploy/uninstall.sh"
```

It creates an immediate verified database backup first. If that backup fails,
uninstallation stops before removing services or files. It then disables and
removes the backup timer and web service, removes Home Hub's cron entries,
installed application and configuration, and deletes the dedicated service
account. It preserves your repository, database, calendar archives, and backup
files. The script asks before deleting recognized Home Hub backup files; it
never removes the repository. Preserved files under `/var/lib/homehub` become
root-owned and are assigned back to the service account if you reinstall.
## Important limitations / follow-up work

- Admins can now create and remove accounts, reset passwords, and configure session limits. Account deletion can transfer or remove owned prototype content.
- Recipe creation and viewing exist, but editing, ingredient normalization, linking recipes to dates, deletion, and shopping-list aggregation remain to build.
- Calendar `.ics` downloads are a one-way snapshot, not two-way synchronization. The basic export covers all-day events only.
- Weather, RSS, games, outbound email, and LLM chat are not wired up yet.
- Login rate limits, backup/restore, and the TLS deployment guide are in
  place. Audit logging and reliable timezone rules are still needed before
  production. Schema versioning and pre-upgrade backups are implemented;
  later releases still need their own migrations and upgrade tests.
- Until TLS is configured, keep the app on localhost for development; do not
  sign in over plain HTTP on the LAN.
