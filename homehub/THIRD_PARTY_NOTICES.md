# Third-party notices

Home Hub is distributed under the GNU General Public License, version 3 only
(GPL-3.0-only); see the repository's `LICENSE` file. The following components
and data have separate copyright and license terms. Their inclusion does not
change those terms or relicense their work under Home Hub's license. Retain
their notices when redistributing the corresponding component or data.

## Python dependencies

Home Hub declares these direct dependencies in `pyproject.toml`:

| Component | Use | License |
| --- | --- | --- |
| Flask | Web application framework | BSD-3-Clause |
| Click | CLI option parsing for `create-admin` | BSD-3-Clause |
| Waitress | WSGI server | Zope Public License 2.1 (ZPL-2.1) |
| argon2-cffi | Password hashing | MIT |
| pytest (development extra) | Test runner | MIT |

The current dependency set also includes these transitive packages. Version
constraints do not pin a single resolved environment; consult installed
distribution metadata for the exact versions in a particular deployment.

| Component | Parent dependency | License |
| --- | --- | --- |
| blinker | Flask | MIT |
| itsdangerous | Flask | BSD-3-Clause |
| Jinja2 | Flask | BSD-3-Clause |
| MarkupSafe | Jinja2 | BSD-3-Clause |
| Werkzeug | Flask | BSD-3-Clause |
| argon2-cffi-bindings | argon2-cffi | MIT; bundled Argon2 code includes CC0 components |
| cffi | argon2-cffi-bindings | MIT |
| pycparser | cffi | BSD-3-Clause |
| iniconfig | pytest | MIT |
| packaging | pytest | Apache-2.0 or BSD-2-Clause |
| pluggy | pytest | MIT |
| Pygments | pytest | BSD-2-Clause |
| colorama | pytest (Windows) | BSD-3-Clause |
| setuptools | build backend | MIT |

The project does not vendor these packages. Their distributions carry their
own license and copyright files. Python's standard library is also used and
is not bundled by Home Hub.

## Word list and generated puzzle content

The checked-in `game_data/word_ladder_words.json` and
`game_data/crossword_fill_words.json` use words from the Canadian English
ESDB/SCOWL `en_CA.txt` release 2026.02.25. The source is available at
<https://github.com/en-wl/wordlist-diff/tree/rel-2026.02.25/en_CA.txt> and
<https://wordlist.aspell.net/>. Copyright 2000-2026 by Kevin Atkinson.

Permission to use, copy, modify, distribute, and sell any part of SCOWLv2, or
word lists created from it, is granted without fee, provided that the above
copyright notice appears in all copies and that both the above copyright
notice and this permission notice appear in supporting documentation. The
database is provided "as is" without express or implied warranty. SCOWL's
upstream notice also explains that it incorporates multiple sources, including
public-domain sources, and contains COCA-derived data used under a separate
agreement. See the upstream project for its complete terms and provenance.

The checked-in crossword fill pool was filtered using English Zipf frequency
scores from `wordfreq` 3.1.1 during data preparation. Neither the `wordfreq`
software nor its frequency tables or score values are included in Home Hub or
needed at runtime. The upstream project licenses its code under Apache-2.0
and identifies its frequency data as CC BY-SA 4.0, with source-specific
attribution terms; see <https://github.com/rspeer/wordfreq/blob/master/LICENSE.txt>
and <https://github.com/rspeer/wordfreq/blob/master/NOTICE.md>. The filter
used those scores to select words from the separately attributed SCOWL list;
it did not copy frequency values into the checked-in fill pool. This notice
records that data-preparation provenance and does not claim that the generated
pool is a redistribution of the wordfreq dataset. If a downstream analysis
determines the selection itself is an adaptation of that dataset, apply the
applicable CC BY-SA attribution and share-alike terms to that adaptation.

The puzzle grids and clue text were generated or written for Home Hub, using
the attributed word lists above. The exact SCOWL terms are also reproduced in
`game_data/WORD_LIST_ATTRIBUTION.md`.

## Services and separately installed software

The application can request forecast data from Open-Meteo. It displays
provider attribution in the Weather interface; the service and its data are
not bundled with the application. RSS content is fetched from feeds selected
by household users and remains subject to each publisher's terms. Optional
Ollama models, Caddy, and operating-system services are installed separately
and are not bundled or relicensed by Home Hub. Consult their respective
licenses and terms when installing or redistributing them.
