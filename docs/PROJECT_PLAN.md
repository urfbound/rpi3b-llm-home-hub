# Home Hub product plan (draft)

This document records decisions made during planning. Open questions below
must be settled before the affected feature is designed and built. Show the
user the visual design of **each feature** and obtain feedback before
implementing it. Implementation will proceed in small, tested feature
increments on feature or ticket branches. Merging into `main` requires the
user's explicit confirmation.

The first-release scope and game rules are agreed. Visual designs for the
account administration, Home, Lists and navigation, Recipes, Calendar,
Weather, RSS, Games, and Chat tabs are approved. Chat remains contingent on
combined app-and-model profiling and answer-quality review described below;
initial model-only Raspberry Pi 3B benchmarks support an optional feature.

## Product and release

- A cozy, local-first household hub on a Raspberry Pi 3B, available on the
  household LAN, with light and dark themes and server-rendered pages.
  Design every feature to be visually attractive and easy to use on both
  desktop and mobile, with responsive layouts, legible text, and comfortable
  touch targets. Keep JavaScript small and preserve usable server-rendered
  paths. Changes made by the current user should appear immediately. Open
  shared lists and calendars should pick up other users' changes while the
  page is visible, checking roughly every 20–30 seconds.
- First release includes secure accounts, lists, recipes, calendar, weather,
  RSS, and all six games below. It can ship with Chat hidden if the Pi cannot
  run useful local answers within acceptable resource use.
- Home shows preview cards in two columns when space permits. Put today's
  Chores and **This weekend** (calendar items for the upcoming Friday through
  Sunday) in the first row, followed by current weather and system status cards.
  The weekend card can filter to events created by or requiring the signed-in
  user. Each event title links directly to its Calendar detail, with a smaller
  end time immediately after the start time and indented lines for invitees
  and the description. Apply event and recipe privacy rules to these lines.
  The chores card groups tasks by person and can filter to the signed-in user;
  open and completed tasks use circle and checkmark markers without list
  bullets or strikethrough. Cards stack on narrow
  screens. Each feature has its own main navigation tab. Weather and RSS may
  use cards within their tabs. Use the balanced desktop layout: 32 px side
  margins up to a 1440 px content width.
- Home includes a Contacts card. Each account owns up to 128 contacts with a
  required name and optional phone, email, organization, and home address. Contacts are
  private by default or can be shared read-only with the household. Owners can
  add, edit, change visibility, and delete contacts. Phone import is one
  contact at a time from the browser's contact picker, with a review step;
  manual entry always remains available. Import name, phone, email,
  organization, and home address when the device and browser support it;
  duplicate phone/email matches warn but do not block save.
- Add a Sticky Notes tab with up to 32 active notes authored per user. Notes
  are limited to 128 characters, use one of eight fixed named colors, and are
  sent at creation to individually selected users or everyone. Each author's
  tab shows their notes; recipients' tabs show notes addressed to them. Everyone
  includes the author. Notes can optionally expire 1 to 365 days after
  creation. Expired notes disappear from all walls and no longer count toward
  the active limit. Notes cannot be edited after creation and can only be
  deleted by their author. Render notes as responsive
  bento cards with author and creation date. Deleting a note frees its active
  slot. Home remains first and Admin last; Sticky Notes is reorderable with the
  other feature tabs.
- An admin can set one household-wide order of feature tabs from the Admin tab
  only. Keep Home fixed at the start and Admin fixed at the end; the feature
  tabs between them can be reordered. The control's visual design is approved.
- Home includes a Contacts card. Each account may own up to 128 contacts with
  a required name and optional phone, email, organization, and home address.
  Contacts are private by default or shared read-only with everyone. Owners can
  create, edit, change visibility, and delete. Phone import is one contact at a
  time when the browser supports it, with a review step and manual fallback.
- Add a Sticky Notes feature tab. Users can create up to 32 active notes, each
  up to 128 characters, select one of eight colors, and address a note to
  selected users or everyone. Notes can optionally expire in 1 to 365 days;
  expired notes leave recipients' walls and free quota. Notes cannot be edited
  after posting and only authors can delete them. Show notes as responsive
  bento cards with author, date, and audience. Admins control feature tab order;
  Home and Admin stay at the ends.
- Stack all Admin cards vertically, with Accounts first. Provide jump links
  to each feature's household configuration section; keep Admin's own settings
  in the Admin tab. Show Calendar limits before Calendar retention.
