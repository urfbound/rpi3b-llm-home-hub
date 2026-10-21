# Home Hub product plan (draft)

This document records decisions made during planning. Open questions below
must be settled before the affected feature is designed and built. Show the
user the visual design of **each feature** and obtain feedback before
implementing it. Implementation will proceed in small, tested feature
increments on feature or ticket branches. Merging into `main` requires the
user's explicit confirmation.

The previously planned functional scope and game rules are agreed. New list
and navigation details are being settled. Visual designs are reviewed feature
by feature before implementation.

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
- Home shows preview cards; each feature has its own main navigation tab.
  Weather and RSS may use cards within their tabs.
- An admin can set one household-wide order of feature tabs from the Admin tab
  only. Keep Home fixed at the start and Admin fixed at the end; the feature
  tabs between them can be reordered. The control's visual design still needs
  approval.
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
  users log in again.
- The project uses GPLv3.

## Accounts, visibility, and sharing

- Only admins create or delete accounts and reset passwords. Users may change
  their own passwords. Password resets issue a one-time temporary password
  and force a change. Reset and deletion revoke existing sessions.
- Sessions expire after 24 hours idle or seven days absolute by default.
  Both limits are configurable by admins. Add login rate limiting and CSRF
  protection before household deployment.
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

- Keep one top-level Lists tab with separate Shopping and To-do sections.
  To-do checklists are collaborative: invited members may add, edit, and
  complete tasks. At creation, the user may decide optional due dates and
  assignees for each item separately. The visual design still needs approval.
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
- A deliberate **Add to list** action copies recipe ingredients into an
  editable shopping-list snapshot. Combine matching ingredient names only
  when units convert reliably; leave uncertain combinations separate.
  Initially support common weight, volume, and count units and free text
  such as “to taste.” Later recipe or event edits do not silently change
  copied shopping items.

## Calendar

- Support all-day and timed events, plus daily, weekly, monthly, and yearly
  recurrence. Edit or cancel a single occurrence or the entire series.
  Recurrence requires an end date, defaulting to one year.
- Admins set the household time zone. Calendar views and event budgets use
  that zone. Add month view and local, one-way downloadable ICS; no cloud
  synchronization in the first release.
- Events may link to recipes. A visible event linked to a private recipe
  shows **Private recipe** to viewers without recipe access. Meal slots are
  breakfast, lunch, and dinner. A meal-prep event template links exactly one
  recipe; users may create multiple meal-prep events.
- Each user, including admins, has a configurable budget, initially eight
  event occurrences per day and 32 per month. Recurring occurrences count.
- Users can be added to events. A private event appears as **Busy** to people
  without access. ICS downloads contain only event details the downloading
  user may view. The creator and admins can add or remove event participants;
  participants can view full event details, including for private events, but
  cannot edit the event. Participation does not grant access to a linked
  private recipe. There is no RSVP or attendance tracking in the first
  release. An optional **Only events requiring me** ICS filter includes
  events the user created or was added to, and excludes generally shared
  events without that user's participation.
- Old-event cleanup is off by default. An admin may select deletion after a
  configurable age or local ICS archiving followed by deletion. Archives are
  downloadable by admins from settings and are not searchable in the active
  calendar. Store archive period and file metadata so a later outbound-email
  feature can send them without changing the archival flow. Emailing archives
  is a future feature dependent on outbound email.

## Weather, RSS, and system status

- Weather uses one admin-set household town or postal code and Open-Meteo as
  the first provider, with required attribution. Admins can turn outbound
  weather and RSS access off. Show a five-day forecast by default; admins
  can choose one to 15 days and switch between metric (default) and imperial
  units. Daily cards show high/low temperature, conditions, and precipitation
  chance. Today's hourly view also shows wind conditions and barometric
  pressure with a rising/steady/falling trend based on the preceding three
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
  An almanac sub-tab, on-demand historical weather, and alternative providers
  are deferred features.
- Users manage personal RSS feeds; admins manage feeds for the house-wide
  account. Adding a feed should be easy. Refresh every 30 minutes by default;
  admins can choose an interval from ten to 240 minutes. Keep the latest 30
  items per feed. When outbound access is off, show cached items with a
  last-updated time. Ordinary users may add public web feeds; only admins may
  add trusted feeds hosted on the household LAN. RSS cards show the title,
  source, publication date, and a short text summary, without loading remote
  images. Accept HTTP(S) feed URLs with size and timeout limits; recheck
  destinations after DNS resolution and redirects. Block loopback and other
  unsafe addresses. Only an admin can explicitly approve a LAN destination.
- Dashboard system status includes RAM use, CPU use, one hour of CPU history
  sampled each minute and reset on restart, Pi temperature, and app RSS.
  Admins can hide this from ordinary users while optionally retaining it
  for the house-wide account.

## Games

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
  daily puzzle from a visible seed without separately storing that seed.
  Practice play may accept a user-entered seed or choose a random one.
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
  a fresh server-side authorization check.

## Delivery sequence

1. Show the account administration visual design, then stabilize foundations:
   account administration and data migration, roles, sessions, login rate
   limiting, CSRF, backup/restore, TLS deployment guide, and baseline memory
   measurement.
2. Show the user a visual design for dashboard and navigation, then build
   the shared shell and status cards.
3. For each remaining feature, show its visual design, incorporate feedback,
   then implement it in a focused branch with relevant tests and profiling:
   lists, recipes, calendar, weather, RSS, and games.
4. Spike Ollama on the target Pi, then design and implement Chat if feasible;
   otherwise keep it hidden for first release.
5. Verify permissions, privacy, resource use, backup/restore, and household
   deployment before the first release.

## Open design reviews

- [Account administration preview](design/account-admin-preview.html): approved
  by the user. Other first-release feature visuals need approval before
  implementation.
- [Lists and navigation preview](design/lists-navigation-preview.html):
  awaiting user review before list and tab-order implementation.

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
