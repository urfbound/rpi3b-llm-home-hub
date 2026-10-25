"""The first Games slice: a private, resumable daily word puzzle."""

import sqlite3
import json
import re
from datetime import date

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def game_app(tmp_path, monkeypatch):
    monkeypatch.setattr('homehub.app.household_today', lambda *args, **kwargs: date(2026, 10, 24))
    app = create_app({'TESTING': True, 'SECRET_KEY': 'games-test-secret',
                      'DATABASE': str(tmp_path / 'hub.db')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        for name, kind in (('alex', 'person'), ('sam', 'person'), ('display', 'house')):
            db.execute('INSERT INTO users(username,display_name,password_hash,role,account_kind) '
                       'VALUES(?,?,?,?,?)', (name, name.title(), PH.hash('test password 123'),
                                           'user', kind))
    return app


def sign_in(app, name):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    response = client.post('/login', data={'username': name, 'password': 'test password 123',
                                            '_csrf': token})
    assert response.status_code == 302
    return client


def token_for(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def test_games_page_requires_login_and_keeps_solution_private(game_app):
    assert game_app.test_client().get('/games').status_code == 302
    alex = sign_in(game_app, 'alex')
    page = alex.get('/games')
    assert page.status_code == 200
    assert b'Word Garden' in page.data
    assert b'Mini Crossword' in page.data
    assert b'Hunt the Wumpus' in page.data
    assert b'Word Ladder' in page.data
    assert b'Mini Nonogram' in page.data
    assert b'Mini Sudoku' in page.data
    from homehub.games import daily_answer
    assert daily_answer(date(2026, 10, 24)).encode() not in page.data


def test_word_bank_covers_thirty_distinct_days():
    from homehub.games import daily_answer
    start = date(2026, 10, 1)
    from datetime import timedelta
    answers = [daily_answer(start + timedelta(days=offset)) for offset in range(30)]
    assert len(set(answers)) == 30
    assert all(len(answer) == 5 and answer.isalpha() for answer in answers)


def test_daily_attempt_is_private_resumable_and_scored_once(game_app):
    from homehub.games import daily_answer
    answer = daily_answer(date(2026, 10, 24))
    alex = sign_in(game_app, 'alex')
    sam = sign_in(game_app, 'sam')
    assert alex.post('/games/word/guess', data={'guess': answer}).status_code == 400
    assert alex.post('/games/word/guess', data={'_csrf': token_for(alex),
                                                'guess': '1bad!'}).status_code == 400
    assert alex.post('/games/word/guess', data={'_csrf': token_for(alex),
                                                'guess': answer}).status_code == 302
    resumed = alex.get('/games').data
    assert b'100 points' in resumed
    assert answer.encode() in resumed
    assert answer.encode() not in sam.get('/games').data
    assert alex.post('/games/word/guess', data={'_csrf': token_for(alex),
                                                'guess': answer}).status_code == 409
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*), SUM(points) FROM game_attempts WHERE mode='daily'").fetchone() == (1, 100)


def test_house_account_can_play_but_is_absent_from_scores(game_app):
    from homehub.games import daily_answer
    house = sign_in(game_app, 'display')
    answer = daily_answer(date(2026, 10, 24))
    response = house.post('/games/word/guess', data={'_csrf': token_for(house),
                                                     'guess': answer})
    assert response.status_code == 302
    assert b'Display' not in sign_in(game_app, 'alex').get('/games').data


def test_repeated_letters_use_answer_counts():
    from homehub.games import mark_guess
    assert mark_guess('ALLEY', 'APPLE') == ('correct', 'present', 'absent', 'present', 'absent')


def test_word_garden_accepts_common_offline_guesses(game_app):
    from homehub.games import VALID_WORDS
    assert len(VALID_WORDS) >= 100
    assert all(len(word) == 5 and word.isalpha() and word.isupper()
               for word in VALID_WORDS)
    alex = sign_in(game_app, 'alex')
    for guess in ('ABOUT', 'AGAIN', 'BRAIN', 'SMILE'):
        assert alex.post('/games/word/guess', data={
            '_csrf': token_for(alex), 'guess': guess
        }).status_code == 302


def test_practice_never_changes_daily_score(game_app):
    from homehub.games import daily_answer
    alex = sign_in(game_app, 'alex')
    response = alex.post('/games/word/practice', data={'_csrf': token_for(alex)})
    assert response.status_code == 302
    practice_url = response.headers['Location']
    assert practice_url.startswith('/games/word/practice/')
    assert alex.get(practice_url).status_code == 200
    assert alex.post(practice_url + '/guess', data={
        '_csrf': token_for(alex), 'guess': daily_answer(date(2026, 10, 24))
    }).status_code == 302
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM game_attempts WHERE mode='daily'").fetchone()[0] == 0
    assert b'No scored wins yet' in alex.get('/games').data
    assert sign_in(game_app, 'sam').get(practice_url).status_code == 404


def test_old_unfinished_attempt_expires_and_cannot_be_resumed(game_app, monkeypatch):
    from homehub.games import daily_answer
    alex = sign_in(game_app, 'alex')
    answer = daily_answer(date(2026, 10, 24))
    assert alex.post('/games/word/guess', data={'_csrf': token_for(alex),
                                                'guess': 'CRANE'}).status_code == 302
    monkeypatch.setattr('homehub.app.household_today',
                        lambda *args, **kwargs: date(2026, 10, 25))
    page = alex.get('/games').data
    assert b'CRANE' not in page
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT status,points FROM game_attempts WHERE mode='daily'").fetchone() == ('expired', 0)
    assert alex.post('/games/word/guess', data={'_csrf': token_for(alex),
                                                'guess': answer}).status_code == 302


def test_all_time_scores_and_streak_follow_household_days(game_app, monkeypatch):
    from homehub.games import daily_answer
    alex = sign_in(game_app, 'alex')
    for day in (date(2026, 10, 24), date(2026, 10, 25)):
        monkeypatch.setattr('homehub.app.household_today',
                            lambda *args, current=day, **kwargs: current)
        assert alex.post('/games/word/guess', data={
            '_csrf': token_for(alex), 'guess': daily_answer(day)
        }).status_code == 302
    page = alex.get('/games').data
    assert b'200 points' in page
    assert b'2-day streak' in page
    monkeypatch.setattr('homehub.app.household_today',
                        lambda *args, **kwargs: date(2026, 10, 27))
    assert b'0-day streak' in alex.get('/games').data


def test_ladder_has_thirty_distinct_solvable_daily_puzzles():
    from datetime import timedelta
    from homehub.game_ladder import daily_puzzle, shortest_path
    puzzles = [daily_puzzle(date(2026, 10, 1) + timedelta(days=offset))
               for offset in range(30)]
    assert len({(start, target) for start, target in puzzles}) == 30
    assert all(3 <= len(shortest_path(start, target)) <= 8 for start, target in puzzles)


def test_ladder_daily_page_displays_its_estimated_difficulty(game_app):
    from homehub.game_ladder import daily_puzzle, puzzle_difficulty

    alex = sign_in(game_app, 'alex')
    pair = daily_puzzle(date(2026, 10, 24))
    difficulty = puzzle_difficulty(*pair).title()

    assert difficulty.encode() in alex.get('/games/ladder').data


def test_sudoku_page_displays_its_estimated_difficulty(game_app):
    from homehub.game_grids import daily_puzzle

    alex = sign_in(game_app, 'alex')
    puzzle = daily_puzzle('sudoku', date(2026, 10, 24))

    assert f"{puzzle['difficulty']} estimate".encode() in alex.get('/games/sudoku').data


def test_ladder_validates_steps_and_scores_shortest_win(game_app):
    from homehub.game_ladder import daily_puzzle, shortest_path
    alex = sign_in(game_app, 'alex')
    start, target = daily_puzzle(date(2026, 10, 24))
    assert start.encode() in alex.get('/games/ladder').data
    assert target.encode() in alex.get('/games/ladder').data
    bad = alex.post('/games/ladder/move', data={'_csrf': token_for(alex), 'word': 'XXXXX'})
    assert bad.status_code == 400
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM game_attempts WHERE game='ladder'").fetchone()[0] == 0
    for word in shortest_path(start, target)[1:]:
        response = alex.post('/games/ladder/move', data={'_csrf': token_for(alex), 'word': word})
        assert response.status_code == 302
    assert b'100 points' in alex.get('/games/ladder').data
    assert alex.post('/games/ladder/move', data={'_csrf': token_for(alex),
                                                'word': target}).status_code == 409


def test_ladder_hints_reduce_score_and_practice_is_private(game_app):
    from homehub.game_ladder import daily_puzzle, shortest_path
    alex = sign_in(game_app, 'alex')
    start, target = daily_puzzle(date(2026, 10, 24))
    assert alex.post('/games/ladder/hint', data={'_csrf': token_for(alex)}).status_code == 302
    for word in shortest_path(start, target)[1:]:
        assert alex.post('/games/ladder/move', data={'_csrf': token_for(alex),
                                                    'word': word}).status_code == 302
    assert b'85 points' in alex.get('/games/ladder').data
    practice = alex.post('/games/ladder/practice', data={'_csrf': token_for(alex)})
    assert practice.status_code == 302
    url = practice.headers['Location']
    assert sign_in(game_app, 'sam').get(url).status_code == 404
    assert b'Practice' in alex.get(url).data


@pytest.mark.parametrize('game', ['crossword', 'nonogram', 'sudoku'])
def test_grid_games_have_thirty_distinct_daily_puzzles(game):
    from datetime import timedelta
    from homehub.game_grids import daily_puzzle
    puzzles = [daily_puzzle(game, date(2026, 10, 1) + timedelta(days=offset))
               for offset in range(30)]
    assert len({puzzle['answer'] for puzzle in puzzles}) == 30
    assert all(puzzle['answer'] != puzzle['initial'] for puzzle in puzzles)
    if game == 'crossword':
        assert all(sum(cell != '#' for cell in puzzle['answer']) >= 17 for puzzle in puzzles)
        assert all(len(puzzle['clues']) == 10 for puzzle in puzzles)
        for puzzle in puzzles:
            rows = tuple(puzzle['answer'][row * 5:(row + 1) * 5].replace('#', '')
                         for row in range(5))
            columns = tuple(''.join(puzzle['answer'][row * 5 + col] for row in range(5))
                            .replace('#', '') for col in range(5))
            assert rows == columns


@pytest.mark.parametrize('game', ['crossword', 'nonogram', 'sudoku'])
def test_grid_games_validate_moves_hints_and_score(game_app, game):
    from homehub.game_grids import daily_puzzle
    alex = sign_in(game_app, 'alex')
    puzzle = daily_puzzle(game, date(2026, 10, 24))
    page = alex.get(f'/games/{game}')
    assert page.status_code == 200
    assert puzzle['answer'].encode() not in page.data
    assert alex.post(f'/games/{game}/submit', data={'_csrf': token_for(alex),
                                                    'board': 'bad'}).status_code == 400
    assert alex.post(f'/games/{game}/hint', data={'_csrf': token_for(alex),
                                                  'cell': '999'}).status_code == 400
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM game_attempts WHERE game=?', (game,)).fetchone()[0] == 0
    assert alex.post(f'/games/{game}/hint', data={'_csrf': token_for(alex),
                                                  'cell': str(puzzle['first_open'])}).status_code == 302
    assert alex.post(f'/games/{game}/submit', data={'_csrf': token_for(alex),
                                                    'board': puzzle['answer']}).status_code == 302
    assert b'85 points' in alex.get(f'/games/{game}').data
    assert alex.post(f'/games/{game}/submit', data={'_csrf': token_for(alex),
                                                    'board': puzzle['answer']}).status_code == 409


@pytest.mark.parametrize('game', ['crossword', 'nonogram', 'sudoku'])
def test_grid_practice_is_private_and_unscored(game_app, game):
    alex = sign_in(game_app, 'alex')
    response = alex.post(f'/games/{game}/practice', data={'_csrf': token_for(alex)})
    assert response.status_code == 302
    url = response.headers['Location']
    assert b'Practice' in alex.get(url).data
    assert sign_in(game_app, 'sam').get(url).status_code == 404
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM game_attempts WHERE mode='daily' AND game=?",
                          (game,)).fetchone()[0] == 0


def test_wumpus_maps_are_shared_deterministic_and_unique_for_a_year():
    from datetime import timedelta
    from homehub.game_wumpus import daily_map, map_for_seed, bat_destination, ROOMS
    assert map_for_seed(1)['placements'] == (8, 6, 17, 10, 3, 15)
    assert bat_destination(1, 1) == 7
    start = date(2026, 1, 1)
    maps = [daily_map(start + timedelta(days=offset)) for offset in range(365)]
    assert daily_map(start) == maps[0]
    assert len({tuple(game['placements']) for game in maps}) == 365
    assert all(len(game['placements']) == 6 for game in maps)
    assert all(len(ROOMS[room]) == 3 for room in ROOMS)
    assert all(room in ROOMS[neighbor] for room, neighbors in ROOMS.items()
               for neighbor in neighbors)


def test_wumpus_map_shows_topology_and_only_visited_clues(game_app):
    from homehub.game_wumpus import ROOMS, daily_map, map_geometry, warnings_for

    points, edges = map_geometry()
    lengths = [sum((points[a][i] - points[b][i]) ** 2 for i in range(3))
               for a, b in edges]
    assert max(lengths) - min(lengths) < .001

    alex = sign_in(game_app, 'alex')
    game = daily_map(date(2026, 10, 24))
    start = game['placements'][0]
    page = alex.get('/games/wumpus').data.decode()
    assert 'id="hide-wumpus-map"' in page
    assert 'wumpus-map.js' in page
    assert page.index('<section class="panel"><div class="wumpus-map-wrap">') < page.index('<h2>Room ')
    assert (page.index('Drag to rotate the cave.') < page.index('id="hide-wumpus-map"')
            < page.index('<svg id="wumpus-map"'))
    corridors = [(classes, int(a), int(b)) for classes, a, b in
                 re.findall(r'<line class="([^"]+)" data-a="(\d+)" data-b="(\d+)"', page)]
    assert len(corridors) == 30
    assert all(('dashed' in classes) == (a != start or b != start)
               for classes, a, b in corridors)
    rooms = dict((int(room), clues) for room, clues in
                 re.findall(r'data-room="(\d+)" data-clues="([^"]*)"', page))
    assert set(rooms) == set(ROOMS)
    assert rooms[start] == ', '.join(warnings_for(start, game['placements']))
    assert all(not clues for room, clues in rooms.items() if room != start)
    assert f'class="wumpus-room-number" x="30">{start}</text>' in page
    assert all(f'class="wumpus-room-number" x="30">{room}</text>' not in page
               for room in ROOMS if room != start)
    assert len(re.findall(r'class="wumpus-room unseen"', page)) == 19
    start_markup = re.search(rf'id="wumpus-room-{start}".*?</g>', page).group()
    assert ('🍃' in start_markup) == ('You feel a draft.' in warnings_for(start, game['placements']))
    assert ('🦇' in start_markup) == ('You hear bats fluttering.' in warnings_for(start, game['placements']))
    assert ('🐲' in start_markup) == ('You smell the Wumpus nearby.' in warnings_for(start, game['placements']))
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM game_attempts WHERE game='wumpus'").fetchone()[0] == 0

    safe = next(room for room in ROOMS[start] if room not in game['placements'][1:])
    alex.post('/games/wumpus/action', data={'_csrf': token_for(alex),
                                            'action': 'move', 'rooms': str(safe)})
    page = alex.get('/games/wumpus').data.decode()
    rooms = dict((int(room), clues) for room, clues in
                 re.findall(r'data-room="(\d+)" data-clues="([^"]*)"', page))
    assert rooms[start] == ', '.join(warnings_for(start, game['placements']))
    assert rooms[safe] == ', '.join(warnings_for(safe, game['placements']))
    assert all(not clues for room, clues in rooms.items() if room not in (start, safe))
    assert f'class="wumpus-room-number" x="30">{safe}</text>' in page
    corridors = [(classes, int(a), int(b)) for classes, a, b in
                 re.findall(r'<line class="([^"]+)" data-a="(\d+)" data-b="(\d+)"', page)]
    assert all(('dashed' in classes) == (a not in (start, safe) or b not in (start, safe))
               for classes, a, b in corridors)
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        attempt = db.execute("SELECT moves,points,state FROM game_attempts WHERE game='wumpus'").fetchone()
    alex.get('/games/wumpus')
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT moves,points,state FROM game_attempts WHERE game='wumpus'").fetchone() == attempt

    legacy = json.loads(attempt[2])
    legacy.pop('seen')
    legacy.pop('bat_rooms')
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        db.execute("UPDATE game_attempts SET state=? WHERE game='wumpus'", (json.dumps(legacy),))
    page = alex.get('/games/wumpus').data.decode()
    rooms = dict((int(room), clues) for room, clues in
                 re.findall(r'data-room="(\d+)" data-clues="([^"]*)"', page))
    assert rooms[start] == ', '.join(warnings_for(start, game['placements']))
    assert rooms[safe] == ', '.join(warnings_for(safe, game['placements']))


def test_wumpus_map_records_bat_encounter_without_unseen_warnings(game_app):
    from homehub.game_wumpus import ROOMS, bat_destination, map_for_seed, warnings_for

    seed, bat = next((seed, bat) for seed in range(1000)
                     for game in [map_for_seed(seed)]
                     for bat in game['placements'][4:]
                     if bat in ROOMS[game['placements'][0]]
                     and bat_destination(seed, 1) != bat
                     and bat_destination(seed, 1) not in game['placements'][1:4]
                     and warnings_for(bat, game['placements']))
    alex = sign_in(game_app, 'alex')
    url = alex.post('/games/wumpus/practice', data={
        '_csrf': token_for(alex), 'seed': str(seed)}).headers['Location']
    alex.post(f'{url}/action', data={'_csrf': token_for(alex),
                                     'action': 'move', 'rooms': str(bat)})
    page = alex.get(url).data.decode()
    rooms = dict((int(room), clues) for room, clues in
                 re.findall(r'data-room="(\d+)" data-clues="([^"]*)"', page))
    assert rooms[bat] == 'Bats carried you away.'
    assert rooms[bat_destination(seed, 1)] == ', '.join(
        warnings_for(bat_destination(seed, 1), map_for_seed(seed)['placements']))
    bat_markup = re.search(rf'id="wumpus-room-{bat}".*?</g>', page).group()
    assert '🦇' in bat_markup
    assert '🍃' not in bat_markup and '🐲' not in bat_markup


@pytest.mark.parametrize(('hazard_index', 'emoji'), [(1, '🍽️'), (2, '⬇️')])
def test_wumpus_map_shows_only_cause_of_death(game_app, hazard_index, emoji):
    from homehub.game_wumpus import ROOMS, map_for_seed

    seed, hazard = next((seed, game['placements'][hazard_index])
                        for seed in range(1000)
                        for game in [map_for_seed(seed)]
                        if game['placements'][hazard_index] in ROOMS[game['placements'][0]])
    alex = sign_in(game_app, 'alex')
    url = alex.post('/games/wumpus/practice', data={
        '_csrf': token_for(alex), 'seed': str(seed)}).headers['Location']
    alex.post(f'{url}/action', data={'_csrf': token_for(alex),
                                     'action': 'move', 'rooms': str(hazard)})
    page = alex.get(url).data.decode()
    room_markup = re.search(rf'id="wumpus-room-{hazard}".*?</g>', page).group()
    assert emoji in room_markup
    assert not any(marker in room_markup for marker in ('🦇', '🍃', '🐲'))


def test_wumpus_move_shoot_score_and_seeded_practice(game_app):
    from homehub.game_wumpus import daily_map, shortest_rooms
    alex = sign_in(game_app, 'alex')
    game = daily_map(date(2026, 10, 24))
    page = alex.get('/games/wumpus')
    assert page.status_code == 200
    assert str(game['seed']).encode() in page.data
    assert alex.post('/games/wumpus/action', data={'_csrf': token_for(alex),
                                                   'action': 'move', 'rooms': '99'}).status_code == 400
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM game_attempts WHERE game='wumpus'").fetchone()[0] == 0
    player, wumpus, *hazards = game['placements']
    path = shortest_rooms(player, wumpus)[1:]
    response = alex.post('/games/wumpus/action', data={'_csrf': token_for(alex),
                                                       'action': 'shoot',
                                                       'rooms': ','.join(map(str, path))})
    assert response.status_code == 302
    assert b'100 points' in alex.get('/games/wumpus').data
    assert alex.post('/games/wumpus/action', data={'_csrf': token_for(alex),
                                                   'action': 'shoot', 'rooms': str(wumpus)}).status_code == 409
    practice = alex.post('/games/wumpus/practice', data={'_csrf': token_for(alex),
                                                         'seed': '123456789'})
    assert practice.status_code == 302
    url = practice.headers['Location']
    assert b'123456789' in alex.get(url).data
    assert sign_in(game_app, 'sam').get(url).status_code == 404


def test_lost_daily_game_resets_its_streak(game_app, monkeypatch):
    from homehub.games import daily_answer
    alex = sign_in(game_app, 'alex')
    answer = daily_answer(date(2026, 10, 24))
    assert alex.post('/games/word/guess', data={'_csrf': token_for(alex),
                                                'guess': answer}).status_code == 302
    monkeypatch.setattr('homehub.app.household_today',
                        lambda *args, **kwargs: date(2026, 10, 25))
    for _ in range(6):
        assert alex.post('/games/word/guess', data={'_csrf': token_for(alex),
                                                    'guess': 'CRANE'}).status_code == 302
    assert b'0-day streak' in alex.get('/games').data


def test_timed_game_ties_rank_by_completion_time(game_app):
    from homehub.game_grids import daily_puzzle
    alex = sign_in(game_app, 'alex')
    puzzle = daily_puzzle('sudoku', date(2026, 10, 24))
    state = json.dumps({'board': puzzle['answer'], 'puzzle': puzzle, 'revealed': []})
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        for name, duration in (('alex', 300), ('sam', 60)):
            uid = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
            db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer,state,status,points,started_at,finished_at) "
                       "VALUES(?,'sudoku','daily','2026-10-24',?,?,'won',100,'2026-10-24 12:00:00',datetime('2026-10-24 12:00:00',?))",
                       (uid, puzzle['answer'], state, f'+{duration} seconds'))
    page = alex.get('/games/sudoku').data
    assert page.find(b'<li>Sam') < page.find(b'<li>Alex')


