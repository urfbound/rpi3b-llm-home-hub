from datetime import datetime, timezone
from http import HTTPStatus

import pytest

from homehub.app import PH, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'contacts-sticky-test-secret',
        'DATABASE': str(tmp_path / 'contacts-sticky.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
            [('alex', 'Alex', PH.hash('alex contacts password'), 'admin'),
             ('bob', 'Bob', PH.hash('bob contacts password'), 'user'),
             ('carol', 'Carol', PH.hash('carol contacts password'), 'user')],
        )
        db.commit()
    return app


def sign_in(app, username):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    response = client.post('/login', data={
        'username': username, 'password': f'{username} contacts password',
        '_csrf': csrf,
    })
    assert response.status_code == HTTPStatus.FOUND
    return client


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def test_contacts_are_owner_managed_and_shared_contacts_are_read_only(app):
    alex = sign_in(app, 'alex')
    created = alex.post('/contacts', data={
        '_csrf': csrf(alex), 'name': 'Grandma', 'phone': '555-0100',
        'email': 'grandma@example.test', 'organization': 'Family',
        'home_address': '12 Maple Street', 'visibility': 'shared',
    })
    assert created.status_code == HTTPStatus.FOUND

    bob = sign_in(app, 'bob')
    page = bob.get('/')
    assert b'Grandma' in page.data
    assert b'12 Maple Street' in page.data
    assert bob.post('/contacts/1/delete', data={'_csrf': csrf(bob)}).status_code == HTTPStatus.FORBIDDEN

    own_private = bob.post('/contacts', data={
        '_csrf': csrf(bob), 'name': 'Private contact', 'visibility': 'private',
    })
    assert own_private.status_code == HTTPStatus.FOUND
    assert b'Private contact' in bob.get('/').data
    assert b'Private contact' not in alex.get('/').data


def test_contact_owner_can_edit_contact_and_visibility(app):
    alex = sign_in(app, 'alex')
    alex.post('/contacts', data={
        '_csrf': csrf(alex), 'name': 'Old name', 'visibility': 'private',
    })
    updated = alex.post('/contacts/1', data={
        '_csrf': csrf(alex), 'name': 'New name', 'home_address': '8 Cedar Lane',
        'visibility': 'shared',
    })
    assert updated.status_code == HTTPStatus.FOUND
    assert b'New name' in sign_in(app, 'bob').get('/').data
    assert b'8 Cedar Lane' in sign_in(app, 'bob').get('/').data


def test_home_contact_duplicate_confirmation_uses_a_styled_dialog(app):
    alex = sign_in(app, 'alex')
    page = alex.get('/').data
    assert b'id="duplicate-contact-dialog"' in page
    assert b'window.confirm' not in page


def test_contact_limit_and_input_validation_are_enforced(app):
    alex = sign_in(app, 'alex')
    bad = alex.post('/contacts', data={
        '_csrf': csrf(alex), 'name': 'x' * 129, 'visibility': 'private',
    })
    assert bad.status_code == HTTPStatus.BAD_REQUEST

    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO contacts(owner_user_id,name,visibility) VALUES(1,?,?)',
            [(f'Contact {number}', 'private') for number in range(128)],
        )
        db.commit()
    full = alex.post('/contacts', data={
        '_csrf': csrf(alex), 'name': 'One too many', 'visibility': 'private',
    })
    assert full.status_code == HTTPStatus.CONFLICT


def test_sticky_notes_are_recipient_private_and_author_deletable(app):
    alex = sign_in(app, 'alex')
    created = alex.post('/sticky-notes', data={
        '_csrf': csrf(alex), 'text': 'Welcome home', 'color': 'yellow',
        'recipient_ids': '2', 'expires_in_days': '7',
    })
    assert created.status_code == HTTPStatus.FOUND
    note_url = '/sticky-notes/1/delete'

    bob = sign_in(app, 'bob')
    assert b'Welcome home' in bob.get('/sticky-notes').data
    assert bob.post(note_url, data={'_csrf': csrf(bob)}).status_code == HTTPStatus.FORBIDDEN
    carol = sign_in(app, 'carol')
    assert b'Welcome home' not in carol.get('/sticky-notes').data
    assert b'Welcome home' in alex.get('/sticky-notes').data

    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        note = get_db().execute(
            'SELECT expires_at FROM sticky_notes WHERE id=1'
        ).fetchone()
        assert note['expires_at'] is not None
        assert datetime.fromisoformat(note['expires_at']).tzinfo == timezone.utc

