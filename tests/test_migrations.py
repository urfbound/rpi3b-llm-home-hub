import sqlite3

import pytest

from homehub.constants import LATEST_SCHEMA_VERSION
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
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
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
    backups = list(tmp_path.glob(f'hub.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert db.execute('SELECT title FROM recipes WHERE id=13').fetchone()[0] == 'Soup'
    create_app(config(path))
    assert list(tmp_path.glob(f'hub.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3')) == backups


def test_newer_database_is_not_changed(tmp_path):
    path = tmp_path / 'future.sqlite3'
    future_schema_version = LATEST_SCHEMA_VERSION + 1
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE marker (value TEXT)')
        db.execute("INSERT INTO marker VALUES ('keep me')")
        db.execute(f'PRAGMA user_version={future_schema_version}')
    with pytest.raises(RuntimeError, match='newer'):
        create_app(config(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == future_schema_version
        assert db.execute('SELECT value FROM marker').fetchone()[0] == 'keep me'
    assert not list(tmp_path.glob(f'future.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))


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
    assert len(list(tmp_path.glob(f'broken.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))) == 1


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
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title FROM recipes').fetchall() == [('Soup',)]
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='login_failures'").fetchone() == ('login_failures',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v1.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
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
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        list_id = db.execute('SELECT list_id FROM shopping_items WHERE text="Bread"').fetchone()[0]
        assert db.execute('SELECT title,kind FROM household_lists WHERE id=?',
                          (list_id,)).fetchone() == ('Groceries', 'shopping')
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v2.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
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
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'chores', 'chore_completions'} <= tables
        assert db.execute('SELECT text FROM shopping_items').fetchall() == [('Milk',)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v3.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
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
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title,ingredients,instructions FROM recipes WHERE id=13').fetchone() == (
            'Soup', '2 cups flour\n1 tsp salt', 'Mix ingredients.\nBake until ready.')
        assert {'recipe_ingredients', 'recipe_steps'} <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v4.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
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
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title,ingredients,instructions,visibility FROM recipes WHERE id=13').fetchone() == (
            'Soup', 'legacy ingredients', 'legacy method', 'shared')
        assert {'recipe_access', 'recipe_notes'} <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v5.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 5
        assert db.execute('SELECT ingredients FROM recipes WHERE id=13').fetchone()[0] == 'legacy ingredients'


def test_v10_upgrade_creates_weather_cache_and_preserves_events(tmp_path):
    path = tmp_path / 'v10.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE weather_cache')
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('weather-test','hash','user')")
        db.execute("INSERT INTO events(title,event_date,created_by) VALUES('Dinner','2026-10-24',1)")
        db.execute('PRAGMA user_version=10')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title FROM events').fetchone() == ('Dinner',)
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='weather_cache'"
        ).fetchone() == ('weather_cache',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v10.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 10
        assert db.execute('SELECT title FROM events').fetchone() == ('Dinner',)


def test_v11_upgrade_adds_system_metrics_without_losing_household_data(tmp_path):
    path = tmp_path / 'v11.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE system_metrics')
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('system-test','hash','user')")
        db.execute("INSERT INTO events(title,event_date,created_by) VALUES('Dinner','2026-10-24',1)")
        db.execute('PRAGMA user_version=11')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title FROM events').fetchone() == ('Dinner',)
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_metrics'"
        ).fetchone() == ('system_metrics',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v11.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 11
        assert db.execute('SELECT title FROM events').fetchone() == ('Dinner',)


def test_v12_upgrade_adds_rss_feed_registry_and_preserves_household_data(tmp_path):
    path = tmp_path / 'v12.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE rss_feeds')
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('rss-test','hash','user')")
        db.execute("INSERT INTO events(title,event_date,created_by) VALUES('Dinner','2026-10-24',1)")
        db.execute('PRAGMA user_version=12')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title FROM events').fetchone() == ('Dinner',)
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rss_feeds'"
        ).fetchone() == ('rss_feeds',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v12.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 12
        assert db.execute('SELECT title FROM events').fetchone()[0] == 'Dinner'


def test_v13_upgrade_adds_explicit_rss_lan_approval_and_preserves_feed_data(tmp_path):
    path = tmp_path / 'v13.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE rss_feeds DROP COLUMN allow_lan')
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('feed-admin','hash','admin')")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,created_by) "
                   "VALUES('https://example.org/rss','Example','household',1)")
        db.execute('PRAGMA user_version=13')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT normalized_url,allow_lan FROM rss_feeds').fetchone() == (
            'https://example.org/rss', 0)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v13.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 13
        assert db.execute('SELECT normalized_url FROM rss_feeds').fetchone()[0] == (
            'https://example.org/rss')


def test_v14_upgrade_adds_calendar_archive_metadata_without_losing_events(tmp_path):
    path = tmp_path / 'v14.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('calendar-admin','hash','admin')")
        db.execute("INSERT INTO events(title,event_date,created_by) "
                   "VALUES('Old dinner','2024-06-12',1)")
        db.execute('PRAGMA user_version=14')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title,event_date FROM events').fetchone() == (
            'Old dinner', '2024-06-12')
        columns = {row[1] for row in db.execute('PRAGMA table_info(calendar_archives)')}
        assert {'year', 'file_name', 'event_count', 'archived_at', 'size_bytes'} <= columns
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='calendar_archive_failures'").fetchone() == (
                              'calendar_archive_failures',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    backups = list(tmp_path.glob(f'v14.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1


def test_v17_upgrade_adds_rss_article_storage_without_losing_feeds(tmp_path):
    path = tmp_path / 'v17.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('rss-reader','hash','user')")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
                   "VALUES('https://feed.example/rss','Example','personal',1,1)")
        db.execute('PRAGMA user_version=17')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT normalized_url,title FROM rss_feeds').fetchone() == (
            'https://feed.example/rss', 'Example')
        assert {'id', 'feed_id', 'article_key', 'title', 'url', 'summary',
                'published', 'first_seen_at', 'content_bytes'} <= {
                    row[1] for row in db.execute('PRAGMA table_info(rss_articles)')}
        assert {'article_id', 'user_id', 'is_read', 'read_at', 'is_favorite',
                'deleted_at'} <= {
                    row[1] for row in db.execute('PRAGMA table_info(rss_article_states)')}
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_v18_upgrade_adds_rss_refresh_status_without_losing_articles(tmp_path):
    path = tmp_path / 'v18.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('rss-reader','hash','user')")
        db.execute("INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) "
                   "VALUES('https://feed.example/rss','Example','personal',1,1)")
        db.execute("INSERT INTO rss_articles(feed_id,article_key,title,summary,published,first_seen_at,content_bytes) "
                   "VALUES(1,?,'Story','','','2026-10-24T12:00:00+00:00',5)", ('a' * 64,))
        db.execute('PRAGMA user_version=18')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT title FROM rss_articles').fetchone() == ('Story',)
        assert {'feed_id', 'attempted_at', 'succeeded_at', 'last_error',
                'articles_added'} <= {
                    row[1] for row in db.execute('PRAGMA table_info(rss_feed_refresh)')}
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_v19_upgrade_adds_rss_user_preferences(tmp_path):
    path = tmp_path / 'v19.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('rss-reader','hash','user')")
        db.execute('PRAGMA user_version=19')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        columns = {row[1] for row in db.execute('PRAGMA table_info(rss_user_preferences)')}
        assert {'user_id', 'ask_before_done_delete'} <= columns
        db.execute('INSERT INTO rss_user_preferences(user_id) VALUES(1)')
        assert db.execute('SELECT ask_before_done_delete FROM rss_user_preferences').fetchone() == (1,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_v15_upgrade_adds_chat_storage_without_losing_household_data(tmp_path):
    path = tmp_path / 'v15.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('alex','hash','user')")
        db.execute("INSERT INTO events(title,event_date,created_by) "
                   "VALUES('Dinner','2026-10-24',1)")
        db.execute('DROP TABLE chat_pairs')
        db.execute('DROP TABLE chat_conversations')
        db.execute('PRAGMA user_version=15')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert {'id', 'owner_user_id', 'title', 'is_pinned', 'created_at', 'updated_at'} <= {
            row[1] for row in db.execute('PRAGMA table_info(chat_conversations)')}
        assert {'id', 'conversation_id', 'position', 'prompt', 'response', 'model',
                'created_at'} <= {
                    row[1] for row in db.execute('PRAGMA table_info(chat_pairs)')}
        assert db.execute('SELECT title FROM events').fetchone() == ('Dinner',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []

    backups = list(tmp_path.glob(f'v15.pre-v{str(LATEST_SCHEMA_VERSION)}-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 15
        assert db.execute('SELECT title FROM events').fetchone() == ('Dinner',)
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='chat_conversations'").fetchone() is None


def test_v20_upgrade_adds_contacts_and_sticky_notes_without_losing_users(tmp_path):
    path = tmp_path / 'v20.sqlite3'
    create_app(config(path))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users(username,password_hash,role) VALUES('owner','hash','user')")
        db.execute('PRAGMA user_version=20')

    create_app(config(path))

    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == LATEST_SCHEMA_VERSION
        assert db.execute('SELECT username FROM users').fetchone() == ('owner',)
        assert {'owner_user_id', 'name', 'home_address', 'visibility'} <= {
            row[1] for row in db.execute('PRAGMA table_info(contacts)')}
        assert {'author_user_id', 'text', 'color', 'everyone', 'expires_at'} <= {
            row[1] for row in db.execute('PRAGMA table_info(sticky_notes)')}
        assert {'note_id', 'user_id'} <= {
            row[1] for row in db.execute('PRAGMA table_info(sticky_note_recipients)')}
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
