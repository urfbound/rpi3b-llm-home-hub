# Home Hub implementation agenda

This agenda condenses the approved product scope in
[PROJECT_PLAN.md](PROJECT_PLAN.md) into implementation work. All tab designs
listed here are approved. Initial Pi 3B Chat benchmarks support an optional
feature; combined app/model profiling remains before enablement. Work proceeds
in small feature branches, test-first; merge
to `main` only after the user approves the completed change.

## Current status

- **Foundations mostly in place:** account administration, roles and sessions,
  CSRF, login rate limiting, verified backup/restore, LAN HTTPS setup with
  loopback-only Waitress and Caddy, TLS deployment guidance,
  and schema versioning are implemented. Initial Ollama latency and memory
  measurements on the Raspberry Pi 3B are recorded in
  [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). App and app-plus-model profiles
  are now recorded; answer-quality review remains.
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
  file-deletion boundary. Tests also verify that a database backup precedes
  service activation and that repeated legacy-cron cleanup preserves unrelated
  jobs. Manually verify repeat install and uninstall prompts, the systemd
  timers, and retained data/config ownership on a Pi using the regular-user/sudo
  flow on the target hostname.
- Release installation path is prepared: tagged GitHub releases build a
  versioned source bundle with a SHA-256 manifest; install and upgrade
  bootstraps fetch the latest release and run the existing backup-preserving
  installer. Before the first release, verify a clean Pi install, an upgrade
  from the previous supported release, checksum failure handling, and the
  public `curl | sh` instructions. The repository must remain public for
  unauthenticated release downloads.
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
- Pi app-only and app-plus-model resource profiles are recorded in
  [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). Targets are under 256 MB
  application RSS where practical and under 10 GB for app, dependencies, and
  data, excluding OS and LLM files.

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
  and a three-feed household limit. Adding a feed triggers an immediate first
  refresh; failures are shown to the user, recorded, and retried after the
  configured interval. When outbound access is disabled, the feed is saved and
  the skipped refresh is explained. The house-wide list is viewable by all
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
- Future investigation: evaluate increasing RSS article retention while
  preserving at least 20% of the 10 GB app/data budget for other growth. Model
  low, medium, and high household usage across up to 32 accounts; measure
  SQLite payload, indexes, WAL/temporary growth, favourites that outlive normal
  retention, and retained database backups together. Do not raise quotas until
  measured totals show adequate headroom and pruning/backup limits are tested.

### Games

- All six first-release games are playable from the Games tab in main
  navigation: Word Garden,
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
- Mini Crossword bank rebuilt from the attributed Canadian English word source
  and curated clue definitions. All 5,000 grids have ten distinct entries;
  adjacent days differ by at least eight open letters and share no more than
  two answers; one-letter variants are spaced over 30 days apart. The previous
  formulaic sound effects and asides are gone. Bank tests cover these limits
  and reproducibility. Before release, conduct broader human solving review
  for clue fairness, editor voice, and the Medium/Medium-Hard labels. See
  [CROSSWORD_QUALITY_AUDIT.md](CROSSWORD_QUALITY_AUDIT.md).
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
- **Contacts:** The Home card now supports up to 128 owner-managed contacts
  with name, phone, email, organization, and home address. Private/shared
  visibility is enforced server-side; shared contacts are read-only to others.
  Owners can edit and delete contacts. The card has search, duplicate warnings,
  scrollable results, and one-at-a-time phone import when supported. Browser
  device verification and visual review on phone/light/dark remain.
- **Sticky Notes:** The new page supports 32 active notes per author, 128
  characters per note, eight colors, direct recipients or everyone, optional
  1-to-365-day expiry, and author-only deletion. Expired notes stop displaying
  and no longer use quota. Responsive bento cards show author/date and audience.
  Sticky Notes is in the shared feature-tab order. Admins can move feature tabs;
  Home remains first and Admin last. Automated privacy, limits, migration, and
  navigation tests pass. Phone/browser review and visual light/dark review
  remain. Add an optional sticky-note stacking layout after a later design
  review.

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
  Combined Pi profiling is recorded in
  [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). Qwen2.5 0.5b retained 244 MiB
  available with the app running and is the current Pi 3B choice for tools;
  Qwen3 0.6b dropped to 32 MiB and is not recommended on 1 GB hardware.
  Representative answer-quality review and manual browser review of the
  approved design remain before household rollout.
- Add a provider interface for possible remote models, off by default. If
  database tools are enabled, enforce the approved read-only per-user scope
  and resource budget; require explicit confirmation and fresh authorization
  for proposed writes. Never expose Ollama beyond loopback.

## Deferred integrations

- Outbound email, including list and calendar archive delivery.
- Inbound email controls.
- Multiple daily puzzles, locally generated puzzle banks, weather almanac,
  on-demand historical weather, and alternate weather providers.