- Target at most 32 users and four admins. Aim for under 256 MB application
  steady-state RSS and under 10 GB for application, dependencies, and data,
  excluding the OS and local model files.
- Use Flask, SQLite, small vanilla JavaScript, pytest, and systemd on the Pi.
  Keep the LLM runtime on loopback. No public Internet exposure.
- Treat upgrades of running households as a primary design requirement. Never
  reset an existing database to apply a release. Record a schema version,
  apply ordered data-preserving migrations, and refuse to open a database
  created by a newer app version. Make a verified pre-upgrade backup before
  changing an existing database. Test upgrades from the current prototype
  and each supported release with representative data, including ownership,
  passwords, and relationships. Document a recovery path if an upgrade fails.
  Version the phone's offline store as well, migrating pending checklist
  changes across app updates rather than silently discarding them.
- Make local backups automatically every day by default. Admins can choose a
  backup interval of one to 31 days or turn automatic backups off, set the
  storage folder and retention count (14 copies by default), download a
  backup, and restore one. Restoring requires confirmation and briefly
  interrupts the app. Verify each backup before retaining it, and verify a
  selected backup before restoring. Restore revokes existing sessions so
  users log in again. Run scheduled checks as a persistent systemd timer,
  separate from the web process, so missed checks run after downtime.
- The project uses GPLv3.

## Accounts, visibility, and sharing

- Only admins create or delete accounts and reset passwords. Users may change
  their own passwords. Password resets issue a one-time temporary password
  and force a change. Reset and deletion revoke existing sessions.
- Sessions expire after 24 hours idle or seven days absolute by default.
  Both limits are configurable by admins. Require CSRF tokens for state
  changes. Limit failed sign-ins to five per username and 30 per source
  address in a rolling 15-minute window; retain no raw usernames or addresses
  in the limiter and expire old attempts.
- Items are shared by default; private items can have a per-user allowlist.
  Admins may view all content and delete any content. Ordinary users edit or
  delete only content they created, except that participants may freely edit
  an invited shared shopping list. Show who added each list item.
- A house-wide account serves shared displays and guests. It is read-only
  for household content, sees shared items and its own admin-managed RSS
  feeds, may play games without appearing on leaderboards, and cannot chat.
  Guests log in with a shared password set by an admin; there is no
  password-free guest entry.
- On account deletion, admins must transfer or delete the user's lists,
  recipes, and events. Delete the user's chats, scores, and personal feeds.
  Warn before deleting a shared list containing other contributors' items.

## Lists and recipes

- Keep one top-level Lists tab with Shopping, To-do, and Chores sections in
  that order. Chores has exactly one permanent shared list for each day of the
  week. Members may add chores and mark them complete; each chore records its
  creator and assignee. Show a subheading for each person in a day list, with
  an **Everyone** filter option that shows all groups and a per-person option
  that shows one group. **Assign to Everyone** repeats the chore under each
  personal account, with independent completion for each person; the shared
  house-wide login is not a person to assign chores to. Deleting that chore
  removes the definition from every person's group. A chore stays in its day
  list until its creator or an admin deletes it. Clear all per-person chore
  completion states at Sunday midnight in the household time zone, leaving
  tasks and assignments in place. Home shows the current day's chores grouped
  by person, with a **Just my chores** checkbox to filter to the signed-in
  user's assignments.
  The Chores visual design is approved, including per-person groups and the
  exclusion of the shared house-wide login from Everyone assignments.
  To-do remains a separate section, with its visual design approved.
  To-do checklists are collaborative: invited members may add, edit, and
  complete tasks. At creation, the user may decide optional due dates and
  assignees for each item separately.
- Show a distinct Delete action for items whether complete or incomplete.
  Show a Delete list action to its creator and admins, with confirmation that
  its items, including contributions from others, will be removed.
- Let users copy a list as plain text, including its name and every visible
  item marked open or complete, so it can be carried outside the house.
  On insecure HTTP, offer selectable text when one-tap clipboard access is
  unavailable. Emailing lists is a deferred feature dependent on outbound
  email.
