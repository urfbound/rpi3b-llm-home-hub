"""Offline grid puzzle banks and board validation."""

import json
from datetime import date
from functools import lru_cache
from pathlib import Path


SUDOKU_START = (
    '530070000' '600195000' '098000060' '800060003' '400803001'
    '700020006' '060000280' '000419005' '000080079'
)
SUDOKU_SOLUTION = (
    '534678912' '672195348' '198342567' '859761423' '426853791'
    '713924856' '961537284' '287419635' '345286179'
)

NONOGRAM_ART = tuple('#' * (row + 1) + '.' * (7 - row) for row in range(8))

_DATA = Path(__file__).with_name('game_data')
_CROSSWORD_PATH = _DATA / 'crossword_editors.jsonl'


def _jsonl_offsets(path):
    offsets = []
    with path.open('rb') as source:
        while True:
            offset = source.tell()
            if not source.readline():
                return tuple(offsets)
            offsets.append(offset)


_CROSSWORD_OFFSETS = _jsonl_offsets(_CROSSWORD_PATH)


@lru_cache(maxsize=32)
def _crossword_record(index):
    with _CROSSWORD_PATH.open('rb') as source:
        source.seek(_CROSSWORD_OFFSETS[index])
        return json.loads(source.readline())


_NONOGRAM_BANK = tuple(json.loads(
    (_DATA / 'mini_nonograms.json').read_text(encoding='utf-8')))
_SUDOKU_BANK = tuple(json.loads(
    (_DATA / 'mini_sudoku.json').read_text(encoding='utf-8')))


def _runs(cells):
    runs = []
    count = 0
    for cell in cells:
        if cell == '#':
            count += 1
        elif count:
            runs.append(count)
            count = 0
    if count:
        runs.append(count)
    return tuple(runs) or (0,)


def _sudoku(day):
    record = _SUDOKU_BANK[day.toordinal() % len(_SUDOKU_BANK)]
    initial = ''.join(str(value) for row in record['puzzle'] for value in row)
    answer = ''.join(str(value) for row in record['solution'] for value in row)
    return {'game': 'sudoku', 'title': 'Mini Sudoku', 'size': 9,
            'initial': initial, 'answer': answer, 'first_open': initial.index('0'),
            'difficulty': record['difficulty'].title(), 'clues': ()}


def _crossword(day):
    record = _crossword_record(day.toordinal() % len(_CROSSWORD_OFFSETS))
    answer = record['answer']
    initial = record['initial']
    clues = tuple((clue['direction'], clue['number'], clue['text'])
                  for clue in record['clues'])
    return {'game': 'crossword', 'title': 'Mini Crossword', 'size': 5,
            'initial': initial, 'answer': answer, 'first_open': 0,
            'difficulty': record['difficulty'], 'clues': clues,
            'editor': record['editor'].title(),
            'style_signature': record['style_signature'],
            'style_version': record['style_version']}


def _nonogram(day):
    record = _NONOGRAM_BANK[day.toordinal() % len(_NONOGRAM_BANK)]
    rows = record['solution']
    answer = ''.join('#' if char == '#' else 'x' for row in rows for char in row)
    row_clues = tuple(tuple(clue) or (0,) for clue in record['rows'])
    col_clues = tuple(tuple(clue) or (0,) for clue in record['columns'])
    return {'game': 'nonogram', 'title': 'Mini Nonogram', 'size': 8,
            'initial': '.' * 64, 'answer': answer, 'first_open': 0,
            'difficulty': '', 'clues': (), 'row_clues': row_clues, 'col_clues': col_clues}


def daily_puzzle(game, day):
    if not isinstance(day, date):
        raise TypeError('day must be a date')
    if game == 'sudoku':
        return _sudoku(day)
    if game == 'crossword':
        return _crossword(day)
    if game == 'nonogram':
        return _nonogram(day)
    raise ValueError('unknown grid game')


def valid_board(puzzle, board, revealed=()):
    if len(board) != len(puzzle['answer']):
        return False
    game = puzzle['game']
    allowed = {'sudoku': '0123456789', 'crossword': '.ABCDEFGHIJKLMNOPQRSTUVWXYZ',
               'nonogram': '#x.'}[game]
    blank = {'sudoku': '0', 'crossword': '.', 'nonogram': '.'}[game]
    return all((char in allowed if initial == blank else char == initial)
               for initial, char in zip(puzzle['initial'], board)) and all(
                   board[index] == puzzle['answer'][index] for index in revealed)
