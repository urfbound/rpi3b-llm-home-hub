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

For the current prototype, only the initial schema migration to version 1 is
implemented. Later releases must add ordered migrations and upgrade tests for
each supported prior release.

## If an upgrade fails

Leave the service stopped. The transactional migration should have left the
original database unchanged. Inspect the error and preserve the failed
database for diagnosis. To restore a known-good pre-upgrade copy, place it at
the configured `HOMEHUB_DB` path while the service is stopped, then restart
the compatible app version. Keep the original and the backup until the
household data has been checked. A pre-upgrade copy may lack data written
after it was made, so use it only when restoring is necessary.
