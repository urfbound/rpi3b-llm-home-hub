# Home Hub implementation agenda

This agenda condenses the approved product scope in
[PROJECT_PLAN.md](PROJECT_PLAN.md) into implementation work. All tab designs
listed here are approved. Initial Pi 3B Chat benchmarks support an optional
feature; combined app/model profiling remains before enablement. Work proceeds
in small feature branches, test-first; merge
to `main` only after the user approves the completed change.

## Current status

- **Foundations mostly in place:** account administration, roles and sessions,
  CSRF, login rate limiting, verified backup/restore, TLS deployment guidance,
  and schema versioning are implemented. Initial Ollama latency and memory
  measurements on the Raspberry Pi 3B are recorded in
  [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). App-only and app-plus-model
  profiling remain to be done.
- **Lists and Recipes:** Shopping, To-do, Chores, recipe privacy/notes,
  serving scaling, and Add to list snapshots are merged to main. Copy/Paste is
  awaiting review. Weekend, weather, and system status cards remain.
- **Calendar:** Month navigation, event details, daily/weekly/monthly/yearly
  recurrence, and their data-preserving schema upgrades are merged to main.
  Event participants, private-event access, and ICS filtering are committed
  for review. Per-user daily/monthly budget enforcement and Admin limits are
  implemented on a dependent feature branch. Recipe links, Home summaries,
  and retention settings remain.
- **Current app prototype:** login/admin, a single shopping list, recipe
  create/view, basic event creation and ICS export, and light/dark theme exist.
  The approved full tab designs are not yet implemented as complete features.
- **Design reviews:** account administration, Home, Lists, Recipes, Calendar,
  Weather, RSS, Games, and Chat are approved. Initial Pi 3B benchmarking
  supports optional Chat; combined app/model profiling and answer-quality
  review remain before enabling it for a household.

## Shared foundations and Admin

- Finish Raspberry Pi install/uninstall hardening. Tests now cover venv
  permissions, private environment-file mode and preservation, repeated runtime
  replacement without touching the checkout/database/backups, and uninstall's
  file-deletion boundary. Still verify backup-before-service-start/schema
  migration, one valid
  systemd/cron schedule after repeated installs, and all retained data/config
  ownership boundaries. Manually verify repeat install and uninstall prompts
  using the regular-user/sudo flow on the target hostname.
- Complete the Lists migration and test each supported upgrade path, including
  pre-upgrade backup creation, data preservation, rollback, and newer-schema
  refusal.
- Keep authorization server-side for every read and write. Cover shared and
  private access, house-wide read-only behavior, ownership, admin override,
  CSRF, account transfer/deletion, and session revocation with tests.
- Build feature modules separately for Lists, Recipes, Calendar, Weather,
  RSS, Games, and Chat as they are implemented. Keep the Flask app factory
  and migrations explicit and small.
- Complete Admin settings as each feature arrives: vertical cards, section
  jump links, fixed Home/Admin ends with reorderable middle tabs, backup and
  restore, account/session controls, and feature-specific settings.
- Measure app-only and app-plus-model resource use on the Pi. Initial model-only
  results are in [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). Targets are under
  256 MB application RSS where practical and under 10 GB for app, dependencies,
  and data, excluding OS and LLM files.

## Tabs and feature work

### Lists

1. Versioned schema migrations, legacy shopping-item preservation, and the
   v5 structured recipe tables are merged to main. The v6 recipe privacy and
   notes migration is merged to main; continue adding tests for every future
   upgrade.
2. Complete Shopping and To-do flows: list creation, selection, sharing,
   private allowlists, static shopping categories, item ownership, To-do
   assignment/due dates, collaborative editing, and item/list deletion
   authorization. Creation, selection, access controls, item assignment/due
   dates, and collaborative To-do editing are merged to main; deletion edge
   cases and responsive visual review remain.
3. Copy/Paste implementation is ready for review: matching ignores case and
   repeated whitespace; checked imports complete unique matches without
   changing ownership, while new items belong to the importing user. Malformed
   or ambiguous imports require correction, and changed lists require a fresh
   review before apply.
4. Weekly Chores and the Home chores card are merged to main: seven weekday
   lists, per-person and Everyone assignments,
   independent completion, creator/admin deletion, Sunday reset based on the
   Pi's configured local date, and Just my chores filtering. The remaining Home
   cards depend on calendar, weather, and system-status work.
5. Phone away-mode caching and checkmark-only sync by stable item ID are
   implemented and merged: pending changes persist across reconnects, while
   sync rechecks login and list permissions and reports concurrent edits and
   deleted items for review. Browser QA remains for the service-worker/offline
   flow on an HTTPS phone browser.

### Recipes

