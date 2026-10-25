import sqlite3

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'SECRET_KEY': 'rss-test',
                       'DATABASE': str(tmp_path / 'rss.sqlite3')})


def test_feed_registry_separates_personal_and_household_feeds(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(rss_feeds)')}

    assert {'id', 'normalized_url', 'title', 'scope', 'owner_user_id', 'allow_lan',
            'created_by', 'sort_order'} <= columns


def test_feed_registry_rejects_same_normalized_url_in_any_scope(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO users(username,display_name,password_hash,role) '
                   'VALUES(?,?,?,?)', ('reader', 'Reader', PH.hash('a strong password'), 'user'))
        reader_id = db.execute('SELECT id FROM users WHERE username=?', ('reader',)).fetchone()[0]
        db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
                   'VALUES(?,?,?,?,?)', ('https://news.example/feed', 'News', 'household', None, reader_id))

        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
                       'VALUES(?,?,?,?,?)', ('https://news.example/feed', 'News', 'personal', reader_id, reader_id))


def test_feed_registry_requires_personal_owner_and_forbids_household_owner(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT INTO users(username,display_name,password_hash,role) '
                   'VALUES(?,?,?,?)', ('reader', 'Reader', PH.hash('a strong password'), 'user'))
        reader_id = db.execute('SELECT id FROM users WHERE username=?', ('reader',)).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
                       'VALUES(?,?,?,?,?)', ('https://one.example/rss', 'One', 'personal', None, reader_id))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
                       'VALUES(?,?,?,?,?)', ('https://two.example/rss', 'Two', 'household', reader_id, reader_id))