def test_sticky_note_without_recipients_defaults_to_author_only(app):
    alex = sign_in(app, 'alex')
    created = alex.post('/sticky-notes', data={
        '_csrf': csrf(alex), 'text': 'Just for me', 'color': 'blue',
    })
    assert created.status_code == HTTPStatus.FOUND
    assert b'Just for me' in alex.get('/sticky-notes').data
    assert b'Just for me' not in sign_in(app, 'bob').get('/sticky-notes').data

def test_sticky_note_sharing_rejects_inactive_or_unknown_recipients(app):
    alex = sign_in(app, 'alex')
    response = alex.post('/sticky-notes', data={
        '_csrf': csrf(alex), 'text': 'Not authorized recipient', 'color': 'blue',
        'recipient_ids': '999',
    })
    assert response.status_code == HTTPStatus.BAD_REQUEST

def test_sticky_notes_offer_owner_edit_copy_and_delete_confirmation(app):
    alex = sign_in(app, 'alex')
    alex.post('/sticky-notes', data={
        '_csrf': csrf(alex), 'text': 'Editable note', 'color': 'blue',
        'everyone': '1',
    })
    page = alex.get('/sticky-notes')
    assert b'Edit sticky note' in page.data
    assert b'Copy to clipboard' in page.data
    assert b'confirm(' in page.data
    assert b'.sticky-card footer{color:#30302b}' in page.data

def test_only_sticky_note_owner_can_edit(app):
    alex = sign_in(app, 'alex')
    alex.post('/sticky-notes', data={
        '_csrf': csrf(alex), 'text': 'Original', 'color': 'blue', 'everyone': '1',
    })
    bob = sign_in(app, 'bob')
    denied = bob.post('/sticky-notes/1/edit', data={
        '_csrf': csrf(bob), 'text': 'Changed', 'color': 'pink',
    })
    assert denied.status_code == HTTPStatus.FORBIDDEN
    saved = alex.post('/sticky-notes/1/edit', data={
        '_csrf': csrf(alex), 'text': 'Changed', 'color': 'pink',
    })
    assert saved.status_code == HTTPStatus.FOUND
    assert b'Changed' in alex.get('/sticky-notes').data


def test_sticky_note_validation_rejects_bad_length_color_and_expiry(app):
    alex = sign_in(app, 'alex')
    for values in (
        {'text': 'x' * 129, 'color': 'yellow', 'expires_in_days': ''},
        {'text': 'hello', 'color': 'ultraviolet', 'expires_in_days': ''},
        {'text': 'hello', 'color': 'yellow', 'expires_in_days': '366'},
    ):
        response = alex.post('/sticky-notes', data={'_csrf': csrf(alex), **values})
        assert response.status_code == HTTPStatus.BAD_REQUEST


def test_expired_notes_do_not_consume_active_note_limit(app):
    alex = sign_in(app, 'alex')
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.execute(
            "INSERT INTO sticky_notes(author_user_id,text,color,everyone,created_at,expires_at) "
            "VALUES(1,'Expired','yellow',1,'2020-01-01T00:00:00+00:00','2020-01-02T00:00:00+00:00')"
        )
        db.executemany(
            "INSERT INTO sticky_notes(author_user_id,text,color,everyone,created_at) "
            "VALUES(1,?,'yellow',1,'2020-01-01T00:00:00+00:00')",
            [(f'Active {number}',) for number in range(31)],
        )
        db.commit()
    response = alex.post('/sticky-notes', data={
        '_csrf': csrf(alex), 'text': 'New note', 'color': 'yellow', 'everyone': '1',
    })
    assert response.status_code == HTTPStatus.FOUND


def test_sticky_note_limit_counts_only_active_notes(app):
    alex = sign_in(app, 'alex')
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            "INSERT INTO sticky_notes(author_user_id,text,color,everyone,created_at) "
            "VALUES(1,?,'yellow',1,'2020-01-01T00:00:00+00:00')",
            [(f'Active {number}',) for number in range(32)],
        )
        db.commit()
    response = alex.post('/sticky-notes', data={
        '_csrf': csrf(alex), 'text': 'Too many', 'color': 'yellow', 'everyone': '1',
    })
    assert response.status_code == HTTPStatus.CONFLICT