- Structured recipe creation, editing, deletion, privacy, per-user notes,
  serving scaling, and Add to list snapshots are merged to main, including an
  upgrade that preserves existing recipe text. Add to calendar with serving
  count and private-title protection are implemented on the current review
  branch; calendar event detail links back to the recipe with servings.
- Preserve authorized notes when sharing changes; prompt before removing notes
  belonging to users who lose access.

### Calendar

- Month grid, month/year selectors, previous/next navigation, Today, and the
  retained-year dropdown are merged to main. The year range defaults to three
  previous years plus ten future years. Calendar retention and archive controls
  are implemented on the current review branch.
- Event details, all-day/timed entry, edit/delete controls, title/description
  validation, and v7-to-v8 migration preserving existing events are merged.
  Timed events export to floating local times in ICS.
- Daily/weekly/monthly/yearly recurrence, one-year default end date, and
  single-occurrence or whole-series edits/cancellation are merged. Occurrences
  are materialized so they appear in month views and ICS downloads.
- Event participants, private-event details, Busy placeholders, ICS
  visibility filtering, per-user daily/monthly occurrence budgets, and Admin
  limit settings are implemented on dependent review branches. Recipe/meal-prep
  links and Calendar retention are implemented on their current review
  branches. Home weekend summaries remain.
