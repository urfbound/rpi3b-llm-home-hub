"""Home Hub phase 1: accounts, sessions, recipes, lists, calendar and ICS exports."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, abort, flash, g, make_response, redirect, render_template, request, session, url_for, Response
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

PH = PasswordHasher(memory_cost=16384, time_cost=2, parallelism=1)
SCHEMA_VERSION = 2
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_USERNAME_LIMIT = 5
LOGIN_ADDRESS_LIMIT = 30
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','admin')),
 active INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS app_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
 id INTEGER PRIMARY KEY, username_key TEXT NOT NULL, address_key TEXT NOT NULL,
 attempted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_failures_username_time
 ON login_failures(username_key, attempted_at);
CREATE INDEX IF NOT EXISTS login_failures_address_time
 ON login_failures(address_key, attempted_at);
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

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user['role'] != 'admin':
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def setting(db, key, default):
    row = db.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
    return int(row['value']) if row else default

def temporary_password():
    return secrets.token_urlsafe(18)

def login_key(secret_key, purpose, value):
    """Derive a stable, keyed identifier without storing login data in plaintext."""
    secret = secret_key.encode('utf-8') if isinstance(secret_key, str) else secret_key
    message = f'{purpose}:{value}'.encode('utf-8')
    return hmac.new(secret, message, hashlib.sha256).hexdigest()

def temporary_password_page(username, password, change_required):
    response = make_response(render_template('temporary_password.html', username=username,
                                             password=password, change_required=change_required))
    response.headers['Cache-Control'] = 'no-store'
    return response

def initialize_database(db_path):
    """Upgrade the prototype schema without replacing household data."""
    existed = db_path.exists() and db_path.stat().st_size > 0
    with closing(sqlite3.connect(db_path, timeout=5)) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError('Database was created by a newer Home Hub version')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Database integrity check failed; upgrade cancelled')
        if version == SCHEMA_VERSION:
            return
        has_data = existed and db.execute(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%')").fetchone()[0]
        if has_data:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup_path = db_path.with_name(
                f'{db_path.stem}.pre-v{SCHEMA_VERSION}-{stamp}-{secrets.token_hex(3)}{db_path.suffix}')
            with closing(sqlite3.connect(backup_path)) as backup:
                db.backup(backup)
                if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Pre-upgrade backup failed verification')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
            for column, definition in (
                ('display_name', "TEXT NOT NULL DEFAULT ''"),
                ('must_change_password', 'INTEGER NOT NULL DEFAULT 0'),
                ('account_kind', "TEXT NOT NULL DEFAULT 'person'"),
            ):
                if column not in columns:
                    db.execute(f'ALTER TABLE users ADD COLUMN {column} {definition}')
            db.execute("UPDATE users SET display_name=username WHERE display_name=''")
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Database relationships failed verification')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            db.commit()
        except Exception:
            db.rollback()
            raise

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
    initialize_database(db_path)

    @app.before_request
    def load_context():
        g.app_db_path = str(db_path)
        g.user = None
        uid = session.get('uid')
        if uid is not None:
            user = get_db().execute('SELECT * FROM users WHERE id=? AND active=1', (uid,)).fetchone()
            now = int(time.time())
            idle = setting(get_db(), 'session_idle_hours', 24) * 3600
            absolute = setting(get_db(), 'session_absolute_days', 7) * 86400
            valid = (user and user['session_version'] == session.get('sv')
                     and now - session.get('last_seen', 0) <= idle
                     and now - session.get('signed_in', 0) <= absolute)
            if valid:
                g.user = user
                session['last_seen'] = now
            else:
                session.clear()
        if g.user and g.user['must_change_password'] and request.endpoint not in (
            'change_password', 'logout', 'static'):
            return redirect(url_for('change_password'))
        if g.user and g.user['account_kind'] == 'house' and request.method in (
            'POST', 'PUT', 'PATCH', 'DELETE') and request.endpoint not in ('logout',):
            abort(403)
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
            db = get_db()
            username = request.form.get('username', '').strip()
            username_key = login_key(app.config['SECRET_KEY'], 'username', username.casefold())
            address_key = login_key(app.config['SECRET_KEY'], 'address', request.remote_addr or 'unknown')
            now = int(time.time())
            cutoff = now - LOGIN_WINDOW_SECONDS
            db.execute('DELETE FROM login_failures WHERE attempted_at < ?', (cutoff,))
            username_failures = db.execute(
                'SELECT COUNT(*) FROM login_failures WHERE username_key=? AND attempted_at>=?',
                (username_key, cutoff)).fetchone()[0]
            address_failures = db.execute(
                'SELECT COUNT(*) FROM login_failures WHERE address_key=? AND attempted_at>=?',
                (address_key, cutoff)).fetchone()[0]
            if username_failures >= LOGIN_USERNAME_LIMIT or address_failures >= LOGIN_ADDRESS_LIMIT:
                db.commit()
                flash('Too many sign-in attempts. Wait a few minutes and try again.')
                return render_template('login.html'), 429

            user = db.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
            ok = False
            if user:
                try:
                    ok = PH.verify(user['password_hash'],request.form.get('password',''))
                except (VerifyMismatchError, VerificationError):
                    pass
            if ok:
                db.execute('DELETE FROM login_failures WHERE username_key=?', (username_key,))
                db.commit()
                session.clear()
                session['uid'],session['sv'] = user['id'],user['session_version']
                session['signed_in'] = session['last_seen'] = int(time.time())
                session['csrf_token'] = secrets.token_urlsafe(24)
                return redirect(url_for('change_password') if user['must_change_password'] else url_for('index'))
            db.execute('INSERT INTO login_failures(username_key,address_key,attempted_at) VALUES(?,?,?)',
                       (username_key, address_key, now))
            db.commit()
            flash('Incorrect username or password.')
        return render_template('login.html')

    @app.post('/logout')
    @login_required
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.route('/account/password', methods=['GET', 'POST'])
    @login_required
    def change_password():
        if request.method == 'POST':
            current = request.form.get('current_password', '')
            try:
                verified = PH.verify(g.user['password_hash'], current)
            except (VerifyMismatchError, VerificationError):
                verified = False
            new = request.form.get('new_password', '')
            if not verified:
                flash('Current password is incorrect.')
            elif len(new) < 12 or len(new) > 256:
                flash('Choose a password between 12 and 256 characters.')
            elif new != request.form.get('confirm_password', ''):
                flash('The new passwords do not match.')
            else:
                db = get_db()
                db.execute('UPDATE users SET password_hash=?, must_change_password=0, '
                           'session_version=session_version+1 WHERE id=?',
                           (PH.hash(new), g.user['id']))
                db.commit()
                session.clear()
                flash('Password changed. Please sign in again.')
                return redirect(url_for('login'))
        return render_template('change_password.html')

    @app.get('/admin/accounts')
    @admin_required
    def accounts():
        db = get_db()
        users = db.execute('SELECT * FROM users ORDER BY account_kind, username COLLATE NOCASE').fetchall()
        counts = {key: db.execute('SELECT COUNT(*) FROM users WHERE ' + where).fetchone()[0]
                  for key, where in (('users', '1=1'), ('admins', "role='admin'"),
                                     ('house', "account_kind='house'"))}
        ownership = {}
        for user in users:
            ownership[user['id']] = {table: db.execute(
                f'SELECT COUNT(*) FROM {table} WHERE created_by=?', (user['id'],)).fetchone()[0]
                for table in ('shopping_items', 'recipes', 'events')}
        return render_template('accounts.html', users=users, counts=counts,
                               ownership=ownership,
                               idle_hours=setting(db, 'session_idle_hours', 24),
                               absolute_days=setting(db, 'session_absolute_days', 7))

    @app.post('/admin/accounts')
    @admin_required
    def create_account():
        db = get_db()
        username = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        role = request.form.get('role', 'user')
        kind = request.form.get('account_kind', 'person')
        if (not 3 <= len(username) <= 32 or not username.replace('_', '').isalnum()
                or not 1 <= len(display_name) <= 80 or role not in ('user', 'admin')
                or kind not in ('person', 'house') or (kind == 'house' and role != 'user')):
            abort(400)
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= 32:
            abort(409, description='The household account limit has been reached.')
        if role == 'admin' and db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] >= 4:
            abort(409, description='The admin account limit has been reached.')
        if kind == 'house' and db.execute("SELECT COUNT(*) FROM users WHERE account_kind='house'").fetchone()[0]:
            abort(409, description='A house-wide account already exists.')
        password = temporary_password()
        try:
            db.execute('INSERT INTO users(username,display_name,password_hash,role,account_kind,must_change_password) '
                       'VALUES(?,?,?,?,?,?)', (username, display_name, PH.hash(password), role, kind,
                                             int(kind == 'person')))
            db.commit()
        except sqlite3.IntegrityError:
            abort(409, description='That username is already in use.')
        return temporary_password_page(username, password, kind == 'person')

    @app.post('/admin/accounts/<int:user_id>/reset-password')
    @admin_required
    def reset_password(user_id):
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            abort(404)
        password = temporary_password()
        db.execute('UPDATE users SET password_hash=?, must_change_password=?, '
                   'session_version=session_version+1 WHERE id=?',
                   (PH.hash(password), int(user['account_kind'] == 'person'), user_id))
        db.commit()
        if user_id == g.user['id']:
            session.clear()
        return temporary_password_page(user['username'], password,
                                       user['account_kind'] == 'person')

    @app.post('/admin/accounts/<int:user_id>/delete')
    @admin_required
    def delete_account(user_id):
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            abort(404)
        if user_id == g.user['id']:
            abort(400, description='You cannot remove your own account.')
        if user['role'] == 'admin' and db.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] <= 1:
            abort(409, description='The last admin cannot be removed.')
        if request.form.get('confirm_username') != user['username']:
            abort(400, description='Type the username to confirm removal.')
        choice = request.form.get('content_choice')
        if choice not in ('transfer', 'delete'):
            abort(400)
        if choice == 'transfer':
            try:
                target_id = int(request.form.get('transfer_to', ''))
            except ValueError:
                abort(400)
            target = db.execute('SELECT id FROM users WHERE id=? AND id!=? AND account_kind=?',
                                (target_id, user_id, 'person')).fetchone()
            if not target:
                abort(400)
        with db:
            if choice == 'transfer':
                for table in ('shopping_items', 'recipes', 'events'):
                    db.execute(f'UPDATE {table} SET created_by=? WHERE created_by=?', (target_id, user_id))
            else:
                db.execute('DELETE FROM events WHERE created_by=?', (user_id,))
                db.execute('DELETE FROM recipes WHERE created_by=?', (user_id,))
                db.execute('DELETE FROM shopping_items WHERE created_by=?', (user_id,))
            db.execute('DELETE FROM users WHERE id=?', (user_id,))
        flash(f'{user["username"]} was removed.')
        return redirect(url_for('accounts'))

    @app.post('/admin/session-settings')
    @admin_required
    def update_session_settings():
        try:
            idle = int(request.form.get('idle_hours', ''))
            absolute = int(request.form.get('absolute_days', ''))
        except ValueError:
            abort(400)
        if not 1 <= idle <= 168 or not 1 <= absolute <= 30:
            abort(400)
        db = get_db()
        with db:
            db.executemany('INSERT INTO app_settings(key,value) VALUES(?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (('session_idle_hours', str(idle)),
                            ('session_absolute_days', str(absolute))))
        flash('Session settings updated.')
        return redirect(url_for('accounts'))

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
        db.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
                   (username, username, PH.hash(password), 'admin'))
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