- Add **Paste list** to a selected shopping or to-do list. Accept the copied
  format: an optional title followed by `[ ] Item` for open items and
  `[anything nonblank] Item` for checked items. Show a review before applying
  it. Match existing items within that list by trimmed, case-insensitive text
  with repeated whitespace collapsed. A unique match may be checked off by
  a checked pasted line; an open pasted line never unchecks an existing item.
  Add unmatched items in the pasted state and record the importing user as
  their creator. Preserve existing item ownership, text, and other metadata;
  do not delete anything. Collapse repeated pasted names, treating any
  checked occurrence as checked. Flag ambiguous matches and malformed lines
  for review rather than guessing. Recheck permissions and current list data
  when applying the review. New to-do tasks start without a due date or
  assignee. Paste requires a connection to the hub and is unavailable to the
  read-only house-wide account.
- Make a shopping list already opened on a phone usable away from the LAN.
  Cache its visible contents on that device and persist checkbox changes
  locally. Show when the hub is unreachable and how many changes await sync.
  When the hub becomes reachable again, upload desired checkbox states by
  stable item ID, rechecking login and list permissions. Keep pending changes
  through session expiry and prompt for login before syncing them. If another
  person changed the same item, show the conflicting states for review rather
  than silently replacing either. Explain deleted items or lost access.
  Offline creation, text edits, sharing, and deletion are not in this initial
  scope. Keep a stable home URL and HTTPS so the page can be reopened offline
  with a small service worker; browser storage remains best effort and may be
  cleared by the browser or user. The copy action is a portable fallback when
  offline storage is unavailable. Sync only on actual hub reachability while
  the page is active, rather than relying solely on the browser's online flag
  or background sync support.
- Support multiple shopping lists with static categories: Groceries,
  Household, Gifts, Health & Personal Care, Pets, Home & Garden, School &
  Activities, and Other. A list's creator or an admin may delete the list.
- Recipes have structured ingredient quantity, unit, name, and optional
  notes, plus a default serving count shown at the top. Users can scale
  quantities to another serving count.
- Limit each user to 128 recipes, each recipe to 64 structured ingredients and
  64 ordered steps, and each step to 256 characters. Give each user at most
  one attributed note per recipe, editable or removable by that user and
  limited to 128 characters. Show notes as text by default; provide Edit
  and Delete controls with at least 44 px touch targets beside a user's own
  note and reveal the editor beneath it only when requested. Confirm before
  deleting a note. Recipe viewers can read the notes. When sharing
  is narrowed, keep notes from people who retain access and warn before
  removing notes belonging to people who lose access. The house-wide account
  can read shared recipes and notes but cannot add notes. Limit titles to 128
  characters, descriptions to 256, and ingredient quantity, unit, name, and
  note to 32, 16, 128, and 128 characters respectively.
  At 32 users filling every recipe, ingredient, step, and note slot, this is
  4,096 recipes, 262,144 ingredients, 262,144 steps, and up to 131,072 notes.
  With the agreed 128-character note limit, a SQLite layout with these text
  caps is roughly 184–204 MiB for one-byte UTF-8 text, or around 651–701 MiB
  if text fills every field with four-byte UTF-8 characters. This estimate
  excludes images, other features, backups,
  and SQLite WAL files.
- A deliberate **Add to list** action copies recipe ingredients into an
  editable shopping-list snapshot. Combine matching ingredient names only
  when units convert reliably; leave uncertain combinations separate.
  Initially support common weight, volume, and count units and free text
  such as “to taste.” Later recipe or event edits do not silently change
  copied shopping items.
- Put **Add to calendar** next to Add to list on the recipe detail. Store the
  selected serving count with the linked calendar event, and let its recipe
  link reopen the recipe at that serving count.

## Calendar

- Support all-day and timed events, plus daily, weekly, monthly, and yearly
  recurrence. Edit or cancel a single occurrence or the entire series.
  Recurrence requires an end date, defaulting to one year.
- The household calendar time zone is derived from the admin-selected weather
  location, falling back to the Pi's local time zone. Calendar views and event
  budgets use that zone. Add month view and local, one-way downloadable ICS; no cloud
  synchronization in the first release. The month view has month and year
  dropdowns, with previous/next buttons around the month on the left, the year
  dropdown centered, and Today on the right. The year dropdown covers every
  retained previous year and future years. Admins choose how
  many previous calendar years remain active, from zero to ten; default three
  previous years plus the current year.
