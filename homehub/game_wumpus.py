"""Deterministic twenty-room cave and one-year nonrepeating daily maps."""

from collections import deque
import hashlib
import math


ROOMS = {
    1: (2, 5, 8), 2: (1, 3, 10), 3: (2, 4, 12), 4: (3, 5, 14),
    5: (1, 4, 6), 6: (5, 7, 15), 7: (6, 8, 17), 8: (1, 7, 9),
    9: (8, 10, 18), 10: (2, 9, 11), 11: (10, 12, 19), 12: (3, 11, 13),
    13: (12, 14, 20), 14: (4, 13, 15), 15: (6, 14, 16),
    16: (15, 17, 20), 17: (7, 16, 18), 18: (9, 17, 19),
    19: (11, 18, 20), 20: (13, 16, 19),
}


def map_geometry():
    """Dodecahedron vertices matched to the cave's numbered corridors."""
    phi = (1 + math.sqrt(5)) / 2
    small = 1 / phi
    vertices = {
        1: (-1, -1, -1), 2: (0, -small, -phi), 3: (1, -1, -1),
        4: (small, -phi, 0), 5: (-small, -phi, 0), 6: (-1, -1, 1),
        7: (-phi, 0, small), 8: (-phi, 0, -small), 9: (-1, 1, -1),
        10: (0, small, -phi), 11: (1, 1, -1), 12: (phi, 0, -small),
        13: (phi, 0, small), 14: (1, -1, 1), 15: (0, -small, phi),
        16: (0, small, phi), 17: (-1, 1, 1), 18: (-small, phi, 0),
        19: (small, phi, 0), 20: (1, 1, 1),
    }
    points = {room: tuple(round(value / phi, 4) for value in vertex)
              for room, vertex in vertices.items()}
    edges = [(room, neighbor) for room, neighbors in ROOMS.items()
             for neighbor in neighbors if room < neighbor]
    return points, edges


def shortest_rooms(start, target):
    queue = deque([(start,)])
    seen = {start}
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        for room in ROOMS[path[-1]]:
            if room not in seen:
                seen.add(room)
                queue.append((*path, room))
    return ()


def map_for_seed(seed):
    if not 0 <= seed < 2**64:
        raise ValueError('seed must be an unsigned 64-bit integer')
    rooms = sorted(range(1, 21), key=lambda room:
                   hashlib.sha256(f'homehub-wumpus-v1:{seed}:{room}'.encode()).digest())
    return {'seed': seed, 'placements': tuple(rooms[:6])}


def bat_destination(seed, move_number):
    digest = hashlib.sha256(f'homehub-bat-v1:{seed}:{move_number}'.encode()).digest()
    return int.from_bytes(digest[:8], 'big') % 20 + 1


def _daily_maps():
    maps = []
    seen = set()
    for index in range(365):
        counter = 0
        while True:
            digest = hashlib.sha256(f'homehub-wumpus-v1:{index}:{counter}'.encode()).digest()
            game = map_for_seed(int.from_bytes(digest[:8], 'big'))
            if game['placements'] not in seen:
                maps.append(game)
                seen.add(game['placements'])
                break
            counter += 1
    return tuple(maps)


DAILY_MAPS = _daily_maps()


def daily_map(day):
    return DAILY_MAPS[day.toordinal() % 365]


def warnings_for(room, placements):
    _, wumpus, pit1, pit2, bat1, bat2 = placements
    neighbors = ROOMS[room]
    warnings = []
    if wumpus in neighbors:
        warnings.append('You smell the Wumpus nearby.')
    if pit1 in neighbors or pit2 in neighbors:
        warnings.append('You feel a draft.')
    if bat1 in neighbors or bat2 in neighbors:
        warnings.append('You hear bats fluttering.')
    return warnings
