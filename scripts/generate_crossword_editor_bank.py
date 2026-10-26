"""Build a deterministic bank of 5x5 crosswords for four house editors."""

from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "homehub" / "game_data"
SEED = 20261024
EDITOR_ORDER = ("larry", "moe", "curly", "shemp")
SIGNATURES = {
    "larry": "wry_wordplay",
    "moe": "direct_definition",
    "curly": "playful_sound",
    "shemp": "warm_anecdote",
}


def _write_jsonl(path: Path, data) -> None:
    path.write_text("".join(json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n"
                     for row in data), encoding="utf-8")


def _grid_fills(words: set[str], five_letter_words: list[str], limit: int):
    """Yield distinct fills for the game's rotationally symmetric 5x5 mask."""
    three_letter_words = sorted(word for word in words if len(word) == 3)
    by_final = defaultdict(list)
    by_initial = defaultdict(list)
    for word in three_letter_words:
        by_final[word[-1]].append(word)
        by_initial[word[0]].append(word)

    centers = sorted(five_letter_words)
    center_pairs = [(across, down) for across in centers for down in centers
                    if across[2] == down[2]]
    random.Random(SEED).shuffle(center_pairs)
    seen = set()
    for across_center, down_center in center_pairs:
        upper = []
        for first_row in by_final[down_center[0]]:
            for second_row in by_final[down_center[1]]:
                first_down = first_row[0] + second_row[0] + across_center[0]
                second_down = first_row[1] + second_row[1] + across_center[1]
                if first_down in words and second_down in words:
                    upper.append((first_row, second_row))

        lower = []
        for first_row in by_initial[down_center[3]]:
            for second_row in by_initial[down_center[4]]:
                first_down = down_center[3] + first_row[1] + second_row[1]
                second_down = down_center[4] + first_row[2] + second_row[2]
                if first_down in words and second_down in words:
                    lower.append((first_row, second_row))

        for upper_rows in upper:
            for lower_rows in lower:
                across = (*upper_rows, across_center, *lower_rows)
                down = (
                    upper_rows[0][0] + upper_rows[1][0] + across_center[0],
                    upper_rows[0][1] + upper_rows[1][1] + across_center[1],
                    down_center,
                    across_center[3] + lower_rows[0][1] + lower_rows[1][1],
                    across_center[4] + lower_rows[0][2] + lower_rows[1][2],
                )
                if all(word in words for word in down):
                    answer = (across[0] + "##" + across[1] + "##" + across[2]
                              + "##" + across[3] + "##" + across[4])
                    if answer not in seen:
                        seen.add(answer)
                        yield answer, across, down
                        if len(seen) >= limit:
                            return


def _clue_text(editor: str, word: str, definitions: list[str], harder: bool,
               variant: int, authored: dict) -> str:
    options = authored.get(word, {}).get(editor)
    if options:
        return options[variant % len(options)]
    primary, alternate = definitions
    selected = alternate if harder or variant % 2 else primary
    if editor == "moe":
        return selected
    return alternate if variant % 2 else primary


def _select_fills(fill_words: dict, per_editor: int):
    words = set(fill_words["3"]) | set(fill_words["5"])
    legacy = set(fill_words["original_clue_words"])
    required = per_editor * len(EDITOR_ORDER)
    candidates = [fill for fill in _grid_fills(words, fill_words["5"], 500000)
                  if len(set(fill[1] + fill[2])) == 10]
    if len(candidates) < required:
        raise RuntimeError(f"Need {required} ten-entry fills, found {len(candidates)}")
    counts = Counter(word for _, across, down in candidates for word in across + down)
    centers = Counter(across[2] for _, across, _ in candidates)
    masks = defaultdict(list)
    for index, (answer, _, _) in enumerate(candidates):
        for position, char in enumerate(answer):
            if char != "#":
                masks[answer[:position] + "." + answer[position + 1:]].append(index)
    neighbors = [set() for _ in candidates]
    for group in masks.values():
        for index in group:
            neighbors[index].update(other for other in group if other != index)
    degree = [len(group) for group in neighbors]
    active = list(range(len(candidates)))
    randomizer = random.Random(SEED)
    while len(active) > required:
        choices = randomizer.sample(active, min(128, len(active)))
        remove = max(choices, key=lambda index: (
            sum(counts[word] for word in candidates[index][1] + candidates[index][2])
            + 5 * centers[candidates[index][1][2]] + 100 * degree[index]
            + 50 * sum(word not in legacy for word in candidates[index][1] + candidates[index][2]),
            candidates[index][0]))
        active.remove(remove)
        _, across, down = candidates[remove]
        counts.subtract(across + down)
        centers[across[2]] -= 1
        for neighbor in neighbors[remove]:
            degree[neighbor] -= 1
    return [candidates[index] for index in active]


