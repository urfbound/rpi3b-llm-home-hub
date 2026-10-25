"""Offline grid puzzle banks and board validation."""

import json
from datetime import date
from pathlib import Path


SUDOKU_START = (
    '530070000' '600195000' '098000060' '800060003' '400803001'
    '700020006' '060000280' '000419005' '000080079'
)
SUDOKU_SOLUTION = (
    '534678912' '672195348' '198342567' '859761423' '426853791'
    '713924856' '961537284' '287419635' '345286179'
)

CROSSWORD_TOPS = tuple(f'{letter}AT' for letter in 'BCFHMPRSTV')
CROSSWORD_BOTTOMS = (('ERE', 'TEN'), ('ERA', 'TAN'), ('EGO', 'TOW'))

CLUES = {
    'ARE': 'Present tense of be', 'TEN': 'Two hands of fingers',
    'TENET': 'A guiding principle', 'ERE': 'Before, in verse',
    'ERA': 'A distinct period of time', 'TAN': 'Light brown shade',
    'EGO': 'Sense of self', 'TOW': 'Pull behind a vehicle',
    'BAT': 'Flying mammal', 'CAT': 'Purring pet', 'FAT': 'Opposite of lean',
    'HAT': 'Headwear', 'MAT': 'Small floor covering', 'PAT': 'Gentle tap',
    'RAT': 'Long-tailed rodent', 'SAT': 'Past tense of sit',
    'TAT': 'Small piece of lace', 'VAT': 'Large liquid container',
}

NONOGRAM_ART = tuple('#' * (row + 1) + '.' * (7 - row) for row in range(8))

_DATA = Path(__file__).with_name('game_data')
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
    index = day.toordinal() % 30
    top = CROSSWORD_TOPS[index % 10]
    lower, bottom = CROSSWORD_BOTTOMS[index // 10]
    rows = (top, 'ARE', 'TENET', lower, bottom)
    answer = top + '##' + 'ARE' + '##' + 'TENET' + '##' + lower + '##' + bottom
    initial = ''.join('#' if char == '#' else '.' for char in answer)
    clues = tuple((direction, number, CLUES[word])
                  for direction in ('Across', 'Down')
                  for number, word in enumerate(rows, start=1))
    return {'game': 'crossword', 'title': 'Mini Crossword', 'size': 5,
            'initial': initial, 'answer': answer, 'first_open': 0,
            'difficulty': '', 'clues': clues}


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
