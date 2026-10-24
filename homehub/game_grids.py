"""Curated and reproducibly transformed offline grid puzzles."""

from datetime import date


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
    index = day.toordinal() % 30
    digit_shift = index % 9
    row_turn = (index // 9) % 3
    transpose = index >= 27

    def transform(source):
        grid = [list(source[row * 9:(row + 1) * 9]) for row in range(9)]
        for band in range(3):
            rows = grid[band * 3:band * 3 + 3]
            grid[band * 3:band * 3 + 3] = rows[row_turn:] + rows[:row_turn]
        if transpose:
            grid = [list(row) for row in zip(*grid)]
        return ''.join(str((int(cell) + digit_shift - 1) % 9 + 1) if cell != '0' else '0'
                       for row in grid for cell in row)

    initial = transform(SUDOKU_START)
    answer = transform(SUDOKU_SOLUTION)
    return {'game': 'sudoku', 'title': 'Mini Sudoku', 'size': 9,
            'initial': initial, 'answer': answer, 'first_open': initial.index('0'),
            'difficulty': 'Easy', 'clues': ()}


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
    index = day.toordinal() % 30
    row_shift, col_shift = index % 8, index // 8
    # This staircase has a forced full row and column. Permutations preserve
    # its unique solution while providing thirty distinct clue sets.
    rows = [NONOGRAM_ART[(row + row_shift) % 8] for row in range(8)]
    rows = [''.join(row[(col + col_shift) % 8] for col in range(8)) for row in rows]
    answer = ''.join('#' if char == '#' else 'x' for row in rows for char in row)
    row_clues = tuple(_runs(row) for row in rows)
    col_clues = tuple(_runs(rows[row][col] for row in range(8)) for col in range(8))
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