- Events may link to recipes. A visible event linked to a private recipe
  shows **Private recipe** to viewers without recipe access. Meal slots are
  breakfast, lunch, and dinner. A meal-prep event template links exactly one
  recipe; users may create multiple meal-prep events. When a linked recipe is
  private, use **Meal prep** as the event's default title, never automatically
  expose its name in the month view, event title, Home summary, or ICS export.
  If an autogenerated title exists when a recipe becomes private, replace it
  with **Meal prep**. Authorized viewers can see the linked recipe in event
  details; other viewers see **Private recipe**.
- Each user, including admins, has a configurable budget, initially eight
  event occurrences per day and 128 per month. Admins can set the monthly
  budget from seven to 512. Recurring occurrences count. Limit event titles
  to 128 characters and descriptions to 256 characters; show the remaining
  description character budget while editing. At the default monthly budget
  and 32 users, a full year has at most 49,152 occurrences. For sizing, assume
  no attachments and all 32 users attached to every event (up to 1,572,864
  event-participant links). A representative SQLite layout with indexes
  measured about 85 MiB with one-byte text or 160 MiB with all text fields
  filled by four-byte UTF-8 characters. Budget roughly 100–120 MiB or
  180–220 MiB per full year to allow schema overhead. The configured maximum
  of 512 occurrences per month can use roughly four times this space.
- Users can be added to events. A private event appears as **Busy** to people
  without access. ICS downloads contain only event details the downloading
  user may view. The creator and admins can add or remove event participants;
  participants can view full event details, including for private events, but
  cannot edit the event. Participation does not grant access to a linked
  private recipe. There is no RSVP or attendance tracking in the first
  release. An optional **Only events requiring me** ICS filter includes
  events the user created or was added to, and excludes generally shared
  events without that user's participation.
- When a year leaves the active calendar retention window, archive it to a
  downloadable local ICS file, then remove its active events. When an admin
  reduces retention, first attempt to archive every newly excluded year;
  keep its events and report the error if export fails. Show the resolved
  archive folder in the Admin Calendar retention card; by default use a
  `calendar-archives` folder beside the configured database. Admins may
  choose direct deletion for automatic age cleanup, but a deliberate
  reduction of retention still attempts ICS export first. Admins may choose
  an earlier cleanup age in months or years. Archives are downloadable by
  admins from settings and are not searchable in the active calendar. Store archive period
  and file metadata so a later outbound-email feature can send them without
  changing the archival flow. Emailing archives is a future feature dependent
  on outbound email.

## Weather, RSS, and system status

