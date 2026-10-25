# Word ladder dictionary and puzzle bank attribution

The 2,733 lowercase four-letter words in `word_ladder_words.json` are from the
Canadian English speller dictionary `en_CA.txt`, release 2026.02.25, published
by the English Speller Database (ESDB, formerly SCOWL). Each of the 5,000
ladder puzzles stores a shortest route through this dictionary. Difficulty is
estimated from the shortest route length: easy is two to three changes,
medium is four, and hard is five or more.

Source: <https://github.com/en-wl/wordlist-diff/tree/rel-2026.02.25/en_CA.txt>
Project: <https://wordlist.aspell.net/>

Mini nonograms are generated as 8 × 8 boards and kept only when their row and
column clues have exactly one solution. Mini Sudoku puzzles are 9 × 9, have
exactly one solution, and store that solution for verification. Sudoku
difficulty is a coarse estimate based on givens: easy has 40–50, medium
35–39, and hard 30–34. It is not a formal human-technique rating.

Copyright 2000-2026 by Kevin Atkinson

Permission to use, copy, modify, distribute, and sell any part of SCOWLv2, or
word lists created from it, is hereby granted without fee, provided that the
above copyright notice appears in all copies and that both the above copyright
notice and this notice appear in supporting documentation. Kevin Atkinson
makes no representations about the suitability of this database for any
purpose. It is provided "as is" without express or implied warranty.

SCOWL is derived from many sources, most of which are in the Public Domain.
Data from the Corpus of Contemporary American English (COCA) was also used.
All data from COCA comes from 3-gram data that is not freely available;
however, the usage is within the rights given by the NDA that was signed when
purchasing the data. More information on COCA is available at
<https://www.english-corpora.org/coca/>.

The primary source of words for SCOWL comes from 12dicts and ENABLE2K. Both
are in the Public Domain, but Alan Beale <biljir@pobox.com> deserves special
credit as he is the author of 12dicts and a major contributor to ENABLE2K. In
addition, he gave Kevin Atkinson feedback and created a number of special
lists to help improve the overall quality of SCOWL.
