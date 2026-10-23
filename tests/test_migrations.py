import sqlite3

import pytest

from homehub.app import PH, create_app


def config(path):
    return {'TESTING': True, 'SECRET_KEY': 'test-secret', 'DATABASE': str(path)}


def legacy_database(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE recipes (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                ingredients TEXT NOT NULL DEFAULT '', instructions TEXT NOT NULL DEFAULT '',
                created_by INTEGER NOT NULL REFERENCES users(id)
            );
            CREATE TABLE shopping_items (
                id INTEGER PRIMARY KEY, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL REFERENCES users(id)
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, event_date TEXT NOT NULL,
                recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
                created_by INTEGER NOT NULL REFERENCES users(id)
            );
        ''')
        db.execute('INSERT INTO users(id,username,password_hash,role) VALUES(?,?,?,?)',
                   (7, 'alex', PH.hash('correct horse battery'), 'admin'))
        db.execute('INSERT INTO recipes(id,title,created_by) VALUES(?,?,?)', (13, 'Soup', 7))
        db.execute('INSERT INTO shopping_items(id,text,done,created_by) VALUES(?,?,?,?)',
                   (17, 'Milk', 1, 7))
        db.execute('INSERT INTO events(id,title,event_date,recipe_id,created_by) '
                   'VALUES(?,?,?,?,?)', (23, 'Dinner', '2026-10-21', 13, 7))


def test_legacy_upgrade_preserves_data_and_creates_backup(tmp_path):
    path = tmp_path / 'hub.sqlite3'
    legacy_database(path)
    create_app(config(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        user = db.execute('SELECT id,username,display_name,password_hash FROM users').fetchone()
        assert user[:3] == (7, 'alex', 'alex')
        assert PH.verify(user[3], 'correct horse battery')
        assert db.execute('SELECT id,title,created_by FROM recipes').fetchall() == [(13, 'Soup', 7)]
        assert db.execute('SELECT id,text,done,created_by FROM shopping_items').fetchall() == [
            (17, 'Milk', 1, 7)]
        migrated_list_id = db.execute(
            'SELECT list_id FROM shopping_items WHERE id=17').fetchone()[0]
        assert db.execute('SELECT kind,title,category,visibility FROM household_lists '
                          'WHERE id=?', (migrated_list_id,)).fetchone() == (
                              'shopping', 'Groceries', 'Groceries', 'shared')
        assert db.execute('SELECT id,recipe_id,created_by FROM events').fetchall() == [(23, 13, 7)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('hub.pre-v9-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert db.execute('SELECT title FROM recipes WHERE id=13').fetchone()[0] == 'Soup'
    create_app(config(path))
    assert list(tmp_path.glob('hub.pre-v9-*.sqlite3')) == backups


def test_newer_database_is_not_changed(tmp_path):
    path = tmp_path / 'future.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE marker (value TEXT)')
        db.execute("INSERT INTO marker VALUES ('keep me')")
        db.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError, match='newer'):
        create_app(config(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 99
        assert db.execute('SELECT value FROM marker').fetchone()[0] == 'keep me'
    assert not list(tmp_path.glob('future.pre-v9-*.sqlite3'))


def test_failed_upgrade_rolls_back(tmp_path):
    path = tmp_path / 'broken.sqlite3'
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO recipes(id,title,created_by) VALUES(?,?,?)', (14, 'Orphan', 999))
    with pytest.raises(RuntimeError, match='relationships'):
        create_app(config(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert 'display_name' not in [row[1] for row in db.execute('PRAGMA table_info(users)')]
        assert db.execute('SELECT title FROM recipes WHERE id=14').fetchone()[0] == 'Orphan'
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chores'").fetchone() is None
    assert len(list(tmp_path.glob('broken.pre-v9-*.sqlite3'))) == 1


def test_version_one_upgrade_adds_login_limiter_without_losing_data(tmp_path):
    path = tmp_path / 'v1.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('alex','Alex',?,'admin')", (PH.hash('correct horse battery'),))
        db.execute("INSERT INTO recipes(title,created_by) VALUES('Soup',1)")
        db.execute('DROP TABLE login_failures')
        db.execute('PRAGMA user_version=1')

    create_app(config(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        assert db.execute('SELECT title FROM recipes').fetchall() == [('Soup',)]
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='login_failures'").fetchone() == ('login_failures',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('v1.pre-v9-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT title FROM recipes').fetchall() == [('Soup',)]


def test_version_two_upgrade_creates_lists_and_links_existing_items(tmp_path):
    path = tmp_path / 'v2.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('alex','Alex',?,'admin')", (PH.hash('correct horse battery'),))
        db.execute("INSERT INTO shopping_items(text,created_by) VALUES('Bread',1)")
        db.execute('DROP TABLE list_access')
        db.execute('DROP TABLE household_lists')
        db.execute('PRAGMA user_version=2')

    create_app(config(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        list_id = db.execute('SELECT list_id FROM shopping_items WHERE text="Bread"').fetchone()[0]
        assert db.execute('SELECT title,kind FROM household_lists WHERE id=?',
                          (list_id,)).fetchone() == ('Groceries', 'shopping')
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('v2.pre-v9-*.sqlite3'))
    assert len(backups) == 1


def test_version_three_upgrade_adds_chores_without_losing_list_data(tmp_path):
    path = tmp_path / 'v3.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role) "
                   "VALUES('alex','Alex',?,'admin')", (PH.hash('correct horse battery'),))
        db.execute("INSERT INTO shopping_items(text,created_by,list_id) "
                   "SELECT 'Milk',1,id FROM household_lists WHERE is_default=1")
        db.execute('DROP TABLE chore_completions')
        db.execute('DROP TABLE chores')
        db.execute('PRAGMA user_version=3')

    create_app(config(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'chores', 'chore_completions'} <= tables
        assert db.execute('SELECT text FROM shopping_items').fetchall() == [('Milk',)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('v3.pre-v9-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT text FROM shopping_items').fetchall() == [('Milk',)]
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chores'").fetchone() is None


def test_version_four_upgrade_preserves_legacy_recipe_text(tmp_path):
    path = tmp_path / 'v4.sqlite3'
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE recipes SET ingredients=?,instructions=? WHERE id=13",
                   ('2 cups flour\n1 tsp salt', 'Mix ingredients.\nBake until ready.'))
        db.execute('PRAGMA user_version=4')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        assert db.execute('SELECT title,ingredients,instructions FROM recipes WHERE id=13').fetchone() == (
            'Soup', '2 cups flour\n1 tsp salt', 'Mix ingredients.\nBake until ready.')
        assert {'recipe_ingredients', 'recipe_steps'} <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('v4.pre-v9-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
        assert db.execute('SELECT ingredients FROM recipes WHERE id=13').fetchone()[0] == '2 cups flour\n1 tsp salt'


def test_version_five_upgrade_adds_recipe_sharing_without_losing_existing_content(tmp_path):
    path = tmp_path / 'v5.sqlite3'
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE recipes SET ingredients=?,instructions=? WHERE id=13",
                   ('legacy ingredients', 'legacy method'))
        db.execute('PRAGMA user_version=5')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 9
        assert db.execute('SELECT title,ingredients,instructions,visibility FROM recipes WHERE id=13').fetchone() == (
            'Soup', 'legacy ingredients', 'legacy method', 'shared')
        assert {'recipe_access', 'recipe_notes'} <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob('v5.pre-v9-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 5
        assert db.execute('SELECT ingredients FROM recipes WHERE id=13').fetchone()[0] == 'legacy ingredients'
