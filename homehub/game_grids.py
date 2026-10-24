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

WORD_SQUARES = tuple((f'{letter}AT', 'ARE', 'TEN') for letter in 'BCFHMPRSTV') + tuple(
    (f'{letter}AD', 'ARE', 'DEN') for letter in 'BDHLMPRSTW') + tuple(
    (f'{letter}AP', 'ARE', 'PEN') for letter in 'CGLMNPRSTY')

CLUES = {
    'ARE': 'Present tense of be', 'TEN': 'Two hands of fingers',
    'DEN': 'A sheltered animal home', 'PEN': 'A writing tool',
    'BAT': 'Flying mammal', 'CAT': 'Purring pet', 'FAT': 'Opposite of lean',
    'HAT': 'Headwear', 'MAT': 'Small floor covering', 'PAT': 'Gentle tap',
    'RAT': 'Long-tailed rodent', 'SAT': 'Past tense of sit',
    'TAT': 'Small piece of lace', 'VAT': 'Large liquid container',
    'BAD': 'Opposite of good', 'DAD': 'Father', 'HAD': 'Possessed in the past',
    'LAD': 'Young boy', 'MAD': 'Very angry', 'PAD': 'Writing tablet',
    'RAD': 'Informal word for excellent', 'SAD': 'Unhappy',
    'TAD': 'A small amount', 'WAD': 'A compact bundle',
    'CAP': 'A brimmed head covering', 'GAP': 'An opening',
    'LAP': 'One circuit of a track', 'MAP': 'Drawing of a place',
    'NAP': 'Short sleep', 'PAP': 'Soft food', 'RAP': 'Knock sharply',
    'SAP': 'Tree fluid', 'TAP': 'Light knock', 'YAP': 'Bark sharply',
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
    rows = WORD_SQUARES[day.toordinal() % len(WORD_SQUARES)]
    answer = '#####' + ''.join('#' + row + '#' for row in rows) + '#####'
    initial = ''.join('#' if char == '#' else '.' for char in answer)
    clues = tuple((direction, number, CLUES[word])
                  for direction in ('Across', 'Down')
                  for number, word in enumerate(rows, start=1))
    return {'game': 'crossword', 'title': 'Mini Crossword', 'size': 5,
            'initial': initial, 'answer': answer, 'first_open': 6,
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