def test_solutions_unlock_for_everyone_after_household_day(game_app, monkeypatch):
    from homehub.games import daily_answer
    alex = sign_in(game_app, 'alex')
    day = date(2026, 10, 24)
    assert alex.get('/games/word/archive/2026-10-24').status_code == 404
    monkeypatch.setattr('homehub.app.household_today',
                        lambda *args, **kwargs: date(2026, 10, 25))
    sam = sign_in(game_app, 'sam')
    page = sam.get('/games/word/archive/2026-10-24')
    assert page.status_code == 200
    assert daily_answer(day).encode() in page.data
    assert game_app.test_client().get('/games/word/archive/2026-10-24').status_code == 302


def test_games_hub_shows_six_playable_puzzles_and_progress(game_app):
    from homehub.game_ladder import daily_puzzle, shortest_path
    alex = sign_in(game_app, 'alex')
    initial = alex.get('/games').data
    assert b'Six daily puzzles' in initial
    assert b"Today's scores across all six games" in initial
    assert initial.count(b'Ready to play') == 6
    start, target = daily_puzzle(date(2026, 10, 24))
    for word in shortest_path(start, target)[1:]:
        alex.post('/games/ladder/move', data={'_csrf': token_for(alex), 'word': word})
    hub = alex.get('/games').data
    assert b'Word Ladder streak: 1 day' in hub
    assert b'<li>Alex' in hub