- Investigate and plan an optional offline full-text English Wikipedia library
  using the no-picture edition. Store downloaded archives outside the
  repository and outside the existing application/data storage budget; do not
  ship content in the repository. Enabling it must be an explicit admin action
  that triggers the initial download, with a configurable 3–12-month refresh
  interval and no refresh more frequently than quarterly. In-place replacement
  is acceptable; keeping the old archive during an update is not required.
  Compare an existing reader/server (including Kiwix) with direct archive
  reading through libzim or another maintained reader, and a small dedicated
  server only if it is more efficient. Prioritize low idle RSS and CPU, bounded
  concurrent access, low update-time resource use, ARM Pi 3B compatibility,
  and maintainability; measure these on the target before enablement. Current
  Kiwix catalog reference: English all-articles no-picture is 49 GB (2026-06).
  Its FAQ says ZIM updates are not incremental, so storage and failure/recovery
  behavior for in-place replacement must be documented. Kiwix describes
  `kiwix-serve` as lightweight and Pi-capable, but its actual RSS and CPU for
  this archive and household concurrency remain unmeasured. Sources:
  [Kiwix catalog](https://download.kiwix.org/zim/wikipedia/),
  [Kiwix FAQ](https://get.kiwix.org/en/faq/),
  [kiwix-serve documentation](https://github.com/kiwix/kiwix-tools/blob/main/docs/kiwix-serve.rst),
  [libzim](https://github.com/openzim/libzim).
- Add an opt-in offline OpenStreetMap feature. Keep map files outside the
  repository and existing application/data storage budget. Require explicit
  admin enablement and a selected Home location before any download; provide
  robust area controls, including radius around a chosen location and
  selectable municipality/city, region, province/state, and country coverage,
  with clear area previews and size estimates where the source permits. Define
  whether multiple areas can be combined and how overlapping coverage is
  handled. Updates must be admin-configurable from 3–12 months and never run
  more frequently than quarterly. Choose data and serving formats with low
  Pi 3B idle memory and CPU use in mind; document attribution/licensing,
  update failure behavior, and the storage needed for downloads and temporary
  files. Detailed comparisons of Ontario/Quebec extracts and Toronto-radius
  packs (100/500/1,000 km) are dropped; the household accepts the likely larger
  packs, subject to displaying estimates before download and keeping those
  files outside the application's existing storage budget. Initial references:
  Geofabrik lists Ontario at 984 MB and Quebec at 1.1 GB raw PBF; IIAB reports
  13–39 MB of vector data for example 100 × 100 km regions at zoom 14. These are
  contextual references, not user-facing pack guarantees. The standard OSM
  tile.openstreetmap.org service prohibits offline/bulk tile downloads; use
  self-hosted extracts or an explicitly permitted source. Sources:
  [Geofabrik Canada](https://download.geofabrik.de/north-america/canada.html),
  [IIAB maps](https://github.com/iiab/iiab/blob/master/roles/maps/README.md),
  [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
- Add a feasibility work item for local Chat agent tools over the Wikipedia
  library. Consider bounded, read-only retrieval with article citations,
  authorization, prompt-injection resistance, and strict result/context caps.
  Review title search versus full-text search, relevant passage extraction, and
  whether this can use the existing single Chat worker and loopback-only model
  flow without introducing a separate always-on indexing service. Test CPU,
  memory, response time, and quality on the Pi before enabling tools; tool use
  must remain behind the existing admin controls.
- Investigate downloadable Project Gutenberg collections and size. Kiwix's
  English all-books ZIM was about 206 GB in the 2025-11 catalog, versus about
  72 GiB for an older 2023-08 edition. Compare the
  full collection with practical subsets and direct ebook formats; establish
  current sizes, licensing/redistribution constraints, serving approach, and
  whether this merits a separate opt-in library.
- Investigate free/open offline dictionaries and thesauri, including English
  Wiktionary (full and smaller/simple editions), WordNet/Open English WordNet,
  and OpenThesaurus. Compare data sizes, language coverage, licenses, update
  needs, and simple lookup integration for household users and the local LLM.
  Prefer compact machine-readable datasets and a lightweight local lookup
  implementation where practical; do not assume a general Kiwix service is
  needed for dictionary or thesaurus lookup.

## Post-R1.0

- Add Chat working-state bubbles: show `🧠...` while the model is reasoning and
  `🗣️...` while it is composing a response.
- Add more read-only and proposal-based Chat tools, including RSS summaries and
  adding a day's recipes to a shopping list. Any write proposal must be shown
  for confirmation and re-authorized by the server before applying it.
- Allow users to navigate away from an in-progress Chat conversation while the
  server-side request continues, then return to see its status and response.

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
