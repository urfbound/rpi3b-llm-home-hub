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
Admins can also create, download, restore, and delete backups. Each backup is
a matched SQLite snapshot and calendar archive bundle; download both files for
an off-device copy. Restores verify and upgrade the database and replace the
calendar archives together, then sign out all users. The default backup folder
is `backups` beside the configured database; choose a protected folder with
enough free space. Backups contain account hashes and private household data.

## Install on Raspberry Pi OS

The supported installer targets a Raspberry Pi running 64-bit Raspberry Pi
OS with Python 3.11 or newer and `sudo` access. It installs the service under
`/opt/homehub`, keeps the database and calendar archives under
`/var/lib/homehub`, and creates a root-owned configuration file at
`/etc/homehub/homehub.env`. It creates a dedicated `homehub` service account,
installs the web service and systemd backup timer, and prompts to install
`python3-venv` if needed. Run it as your normal Pi login; do not prefix it
with `sudo`. The app listens on loopback only.

### Install from a published release

After the first GitHub release is published, a fresh Raspberry Pi OS install
can use this bootstrap command as the regular Pi login:

```sh
curl -fsSL https://raw.githubusercontent.com/urfbound/rpi3b-llm-home-hub/main/deploy/install-release.sh | sh
```

It downloads the latest published release bundle and checks its SHA-256
checksum before running the existing installer. To upgrade an existing
installation to the latest release, run:

```sh
curl -fsSL https://raw.githubusercontent.com/urfbound/rpi3b-llm-home-hub/main/deploy/upgrade.sh | sh
```

These commands are not usable until a release has been published. For a
version-specific install or upgrade, download the matching release bundle and
run `deploy/install.sh` from its extracted directory. The installer continues
to create and verify a database backup before activating the updated service.

### Transfer files from Windows

Open PowerShell in the project folder on Windows. Replace `username` and
`hostname` in the commands with your Pi login and SSH host name or alias.
Windows OpenSSH's `ssh` and `scp` utilities are used.

```powershell
tar.exe -czf "$env:TEMP\homehub-transfer.tar.gz" --exclude=__pycache__ --exclude="*.pyc" -C . homehub deploy scripts pyproject.toml README.md LICENSE
if ($LASTEXITCODE -ne 0) { throw "Could not create the transfer archive." }
ssh username@hostname "mkdir -p ~/homehub-release"
if ($LASTEXITCODE -ne 0) { throw "Could not create the Pi staging directory." }
scp "$env:TEMP\homehub-transfer.tar.gz" username@hostname:~/homehub-release/
if ($LASTEXITCODE -ne 0) { throw "Transfer failed; do not run the installer." }
$extract = 'cd ~/homehub-release && mkdir -p source && tar -tzf homehub-transfer.tar.gz | while IFS= read -r p; do case "$p" in */) mkdir -p "source/$p";; */*) mkdir -p "source/${p%/*}";; esac; done && find source -type d -exec chmod u+rwx {} + && tar --no-same-owner --no-same-permissions --no-overwrite-dir -xzf homehub-transfer.tar.gz -C source && rm homehub-transfer.tar.gz'
ssh username@hostname $extract
if ($LASTEXITCODE -ne 0) { throw "Could not unpack the transfer on the Pi." }
ssh -t username@hostname "cd ~/homehub-release/source && chmod +x deploy/install.sh deploy/uninstall.sh && ./deploy/install.sh"
```

This transfers source and deployment files in an archive, excluding Python
bytecode caches. Before extracting, the Pi creates the archive's directory
tree with your account's write permission, then tells GNU tar to preserve those
permissions while extracting. This handles read-only permission bits that
Windows may have stored in the archive and permits retrying after a partial
extraction. Do not use `sudo scp` or run the installer with `sudo`.

If a previous direct `scp` transfer left CRLF endings, convert those scripts
before retrying that older checkout's installer:

```sh
cd ~/homehub-starter
python3 -c 'from pathlib import Path; [p.write_bytes(p.read_bytes().replace(bytes((13, 10)), bytes((10,)))) for p in Path("deploy").glob("*.sh")]'
chmod +x deploy/install.sh deploy/uninstall.sh
./deploy/install.sh
```

The repository's `.gitattributes` keeps deployment shell scripts in Unix LF
format in future Windows checkouts. The installer requests sudo only for
system changes.

The transfer includes application code, scripts, and deployment examples, not
your Windows `.git` directory, virtual environment, database, or backups. The
installer copies the application into `/opt/homehub`; it leaves the staging
directory in your Pi home directory. An existing database is backed up and
verified before the new service starts.
Keep `/etc/homehub/homehub.env` private and persistent; it contains the
session-signing secret.

For first-time setup, the installer prompts you to create the first admin if
the database has no active admin. Create a tunnel from another PowerShell
window, leaving it open while using the app:

```powershell
ssh -N -L 8080:127.0.0.1:8080 username@hostname
```

Then open <http://127.0.0.1:8080> on the Windows PC. Stop the tunnel with
Ctrl+C. For access from other household devices, install Caddy and run
[`deploy/enable_https.sh`](deploy/enable_https.sh) as described in
[the TLS guide](docs/TLS_DEPLOYMENT.md). This keeps Waitress on loopback and
serves HTTPS on the household LAN; do not expose the service through a router
port.

The installer accepts `--no-prompt` (also `--yes` or `-y`) to accept its
confirmation prompts, including installing the Python venv package if needed.
For a first install with no administrator, provide a username and one line of
password input without putting the password in the command line:

```sh
./deploy/install.sh --no-prompt --admin-username household-admin --admin-password-stdin < /path/to/protected-password-file
```

The password must be at least 12 characters. System authorization through
`sudo` may still request your Pi password.

## Uninstall from Raspberry Pi OS

Run the uninstaller as your regular Pi login from the transferred repository:

```powershell
ssh -t username@hostname "cd ~/homehub-starter && ./deploy/uninstall.sh"
```

It creates an immediate verified database backup first. If that backup fails,
uninstallation stops before removing services or files. It then disables and
removes the backup timer and web service, removes Home Hub's cron entries,
installed application and configuration, and deletes the dedicated service
account. With no flags, it asks whether to delete the database, application
data, archives, and verified backups; the default answer preserves them. It
also offers separate backup deletion when application data is kept. The source
repository is always preserved.

Use `--no-prompt` (also `--yes` or `-y`) for unattended confirmation choices;
this defaults to keeping data and backups. Add `--delete-data` to remove the
database, SQLite sidecar files, `/var/lib/homehub`, calendar archives, and
recognized Home Hub backups. Use `--delete-backups` to remove only recognized
backup files while keeping the database. For example:

```sh
./deploy/uninstall.sh --no-prompt --delete-data
```

`--keep-data` and `--keep-backups` can make preservation explicit. The
uninstaller still creates and verifies a fresh backup before removal, then
deletes that backup too when `--delete-data` or `--delete-backups` is selected.
System authorization through `sudo` may still request your Pi password.
Preserved files under `/var/lib/homehub` become root-owned and are assigned
back to the service account if you reinstall.
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