@pytest.mark.parametrize('game', ['word', 'ladder', 'crossword', 'nonogram', 'sudoku'])
def test_practice_does_not_reveal_tomorrows_daily_answer(game_app, game):
    from homehub.games import daily_answer
    from homehub.game_ladder import daily_puzzle as ladder_puzzle
    from homehub.game_grids import daily_puzzle as grid_puzzle
    alex = sign_in(game_app, 'alex')
    assert alex.post(f'/games/{game}/practice', data={'_csrf': token_for(alex)}).status_code == 302
    tomorrow = date(2026, 10, 25)
    next_answer = (daily_answer(tomorrow) if game == 'word' else
                   ladder_puzzle(tomorrow)[1] if game == 'ladder' else
                   grid_puzzle(game, tomorrow)['answer'])
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        answer = db.execute("SELECT answer FROM game_attempts WHERE game=? AND mode='practice'",
                            (game,)).fetchone()[0]
    assert answer != next_answer


def test_nonogram_bank_has_unambiguous_clues():
    from datetime import timedelta
    from homehub.game_grids import daily_puzzle

    def runs(cells):
        groups = []
        count = 0
        for cell in cells + 'x':
            if cell == '#':
                count += 1
            elif count:
                groups.append(count)
                count = 0
        return tuple(groups) or (0,)

    patterns = [''.join('#' if bits & (1 << col) else 'x' for col in range(8))
                for bits in range(256)]
    for offset in range(30):
        puzzle = daily_puzzle('nonogram', date(2026, 10, 1) + timedelta(days=offset))
        choices = [[row for row in patterns if runs(row) == tuple(clue)]
                   for clue in puzzle['row_clues']]
        column_options = [[row for row in patterns if runs(row) == tuple(clue)]
                          for clue in puzzle['col_clues']]
        solutions = 0

        def search(rows):
            nonlocal solutions
            if solutions > 1:
                return
            depth = len(rows)
            if depth == 8:
                solutions += 1
                return
            for row in choices[depth]:
                next_rows = rows + [row]
                if all(any(option.startswith(''.join(part[col] for part in next_rows))
                           for option in column_options[col]) for col in range(8)):
                    search(next_rows)

        search([])
        assert solutions == 1


