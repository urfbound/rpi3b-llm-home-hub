"""Curated four-letter word graph for small offline ladder puzzles."""

from collections import deque

WORDS = frozenset('''
    COLD CORD CARD WARD WARM WORD WORE CORE CARE CARS BARS BARN BURN TURN
    GOLD GOAD LOAD LOAF LEAF LEAD HEAD HEAL TEAL TELL TALL TAIL MAIL MAIN
    BOLD BALD BALL BELL BELT SAND HAND HARD HARE HATE LATE MATE MATH PATH
    GAME GAVE GIVE FIVE FIRE FIRM FARM FORM FOAM FOOD GOOD MOOD MOON SOON
    BOON BOOK COOK COOL POOL POLL POLE PALE SALE SAGE PAGE PACE PACK BACK
    SACK SICK SILK MILK MILD WILD WILL WELL WALL WALK TALK TANK BANK BUNK
'''.split())


def one_letter_apart(left, right):
    return len(left) == len(right) == 4 and sum(a != b for a, b in zip(left, right)) == 1


NEIGHBORS = {word: tuple(sorted(other for other in WORDS if one_letter_apart(word, other)))
             for word in WORDS}


def shortest_path(start, target):
    queue = deque([(start,)])
    seen = {start}
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        for neighbor in NEIGHBORS[path[-1]]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((*path, neighbor))
    return ()


PAIRS = tuple((start, target) for start in sorted(WORDS) for target in sorted(WORDS)
              if start < target and 3 <= len(shortest_path(start, target)) <= 8)[:30]


def daily_puzzle(day):
    return PAIRS[day.toordinal() % len(PAIRS)]