- The current review branch derives the household calendar timezone from the
  admin-selected weather location (falling back to the Pi's local timezone).
  It configures zero to ten previous calendar years, archives years to ICS
  before deleting on a deliberate retention reduction, and automatically
  archives or deletes years as they age out. Failed exports leave event data
  intact and appear in Admin. Archive metadata and admin-only ICS downloads are
  included; files are stored in `calendar-archives` beside the database.

### Weather and system status

- The Open-Meteo client and shared SQLite cache are implemented on the current
  review branch. The provider request covers current, hourly, and daily fields;
  the cache refreshes after 30 minutes and can serve stale data while outbound
  access is disabled or a refresh fails. Admin location search/selection,
  outbound-access control, configurable 1–15-day forecast, and metric/imperial
  units are implemented on dependent review branches. Current conditions,
  hourly/daily cards, pressure change/trend, wind and precipitation,
  sunrise/sunset, moon illumination and named full moons, and stale-cache age
  are implemented on the current review branch. Tides remain hidden unless a
  supported station can be determined for the configured location.
- Disk, RAM, normalized CPU, one-hour CPU history, board temperature, and app
  RSS are implemented on the current review branch. Disk and RAM lead the
  system card; Admin can hide it from household users while optionally
  allowing the house-wide account. CPU history bars use the approved yellow
  60–80% and red above-80% thresholds.
- Deferred weather work: almanac, on-demand historical data, and alternate
  providers. Historical pulls stay off unless enabled by an admin.

### RSS

- The RSS/Atom parsing and feed URL safety foundation is implemented on the
  current review branch. It normalizes URLs, rejects non-public destinations,
  parses bounded article sets, strips HTML summaries, and resolves safe links.
- The personal/household feed registry schema is also implemented on the
  current review branch, with URL uniqueness across scopes and a tested v12
  upgrade that preserves existing household data.
- Personal feed add/remove/reordering and admin-managed house-wide feed
  add/remove/reordering are implemented on the current review branch. The
  server enforces duplicate checks across scopes, admin-only household changes,
  and a three-feed household limit. The house-wide list is viewable by all
  signed-in accounts.
- Bounded HTTP fetching now pins connections to DNS-validated public addresses,
  rechecks every redirect, enforces time/size/redirect limits, and supports an
  explicit admin approval for a private household feed host. LAN approval does
  not permit loopback or redirects to another private host. Interval-aware
  refresh selection now respects the outbound toggle and records per-feed
  success or failure. A short-lived systemd timer invokes the refresh runner
  every ten minutes; the runner applies the admin-configured 10–240-minute
  interval and outbound toggle. Saved articles continue to display while
  outbound access is disabled.
- Article persistence now stores feed content once and keeps read, favourite,
  and delete state per user; personal-feed visibility is owner-only. Article
  open tracking, feed-grouped display, Done reading with its configurable
  deletion prompt, favourite-deletion confirmation, and centralized Manage
  feeds controls, and the bottom favourites card are implemented. Cleanup
  expires household articles after three days and personal articles after 90;
  personal storage evicts oldest unread, then oldest read until within 64 MB,
  while favourites remain protected.

### Games

- All six first-release games are playable on the Games branch: Word Garden,
  Mini Crossword, Hunt the Wumpus, Word Ladder, Mini Nonogram, and Mini Sudoku.
  Daily puzzles share the household date; each game has at least 30 distinct
  offline puzzles, and Wumpus has 365 nonrepeating seeded maps. Practice uses
  past puzzles for the word and grid games; Wumpus offers a chosen or random
  seed. A v17 upgrade preserves v16 Word Garden attempts.
- Expanded offline content banks now include 5,000 word ladders, 5,000 unique
  8 × 8 nonograms, 5,000 unique 9 × 9 Sudoku puzzles, and 5,000 5 × 5 mini
  crosswords. The crosswords rotate among four distinct editors—Larry, Moe,
  Curly, and Shemp—with 1,250 puzzles each. Crossword clueing is a little above
  easy; most puzzles are Medium, with one in five Medium-Hard. Their style
  profiles and localization guidance are in
  [CROSSWORD_EDITORS.md](CROSSWORD_EDITORS.md). Ladder estimates use shortest-
  path length; Sudoku estimates use clue counts and are labeled as coarse
  rather than formal human-technique ratings. Unit tests check puzzle
  constraints, solvability, broad difficulty coverage, and daily selection
  across the full banks. These exhaustive checks are marked `bank` and skipped
  by default; run them on request with `pytest -m bank`.
- Private resumable attempts, midnight expiry, per-game streaks, score floors,
  optional hints where approved, daily and all-time scoreboards, timed-game
  tie breaking, and next-day solution visibility are implemented and tested.
  The house-wide account can play without ranking. Practice is unscored and
  capped at ten saved attempts per user and game.
- Hunt the Wumpus has a drag-rotatable 3D dodecahedron map above the room
  description. Only visited rooms show numbers; corridors to unvisited rooms
  are dashed. Emoji clues and death markers show what the player encountered.
  A local hide-map preference persists across page loads and does not enter
  scoring.
- Desktop and narrow-phone browser checks of the Games hub and selected puzzle
  pages in light and dark themes found no horizontal overflow. Sudoku was played
  through its server-rendered form without JavaScript. Pi resource profiling
  and broader household playtesting remain before release.
- Deferred game work: more than one daily puzzle per game and Pi-generated
  puzzle content. Broader playtesting should assess whether the Sudoku
  difficulty estimate matches household experience.

### Home

- The Chores and This weekend cards now occupy the first dashboard row. The
  weekend summary includes Friday through Sunday, invitee and description
  sub-lines, timed-event ranges, private-event placeholders, and Just my events
  filtering. This slice is implemented on the current review branch. Weather
  and system status cards remain; keep the responsive two-column desktop
  layout and single-column narrow-phone layout.

### Chat

- Keep Chat hidden by default. Initial Pi 3B warm latency and model-only memory
  are measured; see [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). The
  `feature/chat-initial-release` branch now has test-verified admin-only
  enable/model settings, loopback-only Ollama model discovery, configurable
  queue and pair limits, and private conversation history with pin/delete
  rules, history caps, account isolation, admin visibility, and a schema
  upgrade. Its responsive conversation view now sends bounded prompts through
  one lazy in-process worker, prioritizes admins among waiting requests, shows
  queue positions, and records successful answers with the model name. Ollama
  requests use a 2048-token context and unload the model after each response.
  Read-only list and calendar tools are implemented behind an admin toggle
  that defaults off. They enforce requester visibility, a 31-day calendar
  window, three tool calls per request, and 100-row/16-KiB result caps. The
  Pi resource-budget measurement remains; keep database tools disabled until
  that measurement passes. App-plus-model profiling, answer-quality review,
  and manual browser review of the approved design also remain.
  Before enabling Chat for household use, profile Home Hub and the selected
  model together, then verify answer quality.
- Add a provider interface for possible remote models, off by default. If
  database tools are enabled, enforce the approved read-only per-user scope
  and resource budget; require explicit confirmation and fresh authorization
  for proposed writes. Never expose Ollama beyond loopback.

## Deferred integrations

- Outbound email, including list and calendar archive delivery.
- Inbound email controls.
- Multiple daily puzzles, locally generated puzzle banks, weather almanac,
  on-demand historical weather, and alternate weather providers.

## Release gates

- Test-first workflow: write a failing behavior test before implementation,
  then implement, run focused tests, and run the full suite.
- Exercise permission, privacy, validation, failure, deletion, and migration
  boundaries. Verify old supported databases upgrade without data loss.
- Verify every approved tab at desktop and narrow-phone sizes, in light and
  dark themes, with keyboard/touch access and server-rendered fallback paths.
- Verify away mode on an HTTPS phone browser: open a shopping list while
  connected, reload once so the service worker controls it, turn on airplane
  mode, check items, then reconnect and test login expiry, concurrent changes,
  deleted items, and list access changes. Pending changes must remain visible
  until they are synced or explicitly discarded.
- Profile deployment, backup/restore and recovery, TLS and LAN-only
  exposure, and the LLM separately and alongside the app. Do not release until
  account, data, resource, and upgrade checks pass.