- Weather uses one admin-set household town or postal code and Open-Meteo as
  the first provider, with required attribution. Admins can turn outbound
  weather and RSS access off. Show a five-day forecast by default; admins
  can choose one to 15 days and switch between metric (default) and imperial
  units. Keep the household town visible. The current-day summary shows
  high/low temperature, wind, precipitation chance and predicted daily
  accumulation ("None" when zero), plus pressure reading and three-hour
  trend. Show sunrise and sunset beneath the pressure summary in separate
  cards, using the household location's local time. Daily cards show high/low
  temperature, conditions, and precipitation chance. Today's hourly view
  shows temperature, conditions, precipitation
  chance, wind conditions, and barometric pressure with a
  rising/steady/falling trend based on the preceding three
  hours of provider forecast data. Show the pressure change amount; when a
  defensible threshold is available, qualify slower and faster changes.
  After rounding the three-hour change to 0.1 hPa, use the
  [Met Office pressure-tendency bands](https://weather.metoffice.gov.uk/guides/coast-and-sea)
  as indicative labels: under 0.1 hPa steady, 0.1–1.5 slowly, 1.6–3.5
  rising/falling, 3.6–6.0 quickly, and above 6.0 very rapidly. These bands
  describe observations; the hub applies them to model data and labels the
  result as a forecast. Display hPa in metric mode and inHg in imperial mode.
  Refresh the shared weather cache no more often than every 30 minutes.
  When fetches fail or outbound access is off, show cached data with its age
  and mark it stale after two hours; explain when no cached forecast exists.
  Beneath the daily forecast, show moon phase and a tide-station card. Use
  Open-Meteo's daily moon phase value to draw a representative illuminated
  Moon for the household date and hemisphere, with an approximate percent
  illuminated. Do not add a separate routine Moon data poll. Use local
  almanac rules for traditional names such as Harvest or Hunter's Moon; apply
  a name only to its full Moon or label the next named full Moon separately.
  For a Canadian household town, show predictions for the nearest Canadian
  Hydrographic Service station with valid high/low tide predictions, including
  the station name, distance, times and heights, even for an inland town. Hide
  the tide card when no supported station predictions are available, including
  for locations outside Canada. Do not substitute Open-Meteo's gridded marine
  sea-level estimates for station tide tables. Mark data age and source
  clearly, and cache these optional values for offline display. Tide readings
  are for planning, not navigation.
  An almanac sub-tab, on-demand historical weather, and alternative providers
  are deferred features.
- Users manage personal RSS feeds; admins manage feeds for the house-wide
  account. Group articles by feed, with a personal user's own feeds first and
  house-wide feeds below them. A separate Manage feeds view lets users remove
  and reorder their own feeds; only admins can delete or reorder house-wide
  feeds. Limit the house-wide feed list to three feeds. Adding
  a feed should be easy. Reject a personal subscription when its feed URL is
  already in the house-wide list, as well as duplicate subscriptions within
  the same list. Normalize URLs for duplicate checks and test this rule again
  at the backend boundary when RSS is implemented. Refresh every 30 minutes
  by default; admins can choose an interval from ten to 240 minutes. Keep up
  to 30 ordinary articles per feed. When outbound access is off, show cached
  items with a last-updated time. Ordinary users may add public web feeds;
  only admins may add trusted feeds hosted on the household LAN. RSS cards
  show the title, source, publication date, and a short text summary, without
  loading remote images.
  Accept HTTP(S) feed URLs with size and timeout limits; recheck destinations
  after DNS resolution and redirects. Block loopback and other unsafe
  addresses. Only an admin can explicitly approve a LAN destination.
- Track read/unread, favourites, and article deletion per user, including for
  house-wide feeds when straightforward, so one person's actions do not change
  another's shelf. If per-user tracking proves too costly, house-wide read
  state may be ignored. Keep house-wide articles for no more than three days;
  personal article retention follows the existing 90-day/64 MB per-user cap.
  Provide a deliberate Done reading action that marks an article read and
  prompts whether to keep or delete it. The prompt has a Don't ask me again
  option that repeats the chosen action; users can restore prompting in
  Manage feeds. Test persistence and both automatic choices when implementing
  user settings. Hide Done reading after an article is read, and mark it read
  when its link is opened. Read articles cannot be marked unread. Users can
  also delete articles at any time; deleting a favourite requires confirmation.
  Show all of a user's favourites in a card below the feed groups. Allow at
  most 32 favourites per user and never automatically delete them. Retain
  ordinary articles for at most 90 days and cap each user's visible saved
  articles at 64 MB. At capacity, remove the oldest unread ordinary article
  first; once none remain, remove the oldest read ordinary article. Store shared
  article content once with separate per-user state.
  If favourites alone fill the quota, stop saving new articles for that user
  until they free space; do not silently delete favourites.
- Dashboard system status includes the used percentage of the disk partition
  containing the running server installation, RAM use, CPU use, one hour of
  CPU history sampled each minute and reset on restart, Pi temperature, and
  app RSS.
  Normalize CPU use across all logical cores to a 0–100% per-core average
  before plotting. Color history bars green below 60%, warm yellow from 60%
  through 80%, and muted red above 80%, in both light and dark themes.
  Admins can hide this from ordinary users while optionally retaining it
  for the house-wide account.

## Games

- Before beginning any Games implementation, stop and ask the user to confirm
  they are ready to start that work. The user plans to use a heavier model for
  Games; defer it until the end of the implementation agenda if practical.
- First release games: an original Wordle-style word game, mini crosswords,
  Hunt the Wumpus, word ladders, mini nonograms, and Sudoku.
- Word guessing uses five-letter answers and six guesses, with correct
  handling of repeated letters. Mini crosswords use 5×5 grids with across
  and down clues. Hunt the Wumpus uses a classic 20-room cave, five arrows,
  and a shared daily seed. Word ladders use four- or five-letter words and
  reward fewer valid steps. Mini nonograms use grids from 5×5 to 10×10.
  Sudoku uses a standard 9×9 grid with the day's difficulty labeled.
- Start with a curated offline puzzle bank. Investigate broader generation
  on the Pi as a later feature. Ship at least 30 days of distinct content
  per game, curated or reproducibly randomized as appropriate, adding more
  through updates; curated content may repeat after the bank is exhausted.
  Games suited to randomized play, such as Hunt the Wumpus, may generate a
  daily puzzle from a visible 64-bit seed without separately storing that
  seed. Use a stable household-day seed so every player gets the same cave,
  and avoid repeating a generated map within a rolling 365-day window. Show
  daily and practice seed controls only for games that use them.
- Random Sudoku generation is feasible without an LLM: generate a solved
  grid, remove clues, then rate the puzzle with a deterministic solver that
  records which techniques are needed and whether the solution is unique.
  Keep the curated bank as the first-release source; a later Pi benchmark can
  tune generation attempts and difficulty bands, with curated puzzles as a
  fallback if a target difficulty is not found within a bounded CPU budget.
- Everyone gets the same puzzle for each game on each household day; the
  day changes at midnight in the household time zone. One puzzle per game
  per day in the first release. Issue #1 tracks support for multiple daily
  puzzles without a fixed five-puzzle ceiling.
- The first move starts one scored attempt, which can be resumed but not
  restarted. An unfinished attempt expires at household midnight. Replays
  after completion are practice only. Each game has its own daily win streak;
  a loss or missed day resets that game's streak. A win earns up to 100
  points, adjusted for game-specific efficiency and optional hints; a loss
  earns zero. All-time totals sum those points. Daily and all-time
  scoreboards are visible to all users. Faster completions rank higher in
  timed games. The house-wide account can play without ranking.
- Offer optional score-reducing hints in mini crosswords, word ladders, mini
  nonograms, and Sudoku, up to three hints per scored attempt in each game.
  For crosswords, nonograms, and Sudoku, completion time breaks leaderboard
  ties while points depend on completion and hints. The word-guessing game
  and Hunt the Wumpus have no hints. Public scoreboards never reveal answers.
  A player may see the solution when their scored attempt ends; everyone can
  see it after the household day ends.

### Game scoring

All formulas award zero for a loss or an attempt left incomplete at midnight.
For every win, including Hunt the Wumpus, award `max(1, calculated score)`.
Practice play never scores.

| Game | Win condition | Proposed score |
| --- | --- | --- |
| Word guessing | Find the answer within six valid guesses | `100 - 15 × (guesses - 1)` |
| Mini crossword | Complete every answer correctly | `100 - 15 × hints`; time breaks ties |
| Hunt the Wumpus | Shoot the Wumpus before a hazard or no arrows ends the attempt | `100 - 3 × room moves - 12 × missed arrows` |
| Word ladder | Reach the target through valid one-letter changes | `100 - 10 × (steps - shortest path) - 15 × hints` |
| Mini nonogram | Mark every cell correctly | `100 - 15 × hints`; time breaks ties |
| Sudoku | Complete the grid correctly | `100 - 15 × hints`; time breaks ties |

For hints, a crossword reveals a selected letter, a ladder suggests a valid
next word on a shortest route, a nonogram reveals a selected cell, and Sudoku
reveals a selected digit. The timer starts with the first move and measures
elapsed time, including when the page is closed; it does not lower points in
the three games where it breaks ties.

## Optional local chat

Initial model-only Raspberry Pi 3B results and the remaining profiling gates
are recorded in [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md).

- Hidden until an admin enables it. An admin selects one installed Ollama
  model; only one model and one request run at a time. The runtime remains
  bound to loopback. A response taking minutes is acceptable.
- Queue waiting requests with admins first, FIFO within a priority. Finish
  the current response before starting another. Show users their queue
  position. Admin-configurable maximum waiting depth is eight by default;
  reject new requests once full.
- Prompts are limited to 128 characters. Each user can have 16 chats, up to
  ten pinned. When opening a seventeenth chat, delete the oldest unpinned
  chat; if all chats are pinned, do not open another.
- Admins can set the prompt/response pair limit, initially 16 per chat.
  Warn before the first oldest pair is dropped on continuation. Users can
  delete their own chats; admins can see and delete every user's chats.
  Clearly disclose admin access in the UI. The house-wide account has no
  chat access.
- Chat may read only data the requesting user can see and may propose list
  or calendar changes. Applying a change requires visible confirmation and
  a fresh server-side authorization check. Show the selected model name on
  each response. Keep a provider interface so an optional remote model can be
  added later; remote use is off by default and must disclose that prompts
  leave the home network.
- Database access must use narrow, read-only tools scoped to the requesting
  user's permissions. A small MCP-style tool interface is feasible, but avoid
  another always-on service: start in-process first, or launch a stdio helper
  only while a chat request is active. Proposed incremental budget: no more
  than 16 MiB RSS, under 1% of one CPU core averaged over a minute, and under
  5% of one core for a one-second query burst; cap each result at 100 rows or
  16 KiB. Measure this on the Pi before enabling database access. Any write
  proposal still requires explicit confirmation and fresh authorization.

## Delivery sequence

1. Finish shared foundations: schema migrations, roles and permissions,
   sessions, CSRF, login rate limiting, backup/restore, TLS deployment, and
   baseline app memory measurement. Account, backup, TLS, and schema versioning
   foundations are implemented. The Lists schema migration and subsequent
   feature migrations are implemented; target-Pi deployment and release
   verification remain.
2. Implement Lists, then Recipes and Calendar, because recipes and events
   connect to shopping lists and to each other.
3. Implement Weather, RSS, and all six Games, each in focused test-driven
   increments. Games are part of the first-release scope, not only an approved
   visual design.
4. Complete the Home dashboard after its Chores, Calendar, Weather, and
   system-status data sources are available.
5. Measure the app on the Raspberry Pi, then profile it alongside Ollama.
   Initial model-only Chat measurements are recorded in
   [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md). Chat remains hidden unless an
   admin enables it after combined resource and answer-quality checks. Measure
   the approved read-only database-tool budget on the Pi before enabling it.
6. Verify permissions, privacy, upgrades, resource use, backup/restore, and
   household deployment before the first release.

### Lists implementation increments

1. Add a versioned, data-preserving list schema. Upgrade existing shopping
   items into a shared Groceries list and test migration and rollback.
2. Implement the Shopping and To-do sections, list creation and deletion,
   static shopping categories, private allowlists, collaboration, item
   ownership, and separate completion and deletion actions.
3. Add portable Copy list and reviewed Paste list flows, including matching,
   duplicate handling, ownership preservation, and permission rechecks.
4. Implement the seven persistent weekday Chores lists, per-person and
   Everyone assignments, Sunday completion reset, and the Home chores card.
5. Add away-mode caching and conflict-aware checkmark sync for shopping lists
   after the online list flows are stable.

See [the implementation agenda](IMPLEMENTATION_AGENDA.md) for the full
workstream list, current status, dependencies, deferred work, and release
gates.

## Open design reviews

- [Account administration preview](design/account-admin-preview.html): approved
  for account controls; backup/restore controls have been added to the Admin
  settings section and approved.
- [Home preview](design/home-preview.html): approved; the user had no comments.
- [Lists and navigation preview](design/lists-navigation-preview.html):
  approved by the user before list and tab-order implementation.
- [Recipes preview](design/recipes-preview.html): approved by the user before
  recipe implementation.
- [Calendar preview](design/calendar-preview.html): approved by the user
  before calendar implementation.
- [Weather preview](design/weather-preview.html): approved by the user before
  weather implementation.
- [RSS preview](design/rss-preview.html): approved by the user before RSS
  implementation.
- [Games preview](design/games-preview.html): approved after adding conditional seeds, expandable score/progress cards,
  and a distinct Today progress card.
- [Chat preview](design/chat-preview.html): updated with model names,
  optional remote-provider support, and scoped database-tool disclosure;
  approved by the user. Initial model-only Pi 3B benchmarks are recorded in
  [CHAT_PI_BENCHMARK.md](CHAT_PI_BENCHMARK.md); combined app/model profiling
  and answer-quality review remain before enabling Chat. The database-access
  budget is approved.

## Deferred features

- Multiple daily puzzles per game: GitHub issue #1.
- Outbound email: GitHub issue #2.
- Email delivery of list text, dependent on outbound email: future feature.
- Email delivery of calendar archives, dependent on outbound email: issue #3.
- Inbound email controls: GitHub issue #4.
- Puzzles generated on the Pi: GitHub issue #5.
- Weather almanac sub-tab: GitHub issue #6.
- On-demand historical weather, off unless enabled by an admin: GitHub issue
  #7.
- Alternative weather APIs and feeds: GitHub issue #8.
