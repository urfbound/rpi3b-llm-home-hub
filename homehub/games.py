"""Small, offline household games. The first playable game is Word Garden."""

from collections import Counter
from datetime import date, timedelta
from http import HTTPStatus
import json
import re
import secrets

from flask import Blueprint, abort, g, redirect, render_template, request, url_for
from . import game_ladder, game_grids, game_wumpus


games = Blueprint('games', __name__)

WORD_BANK = (
    'APPLE', 'BLOOM', 'CLOUD', 'DREAM', 'EMBER', 'FLAME', 'GRAPE', 'HEART',
    'IVORY', 'JOLLY', 'KNIFE', 'LEMON', 'MAPLE', 'NOBLE', 'OCEAN', 'PEACH',
    'QUILT', 'RIVER', 'STONE', 'TABLE', 'UNITY', 'VIVID', 'WATER', 'YEAST',
    'ZEBRA', 'ACORN', 'BRAVE', 'CORAL', 'DANCE', 'EAGLE',
)
COMMON_GUESSES = frozenset('''
    ABOUT ABOVE ABUSE ACTOR ACUTE ADAPT AGAIN AGENT AGREE AHEAD ALARM ALBUM
    ALERT ALIVE ALLOW ALONE ALTER AMONG ANGER ANGLE ANGRY APART APPLY ARENA
    ARGUE ARISE ARRAY ASIDE ASSET AUDIO AVOID AWAKE AWARD AWARE BADGE BASIC
    BEACH BEGAN BEGIN BEING BELOW BENCH BERRY BIRTH BLACK BLAME BLEND BLIND
    BLOCK BLOOD BOARD BONUS BOUND BRAIN BRAND BREAK BRIEF BRING BROAD BROKE
    BROWN BUILD BUILT BUYER CABLE CANDY CARRY CATCH CAUSE CHAIN CHAIR CHART
    CHASE CHEAP CHECK CHEEK CHEST CHIEF CHILD CHOSE CIVIL CLAIM CLASS CLEAN
    CLEAR CLICK CLIMB CLOCK CLOSE COACH COAST COUNT COURT COVER CRAFT CRASH
    CREAM CRIME CROSS CROWD CROWN CURVE CYCLE DAILY DAIRY DEATH DEBUT DELAY
    DEPTH DOING DOUBT DOZEN DRAFT DRAMA DRAWN DRESS DRINK DRIVE DROVE EARLY
    EARTH EIGHT ELDER ELECT ELITE EMPTY ENEMY ENJOY ENTER ENTRY EQUAL ERROR
    ESSAY EVENT EVERY EXACT EXIST EXTRA FAITH FALSE FAULT FIBER FIELD FIGHT
    FINAL FIRST FIXED FLASH FLEET FLOOR FOCUS FORCE FORTH FOUND FRAME FRESH
    FRONT FRUIT FUNNY GIANT GIVEN GLASS GLOBE GLOVE GOING GRACE GRADE GRAIN
    GRAND GRANT GRASS GREAT GREEN GROUP GUARD GUEST GUIDE HAPPY HARSH HEAVY
    HELLO HENCE HONEY HORSE HOTEL HUMAN IDEAL IMAGE INDEX INNER INPUT ISSUE
    JOINT JUDGE JUICE KNOWN LABEL LARGE LASER LATER LAUGH LAYER LEARN LEAST
    LEAVE LEGAL LEVEL LIGHT LIMIT LOCAL LOGIC LOOSE LUCKY LUNCH MAGIC MAJOR
    MARCH MATCH MAYBE METAL MIGHT MINOR MODEL MONEY MONTH MOTOR MOUNT MOUTH
    MOVIE MUSIC NEVER NEWLY NOISE NORTH NOVEL NURSE OCCUR OFFER OFTEN ORDER
    OTHER OUGHT PAINT PANEL PAPER PARTY PEACE PHASE PHONE PHOTO PIANO PIECE
    PILOT PITCH PLACE PLAIN PLANE PLATE POINT POWER PRESS PRICE PRIDE PRIME
    PRINT PRIOR PRIZE PROOF PROUD PROVE QUEEN QUICK QUIET QUITE RADIO RAISE
    RANGE RAPID RATIO REACH READY REALM REPLY RESET RIGHT ROBOT ROUGH ROUND
    ROUTE ROYAL RURAL SCALE SCENE SCOPE SCORE SENSE SERVE SEVEN SHADE SHAKE
    SHAPE SHARE SHARP SHEET SHELF SHELL SHIFT SHINE SHIRT SHOCK SHORE SHORT
    SHOWN SIGHT SINCE SIXTH SKILL SLEEP SLICE SLIDE SMALL SMART SMILE SMOKE
    SOLID SOLVE SOUND SOUTH SPACE SPARE SPEAK SPEED SPEND SPENT SPICE SPIKE
    SPITE SPLIT SPOKE SPORT STAFF STAGE STAND START STATE STEAM STEEL STEEP
    STICK STILL STOCK STOOD STORE STORM STORY STRIP STUDY STYLE SUGAR SUITE
    SUPER SWEET SWIFT SWING TAKEN TASTE TEACH TEETH THANK THEME THERE THESE
    THICK THING THINK THIRD THOSE THREE THROW TIGHT TIMES TIRED TITLE TODAY
    TOKEN TOPIC TOTAL TOUCH TOUGH TOWER TRACK TRADE TRAIN TREAT TREND TRIAL
    TRIBE TRICK TRUCK TRULY TRUST TRUTH TWICE UNDER UNION UNITE UNTIL UPPER
    UPSET URBAN USAGE USUAL VALUE VIDEO VISIT VITAL VOICE WASTE WATCH WAVES
    WEIGH WEIRD WHEEL WHERE WHICH WHILE WHITE WHOLE WHOSE WIDEN WOMAN WOMEN
    WORLD WORRY WORSE WORTH WOULD WRITE WRONG YIELD YOUNG YOUTH
'''.split())
VALID_WORDS = frozenset(WORD_BANK) | COMMON_GUESSES | frozenset({
    'CRANE', 'STARE', 'SLATE', 'TRACE', 'RAISE', 'ADIEU', 'ALLEY', 'PLANT',
    'HOUSE', 'MOUSE', 'TRAIN', 'SUGAR', 'LIGHT', 'NIGHT', 'BREAD', 'SHEEP',
})
GAME_NAMES = (
    ('word', 'Word Garden', 'Ready to play'),
    ('crossword', 'Mini Crossword', 'Ready to play'),
    ('wumpus', 'Hunt the Wumpus', 'Ready to play'),
    ('ladder', 'Word Ladder', 'Ready to play'),
    ('nonogram', 'Mini Nonogram', 'Ready to play'),
    ('sudoku', 'Mini Sudoku', 'Ready to play'),
)

