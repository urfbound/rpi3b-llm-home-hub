"""Offline four-letter word graph and daily ladder puzzle bank."""

import json
from collections import deque
from pathlib import Path

_DATA = Path(__file__).with_name('game_data')
WORDS = frozenset(
    word.upper()
    for word in json.loads((_DATA / 'word_ladder_words.json').read_text(encoding='utf-8'))
)
PUZZLES = tuple(json.loads((_DATA / 'word_ladders.json').read_text(encoding='utf-8')))


def one_letter_apart(left, right):
    return len(left) == len(right) == 4 and sum(a != b for a, b in zip(left, right)) == 1


_BUCKETS = {}
for _word in WORDS:
    for _index in range(4):
        _BUCKETS.setdefault(_word[:_index] + '_' + _word[_index + 1:], []).append(_word)
NEIGHBORS = {
    word: tuple(sorted({
        other
        for index in range(4)
        for other in _BUCKETS[word[:index] + '_' + word[index + 1:]]
        if other != word
    }))
    for word in WORDS
}


def shortest_path(start, target):
    queue = deque([(start,)])
    seen = {start}
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        for neighbor in NEIGHBORS[path[-1]]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((*path, neighbor))
    return ()


PAIRS = tuple((puzzle['start'].upper(), puzzle['goal'].upper()) for puzzle in PUZZLES)
_DIFFICULTY = {(start, target): puzzle['difficulty']
               for (start, target), puzzle in zip(PAIRS, PUZZLES)}


def daily_puzzle(day):
    return PAIRS[day.toordinal() % len(PAIRS)]


def puzzle_difficulty(start, target):
    return _DIFFICULTY[(start.upper(), target.upper())]
