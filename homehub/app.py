"""Home Hub phase 1: accounts, sessions, recipes, lists, calendar and ICS exports."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from flask import (Flask, Response, abort, flash, g, make_response, redirect,
                   render_template, request, send_file, session, url_for)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from .backups import (BackupError, backup_filename, create_backup,
                      default_backup_directory, delete_backup, list_backups,
                      prune_backups, restore_backup, validate_backup_directory)

PH = PasswordHasher(memory_cost=16384, time_cost=2, parallelism=1)
SCHEMA_VERSION = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_USERNAME_LIMIT = 5
LOGIN_ADDRESS_LIMIT = 30
SHOPPING_CATEGORIES = (
    'Groceries', 'Gifts', 'Household', 'Health & Personal Care', 'Pets',
    'Home & Garden', 'School & Activities', 'Other',
)
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
CREATE TABLE IF NOT EXISTS recipe_ingredients (
 id INTEGER PRIMARY KEY, recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, quantity TEXT NOT NULL DEFAULT '' CHECK(length(quantity)<=32),
 unit TEXT NOT NULL DEFAULT '' CHECK(length(unit)<=16),
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 128),
 note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=128), UNIQUE(recipe_id,position)
);
CREATE TABLE IF NOT EXISTS recipe_steps (
 id INTEGER PRIMARY KEY, recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, text TEXT NOT NULL CHECK(length(trim(text)) BETWEEN 1 AND 256),
 UNIQUE(recipe_id,position)
);
CREATE INDEX IF NOT EXISTS recipes_owner ON recipes(created_by);
CREATE TABLE IF NOT EXISTS shopping_items (
 id INTEGER PRIMARY KEY, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
 created_by INTEGER NOT NULL REFERENCES users(id),
 list_id INTEGER REFERENCES household_lists(id) ON DELETE CASCADE,
 due_date TEXT, assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 updated_at TEXT
);
CREATE TABLE IF NOT EXISTS household_lists (
 id INTEGER PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('shopping','todo')),
 title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 80),
 category TEXT NOT NULL DEFAULT 'Other',
 visibility TEXT NOT NULL DEFAULT 'shared' CHECK(visibility IN ('shared','private')),
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS household_lists_one_shopping_default
 ON household_lists(kind) WHERE kind='shopping' AND is_default=1;
CREATE TABLE IF NOT EXISTS list_access (
 list_id INTEGER NOT NULL REFERENCES household_lists(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(list_id,user_id)
);
CREATE INDEX IF NOT EXISTS list_access_user ON list_access(user_id,list_id);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, event_date TEXT NOT NULL,
 recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
 created_by INTEGER NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS chores (
 id INTEGER PRIMARY KEY,
 weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
 text TEXT NOT NULL CHECK(length(trim(text)) BETWEEN 1 AND 240),
 created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 assignee_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
 assigned_everyone INTEGER NOT NULL DEFAULT 0 CHECK(assigned_everyone IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((assigned_everyone=1 AND assignee_id IS NULL) OR
       (assigned_everyone=0 AND assignee_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS chores_weekday ON chores(weekday,id);
CREATE TABLE IF NOT EXISTS chore_completions (
 chore_id INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 completed_week TEXT NOT NULL,
 PRIMARY KEY(chore_id,user_id)
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

def household_today():
    """Use the Pi's configured local date until household timezone settings exist."""
    return datetime.now().astimezone().date()

def week_start_for(day):
    """Return the Sunday that begins the week containing day."""
    return day - timedelta(days=(day.weekday() + 1) % 7)

def chore_groups_for(db, users, weekday, week_key):
    groups = []
    for user in users:
        user_chores = db.execute(
            'SELECT c.*, cc.completed_week AS completed_week '
            'FROM chores c LEFT JOIN chore_completions cc ON cc.chore_id=c.id AND cc.user_id=? '
            'AND cc.completed_week=? WHERE c.weekday=? '
            'AND (c.assigned_everyone=1 OR c.assignee_id=?) ORDER BY c.id',
            (user['id'], week_key, weekday, user['id'])
        ).fetchall()
        groups.append({'user': user, 'chores': user_chores})
    return groups