def test_practice_attempts_have_a_per_user_storage_cap(game_app):
    alex = sign_in(game_app, 'alex')
    first_url = None
    for index in range(12):
        response = alex.post('/games/word/practice', data={'_csrf': token_for(alex)})
        assert response.status_code == 302
        if index == 0:
            first_url = response.headers['Location']
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM game_attempts WHERE mode='practice' "
                          "AND game='word'").fetchone()[0] == 10
    assert alex.get(first_url).status_code == 404


def test_game_win_scores_obey_formulas_and_floor():
    from homehub.games import score_for
    assert score_for('word', guesses=1) == 100
    assert score_for('word', guesses=6) == 25
    assert score_for('crossword', hints=3) == 55
    assert score_for('nonogram', hints=3) == 55
    assert score_for('sudoku', hints=3) == 55
    assert score_for('ladder', steps=20, shortest_steps=2, hints=3) == 1
    assert score_for('wumpus', moves=40, missed_arrows=4) == 1


def test_practice_hints_are_private_and_do_not_score(game_app):
    from homehub.game_grids import daily_puzzle
    alex = sign_in(game_app, 'alex')
    ladder_url = alex.post('/games/ladder/practice',
                           data={'_csrf': token_for(alex)}).headers['Location']
    assert alex.post(ladder_url + '/hint',
                     data={'_csrf': token_for(alex)}).status_code == 302
    assert b'Hint: try' in alex.get(ladder_url).data
    grid_url = alex.post('/games/sudoku/practice',
                         data={'_csrf': token_for(alex)}).headers['Location']
    cell = daily_puzzle('sudoku', date(2026, 10, 23))['first_open']
    assert alex.post(grid_url + '/hint', data={'_csrf': token_for(alex),
                                               'cell': str(cell)}).status_code == 302
    sam = sign_in(game_app, 'sam')
    assert sam.post(grid_url + '/hint', data={'_csrf': token_for(sam),
                                              'cell': str(cell)}).status_code == 404
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT SUM(points) FROM game_attempts WHERE mode='practice'").fetchone()[0] == 0


