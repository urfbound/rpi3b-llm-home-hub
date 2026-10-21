"""Home Hub phase 1: accounts, sessions, recipes, lists, calendar and ICS exports."""
import os
import secrets
import sqlite3
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for, Response
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

PH = PasswordHasher(memory_cost=16384, time_cost=2, parallelism=1)
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','admin')),
 active INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recipes (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, ingredients TEXT NOT NULL DEFAULT '',
 instructions TEXT NOT NULL DEFAULT '', created_by INTEGER NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS shopping_items (
 id INTEGER PRIMARY KEY, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
 created_by INTEGER NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, event_date TEXT NOT NULL,
 recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
 created_by INTEGER NOT NULL REFERENCES users(id)
);
"""

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(g.app_db_path, timeout=5)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA busy_timeout=5000')
    return g.db

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.user:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

def safe_ics_text(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('HOMEHUB_SECRET_KEY'),
        DATABASE=os.environ.get('HOMEHUB_DB', str(Path.home()/'.local/share/homehub/homehub.sqlite3')),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        MAX_CONTENT_LENGTH=64*1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY']:
        raise RuntimeError('Set HOMEHUB_SECRET_KEY to a long, random persistent secret')
    db_path = Path(app.config['DATABASE'])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)

    @app.before_request
    def load_context():
        g.app_db_path = str(db_path)
        g.user = None
        uid = session.get('uid')
        if uid is not None:
            user = get_db().execute('SELECT * FROM users WHERE id=? AND active=1', (uid,)).fetchone()
            if user and user['session_version'] == session.get('sv'):
                g.user = user
            else:
                session.clear()
        if request.method in ('POST','PUT','PATCH','DELETE'):
            token = session.get('csrf_token')
            supplied = request.form.get('_csrf', '')
            if not token or not secrets.compare_digest(token, supplied):
                abort(400, description='Invalid CSRF token')

    @app.context_processor
    def inject_globals():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_urlsafe(24)
        return dict(current_user=g.get('user'), csrf_token=session['csrf_token'])

    @app.teardown_appcontext
    def close_db(_err):
        db = g.pop('db', None)
        if db is not None:
            db.close()

    @app.get('/')
    @login_required
    def index():
        db = get_db()
        return render_template('index.html', recipes=db.execute('SELECT * FROM recipes ORDER BY id DESC LIMIT 6').fetchall(),
          events=db.execute('SELECT events.*,recipes.title AS recipe_title FROM events LEFT JOIN recipes ON recipes.id=events.recipe_id ORDER BY event_date LIMIT 12').fetchall(),
          items=db.execute('SELECT * FROM shopping_items ORDER BY done,id DESC').fetchall(), today=date.today().isoformat())

    @app.route('/login', methods=['GET','POST'])
    def login():
        if request.method == 'POST':
            user = get_db().execute('SELECT * FROM users WHERE username=? AND active=1',(request.form.get('username',''),)).fetchone()
            ok = False
            if user:
                try:
                    ok = PH.verify(user['password_hash'],request.form.get('password',''))
                except (VerifyMismatchError, VerificationError):
                    pass
            if ok:
                session.clear()
                session['uid'],session['sv'] = user['id'],user['session_version']
                session['csrf_token'] = secrets.token_urlsafe(24)
                return redirect(url_for('index'))
            flash('Incorrect username or password.')
        return render_template('login.html')

    @app.post('/logout')
    @login_required
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.post('/shopping')
    @login_required
    def add_item():
        text = request.form.get('text','').strip()
        if text and len(text)<=240:
            db=get_db(); db.execute('INSERT INTO shopping_items(text,created_by) VALUES(?,?)',(text,g.user['id']));db.commit()
        return redirect(url_for('index')+'#shopping')

    @app.post('/shopping/<int:item_id>/toggle')
    @login_required
    def toggle_item(item_id):
        db=get_db();db.execute('UPDATE shopping_items SET done=1-done WHERE id=?',(item_id,));db.commit()
        return redirect(url_for('index')+'#shopping')

    @app.post('/shopping/clear')
    @login_required
    def clear_items():
        db=get_db();db.execute('DELETE FROM shopping_items');db.commit()
        return redirect(url_for('index')+'#shopping')

    @app.get('/shopping.txt')
    @login_required
    def shopping_text():
        items=get_db().execute('SELECT text FROM shopping_items WHERE done=0 ORDER BY id').fetchall()
        body='Shopping list\n\n'+'\n'.join('- '+i['text'] for i in items)+'\n'
        return Response(body,mimetype='text/plain',headers={'Content-Disposition':'attachment; filename="shopping.txt"'})

    @app.post('/recipes')
    @login_required
    def add_recipe():
        title=request.form.get('title','').strip()
        if title and len(title)<=160:
            db=get_db();db.execute('INSERT INTO recipes(title,ingredients,instructions,created_by) VALUES(?,?,?,?)',
              (title,request.form.get('ingredients','')[:8000],request.form.get('instructions','')[:12000],g.user['id']));db.commit()
        return redirect(url_for('index')+'#recipes')

    @app.get('/recipes/<int:recipe_id>')
    @login_required
    def recipe_detail(recipe_id):
        recipe=get_db().execute('SELECT * FROM recipes WHERE id=?',(recipe_id,)).fetchone()
        if not recipe: abort(404)
        return render_template('recipe.html', recipe=recipe)

    @app.post('/events')
    @login_required
    def add_event():
        title=request.form.get('title','').strip()
        event_date=request.form.get('event_date','')
        try: date.fromisoformat(event_date)
        except ValueError: abort(400)
        if title and len(title)<=160:
            db=get_db();db.execute('INSERT INTO events(title,event_date,created_by) VALUES(?,?,?)',(title,event_date,g.user['id']));db.commit()
        return redirect(url_for('index')+'#calendar')

    @app.get('/calendar.ics')
    @login_required
    def export_calendar():
        events=get_db().execute('SELECT * FROM events ORDER BY event_date').fetchall()
        lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//HomeHub//Local Calendar//EN','CALSCALE:GREGORIAN']
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        for ev in events:
            lines.extend(['BEGIN:VEVENT',f'UID:homehub-{ev["id"]}@local',f'DTSTAMP:{stamp}',
              f'DTSTART;VALUE=DATE:{ev["event_date"].replace("-","")}',f'SUMMARY:{safe_ics_text(ev["title"])}','END:VEVENT'])
        lines.append('END:VCALENDAR')
        return Response('\r\n'.join(lines)+'\r\n',mimetype='text/calendar',headers={'Content-Disposition':'attachment; filename="homehub.ics"'})

    @app.cli.command('create-admin')
    def create_admin():
        import getpass
        db=sqlite3.connect(db_path)
        username=input('Admin username: ').strip()
        password=getpass.getpass('Admin password (12+ chars): ')
        if not username or len(password)<12:
            raise SystemExit('Username required; password must be at least 12 characters')
        if db.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0]>=32:
            raise SystemExit('User cap reached')
        if db.execute("SELECT COUNT(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0]>=4:
            raise SystemExit('Admin cap reached')
        db.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(username,PH.hash(password),'admin'))
        db.commit();db.close()
        print('Administrator created.')

    return app

def main():
    from waitress import serve
    app=create_app()
    host=os.environ.get('HOMEHUB_BIND','127.0.0.1')
    serve(app,host=host,port=int(os.environ.get('HOMEHUB_PORT','8080')),threads=2)

if __name__=='__main__':
    main()