ATTEMPT_TABLE_SQL = """CREATE TABLE IF NOT EXISTS game_attempts (
 id INTEGER PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 game TEXT NOT NULL CHECK(game IN ('word','crossword','wumpus','ladder','nonogram','sudoku')),
 mode TEXT NOT NULL CHECK(mode IN ('daily','practice')),
 puzzle_date TEXT NOT NULL,
 answer TEXT NOT NULL,
 guesses TEXT NOT NULL DEFAULT '[]',
 status TEXT NOT NULL DEFAULT 'playing' CHECK(status IN ('playing','won','lost','expired')),
 points INTEGER NOT NULL DEFAULT 0 CHECK(points BETWEEN 0 AND 100),
 state TEXT NOT NULL DEFAULT '{}',
 hints INTEGER NOT NULL DEFAULT 0 CHECK(hints BETWEEN 0 AND 3),
 moves INTEGER NOT NULL DEFAULT 0 CHECK(moves>=0),
 seed TEXT,
 started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT
);"""
SCHEMA = ATTEMPT_TABLE_SQL + """
CREATE UNIQUE INDEX IF NOT EXISTS game_daily_once
 ON game_attempts(user_id,game,puzzle_date) WHERE mode='daily';
CREATE INDEX IF NOT EXISTS game_scores ON game_attempts(mode,puzzle_date,status);
"""


