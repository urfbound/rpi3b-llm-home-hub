"""Build a deterministic bank of 5x5 crosswords for four house editors."""

from __future__ import annotations

import json
import random
from collections import defaultdict
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


def _clue_text(editor: str, definitions: list[str], harder: bool, variant: int) -> str:
    primary, alternate = definitions
    selected = alternate if harder and variant % 2 else primary
    if editor == "larry":
        return f"{primary}; or, in another sense, {alternate}"
    if editor == "moe":
        return selected
    if editor == "curly":
        sound = ("boing!", "ka-pow!", "whoosh!", "well, look at that!")[variant % 4]
        return f"{selected} — {sound}"
    aside = ("as you might hear around the house", "the sort of thing a neighbor mentions",
             "as folks say over coffee", "familiar from everyday life")[variant % 4]
    return f"{selected}, {aside}"


def build_bank(vocabulary: dict[str, list[str]], per_editor: int = 1250) -> list[dict]:
    words = set(vocabulary)
    five_letter_words = sorted(word for word in words if len(word) == 5)
    required = per_editor * len(EDITOR_ORDER)
    fills = list(_grid_fills(words, five_letter_words, required))
    if len(fills) != required:
        raise RuntimeError(f"Need {required} unique fills, found {len(fills)}")

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
                variant = (index + number + (0 if direction == "Across" else 3)) % 4
                clues.append({
                    "direction": direction,
                    "number": number,
                    "answer": word,
                    "text": _clue_text(editor, vocabulary[word], harder, variant),
                })
        bank.append({
            "id": f"{editor}-{editor_index + 1:04d}",
            "locale": "en",
            "editor": editor,
            "style_signature": SIGNATURES[editor],
            "style_version": 1,
            "difficulty": "Medium-Hard" if harder else "Medium",
            "initial": "".join("#" if char == "#" else "." for char in answer),
            "answer": answer,
            "clues": clues,
        })
    return bank


def main() -> None:
    vocabulary = json.loads((DATA / "crossword_vocabulary.json").read_text(encoding="utf-8"))
    definitions = {word: pair for length, group in vocabulary.items()
                   for word, pair in group.items() if len(word) == int(length)}
    bank = build_bank(definitions)
    _write_jsonl(DATA / "crossword_editors.jsonl", bank)
    print(f"Wrote {len(bank)} puzzles ({len(definitions)} clueable words)")


if __name__ == "__main__":
    main()
