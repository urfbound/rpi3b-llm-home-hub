from http import HTTPStatus
import sqlite3

from homehub.app import PH, create_app
import pytest


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'rss-state-test',
                      'DATABASE': str(tmp_path / 'rss-state.sqlite3')})
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute("INSERT OR REPLACE INTO app_settings(key,value) "
                   "VALUES('rss_outbound_enabled','0')")
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin password'), 'admin'),
             ('reader', 'Reader', PH.hash('reader password'), 'user'),
             ('other', 'Other', PH.hash('other password'), 'user')],
        )
        reader_id = db.execute("SELECT id FROM users WHERE username='reader'").fetchone()[0]
        other_id = db.execute("SELECT id FROM users WHERE username='other'").fetchone()[0]
        db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,owner_user_id,created_by) '
                   'VALUES(?,?,?,?,?)',
                   ('https://reader.example/feed', 'Reader feed', 'personal', reader_id, reader_id))
        db.execute('INSERT INTO rss_feeds(normalized_url,title,scope,created_by) '
                   'VALUES(?,?,?,?)',
                   ('https://house.example/feed', 'House feed', 'household', other_id))
        db.execute('INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
                   'SELECT id,?,?,?,?,? FROM rss_feeds WHERE scope=?',
                   ('Personal story', 'https://reader.example/story', 'Personal summary', '',
                    '2026-10-23T00:00:00+00:00', 'personal'))
        db.execute('INSERT INTO rss_articles(feed_id,title,url,summary,published,fetched_at) '
                   'SELECT id,?,?,?,?,? FROM rss_feeds WHERE scope=?',
                   ('House story', 'https://house.example/story', 'House summary', '',
                    '2026-10-23T00:00:00+00:00', 'household'))
    return app


def sign_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    assert client.post('/login', data={'username': username, 'password': password,
                                       '_csrf': token}).status_code == HTTPStatus.FOUND
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def article_id(app, title):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return db.execute('SELECT id FROM rss_articles WHERE title=?', (title,)).fetchone()[0]


def state(app, username, title):
    with sqlite3.connect(app.config['DATABASE']) as db:
        row = db.execute(
            'SELECT s.read_at,s.deleted_at FROM rss_article_states s '
            'JOIN users u ON u.id=s.user_id JOIN rss_articles a ON a.id=s.article_id '
            'WHERE u.username=? AND a.title=?', (username, title),
        ).fetchone()
        return row


def test_opening_article_marks_it_read_for_only_that_user_and_redirects(app):
    reader = sign_in(app, 'reader', 'reader password')
    other = sign_in(app, 'other', 'other password')
    house_story = article_id(app, 'House story')

    opened = reader.get(f'/rss/articles/{house_story}/open')
    other_page = other.get('/rss')

    assert opened.status_code == HTTPStatus.FOUND
    assert opened.headers['Location'] == 'https://house.example/story'
    assert state(app, 'reader', 'House story')[0] is not None
    assert state(app, 'other', 'House story') is None
    assert b'class="rss-article-row unread"' in other_page.data


def test_users_cannot_open_articles_from_another_users_personal_feed(app):
    other = sign_in(app, 'other', 'other password')
    personal_story = article_id(app, 'Personal story')

    response = other.get(f'/rss/articles/{personal_story}/open')

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert state(app, 'other', 'Personal story') is None


def test_done_reading_marks_read_and_deletes_only_when_chosen(app):
    reader = sign_in(app, 'reader', 'reader password')
    story = article_id(app, 'Personal story')

    page = reader.get('/rss')
    response = reader.post(f'/rss/articles/{story}/done', data={
        '_csrf': csrf(reader), 'choice': 'keep',
    })

    assert f'action="/rss/articles/{story}/done"'.encode() in page.data
    assert response.status_code == HTTPStatus.FOUND
    assert state(app, 'reader', 'Personal story')[0] is not None
    assert state(app, 'reader', 'Personal story')[1] is None
    assert f'action="/rss/articles/{story}/done"'.encode() not in reader.get('/rss').data

    unread_story = article_id(app, 'House story')
    deleted = reader.post(f'/rss/articles/{unread_story}/done', data={
        '_csrf': csrf(reader), 'choice': 'delete',
    })
    assert deleted.status_code == HTTPStatus.FOUND
    assert state(app, 'reader', 'House story')[0] is not None
    assert state(app, 'reader', 'House story')[1] is not None
    assert b'House story' not in reader.get('/rss').data


def test_done_prompt_can_be_suppressed_by_repeating_the_selected_action(app):
    reader = sign_in(app, 'reader', 'reader password')
    personal_id = article_id(app, 'Personal story')
    house_id = article_id(app, 'House story')

    first = reader.post(f'/rss/articles/{personal_id}/done', data={
        '_csrf': csrf(reader), 'choice': 'delete', 'dont_ask_again': '1',
    })
    second = reader.post(f'/rss/articles/{house_id}/done', data={
        '_csrf': csrf(reader),
    })

    assert first.status_code == HTTPStatus.FOUND
    assert second.status_code == HTTPStatus.FOUND
    assert state(app, 'reader', 'Personal story')[1] is not None
    assert state(app, 'reader', 'House story')[1] is not None
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT prompt_after_done,done_without_prompt '
                          'FROM rss_user_preferences WHERE user_id=(SELECT id FROM users '
                          "WHERE username='reader')").fetchone() == (0, 'delete')
    assert b'name="prompt_after_done" value="1" checked' not in reader.get('/rss').data

    restored = reader.post('/rss/preferences', data={
        '_csrf': csrf(reader), 'prompt_after_done': '1',
    })
    assert restored.status_code == HTTPStatus.FOUND
    assert b'name="prompt_after_done" value="1" checked' in reader.get('/rss').data


def test_done_reading_prompt_is_styled_and_keeps_article_by_default(app):
    reader = sign_in(app, 'reader', 'reader password')

    page = reader.get('/rss')

    assert b'<dialog' in page.data
    assert b"Don't ask me again" in page.data
    assert b'Keep article' in page.data
    assert b'Delete article' in page.data
    assert b'name="choice" value="keep"' in page.data

