"""Build the offline word-ladder, nonogram, and Sudoku puzzle banks."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import random
import re
from collections import defaultdict, deque
from pathlib import Path
from functools import lru_cache


ROOT = Path(__file__).resolve().parents[1]
GAME_DATA = ROOT / "homehub" / "game_data"
SEED = 20261024


def _write_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, separators=(",", ":"), ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def build_ladder_bank(words: list[str], size: int = 5000) -> list[dict]:
    dictionary = sorted({word for word in words if re.fullmatch(r"[a-z]{4}", word)})
    dictionary_set = set(dictionary)
    buckets: dict[str, list[str]] = defaultdict(list)
    for word in dictionary:
        for index in range(4):
            buckets[word[:index] + "_" + word[index + 1 :]].append(word)
    adjacency = {
        word: sorted(
            {
                neighbor
                for index in range(4)
                for neighbor in buckets[word[:index] + "_" + word[index + 1 :]]
                if neighbor != word
            }
        )
        for word in dictionary
    }

    tiers = {"easy": (2, 3), "medium": (4, 4), "hard": (5, 99)}
    quotas = {"easy": 1700, "medium": 1700, "hard": size - 3400}
    heaps: dict[str, list[tuple[bytes, str, str, list[str]]]] = {
        level: [] for level in tiers
    }

    # Keep a bounded deterministic reservoir in each tier rather than retaining
    # every reachable pair from the dictionary graph.
    reservoir_size = {level: quota + 250 for level, quota in quotas.items()}
    for start in dictionary:
        parents = {start: None}
        distances = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            distance = distances[current]
            for neighbor in adjacency[current]:
                if neighbor not in distances:
                    distances[neighbor] = distance + 1
                    parents[neighbor] = current
                    queue.append(neighbor)
        for goal, distance in distances.items():
            if goal <= start or distance == 0:
                continue
            level = next(
                (name for name, (minimum, maximum) in tiers.items() if minimum <= distance <= maximum),
                None,
            )
            if level is None:
                continue
            path = []
            node = goal
            while node is not None:
                path.append(node)
                node = parents[node]
            path.reverse()
            digest = hashlib.sha256(f"{SEED}:{start}:{goal}".encode("ascii")).digest()
            entry = (digest, start, goal, path)
            heap = heaps[level]
            if len(heap) < reservoir_size[level]:
                heapq.heappush(heap, entry)
            elif digest > heap[0][0]:
                heapq.heapreplace(heap, entry)

    bank = []
    for level, quota in quotas.items():
        if len(heaps[level]) < quota:
            counts = {name: len(heap) for name, heap in heaps.items()}
            raise ValueError(f"Not enough word ladders by difficulty: {counts}")
        selected = sorted(heaps[level], key=lambda entry: entry[0])[:quota]
        for _, start, goal, path in selected:
            if any(word not in dictionary_set for word in path):
                raise ValueError("Generated ladder contains a word outside its dictionary")
            bank.append({
                "id": "",
                "start": start,
                "goal": goal,
                "path": path,
                "difficulty": level,
            })
    bank.sort(key=lambda puzzle: (puzzle["difficulty"], puzzle["start"], puzzle["goal"]))
    for number, puzzle in enumerate(bank, 1):
        puzzle["id"] = f"ladder-{number:04d}"
    return bank


def _run_lengths(line: str) -> list[int]:
    return [len(run) for run in re.findall("1+", line)]


@lru_cache(maxsize=128)
def _line_masks(clue: tuple[int, ...], width: int):
    return tuple(
        mask
        for mask in range(1 << width)
        if _run_lengths(f"{mask:0{width}b}") == list(clue)
    )


def _nonogram_solution_count(row_clues, column_clues, limit=2) -> int:
    width = len(column_clues)
    row_options = [_line_masks(tuple(clue), width) for clue in row_clues]
    valid_prefixes = []
    for clue in column_clues:
        complete = _line_masks(tuple(clue), width)
        valid_prefixes.append(
            [
                {mask >> (width - depth) for mask in complete}
                for depth in range(width + 1)
            ]
        )
    count = 0

    def search(row_index, chosen):
        nonlocal count
        if count >= limit:
            return
        if row_index == len(row_clues):
            count += 1
            return
        for mask in row_options[row_index]:
            prefixes = []
            for col, clue in enumerate(column_clues):
                bit = (mask >> (width - col - 1)) & 1
                prefix = (chosen[col] << 1) | bit
                if prefix not in valid_prefixes[col][row_index + 1]:
                    break
                prefixes.append(prefix)
            else:
                search(row_index + 1, prefixes)
                if count >= limit:
                    return

    search(0, [0] * width)
    return count


def _nonogram_clues(solution: tuple[str, ...]):
    rows = [_run_lengths(row.replace("#", "1").replace(".", "0")) for row in solution]
    columns = [
        _run_lengths("".join("1" if solution[row][col] == "#" else "0" for row in range(8)))
        for col in range(8)
    ]
    return rows, columns


def build_nonogram_bank(size: int = 5000) -> list[dict]:
    rng = random.Random(SEED)
    puzzles = []
    seen = set()
    attempts = 0
    while len(puzzles) < size:
        attempts += 1
        density = rng.uniform(0.28, 0.55)
        solution = tuple(
            "".join("#" if rng.random() < density else "." for _ in range(8))
            for _ in range(8)
        )
        if solution in seen or any("#" not in row for row in solution):
            continue
        if any(all(solution[row][col] == "." for row in range(8)) for col in range(8)):
            continue
        rows, columns = _nonogram_clues(solution)
        if _nonogram_solution_count(rows, columns) != 1:
            continue
        seen.add(solution)
        puzzles.append({
            "id": f"nonogram-{len(puzzles) + 1:04d}",
            "rows": rows,
            "columns": columns,
            "solution": list(solution),
        })
        if attempts % 100_000 == 0:
            print(f"Nonograms: {len(puzzles)}/{size} accepted from {attempts} candidates", flush=True)
        if attempts > size * 100_000:
            raise RuntimeError(f"Could not generate {size} unique nonograms")
    return puzzles


def _solved_sudoku(rng: random.Random) -> list[list[int]]:
    bands = list(range(3))
    stacks = list(range(3))
    rng.shuffle(bands)
    rng.shuffle(stacks)
    row_order = [band * 3 + offset for band in bands for offset in rng.sample(range(3), 3)]
    col_order = [stack * 3 + offset for stack in stacks for offset in rng.sample(range(3), 3)]
    symbols = list(range(1, 10))
    rng.shuffle(symbols)
    return [
        [symbols[(row * 3 + row // 3 + col) % 9] for col in col_order]
        for row in row_order
    ]


def _sudoku_solution_count(grid, limit=2) -> int:
    board = [row[:] for row in grid]
    full = (1 << 9) - 1
    rows = [0] * 9
    cols = [0] * 9
    boxes = [0] * 9
    empty = []
    for row in range(9):
        for col in range(9):
            value = board[row][col]
            if value == 0:
                empty.append((row, col))
                continue
            bit = 1 << (value - 1)
            box = (row // 3) * 3 + col // 3
            if rows[row] & bit or cols[col] & bit or boxes[box] & bit:
                return 0
            rows[row] |= bit
            cols[col] |= bit
            boxes[box] |= bit
    solutions = 0

    def search():
        nonlocal solutions
        if solutions >= limit:
            return
        best = None
        candidates_best = 0
        count_best = 10
        for row, col in empty:
            if board[row][col]:
                continue
            box = (row // 3) * 3 + col // 3
            candidates = full & ~(rows[row] | cols[col] | boxes[box])
            count = candidates.bit_count()
            if count < count_best:
                best, candidates_best, count_best = (row, col), candidates, count
                if count <= 1:
                    break
        if best is None:
            solutions += 1
            return
        if count_best == 0:
            return
        row, col = best
        box = (row // 3) * 3 + col // 3
        while candidates_best:
            bit = candidates_best & -candidates_best
            candidates_best ^= bit
            board[row][col] = bit.bit_length()
            rows[row] |= bit
            cols[col] |= bit
            boxes[box] |= bit
            search()
            board[row][col] = 0
            rows[row] ^= bit
            cols[col] ^= bit
            boxes[box] ^= bit
            if solutions >= limit:
                return

    search()
    return solutions


def _make_sudoku(rng: random.Random, minimum: int, maximum: int):
    solution = _solved_sudoku(rng)
    target = rng.randint(minimum, maximum)
    removals = list(range(81))
    rng.shuffle(removals)
    puzzle = [row[:] for row in solution]
    removed = []
    for index in removals[: 81 - target]:
        row, col = divmod(index, 9)
        puzzle[row][col] = 0
        removed.append((row, col))
    while _sudoku_solution_count(puzzle) != 1 and removed:
        row, col = removed.pop()
        puzzle[row][col] = solution[row][col]
    clue_count = sum(value != 0 for row in puzzle for value in row)
    return puzzle, solution, clue_count


def build_sudoku_bank(size: int = 5000) -> list[dict]:
    rng = random.Random(SEED + 1)
    quotas = {"easy": 1600, "medium": 1800, "hard": size - 3400}
    bands = {"easy": (40, 50), "medium": (35, 39), "hard": (30, 34)}
    puzzles = []
    seen = set()
    for level, quota in quotas.items():
        accepted = 0
        attempts = 0
        while accepted < quota:
            attempts += 1
            puzzle, solution, clues = _make_sudoku(rng, *bands[level])
            if not bands[level][0] <= clues <= bands[level][1]:
                continue
            key = tuple(value for row in puzzle for value in row)
            if key in seen:
                continue
            seen.add(key)
            puzzles.append({
                "id": f"sudoku-{len(puzzles) + 1:04d}",
                "puzzle": puzzle,
                "solution": solution,
                "difficulty": level,
            })
            accepted += 1
            if attempts % 10_000 == 0:
                print(f"Sudoku {level}: {accepted}/{quota} accepted from {attempts} candidates", flush=True)
            if attempts > quota * 100_000:
                raise RuntimeError(f"Could not generate enough unique {level} Sudoku puzzles")
    return puzzles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--word-list", type=Path, required=True, help="SCOWL en_CA.txt source list")
    parser.add_argument("--output", type=Path, default=GAME_DATA)
    args = parser.parse_args()
    raw_words = args.word_list.read_text(encoding="utf-8").splitlines()
    ladder_words = sorted({word for word in raw_words if re.fullmatch(r"[a-z]{4}", word)})
    if not ladder_words:
        raise SystemExit("No lowercase four-letter words found in the input list")
    args.output.mkdir(parents=True, exist_ok=True)
    _write_json(args.output / "word_ladder_words.json", ladder_words)
    print(f"Building ladders from {len(ladder_words)} four-letter words", flush=True)
    _write_json(args.output / "word_ladders.json", build_ladder_bank(ladder_words))
    print("Building unique 8x8 nonograms", flush=True)
    _write_json(args.output / "mini_nonograms.json", build_nonogram_bank())
    print("Building unique Sudoku puzzles", flush=True)
    _write_json(args.output / "mini_sudoku.json", build_sudoku_bank())


if __name__ == "__main__":
    main()
