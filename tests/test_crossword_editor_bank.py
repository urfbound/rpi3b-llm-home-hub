import json
from collections import Counter
from pathlib import Path

import pytest


pytestmark = pytest.mark.bank

DATA = Path(__file__).parents[1] / "homehub" / "game_data"
EDITORS = {"larry", "moe", "curly", "shemp"}


def _bank():
    return [json.loads(line) for line in
            (DATA / "crossword_editors.jsonl").read_text(encoding="utf-8").splitlines()]


def _words():
    vocabulary = json.loads((DATA / "crossword_vocabulary.json").read_text(encoding="utf-8"))
    return {word for group in vocabulary.values() for word in group}


def _entries(board, size=5):
    rows = [board[offset:offset + size] for offset in range(0, len(board), size)]
    across = [row.replace("#", "") for row in rows if row.replace("#", "")]
    down = []
    for col in range(size):
        entry = "".join(rows[row][col] for row in range(size)).replace("#", "")
        if entry:
            down.append(entry)
    return across, down


def test_editor_bank_has_1250_unique_solved_puzzles_per_editor():
    puzzles = _bank()
    assert len(puzzles) == 5000
    assert Counter(puzzle["editor"] for puzzle in puzzles) == {editor: 1250 for editor in EDITORS}
    assert len({puzzle["id"] for puzzle in puzzles}) == 5000
    assert len({puzzle["answer"] for puzzle in puzzles}) == 5000


def test_every_editor_puzzle_has_a_valid_solvable_five_by_five_grid():
    words = _words()
    for puzzle in _bank():
        initial = puzzle["initial"]
        answer = puzzle["answer"]
        assert puzzle["locale"] == "en"
        assert len(initial) == len(answer) == 25
        assert all((given == "#" and solution == "#") or
                   (given == "." and solution in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                   for given, solution in zip(initial, answer))
        across, down = _entries(answer)
        assert len(across) == len(down) == 5
        assert all(len(word) in {3, 5} and word in words for word in across + down)
        clues = puzzle["clues"]
        assert Counter(clue["direction"] for clue in clues) == {"Across": 5, "Down": 5}
        for direction, entries in (("Across", across), ("Down", down)):
            directional = [clue for clue in clues if clue["direction"] == direction]
            assert [clue["answer"] for clue in directional] == entries
            assert all(clue["text"].strip() for clue in directional)
        assert puzzle["difficulty"] in {"Medium", "Medium-Hard"}


def test_each_editor_bank_keeps_a_detectable_style_signature():
    signatures = {
        "larry": "wry_wordplay",
        "moe": "direct_definition",
        "curly": "playful_sound",
        "shemp": "warm_anecdote",
    }
    for puzzle in _bank():
        assert puzzle["style_signature"] == signatures[puzzle["editor"]]
        assert puzzle["style_version"] == 1
        clue_text = [clue["text"] for clue in puzzle["clues"]]
        if puzzle["editor"] == "larry":
            assert all("; or, in another sense," in clue for clue in clue_text)
        elif puzzle["editor"] == "moe":
            assert all("!" not in clue and "another sense" not in clue for clue in clue_text)
        elif puzzle["editor"] == "curly":
            assert all(any(sound in clue for sound in ("boing!", "ka-pow!", "whoosh!", "look at that!"))
                       for clue in clue_text)
        else:
            assert all(any(phrase in clue for phrase in
                           ("around the house", "a neighbor mentions", "over coffee",
                            "everyday life")) for clue in clue_text)


def test_each_editor_has_a_broad_mix_of_medium_difficulty_clues():
    for editor in EDITORS:
        levels = Counter(puzzle["difficulty"] for puzzle in _bank()
                         if puzzle["editor"] == editor)
        assert levels["Medium"] >= 800
        assert levels["Medium-Hard"] >= 100


def test_daily_rotation_uses_every_puzzle_once_before_repeating():
    from datetime import date, timedelta

    from homehub.game_grids import daily_puzzle

    start = date(2026, 10, 24)
    puzzles = [daily_puzzle("crossword", start + timedelta(days=offset))
               for offset in range(5000)]
    assert len({puzzle["answer"] for puzzle in puzzles}) == 5000
    assert Counter(puzzle["editor"] for puzzle in puzzles) == {
        "Larry": 1250, "Moe": 1250, "Curly": 1250, "Shemp": 1250,
    }

