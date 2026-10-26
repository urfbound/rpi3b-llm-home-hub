"""Measure crossword repetition and the yield from an optional word source.

The optional source is a newline-separated dictionary. ``wordfreq`` is needed
only for the offline frequency experiment, never by the application.
"""

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from generate_crossword_editor_bank import _grid_fills


DATA = Path(__file__).resolve().parents[1] / "homehub" / "game_data"


def distinct_entries(fill):
    return len(set(fill[1] + fill[2]))


def one_cell_masks(answer):
    return {answer[:index] + "." + answer[index + 1:]
            for index, char in enumerate(answer) if char != "#"}


def audit_bank(bank, legacy_vocabulary, show_clues=False):
    answers = [record["answer"] for record in bank]
    masks = defaultdict(list)
    for index, answer in enumerate(answers):
        for mask in one_cell_masks(answer):
            masks[mask].append(index)
    near_pairs = {(left, right) for group in masks.values()
                  for position, left in enumerate(group) for right in group[position + 1:]}
    adjacent = Counter(sum(a != b for a, b in zip(left, right))
                       for left, right in zip(answers, answers[1:]))
    shared = Counter(len({clue["answer"] for clue in left["clues"]} &
                         {clue["answer"] for clue in right["clues"]})
                     for left, right in zip(bank, bank[1:]))
    centers = Counter(record["clues"][2]["answer"] for record in bank)
    clue_texts = Counter(clue["text"] for record in bank for clue in record["clues"])
    additions = json.loads((DATA / "crossword_vocabulary_additions.json")
                           .read_text(encoding="utf-8"))
    print("legacy clue vocabulary", {length: len(group) for length, group in
                                     legacy_vocabulary.items()})
    print("current clue vocabulary", {length: len(legacy_vocabulary[length]) +
                                      len(additions[length]) for length in ("3", "5")})
    print("bank", len(bank), "one-cell pairs", len(near_pairs),
          "puzzles with one-cell neighbor", len({index for pair in near_pairs for index in pair}))
    print("one-cell pairs within 30 days (cyclic)",
          sum(min(right - left, len(bank) - (right - left)) <= 30
              for left, right in near_pairs))
    print("adjacent distance counts", sorted(adjacent.items()))
    print("adjacent shared entry counts", sorted(shared.items()))
    print("center words", len(centers), "most common", centers.most_common(5))
    print("unique clue strings", len(clue_texts), "most repeated", clue_texts.most_common(5))
    print("puzzles with a repeated entry",
          sum(len({clue["answer"] for clue in record["clues"]}) < 10 for record in bank))
    authored = json.loads((DATA / "crossword_editor_clues.json").read_text(encoding="utf-8"))
    voiced = [sum(clue["answer"] in authored and record["editor"] in
                  authored[clue["answer"]] for clue in record["clues"])
              for record in bank]
    print("authored clue words", len(authored), "authored slots", sum(voiced),
          "puzzles with no authored clue", voiced.count(0),
          "by editor", dict(Counter(record["editor"] for record, count in zip(bank, voiced)
                                    if count == 0)))
    if show_clues:
        for clue, count in clue_texts.most_common(80):
            if count <= 100:
                break
            print("reused clue", count, repr(clue))
        reveals = Counter((clue["answer"], clue["text"])
                          for record in bank for clue in record["clues"]
                          if re.search(rf"\b{re.escape(clue['answer'])}\b", clue["text"], re.I))
        print("answer-revealing clue slots", sum(reveals.values()))
        for (answer, clue), count in reveals.most_common(30):
            print("answer revealed", count, answer, repr(clue))


def audit_fills(words, five, existing, label, limit):
    counts = Counter()
    strong = []
    total = 0
    for fill in _grid_fills(words, sorted(five), limit):
        total += 1
        entries = distinct_entries(fill)
        counts[entries] += 1
        if entries == 10:
            strong.append(fill)
    print(label, "fills", total, "limit reached", total == limit,
          "distinct-entry counts", sorted(counts.items()),
          "10-entry center words", len({fill[1][2] for fill in strong}))
    if len(strong) >= 5000:
        selected = sorted(strong, key=lambda fill:
                          (sum(word not in existing for word in fill[1] + fill[2]), fill[0]))[:5000]
        missing = {word for fill in selected for word in fill[1] + fill[2]
                   if word not in existing}
        print("lowest-new-word 5000", "new clue words", len(missing),
              "3-letter", sum(len(word) == 3 for word in missing),
              "5-letter", sum(len(word) == 5 for word in missing),
              "center words", len({fill[1][2] for fill in selected}))
        seen_masks = set()
        separated = 0
        for answer, _, _ in sorted(strong, key=lambda fill:
                (sum(word not in existing for word in fill[1] + fill[2]), fill[0])):
            fill_masks = one_cell_masks(answer)
            if not fill_masks & seen_masks:
                seen_masks.update(fill_masks)
                separated += 1
        print("greedy selection with no one-cell neighbors", separated)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="newline-separated word list")
    parser.add_argument("--zipf", type=float, help="minimum English wordfreq Zipf score")
    parser.add_argument("--clues", action="store_true", help="show repeated clue text")
    parser.add_argument("--sample", type=int, default=0,
                        help="print this many deterministic sample puzzles per editor")
    args = parser.parse_args()
    if args.zipf is not None and args.source is None:
        parser.error("--zipf requires --source")

    bank = [json.loads(line) for line in
            (DATA / "crossword_editors.jsonl").read_text(encoding="utf-8").splitlines()]
    vocabulary = json.loads((DATA / "crossword_vocabulary.json").read_text(encoding="utf-8"))
    existing = {word for group in vocabulary.values() for word in group}
    audit_bank(bank, vocabulary, args.clues)
    if args.sample:
        for editor in ("larry", "moe", "curly", "shemp"):
            selected = random.Random(20261025).sample(
                [record for record in bank if record["editor"] == editor], args.sample)
            for record in selected:
                print("sample", record["id"], record["answer"])
                for clue in record["clues"]:
                    print(f"  {clue['direction']} {clue['number']} {clue['answer']}: {clue['text']}")
    audit_fills(existing, set(vocabulary["5"]), existing, "legacy vocabulary", 100000)

    if args.source:
        source = args.source.read_text(encoding="utf-8").splitlines()
        available = {length: {word.upper() for word in source if len(word) == length
                              and word.isascii() and word.islower() and word.isalpha()}
                     for length in (3, 5)}
        for length, words in available.items():
            print("source", length, "words", len(words), "new", len(words - existing))
        if args.zipf is not None:
            from wordfreq import zipf_frequency

            pools = {length: set(vocabulary[str(length)]) |
                     {word for word in available[length]
                      if zipf_frequency(word, "en") >= args.zipf}
                     for length in (3, 5)}
            print("filtered source", args.zipf, "pool sizes",
                  {length: len(words) for length, words in pools.items()})
            audit_fills(pools[3] | pools[5], pools[5], existing,
                        "expanded vocabulary", 500000)


if __name__ == "__main__":
    main()
