# Mini Crossword bank quality audit

Audit and rebuild date: 2026-10-25. The historical measurements below describe
the bank before the rebuild. The replacement bank is checked in.

## Bank before the rebuild

The 5,000 records have distinct solved grids, but the differences are often
tiny. Of the 4,999 consecutive day pairs, 2,785 differ in one open letter and
another 484 differ in two. Across the bank, 24,863 unordered pairs differ in
one open letter; 4,944 puzzles have at least one such neighbor. The center
across answer is `TENET` in 1,800 puzzles. The 50,000 clue slots contain only
2,460 distinct clue strings, and 4,996 puzzles repeat an answer within their
own ten across/down entries.

This follows from the generator taking the first 5,000 fills in its search
order. Its editor clue functions also add the same four sound effects to all
Curly clues and the same four asides to all Shemp clues. Larry's clues all use
one double-definition frame. These markers produce mechanically distinct
styles, but do not meet the natural, varied clue-writing goals in the editor
guide.

Exhausting the existing 273 three-letter and 302 five-letter clueable words
under the current 5 x 5 mask yields 16,322 distinct fills. Only 26 have ten
different answer entries. Keeping 5,000 puzzles while requiring ten distinct
entries is therefore impossible with the present vocabulary and mask.

## Dictionary expansion experiment

The project already attributes the Canadian English ESDB/SCOWL speller source
used for Word Ladder. Its `en_CA.txt` release 2026.02.25 contains 820 lowercase,
alphabetic three-letter words and 5,169 lowercase, alphabetic five-letter
words: 547 and 4,867 more than the current crossword vocabulary, respectively.
The source is a spelling list; it does not provide crossword clues or certify
that every word is familiar, family-friendly, or suitable as fill.

For an exploratory filter, `wordfreq` 3.1.1 English Zipf frequency >= 4.0 was
applied to source words while retaining all current clueable words. This left
377 three-letter and 1,032 five-letter words. Exhausting the same grid
generator produced 395,516 fills, including 9,952 with ten distinct entries
and 406 different center across answers. A simple greedy pass found 4,978
ten-entry fills with no one-letter neighbor among those selected; a 5,000-fill
bank can still use near neighbors if they are placed on well-separated dates.

Selecting the 5,000 ten-entry fills that individually require the fewest new
words needs clues for **308 new words** in that selection (92
three-letter and 216 five-letter words). This is a workload estimate, not an
optimized editorial selection or a proof that those words are suitable. A
frequency score cannot validate definitions, natural clues, editor voices, or
fair difficulty. The current 50,000 clues also need rewriting or review; adding
words alone does not fix them.

## Rebuilt bank

The checked-in replacement retains 5,000 puzzles and 1,250 per editor. Its
filtered fill list has 358 three-letter and 1,029 five-letter words; the 899
clueable answers comprise 575 legacy definitions and 324 newly written
definitions. Every grid has ten different across/down entries. The center
across position uses 334 answers; its most frequent answer appears 133 times,
down from 1,800 for `TENET` in the old bank.

The bank still has 4,352 one-open-letter pairs across all 5,000 puzzles, but
none are within 30 days of each other in the cyclic daily order. Consecutive
days differ at 8 to 17 open letters and share at most two answer entries.
These limits include the last-to-first transition of the 5,000-day cycle.

The previous sound-effect and stock-aside clue frames have been removed.
There are 1,713 distinct clue strings, and the most reused string occurs 144
times, versus 900 for the most reused old clue. The decrease from 2,460 old
distinct strings reflects removal of superficial word and aside variants;
string count alone is not a clue-quality measure. The 102 most common answer
words have manually written editor-specific options, used in 25,561 of the
50,000 clue slots. Other slots use two hand-written direct definitions per word;
some Larry, Curly, and Shemp clues consequently remain plain definitions
rather than distinctive voice. A deterministic sample of three puzzles per
editor was read and several awkward clues were revised.

The measurable structure and repetition defects are fixed. Before release,
people should solve a broader sample of puzzles from each editor and check
clue fairness, voice, and the Medium/Medium-Hard labels. No automated metric
or small clue sample establishes human solving difficulty.

## Reproduction

Run `python scripts/audit_crossword_bank.py` for the rebuilt bank and
exhaustive legacy-vocabulary count. Use `--sample 3` to print three puzzles per
editor. Run `python scripts/generate_crossword_editor_bank.py` to reproduce the
checked-in bank from its checked-in fill and clue files. To recreate the
filtered fill list, download
[`en_CA.txt`](https://github.com/en-wl/wordlist-diff/blob/rel-2026.02.25/en_CA.txt)
and run `python scripts/prepare_crossword_fill_words.py PATH` in an environment
with `wordfreq==3.1.1`. For the earlier expansion experiment, run
`python scripts/audit_crossword_bank.py --source PATH --zipf 4.0`. The source
file used here had SHA-256
`905b1b6b0a98fcb3701c9c59290a090a4d6ab9127809017fefacbe9c2c6f15a4`;
it was not checked into the project. The
experiment adds no runtime dependency to the Raspberry Pi application.
