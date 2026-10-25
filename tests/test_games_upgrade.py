"""Games schema upgrades must preserve the first Word Garden release."""

import sqlite3

from homehub.app import create_app
from homehub.constants import LATEST_SCHEMA_VERSION


def test_v16_word_attempt_survives_multigame_upgrade(tmp_path):
    path = tmp_path / 'hub.db'
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO users VALUES (1, 'alex', 'hash', 'user', 1, 0);
            CREATE TABLE game_attempts (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                game TEXT NOT NULL CHECK(game='word'),
                mode TEXT NOT NULL CHECK(mode IN ('daily','practice')),
                puzzle_date TEXT NOT NULL, answer TEXT NOT NULL,
                guesses TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'playing',
                points INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT
            );
            INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer,guesses,status,points)
            VALUES(1,'word','daily','2026-10-24','APPLE','["APPLE"]','won',100);
            PRAGMA user_version=16;
        ''')
    create_app({'TESTING': True, 'SECRET_KEY': 'games-test-secret', 'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT user_id,answer,guesses,points FROM game_attempts').fetchone() == (
            1, 'APPLE', '["APPLE"]', 100)
        db.execute("INSERT INTO game_attempts(user_id,game,mode,puzzle_date,answer) "
                   "VALUES(1,'ladder','daily','2026-10-25','WARM')")
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'hub.pre-v{LATEST_SCHEMA_VERSION}-*.db'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('PRAGMA user_version').fetchone()[0] == 16
        assert backup.execute('SELECT answer FROM game_attempts').fetchone()[0] == 'APPLE'
