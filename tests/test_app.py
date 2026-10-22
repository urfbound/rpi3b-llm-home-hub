import pytest
from homehub.app import create_app, PH

@pytest.fixture
def client(tmp_path):
    app=create_app({'TESTING':True,'SECRET_KEY':'test-only-secret','DATABASE':str(tmp_path/'homehub.db')})
    with app.app_context():
        from flask import g
        g.app_db_path=app.config['DATABASE']
        from homehub.app import get_db
        db=get_db()
        db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",('alice',PH.hash('a long test password'),'admin'))
        db.commit()
    return app.test_client()

def sign_in(client):
    client.get('/login')
    with client.session_transaction() as sess: token=sess['csrf_token']
    response=client.post('/login',data={'username':'alice','password':'a long test password','_csrf':token})
    assert response.status_code==302

def test_requires_login(client):
    assert client.get('/').status_code==302

def test_login_and_ics(client):
    sign_in(client)
    assert b'A little home for everything' in client.get('/').data
    with client.session_transaction() as sess: token=sess['csrf_token']
    client.post('/events',data={'title':'Pasta night','event_date':'2026-10-21','_csrf':token})
    calendar=client.get('/calendar.ics')
    assert calendar.status_code==200
    assert b'SUMMARY:Pasta night' in calendar.data
    assert b'DTSTART;VALUE=DATE:20261021' in calendar.data

def test_csrf_required(client):
    sign_in(client)
    assert client.post('/shopping',data={'text':'milk'}).status_code==400

def test_secure_cookie_can_be_enabled_for_https_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv('HOMEHUB_COOKIE_SECURE', 'true')
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret',
                      'DATABASE': str(tmp_path / 'secure-cookie.db')})
    assert app.config['SESSION_COOKIE_SECURE'] is True
    response = app.test_client().get('/login')
    set_cookie = response.headers['Set-Cookie']
    assert 'Secure' in set_cookie
    assert 'HttpOnly' in set_cookie
    assert 'SameSite=Lax' in set_cookie

def test_secure_cookie_stays_off_for_local_http_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv('HOMEHUB_COOKIE_SECURE', raising=False)
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only-secret',
                      'DATABASE': str(tmp_path / 'http-cookie.db')})
    assert app.config['SESSION_COOKIE_SECURE'] is False

def test_shopping(client):
    sign_in(client)
    with client.session_transaction() as sess: token=sess['csrf_token']
    client.post('/shopping',data={'text':'Milk','_csrf':token})
    assert b'- Milk' in client.get('/shopping.txt').data
