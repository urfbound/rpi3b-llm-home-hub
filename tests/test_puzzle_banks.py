import json
import re
from collections import deque
from datetime import date, timedelta
from functools import lru_cache
from itertools import product
from pathlib import Path

import pytest


pytestmark = pytest.mark.bank


DATA = Path(__file__).parents[1] / "homehub" / "game_data"


def _read(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _neighbors(word, dictionary):
    return dictionary[word]


@lru_cache(maxsize=1)
def _ladder_graph(dictionary_words):
    buckets = {}
    for word in dictionary_words:
        for index in range(4):
            buckets.setdefault(word[:index] + "_" + word[index + 1 :], []).append(word)
    return {
        word: tuple(sorted({
            neighbor
            for index in range(4)
            for neighbor in buckets[word[:index] + "_" + word[index + 1 :]]
            if neighbor != word
        }))
        for word in dictionary_words
    }


@lru_cache(maxsize=None)
def _ladder_distance(start, goal, dictionary_words):
    dictionary = _ladder_graph(dictionary_words)
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        word, distance = queue.popleft()
        if word == goal:
            return distance
        for candidate in _neighbors(word, dictionary):
            if candidate not in seen:
                seen.add(candidate)
                queue.append((candidate, distance + 1))
    return None


def _runs(bits):
    return [len(run) for run in re.findall(r"1+", bits) if run]


@lru_cache(maxsize=128)
def _line_masks(clue, width=8):
    return tuple(
        mask
        for mask in range(1 << width)
        if tuple(_runs(f"{mask:0{width}b}")) == clue
    )


def _count_nonogram_solutions(rows, columns, limit=2):
    width = len(columns)
    row_options = [_line_masks(tuple(clue), width) for clue in rows]
    valid_prefixes = []
    for clue in columns:
        complete = _line_masks(tuple(clue), width)
        valid_prefixes.append(
            [
                {mask >> (width - depth) for mask in complete}
                for depth in range(width + 1)
            ]
        )
    solutions = 0

    def search(row_index, chosen):
        nonlocal solutions
        if solutions >= limit:
            return
        if row_index == len(rows):
            solutions += 1
            return
        for mask in row_options[row_index]:
            prefixes = []
            for col, clue in enumerate(columns):
                bit = (mask >> (width - col - 1)) & 1
                prefix = (chosen[col] << 1) | bit
                if prefix not in valid_prefixes[col][row_index + 1]:
                    break
                prefixes.append(prefix)
            else:
                search(row_index + 1, prefixes)
                if solutions >= limit:
                    return

    search(0, [0] * width)
    return solutions


def _sudoku_solution_count(grid, limit=2):
    board = [row[:] for row in grid]
    full = (1 << 9) - 1
    row_used = [0] * 9
    col_used = [0] * 9
    box_used = [0] * 9
    empty = []
    for row in range(9):
        for col in range(9):
            value = board[row][col]
            if value == 0:
                empty.append((row, col))
                continue
            bit = 1 << (value - 1)
            box = (row // 3) * 3 + col // 3
            if row_used[row] & bit or col_used[col] & bit or box_used[box] & bit:
                return 0
            row_used[row] |= bit
            col_used[col] |= bit
            box_used[box] |= bit

    solutions = 0

    def search():
        nonlocal solutions
        if solutions >= limit:
            return
        best_index = -1
        best_candidates = 0
        best_count = 10
        for index, (row, col) in enumerate(empty):
            if board[row][col]:
                continue
            box = (row // 3) * 3 + col // 3
            candidates = full & ~(row_used[row] | col_used[col] | box_used[box])
            count = candidates.bit_count()
            if count < best_count:
                best_index, best_candidates, best_count = index, candidates, count
                if count <= 1:
                    break
        if best_index < 0:
            solutions += 1
            return
        if best_count == 0:
            return
        row, col = empty[best_index]
        box = (row // 3) * 3 + col // 3
        while best_candidates:
            bit = best_candidates & -best_candidates
            best_candidates ^= bit
            board[row][col] = bit.bit_length()
            row_used[row] |= bit
            col_used[col] |= bit
            box_used[box] |= bit
            search()
            board[row][col] = 0
            row_used[row] ^= bit
            col_used[col] ^= bit
            box_used[box] ^= bit
            if solutions >= limit:
                return

    search()
    return solutions


def _difficulty_from_ladder_steps(steps):
    if 2 <= steps <= 3:
        return "easy"
    if steps == 4:
        return "medium"
    return "hard"


def _difficulty_from_sudoku_givens(givens):
    if givens >= 40:
        return "easy"
    if givens >= 35:
        return "medium"
    return "hard"


def test_word_ladder_bank_has_5000_solvable_shortest_paths_and_varied_difficulty():
    bank = _read("word_ladders.json")
    dictionary = _read("word_ladder_words.json")
    dictionary_tuple = tuple(dictionary)
    dictionary_set = set(dictionary)

    assert len(bank) == 5000
    assert len(dictionary_set) == len(dictionary)
    assert all(re.fullmatch(r"[a-z]{4}", word) for word in dictionary)
    assert [puzzle["id"] for puzzle in bank] == [
        f"ladder-{number:04d}" for number in range(1, 5001)
    ]
    assert len({(puzzle["start"], puzzle["goal"]) for puzzle in bank}) == 5000

    levels = {"easy": 0, "medium": 0, "hard": 0}
    for puzzle in bank:
        path = puzzle["path"]
        assert path[0] == puzzle["start"]
        assert path[-1] == puzzle["goal"]
        assert all(word in dictionary_set for word in path)
        assert all(
            sum(left != right for left, right in zip(first, second)) == 1
            for first, second in zip(path, path[1:])
        )
        distance = _ladder_distance(path[0], path[-1], dictionary_tuple)
        assert distance >= 2
        assert distance == len(path) - 1
        assert puzzle["difficulty"] == _difficulty_from_ladder_steps(distance)
        levels[puzzle["difficulty"]] += 1

    assert min(levels.values()) >= 500


def test_mini_nonogram_bank_has_5000_unique_solvable_eight_by_eight_boards():
    bank = _read("mini_nonograms.json")

    assert len(bank) == 5000
    assert [puzzle["id"] for puzzle in bank] == [
        f"nonogram-{number:04d}" for number in range(1, 5001)
    ]
    assert len({tuple(puzzle["solution"]) for puzzle in bank}) == len(bank)

    for puzzle in bank:
        solution = puzzle["solution"]
        rows = puzzle["rows"]
        columns = puzzle["columns"]
        assert len(solution) == len(rows) == len(columns) == 8
        assert all(re.fullmatch(r"[.#]{8}", row) for row in solution)
        expected_rows = [_runs(row.replace("#", "1").replace(".", "0")) for row in solution]
        expected_columns = [
            _runs("".join("1" if solution[row][col] == "#" else "0" for row in range(8)))
            for col in range(8)
        ]
        assert rows == expected_rows
        assert columns == expected_columns
        assert _count_nonogram_solutions(rows, columns) == 1


def test_mini_sudoku_bank_has_5000_valid_unique_solutions_and_varied_difficulty():
    bank = _read("mini_sudoku.json")

    assert len(bank) == 5000
    assert [puzzle["id"] for puzzle in bank] == [
        f"sudoku-{number:04d}" for number in range(1, 5001)
    ]
    assert len({tuple(tuple(row) for row in puzzle["puzzle"]) for puzzle in bank}) == len(bank)

    levels = {"easy": 0, "medium": 0, "hard": 0}
    expected = set(range(1, 10))
    for puzzle in bank:
        givens = puzzle["puzzle"]
        solution = puzzle["solution"]
        assert len(givens) == len(solution) == 9
        assert all(len(row) == 9 for row in givens + solution)
        assert all(all(0 <= value <= 9 for value in row) for row in givens)
        assert all(all(1 <= value <= 9 for value in row) for row in solution)
        assert all(set(row) == expected for row in solution)
        assert all(
            {solution[row][col] for row in range(9)} == expected
            for col in range(9)
        )
        assert all(
            {solution[row][col] for row in range(box_row, box_row + 3) for col in range(box_col, box_col + 3)} == expected
            for box_row, box_col in product((0, 3, 6), repeat=2)
        )
        assert all(
            givens[row][col] in (0, solution[row][col])
            for row, col in product(range(9), repeat=2)
        )
        clue_count = sum(value != 0 for row in givens for value in row)
        assert 30 <= clue_count <= 50
        assert puzzle["difficulty"] == _difficulty_from_sudoku_givens(clue_count)
        assert _sudoku_solution_count(givens) == 1
        levels[puzzle["difficulty"]] += 1

    assert min(levels.values()) >= 500


def test_daily_games_select_from_the_full_stored_banks():
    from homehub.game_grids import daily_puzzle as grid_puzzle
    from homehub.game_ladder import daily_puzzle as ladder_puzzle, puzzle_difficulty

    first_day = date(2026, 1, 1)
    ladder_pairs = set()
    nonogram_answers = set()
    sudoku_answers = set()
    for offset in range(5000):
        day = first_day + timedelta(days=offset)
        pair = ladder_puzzle(day)
        ladder_pairs.add(pair)
        assert puzzle_difficulty(*pair) in {"easy", "medium", "hard"}

        nonogram = grid_puzzle("nonogram", day)
        sudoku = grid_puzzle("sudoku", day)
        nonogram_answers.add(nonogram["answer"])
        sudoku_answers.add(sudoku["answer"])
        assert sudoku["difficulty"] in {"Easy", "Medium", "Hard"}

    assert len(ladder_pairs) == len(nonogram_answers) == len(sudoku_answers) == 5000
