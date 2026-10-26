# Upgrading an existing Home Hub database

Home Hub upgrades must preserve household data. Keep the service stopped while
installing a release and let the new version open the existing `HOMEHUB_DB`
file. Do not delete or replace that file to apply an update.

The app records its database schema version in SQLite's `user_version`. On the
first start with a newer schema, it checks the current database, makes and
verifies a timestamped pre-upgrade copy beside it, and applies the migration.
The copy is named like `homehub.pre-v1-YYYYMMDDTHHMMSSZ-xxxxxx.sqlite3`.
No copy is made for a new, empty database or a database already at the current
schema version. If the database is from a newer app version, startup stops
without modifying it. Migration errors roll back the schema change.

## Before an upgrade

1. Stop the running service (`sudo systemctl stop homehub` on the Pi).
2. Confirm the configured `HOMEHUB_DB` path and make an additional off-device
   backup of that database and any related application files. Keep the backup
   private: it contains account hashes and household content.
3. Install the new app version in its virtual environment.
4. Start the service and confirm that login, recipes, shopping items, and
   calendar events are present. Check service logs if startup fails.

Once GitHub releases are published, the latest release can also be installed
or applied with the public `deploy/upgrade.sh` bootstrap. It downloads and
checks the release bundle, then invokes the same installer described above;
the service installer makes a verified backup before activating the new app.
Do not run the release upgrade bootstrap until at least one release exists.

Schema version 2 adds the persistent login-attempt table and indexes.
Schema version 3 adds the named-list and private-access tables, then links
existing shopping items to a shared **Groceries** list without changing their
IDs, text, completion states, or owners. The v3 upgrade creates a verified
pre-v3 copy before changing a populated earlier database. Schema version 4
adds chores and per-user weekly completion state without changing existing
lists, recipes, or events; its upgrade creates a verified pre-v4 copy. Later
releases must continue adding ordered migrations and upgrade tests for each
supported prior release.

Schema version 21 adds per-user contacts and sticky notes with recipient
assignments. Contact ownership and note authorship cascade when an account is
deleted; removing a recipient clears only that recipient's assignment. The
migration preserves existing account and household data while adding the new
tables and indexes.

## If an upgrade fails

Leave the service stopped. The transactional migration should have left the
original database unchanged. Inspect the error and preserve the failed
database for diagnosis. To restore a known-good pre-upgrade copy, place it at
the configured `HOMEHUB_DB` path while the service is stopped, then restart
the compatible app version. Keep the original and the backup until the
household data has been checked. A pre-upgrade copy may lack data written
after it was made, so use it only when restoring is necessary.