def upgrade_multigame_schema(db):
    """Replace the v16 word-only constraint while retaining saved attempts."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(game_attempts)')}
    if 'state' in columns:
        return
    db.execute(ATTEMPT_TABLE_SQL.replace('game_attempts (', 'game_attempts_new ('))
    db.execute("""INSERT INTO game_attempts_new
        (id,user_id,game,mode,puzzle_date,answer,guesses,status,points,started_at,finished_at)
        SELECT id,user_id,game,mode,puzzle_date,answer,guesses,status,points,started_at,finished_at
        FROM game_attempts""")
    db.execute('DROP TABLE game_attempts')
    db.execute('ALTER TABLE game_attempts_new RENAME TO game_attempts')
    db.execute("CREATE UNIQUE INDEX game_daily_once ON game_attempts(user_id,game,puzzle_date) "
               "WHERE mode='daily'")
    db.execute('CREATE INDEX game_scores ON game_attempts(mode,puzzle_date,status)')


def daily_answer(day):
    return WORD_BANK[day.toordinal() % len(WORD_BANK)]


def score_for(game, *, guesses=0, hints=0, moves=0, missed_arrows=0,
              steps=0, shortest_steps=0):
    if game == 'word':
        raw = 100 - 15 * (guesses - 1)
    elif game in {'crossword', 'nonogram', 'sudoku'}:
        raw = 100 - 15 * hints
    elif game == 'ladder':
        raw = 100 - 10 * (steps - shortest_steps) - 15 * hints
    elif game == 'wumpus':
        raw = 100 - 3 * moves - 12 * missed_arrows
    else:
        raise ValueError('unknown game')
    return max(1, raw)


def mark_guess(guess, answer):
    """Consume exact matches first so duplicate letters cannot overcount."""
    marks = ['absent'] * 5
    remaining = Counter()
    for index, letter in enumerate(guess):
        if letter == answer[index]:
            marks[index] = 'correct'
        else:
            remaining[answer[index]] += 1
    for index, letter in enumerate(guess):
        if marks[index] != 'correct' and remaining[letter]:
            marks[index] = 'present'
            remaining[letter] -= 1
    return tuple(marks)


def _context():
    from .app import get_db, household_today
    return get_db(), household_today()


def _require_user():
    if not g.user:
        return redirect(url_for('login'))
    return None


def _expire(db, today):
    db.execute("UPDATE game_attempts SET status='expired',points=0,finished_at=CURRENT_TIMESTAMP "
               "WHERE mode='daily' AND status='playing' AND puzzle_date < ?", (today.isoformat(),))
    db.commit()


def _prune_practice(db, game):
    db.execute("""DELETE FROM game_attempts
        WHERE user_id=? AND game=? AND mode='practice' AND id NOT IN (
            SELECT id FROM game_attempts WHERE user_id=? AND game=? AND mode='practice'
            ORDER BY id DESC LIMIT 10)""",
        (g.user['id'], game, g.user['id'], game))


def _attempt(db, user_id, day):
    return db.execute("SELECT * FROM game_attempts WHERE user_id=? AND game='word' "
                      "AND mode='daily' AND puzzle_date=?", (user_id, day.isoformat())).fetchone()


def _view(attempt):
    if attempt is None:
        return None
    guesses = json.loads(attempt['guesses'])
    return {'row': attempt, 'guesses': [(guess, mark_guess(guess, attempt['answer']))
                                       for guess in guesses]}


def _scoreboard(db, today=None, game=None):
    day_filter = 'AND a.puzzle_date=?' if today else ''
    game_filter = 'AND a.game=?' if game else ''
    timed = game in {'crossword', 'nonogram', 'sudoku'}
    time_order = ', elapsed ASC' if timed else ''
    parameters = ((today.isoformat(),) if today else ()) + ((game,) if game else ())
    return db.execute(f"""
        SELECT u.display_name, SUM(a.points) AS points, COUNT(*) AS wins,
               SUM(CAST((julianday(a.finished_at)-julianday(a.started_at))*86400 AS INTEGER)) AS elapsed
        FROM game_attempts a JOIN users u ON u.id=a.user_id
        WHERE a.mode='daily' AND a.status='won' AND u.account_kind='person'
          AND u.active=1 {day_filter} {game_filter}
        GROUP BY u.id ORDER BY points DESC{time_order}, wins DESC, u.display_name COLLATE NOCASE
    """, parameters).fetchall()


def _streak(db, user_id, today, game='word'):
    rows = db.execute("SELECT puzzle_date,status FROM game_attempts WHERE user_id=? AND game=? "
                      "AND mode='daily' AND puzzle_date>=? ORDER BY puzzle_date DESC",
                      (user_id, game, (today - timedelta(days=366)).isoformat())).fetchall()
    if not rows:
        return 0
    latest = rows[0]
    if latest['puzzle_date'] == today.isoformat() and latest['status'] in {'lost', 'expired'}:
        return 0
    wins = {row['puzzle_date'] for row in rows if row['status'] == 'won'}
    expected = today if today.isoformat() in wins else today - timedelta(days=1)
    streak = 0
    while expected.isoformat() in wins:
        streak += 1
        expected -= timedelta(days=1)
    return streak


def _render(db, today, attempt=None):
    if attempt is None:
        attempt = _attempt(db, g.user['id'], today)
    statuses = {row['game']: row['status'] for row in db.execute(
        "SELECT game,status FROM game_attempts WHERE user_id=? AND mode='daily' AND puzzle_date=?",
        (g.user['id'], today.isoformat()))}
    streaks = {game: _streak(db, g.user['id'], today, game)
               for game, _, _ in GAME_NAMES}
    return render_template('games.html', games=GAME_NAMES, day=today,
                           yesterday=today - timedelta(days=1),
                           attempt=_view(attempt), scores=_scoreboard(db, today),
                           all_scores=_scoreboard(db), streak=streaks['word'],
                           streaks=streaks, statuses=statuses)


@games.get('/games')
def page():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    return _render(db, today)


def _submit_guess(db, attempt, guess):
    if attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    guess = guess.strip().upper()
    if not re.fullmatch(r'[A-Z]{5}', guess) or guess not in VALID_WORDS:
        abort(HTTPStatus.BAD_REQUEST, description='Enter a valid five-letter word.')
    guesses = json.loads(attempt['guesses'])
    guesses.append(guess)
    won = guess == attempt['answer']
    status = 'won' if won else 'lost' if len(guesses) == 6 else 'playing'
    points = score_for('word', guesses=len(guesses)) if won and attempt['mode'] == 'daily' else 0
    db.execute("UPDATE game_attempts SET guesses=?,status=?,points=?, "
               "finished_at=CASE WHEN ?='playing' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE id=?",
               (json.dumps(guesses), status, points, status, attempt['id']))
    db.commit()


@games.post('/games/word/guess')
def daily_guess():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    guess = request.form.get('guess', '').strip().upper()
    if not re.fullmatch(r'[A-Z]{5}', guess) or guess not in VALID_WORDS:
        abort(HTTPStatus.BAD_REQUEST, description='Enter a valid five-letter word.')
    attempt = _attempt(db, g.user['id'], today)
    if attempt is None:
        db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer) VALUES(?,'word','daily',?,?)",
                   (g.user['id'], today.isoformat(), daily_answer(today)))
        db.commit()
        attempt = _attempt(db, g.user['id'], today)
    _submit_guess(db, attempt, guess)
    return redirect(url_for('games.page'))


@games.post('/games/word/practice')
def practice_start():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    answer = daily_answer(today - timedelta(days=1))
    cursor = db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer) "
                        "VALUES(?,'word','practice',?,?)", (g.user['id'], today.isoformat(), answer))
    _prune_practice(db, 'word')
    db.commit()
    return redirect(url_for('games.practice_page', attempt_id=cursor.lastrowid))


def _private_practice(db, attempt_id):
    row = db.execute("SELECT * FROM game_attempts WHERE id=? AND mode='practice' AND user_id=?",
                     (attempt_id, g.user['id'])).fetchone()
    if row is None:
        abort(HTTPStatus.NOT_FOUND)
    return row


@games.get('/games/word/practice/<int:attempt_id>')
def practice_page(attempt_id):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    return _render(db, today, _private_practice(db, attempt_id))


@games.post('/games/word/practice/<int:attempt_id>/guess')
def practice_guess(attempt_id):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, _ = _context()
    _submit_guess(db, _private_practice(db, attempt_id), request.form.get('guess', ''))
    return redirect(url_for('games.practice_page', attempt_id=attempt_id))


def _ladder_attempt(db, day):
    return db.execute("SELECT * FROM game_attempts WHERE user_id=? AND game='ladder' "
                      "AND mode='daily' AND puzzle_date=?",
                      (g.user['id'], day.isoformat())).fetchone()


def _create_ladder(db, day, mode):
    start, target = game_ladder.daily_puzzle(day if mode == 'daily' else day - timedelta(days=1))
    cursor = db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer,state) "
                        "VALUES(?,'ladder',?,?,?,?)",
                        (g.user['id'], mode, day.isoformat(), target, json.dumps({'path': [start]})))
    if mode == 'practice':
        _prune_practice(db, 'ladder')
    db.commit()
    return db.execute('SELECT * FROM game_attempts WHERE id=?', (cursor.lastrowid,)).fetchone()


def _ladder_practice(db, attempt_id):
    row = db.execute("SELECT * FROM game_attempts WHERE id=? AND user_id=? AND game='ladder' "
                     "AND mode='practice'", (attempt_id, g.user['id'])).fetchone()
    if row is None:
        abort(HTTPStatus.NOT_FOUND)
    return row


def _ladder_render(db, today, attempt, practice=False):
    if attempt:
        state = json.loads(attempt['state'])
        start, target = state['path'][0], attempt['answer']
    else:
        start, target = game_ladder.daily_puzzle(today)
        state = {'path': [start]}
    return render_template('game_ladder.html', day=today, attempt=attempt, state=state,
                           start=start, target=target, practice=practice,
                           shortest=game_ladder.shortest_path(start, target),
                           scores=_scoreboard(db, today, 'ladder'), all_scores=_scoreboard(db, game='ladder'),
                           streak=_streak(db, g.user['id'], today, game='ladder'))


@games.get('/games/ladder')
def ladder_page():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    return _ladder_render(db, today, _ladder_attempt(db, today))


def _ladder_move(db, attempt, word):
    if attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    path = json.loads(attempt['state'])['path']
    word = word.strip().upper()
    if word not in game_ladder.WORDS or not game_ladder.one_letter_apart(path[-1], word):
        abort(HTTPStatus.BAD_REQUEST, description='Change exactly one letter to a listed word.')
    if len(path) >= 20:
        abort(HTTPStatus.CONFLICT)
    path.append(word)
    won = word == attempt['answer']
    status = 'won' if won else 'lost' if len(path) >= 20 else 'playing'
    shortest = len(game_ladder.shortest_path(path[0], attempt['answer'])) - 1
    points = (score_for('ladder', steps=len(path) - 1, shortest_steps=shortest,
                        hints=attempt['hints'])
              if won and attempt['mode'] == 'daily' else 0)
    db.execute("UPDATE game_attempts SET state=?,moves=?,status=?,points=?, "
               "finished_at=CASE WHEN ?='playing' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE id=?",
               (json.dumps({'path': path}), len(path) - 1, status, points, status, attempt['id']))
    db.commit()


@games.post('/games/ladder/move')
def ladder_move():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    word = request.form.get('word', '').strip().upper()
    attempt = _ladder_attempt(db, today)
    if attempt is not None and attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    start = json.loads(attempt['state'])['path'][-1] if attempt else game_ladder.daily_puzzle(today)[0]
    if word not in game_ladder.WORDS or not game_ladder.one_letter_apart(start, word):
        abort(HTTPStatus.BAD_REQUEST)
    if attempt is None:
        attempt = _create_ladder(db, today, 'daily')
    _ladder_move(db, attempt, word)
    return redirect(url_for('games.ladder_page'))


@games.post('/games/ladder/hint')
def ladder_hint():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    attempt = _ladder_attempt(db, today) or _create_ladder(db, today, 'daily')
    _add_ladder_hint(db, attempt)
    return redirect(url_for('games.ladder_page'))


def _add_ladder_hint(db, attempt):
    if attempt['status'] != 'playing' or attempt['hints'] >= 3:
        abort(HTTPStatus.CONFLICT)
    state = json.loads(attempt['state'])
    route = game_ladder.shortest_path(state['path'][-1], attempt['answer'])
    state['hint'] = route[1] if len(route) > 1 else attempt['answer']
    db.execute('UPDATE game_attempts SET state=?,hints=hints+1 WHERE id=?',
               (json.dumps(state), attempt['id']))
    db.commit()


@games.post('/games/ladder/practice')
def ladder_practice_start():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    attempt = _create_ladder(db, today, 'practice')
    return redirect(url_for('games.ladder_practice_page', attempt_id=attempt['id']))


@games.get('/games/ladder/practice/<int:attempt_id>')
def ladder_practice_page(attempt_id):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    return _ladder_render(db, today, _ladder_practice(db, attempt_id), practice=True)


@games.post('/games/ladder/practice/<int:attempt_id>/move')
def ladder_practice_move(attempt_id):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, _ = _context()
    _ladder_move(db, _ladder_practice(db, attempt_id), request.form.get('word', ''))
    return redirect(url_for('games.ladder_practice_page', attempt_id=attempt_id))


@games.post('/games/ladder/practice/<int:attempt_id>/hint')
def ladder_practice_hint(attempt_id):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, _ = _context()
    _add_ladder_hint(db, _ladder_practice(db, attempt_id))
    return redirect(url_for('games.ladder_practice_page', attempt_id=attempt_id))


GRID_GAMES = frozenset({'crossword', 'nonogram', 'sudoku'})


def _grid_game(game):
    if game not in GRID_GAMES:
        abort(HTTPStatus.NOT_FOUND)
    return game


def _grid_attempt(db, game, day):
    return db.execute("SELECT * FROM game_attempts WHERE user_id=? AND game=? AND mode='daily' "
                      "AND puzzle_date=?", (g.user['id'], game, day.isoformat())).fetchone()


def _grid_practice(db, game, attempt_id):
    row = db.execute("SELECT * FROM game_attempts WHERE id=? AND user_id=? AND game=? "
                     "AND mode='practice'", (attempt_id, g.user['id'], game)).fetchone()
    if row is None:
        abort(HTTPStatus.NOT_FOUND)
    return row


def _create_grid(db, game, day, mode):
    puzzle = game_grids.daily_puzzle(game, day if mode == 'daily' else day - timedelta(days=1))
    state = {'board': puzzle['initial'], 'puzzle': puzzle, 'revealed': []}
    cursor = db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer,state) "
                        "VALUES(?,?,?,?,?,?)",
                        (g.user['id'], game, mode, day.isoformat(), puzzle['answer'], json.dumps(state)))
    if mode == 'practice':
        _prune_practice(db, game)
    db.commit()
    return db.execute('SELECT * FROM game_attempts WHERE id=?', (cursor.lastrowid,)).fetchone()


def _grid_render(db, game, today, attempt=None, practice=False):
    if attempt:
        state = json.loads(attempt['state'])
        puzzle = state['puzzle']
    else:
        puzzle = game_grids.daily_puzzle(game, today)
        state = {'board': puzzle['initial'], 'revealed': []}
    return render_template('game_grid.html', game=game, day=today, attempt=attempt,
                           state=state, puzzle=puzzle, practice=practice,
                           scores=_scoreboard(db, today, game), all_scores=_scoreboard(db, game=game),
                           streak=_streak(db, g.user['id'], today, game=game))


@games.get('/games/<game>')
def grid_page(game):
    _grid_game(game)
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    return _grid_render(db, game, today, _grid_attempt(db, game, today))


def _board_from_form(puzzle):
    direct = request.form.get('board')
    if direct is not None:
        return direct.upper() if puzzle['game'] == 'crossword' else direct
    blank = '0' if puzzle['game'] == 'sudoku' else '.'
    board = ''.join(request.form.get(f'cell{index}', '') or blank
                    for index in range(len(puzzle['answer'])))
    return board.upper() if puzzle['game'] == 'crossword' else board


def _grid_submit(db, attempt, puzzle, board):
    if attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    state = json.loads(attempt['state'])
    if not game_grids.valid_board(puzzle, board, state['revealed']):
        abort(HTTPStatus.BAD_REQUEST)
    state['board'] = board
    won = board == attempt['answer']
    blank = '0' if puzzle['game'] == 'sudoku' else '.'
    state['feedback'] = ('Progress saved. Keep going.' if blank in board else
                         'Progress saved. The board needs another look.')
    status = 'won' if won else 'playing'
    points = score_for(attempt['game'], hints=attempt['hints']) if won and attempt['mode'] == 'daily' else 0
    db.execute("UPDATE game_attempts SET state=?,moves=moves+1,status=?,points=?, "
               "finished_at=CASE WHEN ?='won' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",
               (json.dumps(state), status, points, status, attempt['id']))
    db.commit()


@games.post('/games/<game>/submit')
def grid_submit(game):
    _grid_game(game)
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    attempt = _grid_attempt(db, game, today)
    if attempt and attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    puzzle = json.loads(attempt['state'])['puzzle'] if attempt else game_grids.daily_puzzle(game, today)
    board = _board_from_form(puzzle)
    if not game_grids.valid_board(puzzle, board) or (not attempt and board == puzzle['initial']):
        abort(HTTPStatus.BAD_REQUEST)
    if attempt is None:
        attempt = _create_grid(db, game, today, 'daily')
    _grid_submit(db, attempt, puzzle, board)
    return redirect(url_for('games.grid_page', game=game))


def _hint_cell(puzzle):
    try:
        if 'cell' in request.form:
            cell = int(request.form['cell'])
        else:
            cell = (int(request.form['row']) - 1) * puzzle['size'] + int(request.form['column']) - 1
    except (KeyError, ValueError):
        abort(HTTPStatus.BAD_REQUEST)
    blank = '0' if puzzle['game'] == 'sudoku' else '.'
    if not 0 <= cell < len(puzzle['answer']) or puzzle['initial'][cell] != blank:
        abort(HTTPStatus.BAD_REQUEST)
    return cell


@games.post('/games/<game>/hint')
def grid_hint(game):
    _grid_game(game)
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    attempt = _grid_attempt(db, game, today)
    puzzle = json.loads(attempt['state'])['puzzle'] if attempt else game_grids.daily_puzzle(game, today)
    cell = _hint_cell(puzzle)
    if attempt and attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    if attempt is None:
        attempt = _create_grid(db, game, today, 'daily')
    _add_grid_hint(db, attempt, puzzle, cell)
    return redirect(url_for('games.grid_page', game=game))


def _add_grid_hint(db, attempt, puzzle, cell):
    state = json.loads(attempt['state'])
    if attempt['status'] != 'playing' or attempt['hints'] >= 3 or cell in state['revealed']:
        abort(HTTPStatus.CONFLICT)
    board = list(state['board'])
    board[cell] = puzzle['answer'][cell]
    state['board'] = ''.join(board)
    state['revealed'].append(cell)
    db.execute('UPDATE game_attempts SET state=?,hints=hints+1 WHERE id=?',
               (json.dumps(state), attempt['id']))
    db.commit()


@games.post('/games/<game>/practice')
def grid_practice_start(game):
    _grid_game(game)
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    attempt = _create_grid(db, game, today, 'practice')
    return redirect(url_for('games.grid_practice_page', game=game, attempt_id=attempt['id']))


@games.get('/games/<game>/practice/<int:attempt_id>')
def grid_practice_page(game, attempt_id):
    _grid_game(game)
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    return _grid_render(db, game, today, _grid_practice(db, game, attempt_id), practice=True)


@games.post('/games/<game>/practice/<int:attempt_id>/submit')
def grid_practice_submit(game, attempt_id):
    _grid_game(game)
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, _ = _context()
    attempt = _grid_practice(db, game, attempt_id)
    puzzle = json.loads(attempt['state'])['puzzle']
    _grid_submit(db, attempt, puzzle, _board_from_form(puzzle))
    return redirect(url_for('games.grid_practice_page', game=game, attempt_id=attempt_id))


@games.post('/games/<game>/practice/<int:attempt_id>/hint')
def grid_practice_hint(game, attempt_id):
    _grid_game(game)
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, _ = _context()
    attempt = _grid_practice(db, game, attempt_id)
    puzzle = json.loads(attempt['state'])['puzzle']
    cell = _hint_cell(puzzle)
    _add_grid_hint(db, attempt, puzzle, cell)
    return redirect(url_for('games.grid_practice_page', game=game, attempt_id=attempt_id))


def _wumpus_attempt(db, day):
    return db.execute("SELECT * FROM game_attempts WHERE user_id=? AND game='wumpus' "
                      "AND mode='daily' AND puzzle_date=?",
                      (g.user['id'], day.isoformat())).fetchone()


def _wumpus_practice(db, attempt_id):
    row = db.execute("SELECT * FROM game_attempts WHERE id=? AND user_id=? AND game='wumpus' "
                     "AND mode='practice'", (attempt_id, g.user['id'])).fetchone()
    if row is None:
        abort(HTTPStatus.NOT_FOUND)
    return row


def _create_wumpus(db, day, mode, game):
    state = {'room': game['placements'][0], 'arrows': 5, 'misses': 0, 'history': []}
    cursor = db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer,state,seed) "
                        "VALUES(?,'wumpus',?,?,?,?,?)",
                        (g.user['id'], mode, day.isoformat(), json.dumps(game['placements']),
                         json.dumps(state), str(game['seed'])))
    if mode == 'practice':
        _prune_practice(db, 'wumpus')
    db.commit()
    return db.execute('SELECT * FROM game_attempts WHERE id=?', (cursor.lastrowid,)).fetchone()


def _wumpus_render(db, today, attempt=None, practice=False):
    game = ({'seed': int(attempt['seed']), 'placements': json.loads(attempt['answer'])}
            if attempt else game_wumpus.daily_map(today))
    state = json.loads(attempt['state']) if attempt else {
        'room': game['placements'][0], 'arrows': 5, 'misses': 0, 'history': []}
    return render_template('game_wumpus.html', day=today, attempt=attempt,
                           state=state, game=game, practice=practice,
                           exits=game_wumpus.ROOMS[state['room']],
                           warnings=game_wumpus.warnings_for(state['room'], game['placements']),
                           scores=_scoreboard(db, today, 'wumpus'), all_scores=_scoreboard(db, game='wumpus'),
                           streak=_streak(db, g.user['id'], today, game='wumpus'))


@games.get('/games/wumpus')
def wumpus_page():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    return _wumpus_render(db, today, _wumpus_attempt(db, today))


def _wumpus_request(room):
    action = request.form.get('action')
    try:
        rooms = [int(value.strip()) for value in request.form.get('rooms', '').split(',')]
    except ValueError:
        abort(HTTPStatus.BAD_REQUEST)
    if action == 'move' and len(rooms) != 1:
        abort(HTTPStatus.BAD_REQUEST)
    if action == 'shoot' and not 1 <= len(rooms) <= 5:
        abort(HTTPStatus.BAD_REQUEST)
    if action not in {'move', 'shoot'}:
        abort(HTTPStatus.BAD_REQUEST)
    previous = room
    for next_room in rooms:
        if next_room not in game_wumpus.ROOMS[previous]:
            abort(HTTPStatus.BAD_REQUEST)
        previous = next_room
    return action, rooms


def _wumpus_action(db, attempt, action, rooms):
    if attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    state = json.loads(attempt['state'])
    player, wumpus, pit1, pit2, bat1, bat2 = json.loads(attempt['answer'])
    status = 'playing'
    moves = attempt['moves']
    if action == 'move':
        room = rooms[0]
        moves += 1
        if room in (bat1, bat2):
            room = game_wumpus.bat_destination(int(attempt['seed']), moves)
            state['history'].append('Bats carried you to another room.')
        state['room'] = room
        if room == wumpus or room in (pit1, pit2):
            status = 'lost'
        state['history'].append(f'Moved to room {room}.')
    else:
        state['arrows'] -= 1
        if wumpus in rooms:
            status = 'won'
            state['history'].append('Your arrow found the Wumpus!')
        else:
            state['misses'] += 1
            state['history'].append('The arrow missed.')
            if state['arrows'] == 0:
                status = 'lost'
    points = (score_for('wumpus', moves=moves, missed_arrows=state['misses'])
              if status == 'won' and attempt['mode'] == 'daily' else 0)
    db.execute("UPDATE game_attempts SET state=?,moves=?,status=?,points=?, "
               "finished_at=CASE WHEN ?='playing' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE id=?",
               (json.dumps(state), moves, status, points, status, attempt['id']))
    db.commit()


@games.post('/games/wumpus/action')
def wumpus_action():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    _expire(db, today)
    attempt = _wumpus_attempt(db, today)
    if attempt and attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    game = game_wumpus.daily_map(today)
    room = json.loads(attempt['state'])['room'] if attempt else game['placements'][0]
    action, rooms = _wumpus_request(room)
    if attempt is None:
        attempt = _create_wumpus(db, today, 'daily', game)
    _wumpus_action(db, attempt, action, rooms)
    return redirect(url_for('games.wumpus_page'))


@games.post('/games/wumpus/practice')
def wumpus_practice_start():
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    raw_seed = request.form.get('seed', '').strip()
    try:
        seed = int(raw_seed) if raw_seed else secrets.randbits(64)
        game = game_wumpus.map_for_seed(seed)
    except ValueError:
        abort(HTTPStatus.BAD_REQUEST)
    db, today = _context()
    attempt = _create_wumpus(db, today, 'practice', game)
    return redirect(url_for('games.wumpus_practice_page', attempt_id=attempt['id']))


@games.get('/games/wumpus/practice/<int:attempt_id>')
def wumpus_practice_page(attempt_id):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, today = _context()
    return _wumpus_render(db, today, _wumpus_practice(db, attempt_id), practice=True)


@games.post('/games/wumpus/practice/<int:attempt_id>/action')
def wumpus_practice_action(attempt_id):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    db, _ = _context()
    attempt = _wumpus_practice(db, attempt_id)
    if attempt['status'] != 'playing':
        abort(HTTPStatus.CONFLICT)
    action, rooms = _wumpus_request(json.loads(attempt['state'])['room'])
    _wumpus_action(db, attempt, action, rooms)
    return redirect(url_for('games.wumpus_practice_page', attempt_id=attempt_id))


@games.get('/games/<game>/archive/<day_text>')
def archive(game, day_text):
    unauthorized = _require_user()
    if unauthorized:
        return unauthorized
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        abort(HTTPStatus.NOT_FOUND)
    _, today = _context()
    if day.isoformat() != day_text or day >= today:
        abort(HTTPStatus.NOT_FOUND)
    titles = {'word': 'Word Garden', 'ladder': 'Word Ladder',
              'crossword': 'Mini Crossword', 'nonogram': 'Mini Nonogram',
              'sudoku': 'Mini Sudoku', 'wumpus': 'Hunt the Wumpus'}
    if game not in titles:
        abort(HTTPStatus.NOT_FOUND)
    if game == 'word':
        solution = daily_answer(day)
    elif game == 'ladder':
        start, target = game_ladder.daily_puzzle(day)
        solution = ' → '.join(game_ladder.shortest_path(start, target))
    elif game == 'wumpus':
        solution = f"The Wumpus was in room {game_wumpus.daily_map(day)['placements'][1]}."
    else:
        puzzle = game_grids.daily_puzzle(game, day)
        size = puzzle['size']
        solution = '\n'.join(puzzle['answer'][index:index + size]
                             for index in range(0, len(puzzle['answer']), size))
    return render_template('game_archive.html', title=titles[game], day=day,
                           solution=solution)