def visible_lists(db, user, kind=None):
    """Return lists visible to this account without leaking private titles."""
    clauses = []
    params = []
    if kind:
        clauses.append('l.kind=?')
        params.append(kind)
    if user['role'] != 'admin':
        clauses.append('(l.visibility=\'shared\' OR l.created_by=? OR EXISTS '
                       '(SELECT 1 FROM list_access a WHERE a.list_id=l.id AND a.user_id=?))')
        params.extend((user['id'], user['id']))
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    return db.execute(
        'SELECT l.* FROM household_lists l' + where +
        ' ORDER BY l.is_default DESC, l.created_at, l.id', params
    ).fetchall()

def visible_list(db, user, list_id):
    try:
        list_id = int(list_id)
    except (TypeError, ValueError):
        abort(404)
    row = db.execute('SELECT * FROM household_lists WHERE id=?', (list_id,)).fetchone()
    if not row:
        abort(404)
    if user['role'] == 'admin' or row['visibility'] == 'shared' or row['created_by'] == user['id']:
        return row
    allowed = db.execute('SELECT 1 FROM list_access WHERE list_id=? AND user_id=?',
                         (list_id, user['id'])).fetchone()
    if not allowed:
        abort(404)
    return row

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
            item_columns = {row[1] for row in db.execute('PRAGMA table_info(shopping_items)')}
            for column, definition in (
                ('list_id', 'INTEGER REFERENCES household_lists(id) ON DELETE CASCADE'),
                ('due_date', 'TEXT'),
                ('assignee_id', 'INTEGER REFERENCES users(id) ON DELETE SET NULL'),
                ('updated_at', 'TEXT'),
            ):
                if column not in item_columns:
                    db.execute(f'ALTER TABLE shopping_items ADD COLUMN {column} {definition}')
            recipe_columns = {row[1] for row in db.execute('PRAGMA table_info(recipes)')}
            for column, definition in (
                ('description', "TEXT NOT NULL DEFAULT ''"),
                ('default_servings', 'INTEGER NOT NULL DEFAULT 4'),
            ):
                if column not in recipe_columns:
                    db.execute(f'ALTER TABLE recipes ADD COLUMN {column} {definition}')
            default_list = db.execute(
                "SELECT id FROM household_lists WHERE kind='shopping' AND is_default=1"
            ).fetchone()
            if default_list is None:
                db.execute("INSERT INTO household_lists(kind,title,category,visibility,is_default) "
                           "VALUES('shopping','Groceries','Groceries','shared',1)")
                default_list_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            else:
                default_list_id = default_list[0]
            db.execute('UPDATE shopping_items SET list_id=? WHERE list_id IS NULL',
                       (default_list_id,))
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Database relationships failed verification')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            db.commit()
        except Exception:
            db.rollback()
            raise