def test_crossword_blocks_cannot_be_played_as_letters(game_app):
    from homehub.game_grids import daily_puzzle
    alex = sign_in(game_app, 'alex')
    puzzle = daily_puzzle('crossword', date(2026, 10, 24))
    board = list(puzzle['initial'])
    board[puzzle['first_open']] = '#'
    assert alex.post('/games/crossword/submit', data={
        '_csrf': token_for(alex), 'board': ''.join(board)
    }).status_code == 400
    with sqlite3.connect(game_app.config['DATABASE']) as db:
        assert db.execute("SELECT COUNT(*) FROM game_attempts WHERE game='crossword'").fetchone()[0] == 0


def test_grid_check_reports_saved_incomplete_progress(game_app):
    from homehub.game_grids import daily_puzzle
    alex = sign_in(game_app, 'alex')
    puzzle = daily_puzzle('sudoku', date(2026, 10, 24))
    board = list(puzzle['initial'])
    board[puzzle['first_open']] = puzzle['answer'][puzzle['first_open']]
    assert alex.post('/games/sudoku/submit', data={'_csrf': token_for(alex),
                                                  'board': ''.join(board)}).status_code == 302
    assert b'Progress saved' in alex.get('/games/sudoku').data


def test_curated_sudoku_has_one_solution():
    from homehub.game_grids import SUDOKU_START, SUDOKU_SOLUTION
    board = list(SUDOKU_START)
    solutions = []

    def candidates(index):
        row, col = divmod(index, 9)
        used = {board[row * 9 + other] for other in range(9)}
        used.update(board[other * 9 + col] for other in range(9))
        used.update(board[(row // 3 * 3 + dr) * 9 + col // 3 * 3 + dc]
                    for dr in range(3) for dc in range(3))
        return set('123456789') - used

    def solve():
        if len(solutions) > 1:
            return
        blanks = [index for index, digit in enumerate(board) if digit == '0']
        if not blanks:
            solutions.append(''.join(board))
            return
        index = min(blanks, key=lambda cell: len(candidates(cell)))
        for digit in sorted(candidates(index)):
            board[index] = digit
            solve()
            board[index] = '0'

    solve()
    assert solutions == [SUDOKU_SOLUTION]


def test_house_account_can_play_every_game_without_ranking(game_app):
    from homehub.game_grids import daily_puzzle
    from homehub.game_ladder import daily_puzzle as ladder_puzzle, shortest_path
    from homehub.game_wumpus import daily_map, shortest_rooms
    house = sign_in(game_app, 'display')
    day = date(2026, 10, 24)
    for game in ('crossword', 'nonogram', 'sudoku'):
        puzzle = daily_puzzle(game, day)
        assert house.post(f'/games/{game}/submit', data={
            '_csrf': token_for(house), 'board': puzzle['answer']
        }).status_code == 302
    start, target = ladder_puzzle(day)
    for word in shortest_path(start, target)[1:]:
        assert house.post('/games/ladder/move', data={
            '_csrf': token_for(house), 'word': word
        }).status_code == 302
    player, wumpus, *_ = daily_map(day)['placements']
    assert house.post('/games/wumpus/action', data={
        '_csrf': token_for(house), 'action': 'shoot',
        'rooms': ','.join(map(str, shortest_rooms(player, wumpus)[1:]))
    }).status_code == 302
    assert b'<li>Display' not in sign_in(game_app, 'alex').get('/games').data
