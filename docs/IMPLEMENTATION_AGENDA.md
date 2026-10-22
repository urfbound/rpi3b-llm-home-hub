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
- **Lists is the active workstream:** schema version 3 and the initial Shopping
  and To-do workflows are merged to main. Copy/Paste is implemented on
  `feature/list-copy-paste` and awaiting review. The migration creates
  named-list/private-access tables and links legacy shopping items to a shared
  Groceries list without changing item IDs or content. Shopping and To-do lists
  support shared/private visibility, private allowlists, item creation and
  completion, and creator/admin deletion controls.
- **Current app prototype:** login/admin, a single shopping list, recipe
  create/view, basic event creation and ICS export, and light/dark theme exist.
  The approved full tab designs are not yet implemented as complete features.
- **Design reviews:** account administration, Home, Lists, Recipes, Calendar,
  Weather, RSS, Games, and Chat are approved. Initial Pi 3B benchmarking
  supports optional Chat; combined app/model profiling and answer-quality
  review remain before enabling it for a household.

## Shared foundations and Admin

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

1. Finish verification of the versioned schema and legacy shopping-item
   migration, including all supported upgrade paths and recovery behavior.
2. Complete Shopping and To-do flows: list creation, selection, sharing,
   private allowlists, static shopping categories, item ownership, To-do
   assignment/due dates, and item/list deletion authorization. Basic creation,
   selection, and access controls are implemented on the active branch; full
   editing, assignment, deletion edge cases, and responsive visual review
   remain.
3. Implemented on the feature branch: plain-text Copy list and reviewed Paste
   list. Matching ignores
   case and repeated whitespace; checked imports complete existing items
   without changing their owner, while new items belong to the importing user.
   Private-list access is rechecked at apply time, and exported text is
   non-cacheable.
4. Implement seven persistent weekday Chores lists, user and Everyone
   assignments, independent completion, creator/admin deletion, and a Sunday
   midnight reset in the household timezone. Add the Home chores card and
   user filter.
5. Implement phone away-mode cache and checkmark-only sync by stable item ID,
   including login renewal, permission checks, conflicts, deleted items, and
   retained pending changes.

### Recipes

- Add create, edit, view, and delete flows; structured ingredients and ordered
  steps; serving scaling; limits and validation; per-user notes and sharing
  privacy; Add to list snapshots; and Add to calendar with serving count.
- Preserve recipe notes for users who retain access when sharing changes;
  prompt before removing notes for users who lose access.

### Calendar

- Build month view with month/year selectors, navigation, Today, retained years,
  event details, all-day and timed events, and daily/weekly/monthly/yearly
  recurrence with single-occurrence or series edits/cancellation.
- Add event participants and privacy, event title/description limits, per-user
  daily/monthly budgets, ICS visibility filtering, recipe/meal-prep links, and
  Home weekend summaries for Friday through Sunday.
- Add admin timezone and retention settings. Archive excluded years to ICS
  before deletion, report failed exports without deleting events, and keep
  archive metadata. Include the ICS calendar archive template behavior.

### Weather and system status

- Use the selected Open-Meteo source, admin-set household town/postcode,
  attribution, outbound-access control, configurable 1–15-day forecast,
  metric/imperial units, and a shared cache no fresher than every 30 minutes.
- Implement current, daily, and hourly cards, pressure change/trend, wind and
  precipitation, sunrise/sunset, moon illumination and named full moons, and
  the nearest supported Canadian tide station. Hide unsupported tide data and
  show stale-cache age on fetch failure or when outbound access is disabled.
- Add disk, RAM, CPU, one-hour CPU history, temperature, and app RSS to Home;
  respect the admin visibility setting and use per-core CPU thresholds for
  history colors.
- Deferred weather work: almanac, on-demand historical data, and alternate
  providers. Historical pulls stay off unless enabled by an admin.

### RSS

- Add personal feeds and admin-managed house-wide feeds, ordering and
  management, duplicate normalization across both scopes, safe URL fetching,
  refresh settings, and outbound-access behavior.
- Store shared article bodies once with separate per-user read, favourite,
  and delete state. Implement article-open read tracking, Done reading
  behavior, prompt preference, favourite confirmation, article retention and
  quota eviction, and the bottom favourites card.

### Games

- Implement all six first-release games: Wordle-style word guessing, mini
  crosswords, Hunt the Wumpus, word ladders, mini nonograms, and Sudoku.
- Start with a curated offline bank covering at least 30 days. Add daily
  household-wide puzzles, practice mode, scoring and hints, per-user streaks,
  visible daily/all-time scoreboards, and house-wide play without leaderboard
  ranking.
- Use visible daily/practice seeds only where appropriate; make Hunt the
  Wumpus maps deterministic and avoid repeats within a rolling year. Keep
  score floors, tie-breaking, attempt expiry, and answer visibility covered
  by tests.
- Deferred game work: more than one daily puzzle per game and Pi-generated
  puzzle content. Sudoku generation/difficulty rating is a later benchmark.

### Home

- Complete the dashboard after its data sources exist: first row Chores and
  This weekend; then current weather and system status. Show invitees and event
  descriptions under weekend titles, support Just my events, and apply privacy
  rules. Keep the responsive two-column desktop layout and single-column
  narrow-phone layout.

### Chat

- Keep Chat hidden by default. Initial Pi 3B warm latency and model-only memory
  are measured; see [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). Before
  enabling it, profile Home Hub and the selected model together, then verify
  answer quality. Implement one installed model at a time, one queued request
  at a time, admin priority, queue depth and position, per-user private chat
  history and pin/delete limits, and 128-character prompts.
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
- Profile the Pi deployment, backup/restore and recovery, TLS and LAN-only
  exposure, and the LLM separately and alongside the app. Do not release until
  account, data, resource, and upgrade checks pass.