def safe_ics_text(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')

def recipe_form_values(form):
    title = form.get('title', '').strip()
    description = form.get('description', '').strip()
    try:
        servings = int(form.get('default_servings', ''))
    except ValueError:
        abort(400)
    quantities = form.getlist('ingredient_quantity')
    units = form.getlist('ingredient_unit')
    names = form.getlist('ingredient_name')
    notes = form.getlist('ingredient_note')
    steps = form.getlist('step')
    if (not title or len(title) > 128 or len(description) > 256
            or not 1 <= servings <= 99 or not 1 <= len(names) <= 64
            or len(quantities) != len(names) or len(units) != len(names)
            or len(notes) != len(names) or not 1 <= len(steps) <= 64):
        abort(400)
    ingredients = []
    for quantity, unit, name, note in zip(quantities, units, names, notes):
        ingredient = (quantity.strip(), unit.strip(), name.strip(), note.strip())
        if (not ingredient[2] or len(ingredient[0]) > 32 or len(ingredient[1]) > 16
                or len(ingredient[2]) > 128 or len(ingredient[3]) > 128):
            abort(400)
        ingredients.append(ingredient)
    clean_steps = [step.strip() for step in steps]
    if any(not step or len(step) > 256 for step in clean_steps):
        abort(400)
    return title, description, servings, ingredients, clean_steps

def save_recipe_content(db, recipe_id, values):
    title, description, servings, ingredients, steps = values
    db.execute('UPDATE recipes SET title=?,description=?,default_servings=?,ingredients=?,instructions=? '
               'WHERE id=?', (title, description, servings, '', '', recipe_id))
    db.execute('DELETE FROM recipe_ingredients WHERE recipe_id=?', (recipe_id,))
    db.execute('DELETE FROM recipe_steps WHERE recipe_id=?', (recipe_id,))
    db.executemany(
        'INSERT INTO recipe_ingredients(recipe_id,position,quantity,unit,name,note) VALUES(?,?,?,?,?,?)',
        [(recipe_id, position, *ingredient) for position, ingredient in enumerate(ingredients)]
    )
    db.executemany(
        'INSERT INTO recipe_steps(recipe_id,position,text) VALUES(?,?,?)',
        [(recipe_id, position, step) for position, step in enumerate(steps)]
    )

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('HOMEHUB_SECRET_KEY'),
        DATABASE=os.environ.get('HOMEHUB_DB', str(Path.home()/'.local/share/homehub/homehub.sqlite3')),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('HOMEHUB_COOKIE_SECURE', '').strip().lower()
            in {'1', 'true', 'yes', 'on'},
        MAX_CONTENT_LENGTH=1024*1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY']:
        raise RuntimeError('Set HOMEHUB_SECRET_KEY to a long, random persistent secret')
    db_path = Path(app.config['DATABASE'])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initialize_database(db_path)
    maintenance = threading.Condition()
    request_state = {'active': 0, 'restoring': False}

    @app.before_request
    def enter_request():
        with maintenance:
            if request_state['restoring']:
                return 'The hub is restoring a verified backup. Try again shortly.', 503
            request_state['active'] += 1
            g.request_counted = True

    def begin_restore():
        with maintenance:
            if request_state['restoring']:
                abort(409, description='Another restore is already in progress.')
            request_state['restoring'] = True
            while request_state['active'] > 1:
                maintenance.wait()

    def end_restore():
        with maintenance:
            request_state['restoring'] = False
            maintenance.notify_all()

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
        if g.pop('request_counted', False):
            with maintenance:
                request_state['active'] -= 1
                maintenance.notify_all()

    @app.get('/')
    @login_required
    def index():
        db = get_db()
        today = household_today()
        list_kind = request.args.get('kind', 'shopping')
        if list_kind not in ('shopping', 'todo', 'chores'):
            abort(400)
        lists = visible_lists(db, g.user, list_kind)
        selected_id = request.args.get('list_id')
        if selected_id:
            selected_list = visible_list(db, g.user, selected_id)
            if selected_list['kind'] != list_kind:
                abort(404)
        else:
            selected_list = next((item for item in lists if item['is_default']), None)
            if selected_list is None and lists:
                selected_list = lists[0]
        items = []
        if selected_list and selected_list['kind'] in ('shopping', 'todo'):
            items = db.execute(
                'SELECT shopping_items.*, users.display_name AS assignee_name, '
                'users.username AS assignee_username FROM shopping_items '
                'LEFT JOIN users ON users.id=shopping_items.assignee_id '
                'WHERE list_id=? ORDER BY done,id DESC',
                (selected_list['id'],)
            ).fetchall()
        list_users = db.execute(
            "SELECT id,username,display_name FROM users "
            "WHERE active=1 AND account_kind='person' ORDER BY display_name COLLATE NOCASE, id"
        ).fetchall()
        todo_assignees = list_users
        if selected_list and selected_list['kind'] == 'todo' and selected_list['visibility'] == 'private':
            allowed_ids = {selected_list['created_by']}
            allowed_ids.update(row['user_id'] for row in db.execute(
                'SELECT user_id FROM list_access WHERE list_id=?', (selected_list['id'],)
            ).fetchall())
            todo_assignees = [user for user in list_users if user['id'] in allowed_ids]
        weekdays = ('Monday', 'Tuesday', 'Wednesday', 'Thursday',
                    'Friday', 'Saturday', 'Sunday')
        selected_weekday = request.args.get('weekday', str(today.weekday()))
        try:
            selected_weekday = int(selected_weekday)
        except ValueError:
            abort(400)
        if not 0 <= selected_weekday < len(weekdays):
            abort(400)
        chore_filter = request.args.get('assignee_id', '')
        if chore_filter:
            try:
                chore_filter = int(chore_filter)
            except ValueError:
                abort(400)
            if chore_filter not in {user['id'] for user in list_users}:
                abort(400)
        chore_groups = []
        if list_kind == 'chores':
            week_key = week_start_for(today).isoformat()
            group_users = [user for user in list_users
                           if not chore_filter or user['id'] == chore_filter]
            chore_groups = chore_groups_for(db, group_users, selected_weekday, week_key)
        home_weekday = today.weekday()
        just_my_chores = request.args.get('just_my_chores') == '1'
        home_users = ([user for user in list_users if user['id'] == g.user['id']]
                      if just_my_chores and g.user['account_kind'] == 'person'
                      else list_users)
        home_chore_groups = chore_groups_for(
            db, home_users, home_weekday,
            week_start_for(today).isoformat()
        )
        return render_template('index.html', recipes=db.execute('SELECT * FROM recipes ORDER BY id DESC LIMIT 6').fetchall(),
          events=db.execute('SELECT events.*,recipes.title AS recipe_title FROM events LEFT JOIN recipes ON recipes.id=events.recipe_id ORDER BY event_date LIMIT 12').fetchall(),
          items=items, lists=lists, selected_list=selected_list, list_kind=list_kind,
          list_users=list_users, todo_assignees=todo_assignees,
          shopping_categories=SHOPPING_CATEGORIES,
          today=today.isoformat(), weekdays=weekdays,
          selected_weekday=selected_weekday, chore_filter=chore_filter,
          chore_groups=chore_groups, home_weekday=home_weekday,
          home_chore_groups=home_chore_groups, just_my_chores=just_my_chores)

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
        stored_folder = db.execute('SELECT value FROM app_settings WHERE key=?',
                                    ('backup_folder',)).fetchone()
        configured_folder = (Path(stored_folder['value']) if stored_folder
                             else default_backup_directory(db_path))
        try:
            backup_folder = validate_backup_directory(configured_folder)
            backup_folder_available = True
        except BackupError:
            backup_folder = configured_folder
            backup_folder_available = False
        backup_frequency = setting(db, 'backup_interval_days', 1)
        backup_retention = setting(db, 'backup_retention', 14)
        backups = list_backups(backup_folder, backup_retention) if backup_folder_available else []
        return render_template('accounts.html', users=users, counts=counts,
                               ownership=ownership,
                               idle_hours=setting(db, 'session_idle_hours', 24),
                               absolute_days=setting(db, 'session_absolute_days', 7),
                               backup_folder=str(configured_folder),
                               backup_folder_available=backup_folder_available,
                               backup_frequency=backup_frequency,
                               backup_retention=backup_retention,
                               backups=backups)

    def configured_backup_directory(db):
        row = db.execute('SELECT value FROM app_settings WHERE key=?',
                         ('backup_folder',)).fetchone()
        configured = Path(row['value']) if row else default_backup_directory(db_path)
        return validate_backup_directory(configured)

    @app.post('/admin/backups/settings')
    @admin_required
    def update_backup_settings():
        db = get_db()
        backups_off = request.form.get('backups_off') == 'on'
        try:
            frequency = 0 if backups_off else int(
                request.form.get('backup_frequency', ''))
            retention = int(request.form.get('backup_retention') or
                            setting(db, 'backup_retention', 14))
            folder = validate_backup_directory(request.form.get('backup_folder', ''))
        except (ValueError, BackupError):
            flash('Enter a writable absolute backup folder, an interval from 1 to 31 days, '
                  'and a retention count from 1 to 365, or turn automatic backups off.')
            return redirect(url_for('accounts'))
        if ((not backups_off and not 1 <= frequency <= 31)
                or not 1 <= retention <= 365):
            flash('Enter a backup interval from 1 to 31 days and a retention count from 1 to 365.')
            return redirect(url_for('accounts'))
        with db:
            db.executemany('INSERT INTO app_settings(key,value) VALUES(?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (('backup_folder', str(folder)),
                            ('backup_interval_days', str(frequency)),
                            ('backup_retention', str(retention))))
        flash('Backup settings updated.')
        return redirect(url_for('accounts'))

    @app.post('/admin/backups/create')
    @admin_required
    def create_backup_now():
        db = get_db()
        try:
            folder = configured_backup_directory(db)
            path = create_backup(db_path, folder)
            prune_backups(folder, setting(db, 'backup_retention', 14))
        except BackupError as exc:
            flash(str(exc))
        else:
            flash(f'Backup created and verified: {path.name}.')
        return redirect(url_for('accounts'))

    @app.get('/admin/backups/<filename>/download')
    @admin_required
    def download_backup(filename):
        try:
            backup_filename(filename)
            path = configured_backup_directory(get_db()) / filename
            if (path.is_symlink() or path.resolve().parent != path.parent.resolve()
                    or not path.is_file()):
                abort(404)
            return send_file(path, as_attachment=True, download_name=path.name,
                             mimetype='application/vnd.sqlite3', max_age=0)
        except BackupError:
            abort(404)

    @app.post('/admin/backups/<filename>/delete')
    @admin_required
    def remove_backup(filename):
        try:
            delete_backup(configured_backup_directory(get_db()), filename)
        except BackupError as exc:
            abort(400, description=str(exc))
        flash(f'Backup {filename} deleted.')
        return redirect(url_for('accounts'))

    @app.post('/admin/backups/<filename>/restore')
    @admin_required
    def restore_selected_backup(filename):
        db = get_db()
        restore_started = False
        try:
            folder = configured_backup_directory(db)
            begin_restore()
            restore_started = True
            db.close()
            g.pop('db', None)
            restore_backup(db_path, folder, filename, initialize_database, SCHEMA_VERSION)
        except BackupError as exc:
            flash(str(exc))
            return redirect(url_for('accounts'))
        finally:
            if restore_started:
                end_restore()
        session.clear()
        flash('Backup restored and verified. Please sign in again.')
        return redirect(url_for('login'))

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

    @app.post('/lists')
    @login_required
    def create_list():
        kind = request.form.get('kind', 'shopping').strip()
        title = request.form.get('title', '').strip()
        visibility = request.form.get('visibility', 'shared').strip()
        category = request.form.get('category', 'Other').strip()
        if kind not in ('shopping', 'todo') or visibility not in ('shared', 'private'):
            abort(400)
        if not title or len(title) > 80:
            abort(400)
        if kind == 'shopping' and category not in SHOPPING_CATEGORIES:
            abort(400)
        if kind == 'todo':
            category = 'Other'

        allowed_ids = []
        if visibility == 'private':
            try:
                allowed_ids = list(dict.fromkeys(
                    int(value) for value in request.form.getlist('allowed_user_ids')
                ))
            except ValueError:
                abort(400)
        db = get_db()
        if allowed_ids:
            placeholders = ','.join('?' for _ in allowed_ids)
            valid_ids = {row['id'] for row in db.execute(
                f"SELECT id FROM users WHERE active=1 AND account_kind='person' "
                f"AND id IN ({placeholders})", allowed_ids
            ).fetchall()}
            if valid_ids != set(allowed_ids):
                abort(400)
        with db:
            cursor = db.execute(
                'INSERT INTO household_lists(kind,title,category,visibility,created_by) '
                'VALUES(?,?,?,?,?)',
                (kind, title, category, visibility, g.user['id'])
            )
            list_id = cursor.lastrowid
            if visibility == 'private' and allowed_ids:
                db.executemany('INSERT INTO list_access(list_id,user_id) VALUES(?,?)',
                               ((list_id, user_id) for user_id in allowed_ids))
        return redirect(url_for('index', kind=kind, list_id=list_id))

    @app.post('/chores')
    @login_required
    def create_chore():
        text = request.form.get('text', '').strip()
        assignee = request.form.get('assignee', '').strip()
        try:
            weekday = int(request.form.get('weekday', ''))
        except ValueError:
            abort(400)
        if not 0 <= weekday <= 6 or not text or len(text) > 240:
            abort(400)
        db = get_db()
        assigned_everyone = assignee.casefold() == 'everyone'
        assignee_id = None
        if not assigned_everyone:
            try:
                assignee_id = int(assignee)
            except ValueError:
                abort(400)
            assigned_user = db.execute(
                "SELECT id FROM users WHERE id=? AND active=1 AND account_kind='person'",
                (assignee_id,)
            ).fetchone()
            if not assigned_user:
                abort(400)
        with db:
            db.execute(
                'INSERT INTO chores(weekday,text,created_by,assignee_id,assigned_everyone) '
                'VALUES(?,?,?,?,?)',
                (weekday, text, g.user['id'], assignee_id, int(assigned_everyone))
            )
        return redirect(url_for('index', kind='chores', weekday=weekday))

    @app.post('/chores/<int:chore_id>/toggle')
    @login_required
    def toggle_chore(chore_id):
        db = get_db()
        chore = db.execute('SELECT * FROM chores WHERE id=?', (chore_id,)).fetchone()
        if not chore:
            abort(404)
        if (g.user['account_kind'] != 'person' or
                (not chore['assigned_everyone'] and chore['assignee_id'] != g.user['id'])):
            abort(403)
        week_key = week_start_for(household_today()).isoformat()
        with db:
            completion = db.execute(
                'SELECT completed_week FROM chore_completions WHERE chore_id=? AND user_id=?',
                (chore_id, g.user['id'])
            ).fetchone()
            if completion and completion['completed_week'] == week_key:
                db.execute('DELETE FROM chore_completions WHERE chore_id=? AND user_id=?',
                           (chore_id, g.user['id']))
            else:
                db.execute(
                    'INSERT INTO chore_completions(chore_id,user_id,completed_week) VALUES(?,?,?) '
                    'ON CONFLICT(chore_id,user_id) DO UPDATE SET completed_week=excluded.completed_week',
                    (chore_id, g.user['id'], week_key)
                )
        if request.args.get('return_to') == 'home':
            return redirect(url_for('index'))
        return redirect(url_for('index', kind='chores', weekday=chore['weekday']))

    @app.post('/chores/<int:chore_id>/delete')
    @login_required
    def delete_chore(chore_id):
        db = get_db()
        chore = db.execute('SELECT * FROM chores WHERE id=?', (chore_id,)).fetchone()
        if not chore:
            abort(404)
        if chore['created_by'] != g.user['id'] and g.user['role'] != 'admin':
            abort(403)
        with db:
            db.execute('DELETE FROM chores WHERE id=?', (chore_id,))
        return redirect(url_for('index', kind='chores', weekday=chore['weekday']))

    def add_list_item(expected_kind):
        text = request.form.get('text','').strip()
        if not text or len(text) > 240:
            abort(400)
        db = get_db()
        due_date = None
        assignee_id = None
        if expected_kind == 'todo':
            due_date = request.form.get('due_date', '').strip() or None
            if due_date:
                try:
                    if date.fromisoformat(due_date).isoformat() != due_date:
                        abort(400)
                except ValueError:
                    abort(400)
            raw_assignee = request.form.get('assignee_id', '').strip()
            if raw_assignee:
                try:
                    assignee_id = int(raw_assignee)
                except ValueError:
                    abort(400)
                assignee = db.execute(
                    "SELECT id FROM users WHERE id=? AND active=1 AND account_kind='person'",
                    (assignee_id,)
                ).fetchone()
                if not assignee:
                    abort(400)
        selected_id = request.form.get('list_id')
        if selected_id:
            target = visible_list(db, g.user, selected_id)
        elif expected_kind == 'shopping':
            target = db.execute(
                "SELECT * FROM household_lists WHERE kind='shopping' AND is_default=1"
            ).fetchone()
        else:
            abort(400)
        if not target or target['kind'] != expected_kind:
            abort(404)
        if (assignee_id and target['kind'] == 'todo' and target['visibility'] == 'private'
                and assignee_id != target['created_by']):
            allowed = db.execute(
                'SELECT 1 FROM list_access WHERE list_id=? AND user_id=?',
                (target['id'], assignee_id)
            ).fetchone()
            if not allowed:
                abort(400)
        with db:
            db.execute('INSERT INTO shopping_items(text,created_by,list_id,due_date,assignee_id,updated_at) '
                       'VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)',
                       (text, g.user['id'], target['id'], due_date, assignee_id))
        return redirect(url_for('index', kind=expected_kind, list_id=target['id']) +
                        ('#shopping' if expected_kind == 'shopping' else '#todo'))

    @app.post('/shopping')
    @login_required
    def add_item():
        return add_list_item('shopping')

    @app.post('/todo')
    @login_required
    def add_todo_item():
        return add_list_item('todo')

    @app.post('/todo/<int:item_id>/edit')
    @login_required
    def edit_todo_item(item_id):
        db = get_db()
        item = db.execute('SELECT * FROM shopping_items WHERE id=?', (item_id,)).fetchone()
        if not item or not item['list_id']:
            abort(404)
        target = visible_list(db, g.user, item['list_id'])
        if target['kind'] != 'todo':
            abort(404)
        text = request.form.get('text', '').strip()
        if not text or len(text) > 240:
            abort(400)
        due_date = request.form.get('due_date', '').strip() or None
        if due_date:
            try:
                if date.fromisoformat(due_date).isoformat() != due_date:
                    abort(400)
            except ValueError:
                abort(400)
        raw_assignee = request.form.get('assignee_id', '').strip()
        assignee_id = None
        if raw_assignee:
            try:
                assignee_id = int(raw_assignee)
            except ValueError:
                abort(400)
            assignee = db.execute(
                "SELECT id FROM users WHERE id=? AND active=1 AND account_kind='person'",
                (assignee_id,)
            ).fetchone()
            if not assignee:
                abort(400)
            if target['visibility'] == 'private' and assignee_id != target['created_by']:
                allowed = db.execute(
                    'SELECT 1 FROM list_access WHERE list_id=? AND user_id=?',
                    (target['id'], assignee_id)
                ).fetchone()
                if not allowed:
                    abort(400)
        with db:
            db.execute('UPDATE shopping_items SET text=?,due_date=?,assignee_id=?,updated_at=CURRENT_TIMESTAMP '
                       'WHERE id=?', (text, due_date, assignee_id, item_id))
        return redirect(url_for('index', kind='todo', list_id=target['id']) + '#todo')

    @app.post('/shopping/<int:item_id>/toggle')
    @login_required
    def toggle_item(item_id):
        db = get_db()
        item = db.execute('SELECT * FROM shopping_items WHERE id=?', (item_id,)).fetchone()
        if not item or not item['list_id']:
            abort(404)
        target = visible_list(db, g.user, item['list_id'])
        if target['kind'] not in ('shopping', 'todo'):
            abort(404)
        with db:
            db.execute('UPDATE shopping_items SET done=1-done,updated_at=CURRENT_TIMESTAMP '
                       'WHERE id=?', (item_id,))
        return redirect(url_for('index', kind=target['kind'], list_id=target['id']) +
                        ('#shopping' if target['kind'] == 'shopping' else '#todo'))

    @app.post('/shopping/<int:item_id>/delete')
    @login_required
    def delete_list_item(item_id):
        db = get_db()
        item = db.execute('SELECT * FROM shopping_items WHERE id=?', (item_id,)).fetchone()
        if not item or not item['list_id']:
            abort(404)
        target = visible_list(db, g.user, item['list_id'])
        if item['created_by'] != g.user['id'] and g.user['role'] != 'admin':
            abort(403)
        with db:
            db.execute('DELETE FROM shopping_items WHERE id=?', (item_id,))
        return redirect(url_for('index', kind=target['kind'], list_id=target['id']) +
                        ('#shopping' if target['kind'] == 'shopping' else '#todo'))

    @app.post('/lists/<int:list_id>/delete')
    @login_required
    def delete_list(list_id):
        db = get_db()
        target = db.execute('SELECT * FROM household_lists WHERE id=?', (list_id,)).fetchone()
        if not target:
            abort(404)
        if target['created_by'] != g.user['id'] and g.user['role'] != 'admin':
            abort(403)
        kind = target['kind']
        with db:
            db.execute('DELETE FROM household_lists WHERE id=?', (list_id,))
        return redirect(url_for('index', kind=kind))

    @app.get('/shopping.txt')
    @login_required
    def shopping_text():
        db = get_db()
        selected_id = request.args.get('list_id')
        if selected_id:
            target = visible_list(db, g.user, selected_id)
        else:
            target = db.execute(
                "SELECT * FROM household_lists WHERE kind='shopping' AND is_default=1"
            ).fetchone()
        if not target or target['kind'] != 'shopping':
            abort(404)
        items = db.execute('SELECT text,done FROM shopping_items WHERE list_id=? ORDER BY id',
                           (target['id'],)).fetchall()
        body = target['title'] + '\n\n' + '\n'.join(
            ('[x] ' if item['done'] else '[ ] ') + item['text'] for item in items
        ) + '\n'
        return Response(body,mimetype='text/plain',headers={'Content-Disposition':'attachment; filename="shopping.txt"'})

    @app.post('/recipes')
    @login_required
    def add_recipe():
        values = recipe_form_values(request.form)
        title, description, servings, _, _ = values
        db = get_db()
        count = db.execute('SELECT COUNT(*) FROM recipes WHERE created_by=?',
                           (g.user['id'],)).fetchone()[0]
        if count >= 128:
            abort(409, description='Your recipe book is full (128 recipes).')
        with db:
            cursor = db.execute(
                'INSERT INTO recipes(title,description,default_servings,created_by) VALUES(?,?,?,?)',
                (title, description, servings, g.user['id'])
            )
            recipe_id = cursor.lastrowid
            save_recipe_content(db, recipe_id, values)
        return redirect(url_for('index')+'#recipes')

    @app.get('/recipes/<int:recipe_id>')
    @login_required
    def recipe_detail(recipe_id):
        db = get_db()
        recipe = db.execute('SELECT * FROM recipes WHERE id=?', (recipe_id,)).fetchone()
        if not recipe: abort(404)
        ingredients = db.execute(
            'SELECT quantity,unit,name,note FROM recipe_ingredients WHERE recipe_id=? ORDER BY position',
            (recipe_id,)
        ).fetchall()
        steps = db.execute(
            'SELECT text FROM recipe_steps WHERE recipe_id=? ORDER BY position', (recipe_id,)
        ).fetchall()
        legacy_ingredients = not ingredients and bool(recipe['ingredients'])
        legacy_steps = not steps and bool(recipe['instructions'])
        return render_template('recipe.html', recipe=recipe, ingredients=ingredients,
                               steps=steps, legacy_ingredients=legacy_ingredients,
                               legacy_steps=legacy_steps,
                               can_edit=recipe['created_by'] == g.user['id'],
                               can_delete=(recipe['created_by'] == g.user['id']
                                           or g.user['role'] == 'admin'))

    @app.route('/recipes/<int:recipe_id>/edit', methods=['GET', 'POST'])
    @login_required
    def edit_recipe(recipe_id):
        db = get_db()
        recipe = db.execute('SELECT * FROM recipes WHERE id=?', (recipe_id,)).fetchone()
        if not recipe:
            abort(404)
        if recipe['created_by'] != g.user['id']:
            abort(403)
        if request.method == 'POST':
            values = recipe_form_values(request.form)
            with db:
                save_recipe_content(db, recipe_id, values)
            return redirect(url_for('recipe_detail', recipe_id=recipe_id))
        ingredients = db.execute(
            'SELECT quantity,unit,name,note FROM recipe_ingredients WHERE recipe_id=? ORDER BY position',
            (recipe_id,)
        ).fetchall()
        steps = db.execute('SELECT text FROM recipe_steps WHERE recipe_id=? ORDER BY position',
                            (recipe_id,)).fetchall()
        if not ingredients and recipe['ingredients']:
            ingredients = [{'quantity': '', 'unit': '', 'name': line, 'note': ''}
                           for line in recipe['ingredients'].splitlines() if line.strip()]
        if not steps and recipe['instructions']:
            steps = [{'text': line} for line in recipe['instructions'].splitlines() if line.strip()]
        return render_template('edit_recipe.html', recipe=recipe,
                               ingredients=ingredients or [{'quantity': '', 'unit': '', 'name': '', 'note': ''}],
                               steps=steps or [{'text': ''}])

    @app.post('/recipes/<int:recipe_id>/delete')
    @login_required
    def delete_recipe(recipe_id):
        db = get_db()
        recipe = db.execute('SELECT id,created_by FROM recipes WHERE id=?', (recipe_id,)).fetchone()
        if not recipe:
            abort(404)
        if recipe['created_by'] != g.user['id'] and g.user['role'] != 'admin':
            abort(403)
        with db:
            db.execute('DELETE FROM recipes WHERE id=?', (recipe_id,))
        return redirect(url_for('index') + '#recipes')

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