def _spaced_fills(fills):
    """Place near neighbors on different days without loading data at runtime."""
    fills = fills[:]
    random.Random(SEED).shuffle(fills)
    entries = {answer: set(across + down) for answer, across, down in fills}

    def compatible(left, right):
        return (left[1][2] != right[1][2] and
                sum(a != b for a, b in zip(left[0], right[0])) >= 8 and
                len(entries[left[0]] & entries[right[0]]) <= 2)

    size = len(fills)
    for index in range(size):
        next_index = (index + 1) % size
        if compatible(fills[index], fills[next_index]):
            continue
        for swap_index in range(size):
            if swap_index in {index, next_index}:
                continue
            fills[next_index], fills[swap_index] = fills[swap_index], fills[next_index]
            affected = {(next_index - 1) % size, next_index,
                        (swap_index - 1) % size, swap_index}
            if all(compatible(fills[position], fills[(position + 1) % size])
                   for position in affected):
                break
            fills[next_index], fills[swap_index] = fills[swap_index], fills[next_index]
        else:
            raise RuntimeError("Could not space neighboring crossword grids")

    masks = defaultdict(list)
    for answer, _, _ in fills:
        for position, char in enumerate(answer):
            if char != "#":
                masks[answer[:position] + "." + answer[position + 1:]].append(answer)
    near = defaultdict(set)
    for group in masks.values():
        for answer in group:
            near[answer].update(other for other in group if other != answer)
    positions = {fill[0]: index for index, fill in enumerate(fills)}

    def nearby(index, answer):
        return any(min(abs(index - positions[other]),
                       size - abs(index - positions[other])) <= 30
                   for other in near[answer])

    for index in range(size):
        if not nearby(index, fills[index][0]):
            continue
        for step in range(31, size):
            swap_index = (index + step) % size
            if swap_index == index:
                continue
            fills[index], fills[swap_index] = fills[swap_index], fills[index]
            positions[fills[index][0]] = index
            positions[fills[swap_index][0]] = swap_index
            affected = {(index - 1) % size, index,
                        (swap_index - 1) % size, swap_index}
            if (not nearby(index, fills[index][0]) and
                    not nearby(swap_index, fills[swap_index][0]) and
                    all(compatible(fills[position], fills[(position + 1) % size])
                        for position in affected)):
                break
            fills[index], fills[swap_index] = fills[swap_index], fills[index]
            positions[fills[index][0]] = index
            positions[fills[swap_index][0]] = swap_index
        else:
            raise RuntimeError("Could not space one-letter variants over 30 days")
    return fills


def build_bank(vocabulary: dict[str, list[str]], fill_words: dict, authored: dict,
               per_editor: int = 1250) -> list[dict]:
    fills = _spaced_fills(_select_fills(fill_words, per_editor))
    missing = sorted({word for _, across, down in fills for word in across + down
                      if word not in vocabulary})
    if missing:
        raise RuntimeError(f"Missing clues for {len(missing)} words: {', '.join(missing)}")

    bank = []
    counters = dict.fromkeys(EDITOR_ORDER, 0)
    for index, (answer, across, down) in enumerate(fills):
        editor = EDITOR_ORDER[index % len(EDITOR_ORDER)]
        editor_index = counters[editor]
        counters[editor] += 1
        harder = editor_index % 5 == 0
        clues = []
        for direction, entries in (("Across", across), ("Down", down)):
            for number, word in enumerate(entries, start=1):
                variant = editor_index + number + (0 if direction == "Across" else 7)
                clues.append({
                    "direction": direction,
                    "number": number,
                    "answer": word,
                    "text": _clue_text(editor, word, vocabulary[word], harder,
                                       variant, authored),
                })
        bank.append({
            "id": f"{editor}-{editor_index + 1:04d}",
            "locale": "en",
            "editor": editor,
            "style_signature": SIGNATURES[editor],
            "style_version": 2,
            "difficulty": "Medium-Hard" if harder else "Medium",
            "initial": "".join("#" if char == "#" else "." for char in answer),
            "answer": answer,
            "clues": clues,
        })
    return bank


def main() -> None:
    vocabulary = json.loads((DATA / "crossword_vocabulary.json").read_text(encoding="utf-8"))
    additions = json.loads((DATA / "crossword_vocabulary_additions.json")
                           .read_text(encoding="utf-8"))
    definitions = {word: pair for length, group in vocabulary.items()
                   for word, pair in group.items() if len(word) == int(length)}
    for length, group in additions.items():
        for word, pair in group.items():
            if len(word) != int(length) or word in definitions or len(pair) != 2:
                raise ValueError(f"Invalid added crossword word: {word}")
            definitions[word] = pair
    fill_words = json.loads((DATA / "crossword_fill_words.json").read_text(encoding="utf-8"))
    authored = json.loads((DATA / "crossword_editor_clues.json").read_text(encoding="utf-8"))
    if "--missing" in sys.argv:
        fills = _select_fills(fill_words, 1250)
        missing = {word for _, across, down in fills for word in across + down
                   if word not in definitions}
        for length in (3, 5):
            matching = sorted(word for word in missing if len(word) == length)
            print(f"{length} letters ({len(matching)}): {' '.join(matching)}")
        return
    if "--stats" in sys.argv:
        if "--pool" in sys.argv:
            words = set(fill_words["3"]) | set(fill_words["5"])
            pool = [fill for fill in _grid_fills(words, fill_words["5"], 500000)
                    if len(set(fill[1] + fill[2])) == 10]
            pool_entries = Counter(word for _, across, down in pool for word in across + down)
            print("pool", len(pool), "top entries", pool_entries.most_common(20))
        fills = _spaced_fills(_select_fills(fill_words, 1250))
        entries = Counter(word for _, across, down in fills for word in across + down)
        centers = Counter(across[2] for _, across, _ in fills)
        print("selected", len(fills), "entries", len(entries),
              "centers", len(centers), "top entries", entries.most_common(100),
              "top centers", centers.most_common(10))
        return
    bank = build_bank(definitions, fill_words, authored)
    _write_jsonl(DATA / "crossword_editors.jsonl", bank)
    print(f"Wrote {len(bank)} puzzles ({len(definitions)} clueable words)")


if __name__ == "__main__":
    main()
