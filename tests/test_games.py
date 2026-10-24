"""The first Games slice: a private, resumable daily word puzzle."""

import sqlite3
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
