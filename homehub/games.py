"""Small, offline household games. The first playable game is Word Garden."""

from collections import Counter
from datetime import date, timedelta
from http import HTTPStatus
import json
import re

from flask import Blueprint, abort, g, redirect, render_template, request, url_for


games = Blueprint('games', __name__)

WORD_BANK = (
    'APPLE', 'BLOOM', 'CLOUD', 'DREAM', 'EMBER', 'FLAME', 'GRAPE', 'HEART',
    'IVORY', 'JOLLY', 'KNIFE', 'LEMON', 'MAPLE', 'NOBLE', 'OCEAN', 'PEACH',
    'QUILT', 'RIVER', 'STONE', 'TABLE', 'UNITY', 'VIVID', 'WATER', 'YEAST',
    'ZEBRA', 'ACORN', 'BRAVE', 'CORAL', 'DANCE', 'EAGLE',
)
VALID_WORDS = frozenset(WORD_BANK) | frozenset({
    'CRANE', 'STARE', 'SLATE', 'TRACE', 'RAISE', 'ADIEU', 'ALLEY', 'PLANT',
    'HOUSE', 'MOUSE', 'TRAIN', 'SUGAR', 'LIGHT', 'NIGHT', 'BREAD', 'SHEEP',
})
GAME_NAMES = (
    ('Word Garden', 'Ready to play'),
    ('Mini Crossword', 'Coming soon'),
    ('Hunt the Wumpus', 'Coming soon'),
    ('Word Ladder', 'Coming soon'),
    ('Mini Nonogram', 'Coming soon'),
    ('Mini Sudoku', 'Coming soon'),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS game_attempts (
 id INTEGER PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 game TEXT NOT NULL CHECK(game='word'),
 mode TEXT NOT NULL CHECK(mode IN ('daily','practice')),
 puzzle_date TEXT NOT NULL,
 answer TEXT NOT NULL,
 guesses TEXT NOT NULL DEFAULT '[]',
 status TEXT NOT NULL DEFAULT 'playing' CHECK(status IN ('playing','won','lost','expired')),
 points INTEGER NOT NULL DEFAULT 0 CHECK(points BETWEEN 0 AND 100),
 started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS game_daily_once
 ON game_attempts(user_id,game,puzzle_date) WHERE mode='daily';
CREATE INDEX IF NOT EXISTS game_scores ON game_attempts(mode,puzzle_date,status);
"""


def daily_answer(day):
    return WORD_BANK[day.toordinal() % len(WORD_BANK)]


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


def _attempt(db, user_id, day):
    return db.execute("SELECT * FROM game_attempts WHERE user_id=? AND game='word' "
                      "AND mode='daily' AND puzzle_date=?", (user_id, day.isoformat())).fetchone()


def _view(attempt):
    if attempt is None:
        return None
    guesses = json.loads(attempt['guesses'])
    return {'row': attempt, 'guesses': [(guess, mark_guess(guess, attempt['answer']))
                                       for guess in guesses]}


def _scoreboard(db, today=None):
    day_filter = 'AND a.puzzle_date=?' if today else ''
    return db.execute(f"""
        SELECT u.display_name, SUM(a.points) AS points, COUNT(*) AS wins
        FROM game_attempts a JOIN users u ON u.id=a.user_id
        WHERE a.mode='daily' AND a.status='won' AND u.account_kind='person'
          AND u.active=1 {day_filter}
        GROUP BY u.id ORDER BY points DESC, wins DESC, u.display_name COLLATE NOCASE
    """, (today.isoformat(),) if today else ()).fetchall()


def _streak(db, user_id, today):
    days = [date.fromisoformat(row['puzzle_date']) for row in db.execute(
        "SELECT puzzle_date FROM game_attempts WHERE user_id=? AND game='word' "
        "AND mode='daily' AND status='won' ORDER BY puzzle_date DESC", (user_id,))]
    if not days or days[0] < today - timedelta(days=1):
        return 0
    expected = days[0]
    streak = 0
    for day in days:
        if day != expected:
            break
        streak += 1
        expected -= timedelta(days=1)
    return streak


def _render(db, today, attempt=None):
    if attempt is None:
        attempt = _attempt(db, g.user['id'], today)
    return render_template('games.html', games=GAME_NAMES, day=today,
                           attempt=_view(attempt), scores=_scoreboard(db, today),
                           all_scores=_scoreboard(db), streak=_streak(db, g.user['id'], today))


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
    points = max(1, 100 - 15 * (len(guesses) - 1)) if won and attempt['mode'] == 'daily' else 0
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
    answer = daily_answer(today + timedelta(days=1))
    cursor = db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer) "
                        "VALUES(?,'word','practice',?,?)", (g.user['id'], today.isoformat(), answer))
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
