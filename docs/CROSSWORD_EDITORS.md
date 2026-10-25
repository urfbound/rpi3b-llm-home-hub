# Mini Crossword editor guide

The four editor profiles below define the house style for the offline Mini
Crossword bank. Keep each voice recognizable in its clue writing, not just in
the editor label. The machine-readable signature and version are stored with
each puzzle in `homehub/game_data/crossword_editors.json`.

## Shared puzzle contract

- Each puzzle is a solved 5 × 5 English-language crossword using the existing
  rotationally symmetric block pattern. Across and Down entries are three or
  five letters; every open cell belongs to an entry in both directions.
- The current bank has 1,250 puzzles per editor. The daily schedule rotates
  Larry, Moe, Curly, and Shemp, so each editor supplies every fourth daily
  puzzle and the 5,000-record bank lasts about 13.7 years before cycling.
- Difficulty is intentionally a little above an easy mini: mostly Medium, with
  one in five labeled Medium-Hard. Use familiar fill and raise challenge mainly
  through clue construction, crossings, and restrained misdirection.
- Keep clues fair, family-friendly, answerable from the clue and crossings,
  and free of obscure specialist knowledge. Do not use the clue text to expose
  its answer directly.
- `style_signature` is a stable machine-readable identity; `style_version`
  changes when the editorial rules change. Preserve both when rebuilding or
  translating a bank. Records identify their source language with `locale`.

## Larry — `wry_wordplay`

Larry’s puzzles are built around clean misdirection: a clue first suggests one
familiar meaning, then snaps into another without needing trivia. He favors
compact, polished phrasing, double definitions, reversals, and a few elegant
puns. The vocabulary stays household-friendly, and every trick has a fair
route through the crossings. Themes tend to be clever rather than topical,
with answers linked by a quiet idea the solver notices late. His signature is
restraint: no clue shouts that it is being funny. A style detector could
measure concise clues, ambiguity markers, wordplay tags, and low obscurity.
Solvers should feel pleasantly outsmarted, never scolded.

**Writing rules:** favor concise clues; use ambiguity, double meaning, and
reversal only when both readings are fair; let crossings resolve the turn;
keep obscure fill out. Avoid explaining the joke or announcing a trick.

**Detectable traits:** shorter clue length than Shemp, more semantic pivots and
double definitions, low obscurity, restrained punctuation.

## Moe — `direct_definition`

Moe’s puzzles are blunt, brisk, and mechanically fair. Clues favor direct
definitions, practical knowledge, and short commands; wordplay appears
sparingly and is never allowed to hide an answer behind specialist trivia. The
theme usually has a clear organizing rule—tools, chores, sports, or things
that go wrong—and crossings reinforce rather than rescue the fill. His voice
is impatient in measure: clipped sentences, pointed questions, and the
occasional mock reprimand, while staying kind enough for family play. A
detector could spot shorter clues, low pun density, concrete nouns, and
imperative phrasing. The solver gets footing, then a march to completion.

**Writing rules:** prefer a single direct definition; use concrete everyday
language; keep clues short and assertive; reserve a mild mock reprimand for an
occasional payoff. Never make the solver feel foolish.

**Detectable traits:** shortest average clues, lowest pun density, concrete
nouns and verbs, rare exclamation marks.

## Curly — `playful_sound`

Curly’s puzzles treat language like a toy box: sound, rhythm, repetition,
comic comparisons, and harmless surprise drive the clueing. He likes lively
themes—kitchens, pets, playground mishaps—and clues that can be read aloud
with a grin. Puns are frequent, but answer lengths and crossing letters keep
them fair; nonsense is seasoning, never the solution. His clues use expressive
punctuation and occasional sound effects, while the fill remains ordinary
English. A style detector could track onomatopoeia, exclamations, repeated
patterns, and playful semantic jumps. The intended feeling is a family table
laughing together, with enough deduction to make the laughter earned.

**Writing rules:** use sound play, repetition, comic images, and lively
punctuation; make the answer recoverable without guessing the sound effect;
keep the fill ordinary and the humor gentle.

**Detectable traits:** most sound-symbolic words and playful semantic jumps;
more exclamations and rhythmic phrasing than the other editors.

## Shemp — `warm_anecdote`

Shemp’s puzzles sound like a neighbor telling a good story over coffee. Clues
draw on home routines, familiar objects, old sayings, food, weather, and small
everyday reversals. The humor is warm and slightly sideways: a phrase may
invite a literal reading before the conversational answer clicks. Themes feel
like miniature stories rather than lists, and vocabulary stays accessible
across generations. His clue sentences are longer than Moe’s, each carries
concrete image and gentle payoff. A detector could measure domestic
vocabulary, idiom references, anecdotal phrasing, and low obscurity. Solvers
should feel welcomed into the joke, not tested on credentials.

**Writing rules:** use conversational phrasing and everyday settings; build
small anecdotal images; allow gentle, literal-first misdirection; prefer
familiar vocabulary and warm payoffs over punchlines.

**Detectable traits:** longest average clues, most domestic vocabulary and
idiom references, conversational syntax, low obscurity.

## Future language packs

Translate the answer vocabulary and clue meanings as a coherent localized
crossword set; translating English clue strings word-for-word will often break
wordplay, idioms, and crossing compatibility. Preserve the four editorial
functions—wry ambiguity, directness, playful sound, and warm anecdote—while
adapting examples, idioms, and sound effects to the target language. A language
pack should have its own word list, clue definitions, style review, and
solvability tests. Store its locale and style version with generated records.

## Generation and review

`scripts/generate_crossword_editor_bank.py` deterministically constructs fills
from `homehub/game_data/crossword_vocabulary.json`. It checks every across and
down entry against the curated vocabulary before storing the bank as
line-delimited JSON. The runtime indexes line offsets and reads only the
selected puzzle, keeping the 5,000-puzzle bank from loading all clue objects
into memory. Unit tests verify counts, fill consistency, clue coverage,
difficulty mix, and style identity. Those checks cannot reliably judge
whether a clue is natural or whether a pun lands; review a sample from each
editor before future releases.
