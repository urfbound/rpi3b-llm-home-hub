"""Prepare an offline crossword fill list from the attributed ESDB word list.

Requires the build-time-only ``wordfreq`` package. The output is checked in so
normal bank regeneration and the Raspberry Pi app need no external download.
"""

import argparse
import hashlib
import json
from pathlib import Path

from wordfreq import zipf_frequency


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = "905b1b6b0a98fcb3701c9c59290a090a4d6ab9127809017fefacbe9c2c6f15a4"
EXCLUDE = {
    "ASS", "CUM", "SEX", "III", "PETER", "KEN", "NON", "COS", "VOL",
    "SLAVE", "COM", "GEN", "INC", "LTD", "MPH", "EST", "TRANS", "THO",
    "THY", "TIL", "SEC", "REV",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="en_CA.txt from ESDB release 2026.02.25")
    args = parser.parse_args()
    data = args.source.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        parser.error("source checksum does not match the reviewed ESDB release")
    vocabulary = json.loads((ROOT / "homehub/game_data/crossword_vocabulary.json")
                            .read_text(encoding="utf-8"))
    output = {}
    for length in (3, 5):
        source_words = {word.upper() for word in data.decode("utf-8").splitlines()
                        if len(word) == length and word.isascii() and word.islower()
                        and word.isalpha() and zipf_frequency(word, "en") >= 4.0}
        output[str(length)] = sorted((source_words | set(vocabulary[str(length)])) - EXCLUDE)
    path = ROOT / "homehub/game_data/crossword_fill_words.json"
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        output["original_clue_words"] = previous["original_clue_words"]
    else:
        output["original_clue_words"] = sorted(
            word for group in vocabulary.values() for word in group)
    path.write_text(json.dumps(output, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(output['3'])} three-letter and {len(output['5'])} five-letter words")


if __name__ == "__main__":
    main()
