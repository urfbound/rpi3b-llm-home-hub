"""Home Hub phase 1: accounts, sessions, recipes, lists, calendar and ICS exports."""
import hashlib
import hmac
import os
import re
import calendar as pycalendar
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from flask import (Flask, Response, abort, flash, g, jsonify, make_response, redirect,
                   render_template, request, send_file, send_from_directory, session, url_for)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from .backups import (BackupError, backup_filename, create_backup,
                      default_backup_directory, delete_backup, list_backups,
                      prune_backups, restore_backup, validate_backup_directory)
from .weather import get_cached_forecast

PH = PasswordHasher(memory_cost=16384, time_cost=2, parallelism=1)
SCHEMA_VERSION = 11
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_USERNAME_LIMIT = 5
LOGIN_ADDRESS_LIMIT = 30
SHOPPING_CATEGORIES = (
    'Groceries', 'Gifts', 'Household', 'Health & Personal Care', 'Pets',
    'Home & Garden', 'School & Activities', 'Other',
)
RECIPE_SERVING_MIN = 1
RECIPE_SERVING_MAX = 99
VULGAR_FRACTIONS = {
    '¼': Fraction(1, 4), '½': Fraction(1, 2), '¾': Fraction(3, 4),
    '⅐': Fraction(1, 7), '⅑': Fraction(1, 9), '⅒': Fraction(1, 10),
    '⅓': Fraction(1, 3), '⅔': Fraction(2, 3), '⅕': Fraction(1, 5),
    '⅖': Fraction(2, 5), '⅗': Fraction(3, 5), '⅘': Fraction(4, 5),
    '⅙': Fraction(1, 6), '⅚': Fraction(5, 6), '⅛': Fraction(1, 8),
    '⅜': Fraction(3, 8), '⅝': Fraction(5, 8), '⅞': Fraction(7, 8),
}
RECIPE_UNIT_FACTORS = {
    # Values are expressed in teaspoons for volume, grams for weight, and
    # individual items for count. A group can only combine within one family.
    'tsp': ('volume', Fraction(1), 'tsp'),
    'teaspoon': ('volume', Fraction(1), 'tsp'),
    'teaspoons': ('volume', Fraction(1), 'tsp'),
    'tbsp': ('volume', Fraction(3), 'tbsp'),
    'tablespoon': ('volume', Fraction(3), 'tbsp'),
    'tablespoons': ('volume', Fraction(3), 'tbsp'),
    'cup': ('volume', Fraction(48), 'cup'),
    'cups': ('volume', Fraction(48), 'cup'),
    'fl oz': ('volume', Fraction(6), 'fl oz'),
    'fluid ounce': ('volume', Fraction(6), 'fl oz'),
    'fluid ounces': ('volume', Fraction(6), 'fl oz'),
    'pint': ('volume', Fraction(96), 'pint'),
    'pints': ('volume', Fraction(96), 'pint'),
    'quart': ('volume', Fraction(192), 'quart'),
    'quarts': ('volume', Fraction(192), 'quart'),
    'gallon': ('volume', Fraction(768), 'gallon'),
    'gallons': ('volume', Fraction(768), 'gallon'),
    'ml': ('volume', Fraction(50721, 250), 'ml'),
    'milliliter': ('volume', Fraction(50721, 250), 'ml'),
    'milliliters': ('volume', Fraction(50721, 250), 'ml'),
    'l': ('volume', Fraction(202884, 250), 'l'),
    'liter': ('volume', Fraction(202884, 250), 'l'),
    'liters': ('volume', Fraction(202884, 250), 'l'),
    'g': ('weight', Fraction(1), 'g'),
    'gram': ('weight', Fraction(1), 'g'),
    'grams': ('weight', Fraction(1), 'g'),
    'kg': ('weight', Fraction(1000), 'kg'),
    'kilogram': ('weight', Fraction(1000), 'kg'),
    'kilograms': ('weight', Fraction(1000), 'kg'),
    'oz': ('weight', Fraction(56699, 2000), 'oz'),
    'ounce': ('weight', Fraction(56699, 2000), 'oz'),
    'ounces': ('weight', Fraction(56699, 2000), 'oz'),
    'lb': ('weight', Fraction(45359237, 100000), 'lb'),
    'lbs': ('weight', Fraction(45359237, 100000), 'lb'),
    'pound': ('weight', Fraction(45359237, 100000), 'lb'),
    'pounds': ('weight', Fraction(45359237, 100000), 'lb'),
    'each': ('count', Fraction(1), 'each'),
    'ea': ('count', Fraction(1), 'each'),
    'item': ('count', Fraction(1), 'each'),
    'items': ('count', Fraction(1), 'each'),
}
QUANTITY_NUMBER = re.compile(
    r'(?P<mixed_vulgar>\d+\s*[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])|'
    r'(?P<mixed>\d+\s+\d+/\d+)|(?P<fraction>\d+/\d+)|'
    r'(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)|'
    r'(?P<vulgar>[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])'
)


def format_recipe_amount(amount):
    """Format a scaled fraction as a common cooking fraction or short decimal."""
    sign = '-' if amount < 0 else ''
    amount = abs(amount)
    whole = amount.numerator // amount.denominator
    remainder = amount - whole
    if not remainder:
        return f'{sign}{whole}'
    common_denominators = (2, 3, 4, 5, 6, 8, 10, 12, 16)
    candidates = []
    for denominator in common_denominators:
        numerator = round(float(remainder * denominator))
        if numerator:
            candidate = Fraction(numerator, denominator)
            candidates.append((abs(candidate - remainder), candidate))
    if candidates:
        error, approximation = min(candidates, key=lambda value: value[0])
        if error <= Fraction(1, 100):
            if approximation >= 1:
                whole += 1
                approximation -= 1
            if not approximation:
                return f'{sign}{whole}'
            fraction_text = f'{approximation.numerator}/{approximation.denominator}'
            return f'{sign}{whole} {fraction_text}' if whole else f'{sign}{fraction_text}'
    decimal_value = (Decimal(amount.numerator) / Decimal(amount.denominator)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    if decimal_value == 0:
        return f'{sign}<0.01'
    return f'{sign}{format(decimal_value, "f").rstrip("0").rstrip(".")}'


def scale_recipe_quantity(quantity, default_servings, selected_servings):
    """Scale numeric parts of a quantity; leave descriptive amounts intact."""
    if not quantity or selected_servings == default_servings:
        return quantity, bool(QUANTITY_NUMBER.search(quantity or ''))
    ratio = Fraction(selected_servings, default_servings)
    scaled_any = False

    def replace(match):
        nonlocal scaled_any
        token = match.group(0)
        try:
            if match.group('vulgar'):
                amount = VULGAR_FRACTIONS[token]
            elif match.group('mixed_vulgar'):
                integer = re.match(r'\d+', token).group(0)
                fraction = token[len(integer):].strip()
                amount = Fraction(int(integer), 1) + VULGAR_FRACTIONS[fraction]
            elif match.group('mixed'):
                integer, fraction = token.split()
                numerator, denominator = fraction.split('/')
                amount = Fraction(int(integer), 1) + Fraction(int(numerator), int(denominator))
            elif match.group('fraction'):
                numerator, denominator = token.split('/')
                amount = Fraction(int(numerator), int(denominator))
            else:
                amount = Fraction(token.replace(',', ''))
        except (ValueError, ZeroDivisionError):
            return token
        scaled_any = True
        return format_recipe_amount(amount * ratio)

    scaled = QUANTITY_NUMBER.sub(replace, quantity)
    return scaled, scaled_any


def parse_recipe_amount(quantity):
    """Return a precise numeric amount when the full field is numeric."""
    token = (quantity or '').strip()
    match = QUANTITY_NUMBER.fullmatch(token)
    if not match:
        return None
    try:
        if match.group('vulgar'):
            return VULGAR_FRACTIONS[token]
        if match.group('mixed_vulgar'):
            integer = re.match(r'\d+', token).group(0)
            fraction = token[len(integer):].strip()
            return Fraction(int(integer), 1) + VULGAR_FRACTIONS[fraction]
        if match.group('mixed'):
            integer, fraction = token.split()
            numerator, denominator = fraction.split('/')
            return Fraction(int(integer), 1) + Fraction(int(numerator), int(denominator))
        if match.group('fraction'):
            numerator, denominator = token.split('/')
            return Fraction(int(numerator), int(denominator))
        return Fraction(token.replace(',', ''))
    except (ValueError, ZeroDivisionError):
        return None


def recipe_item_snapshots(ingredients, default_servings, selected_servings):
    """Build immutable list-item text, combining only compatible measures."""
    rows = []
    for ingredient in ingredients:
        quantity, _ = scale_recipe_quantity(
            ingredient['quantity'], default_servings, selected_servings
        )
        rows.append({**dict(ingredient), 'quantity': quantity})

    consumed = set()
    snapshots = []
    for index, first in enumerate(rows):
        if index in consumed:
            continue
        amount = parse_recipe_amount(first['quantity'])
        unit_key = first['unit'].strip().casefold()
        unit_info = RECIPE_UNIT_FACTORS.get(unit_key)
        if amount is not None:
            total = amount * (unit_info[1] if unit_info else 1)
            for other_index in range(index + 1, len(rows)):
                other = rows[other_index]
                other_amount = parse_recipe_amount(other['quantity'])
                other_unit_key = other['unit'].strip().casefold()
                other_info = RECIPE_UNIT_FACTORS.get(other_unit_key)
                same_ingredient = (other['name'].strip().casefold()
                                   == first['name'].strip().casefold())
                same_note = other['note'].strip() == first['note'].strip()
                if not same_ingredient or not same_note or other_amount is None:
                    continue
                if unit_info:
                    if not other_info or other_info[0] != unit_info[0]:
                        continue
                    total += other_amount * other_info[1]
                    consumed.add(other_index)
                elif other_unit_key == unit_key:
                    total += other_amount
                    consumed.add(other_index)
            if unit_info:
                total /= unit_info[1]
                output_unit = unit_info[2]
            else:
                output_unit = first['unit'].strip()
            output_quantity = format_recipe_amount(total)
        else:
            output_quantity = first['quantity'].strip()
            output_unit = first['unit'].strip()

        text = ' '.join(part for part in (
            output_quantity,
            output_unit,
            first['name'].strip(),
        ) if part)
        if first['note'].strip():
            text += f" ({first['note'].strip()})"
        snapshots.append(text)
    return snapshots
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
 instructions TEXT NOT NULL DEFAULT '', created_by INTEGER NOT NULL REFERENCES users(id),
 visibility TEXT NOT NULL DEFAULT 'shared' CHECK(visibility IN ('shared','private'))
);
CREATE TABLE IF NOT EXISTS recipe_access (
 recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(recipe_id,user_id)
);
CREATE TABLE IF NOT EXISTS recipe_notes (
 id INTEGER PRIMARY KEY, recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 note TEXT NOT NULL CHECK(length(trim(note)) BETWEEN 1 AND 128),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(recipe_id,user_id)
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
 created_by INTEGER NOT NULL REFERENCES users(id),
 description TEXT NOT NULL DEFAULT '',
 all_day INTEGER NOT NULL DEFAULT 1 CHECK(all_day IN (0,1)),
 start_time TEXT, end_time TEXT,
 series_id INTEGER, recurrence_frequency TEXT, recurrence_until TEXT,
 visibility TEXT NOT NULL DEFAULT 'shared' CHECK(visibility IN ('shared','private')),
 recipe_servings INTEGER,
 meal_prep INTEGER NOT NULL DEFAULT 0 CHECK(meal_prep IN (0,1)),
 meal_slot TEXT CHECK(meal_slot IS NULL OR meal_slot IN ('breakfast','lunch','dinner')),
 recipe_title_auto INTEGER NOT NULL DEFAULT 0 CHECK(recipe_title_auto IN (0,1))
);
CREATE INDEX IF NOT EXISTS events_date ON events(event_date,id);
CREATE TABLE IF NOT EXISTS event_participants (
 event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(event_id,user_id)
);
CREATE INDEX IF NOT EXISTS event_participants_user ON event_participants(user_id,event_id);
CREATE TABLE IF NOT EXISTS weather_cache (
 cache_key TEXT PRIMARY KEY,
 payload TEXT NOT NULL,
 fetched_at TEXT NOT NULL
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

def visible_recipes(db, user):
    if user['role'] == 'admin':
        return db.execute('SELECT * FROM recipes ORDER BY id DESC').fetchall()
    return db.execute(
        'SELECT r.* FROM recipes r WHERE r.visibility=\'shared\' OR r.created_by=? OR EXISTS '
        '(SELECT 1 FROM recipe_access a WHERE a.recipe_id=r.id AND a.user_id=?) '
        'ORDER BY r.id DESC', (user['id'], user['id'])
    ).fetchall()

def visible_recipe(db, user, recipe_id):
    recipe = db.execute('SELECT * FROM recipes WHERE id=?', (recipe_id,)).fetchone()
    if not recipe:
        abort(404)
    if user['role'] != 'admin' and recipe['visibility'] == 'private' and recipe['created_by'] != user['id']:
        allowed = db.execute('SELECT 1 FROM recipe_access WHERE recipe_id=? AND user_id=?',
                             (recipe_id, user['id'])).fetchone()
        if not allowed:
            abort(404)
    return recipe

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
                ('visibility', "TEXT NOT NULL DEFAULT 'shared'"),
            ):
                if column not in recipe_columns:
                    db.execute(f'ALTER TABLE recipes ADD COLUMN {column} {definition}')
            event_columns = {row[1] for row in db.execute('PRAGMA table_info(events)')}
            for column, definition in (
                ('description', "TEXT NOT NULL DEFAULT ''"),
                ('all_day', 'INTEGER NOT NULL DEFAULT 1'),
                ('start_time', 'TEXT'),
                ('end_time', 'TEXT'),
                ('series_id', 'INTEGER'),
                ('recurrence_frequency', 'TEXT'),
                ('recurrence_until', 'TEXT'),
                ('visibility', "TEXT NOT NULL DEFAULT 'shared'"),
                ('recipe_servings', 'INTEGER'),
                ('meal_prep', 'INTEGER NOT NULL DEFAULT 0'),
                ('meal_slot', 'TEXT'),
                ('recipe_title_auto', 'INTEGER NOT NULL DEFAULT 0'),
            ):
                if column not in event_columns:
                    db.execute(f'ALTER TABLE events ADD COLUMN {column} {definition}')
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

def event_form_values(form):
    title = form.get('title', '').strip()
    event_date = form.get('event_date', '').strip()
    description = form.get('description', '').strip()
    try:
        if date.fromisoformat(event_date).isoformat() != event_date:
            raise ValueError
    except ValueError:
        abort(400, description='Enter a valid event date.')
    if not title or len(title) > 128 or len(description) > 256:
        abort(400, description='Event titles are limited to 128 characters and descriptions to 256.')
    all_day = (form.get('all_day') == '1' or
               (form.get('all_day') is None and form.get('start_time') is None
                and form.get('end_time') is None))
    if all_day:
        start_time = end_time = None
    else:
        start_time = form.get('start_time', '').strip()
        end_time = form.get('end_time', '').strip() or None
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', start_time):
            abort(400, description='Choose a valid start time for a timed event.')
        if end_time and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', end_time):
            abort(400, description='Choose a valid end time.')
        if end_time and end_time <= start_time:
            abort(400, description='The end time must be later than the start time.')
    return {
        'title': title, 'event_date': event_date, 'description': description,
        'all_day': int(all_day), 'start_time': start_time, 'end_time': end_time,
    }

def recurrence_values(form, event_date):
    """Validate a recurrence rule and return its inclusive end date."""
    frequency = form.get('recurrence_frequency', '').strip()
    if frequency not in ('', 'daily', 'weekly', 'monthly', 'yearly'):
        abort(400, description='Choose a supported recurrence interval.')
    if not frequency:
        return None, None
    try:
        until_text = form.get('recurrence_until', '').strip()
        if until_text:
            until = date.fromisoformat(until_text)
            if until.isoformat() != until_text:
                raise ValueError
        else:
            start = date.fromisoformat(event_date)
            try:
                until = start.replace(year=start.year + 1)
            except ValueError:
                until = start.replace(year=start.year + 1, day=28)
    except ValueError:
        abort(400, description='Enter a valid recurrence end date.')
    if until < date.fromisoformat(event_date):
        abort(400, description='The recurrence end date must be on or after the event date.')
    return frequency, until.isoformat()

def recurrence_dates(start_text, frequency, until_text):
    """Generate dates through the inclusive end date; month/year dates clamp."""
    start = date.fromisoformat(start_text)
    until = date.fromisoformat(until_text)
    result = []
    index = 0
    while True:
        if frequency == 'daily':
            occurrence = start + timedelta(days=index)
        elif frequency == 'weekly':
            occurrence = start + timedelta(weeks=index)
        elif frequency in ('monthly', 'yearly'):
            months = index if frequency == 'monthly' else index * 12
            year = start.year + (start.month - 1 + months) // 12
            month = (start.month - 1 + months) % 12 + 1
            day = min(start.day, pycalendar.monthrange(year, month)[1])
            occurrence = date(year, month, day)
        else:
            raise ValueError('Unsupported recurrence frequency')
        if occurrence > until:
            break
        result.append(occurrence.isoformat())
        index += 1
        if index > 3660:
            abort(400, description='A recurring series cannot exceed 3,660 occurrences.')
    return result

def event_access_values(db, form):
    visibility = form.get('visibility', 'shared').strip()
    if visibility not in ('shared', 'private'):
        abort(400, description='Choose whether this event is shared or private.')
    participant_ids = set()
    for raw_id in form.getlist('participant_ids'):
        try:
            user_id = int(raw_id)
        except ValueError:
            abort(400, description='Choose valid event participants.')
        user = db.execute('SELECT id FROM users WHERE id=? AND active=1',
                          (user_id,)).fetchone()
        if not user:
            abort(400, description='Choose active household accounts as participants.')
        participant_ids.add(user_id)
    return visibility, sorted(participant_ids)

def set_event_participants(db, event_id, participant_ids):
    db.execute('DELETE FROM event_participants WHERE event_id=?', (event_id,))
    db.executemany('INSERT INTO event_participants(event_id,user_id) VALUES(?,?)',
                   [(event_id, user_id) for user_id in participant_ids])

def can_view_recipe(db, user, recipe):
    if not recipe:
        return False
    if user['role'] == 'admin' or recipe['visibility'] == 'shared' \
            or recipe['created_by'] == user['id']:
        return True
    return db.execute(
        'SELECT 1 FROM recipe_access WHERE recipe_id=? AND user_id=?',
        (recipe['id'], user['id']),
    ).fetchone() is not None

def event_recipe_values(db, user, form, values, existing_recipe_id=None):
    raw_recipe_id = form.get('recipe_id', '').strip()
    if not raw_recipe_id and existing_recipe_id:
        raw_recipe_id = str(existing_recipe_id)
    recipe = None
    if raw_recipe_id:
        try:
            recipe_id = int(raw_recipe_id)
        except ValueError:
            abort(400, description='Choose a valid recipe.')
        recipe = db.execute('SELECT * FROM recipes WHERE id=?', (recipe_id,)).fetchone()
        if not recipe:
            abort(404)
        if not can_view_recipe(db, user, recipe) and recipe_id != existing_recipe_id:
            abort(404)
    meal_prep = form.get('meal_prep') in ('1', 'on')
    meal_slot = form.get('meal_slot', '').strip() or None
    if meal_slot not in (None, 'breakfast', 'lunch', 'dinner'):
        abort(400, description='Choose breakfast, lunch, or dinner.')
    raw_servings = form.get('recipe_servings', '').strip()
    auto_title = form.get('recipe_title_auto') == '1'
    if not recipe:
        if meal_prep or meal_slot or raw_servings or auto_title:
            abort(400, description='Meal prep events must link to one visible recipe.')
        return {'recipe_id': None, 'recipe_servings': None, 'meal_prep': 0,
                'meal_slot': None, 'recipe_title_auto': 0}
    if meal_prep and not recipe:
        abort(400, description='Meal prep events must link to one visible recipe.')
    try:
        servings = int(raw_servings) if raw_servings else int(recipe['default_servings'])
    except ValueError:
        abort(400, description='Recipe servings must be from 1 to 99.')
    if not 1 <= servings <= 99:
        abort(400, description='Recipe servings must be from 1 to 99.')
    if auto_title:
        if recipe['visibility'] == 'private':
            values['title'] = 'Meal prep'
        elif values['title'] != recipe['title']:
            auto_title = False
    return {
        'recipe_id': recipe['id'], 'recipe_servings': servings,
        'meal_prep': int(meal_prep), 'meal_slot': meal_slot,
        'recipe_title_auto': int(auto_title),
    }

def enforce_event_budget(db, user_id, occurrence_dates, *, exclude_event_id=None,
                         exclude_series_id=None):
    """Keep each user's proposed occurrence set within the daily/monthly caps."""
    from collections import Counter

    if not occurrence_dates:
        return
    daily_limit = setting(db, 'calendar_events_per_day', 8)
    monthly_limit = setting(db, 'calendar_events_per_month', 128)
    required_days = Counter(occurrence_dates)
    required_months = Counter(day[:7] for day in occurrence_dates)
    first_day, last_day = min(occurrence_dates), max(occurrence_dates)
    existing_days = Counter(dict(db.execute(
        'SELECT event_date,COUNT(*) FROM events WHERE created_by=? '
        'AND event_date>=? AND event_date<=? GROUP BY event_date',
        (user_id, first_day, last_day),
    ).fetchall()))
    first_month, last_month = first_day[:7], last_day[:7]
    existing_months = Counter(dict(db.execute(
        'SELECT substr(event_date,1,7),COUNT(*) FROM events WHERE created_by=? '
        'AND substr(event_date,1,7)>=? AND substr(event_date,1,7)<=? '
        'GROUP BY substr(event_date,1,7)',
        (user_id, first_month, last_month),
    ).fetchall()))
    if exclude_series_id is not None:
        excluded_dates = [row['event_date'] for row in db.execute(
            'SELECT event_date FROM events WHERE created_by=? AND series_id=?',
            (user_id, exclude_series_id),
        ).fetchall()]
    elif exclude_event_id is not None:
        excluded_dates = [row['event_date'] for row in db.execute(
            'SELECT event_date FROM events WHERE created_by=? AND id=?',
            (user_id, exclude_event_id),
        ).fetchall()]
    else:
        excluded_dates = []
    excluded_days = Counter(excluded_dates)
    excluded_months = Counter(day[:7] for day in excluded_dates)
    for occurrence_date, proposed in required_days.items():
        if existing_days[occurrence_date] - excluded_days[occurrence_date] + proposed > daily_limit:
            abort(400, description=f'You have reached your limit of {daily_limit} events for that day.')
    for occurrence_month, proposed in required_months.items():
        if existing_months[occurrence_month] - excluded_months[occurrence_month] + proposed > monthly_limit:
            abort(400, description=f'You have reached your limit of {monthly_limit} events for that month.')

def event_time_label(value):
    if not value:
        return ''
    return datetime.strptime(value, '%H:%M').strftime('%I:%M %p').lstrip('0')

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

def recipe_sharing_values(db, form, owner_id):
    visibility = form.get('visibility', 'shared')
    if visibility not in ('shared', 'private'):
        abort(400)
    user_ids = set()
    if visibility == 'private':
        for raw_id in form.getlist('allowed_user_ids'):
            try:
                user_id = int(raw_id)
            except ValueError:
                abort(400)
            user = db.execute(
                "SELECT id FROM users WHERE id=? AND active=1 AND account_kind='person'",
                (user_id,)
            ).fetchone()
            if not user or user_id == owner_id:
                abort(400)
            user_ids.add(user_id)
    return visibility, sorted(user_ids)

def recipe_notes_losing_access(db, recipe_id, owner_id, visibility, allowed_ids):
    if visibility == 'shared':
        return []
    retained_ids = {owner_id, *allowed_ids}
    notes = db.execute(
        'SELECT recipe_notes.user_id,users.display_name,users.username FROM recipe_notes '
        'JOIN users ON users.id=recipe_notes.user_id WHERE recipe_notes.recipe_id=?',
        (recipe_id,)
    ).fetchall()
    return [note for note in notes if note['user_id'] not in retained_ids]

def apply_recipe_sharing(db, recipe_id, visibility, allowed_ids, losing_note_users=()):
    db.execute('UPDATE recipes SET visibility=? WHERE id=?', (visibility, recipe_id))
    db.execute('DELETE FROM recipe_access WHERE recipe_id=?', (recipe_id,))
    if visibility == 'private':
        db.executemany('INSERT INTO recipe_access(recipe_id,user_id) VALUES(?,?)',
                       [(recipe_id, user_id) for user_id in allowed_ids])
    db.executemany('DELETE FROM recipe_notes WHERE recipe_id=? AND user_id=?',
                   [(recipe_id, row['user_id']) for row in losing_note_users])

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
            supplied = request.form.get('_csrf', '') or request.headers.get('X-CSRF-Token', '')
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
        try:
            calendar_month = int(request.args.get('month', today.month))
            calendar_year = int(request.args.get('year', today.year))
        except ValueError:
            abort(400)
        retention = db.execute(
            "SELECT value FROM app_settings WHERE key='calendar_retention_years'"
        ).fetchone()
        previous_years = int(retention['value']) if retention else 3
        minimum_calendar_year = max(1, today.year - previous_years)
        maximum_calendar_year = today.year + 10
        if (not 1 <= calendar_month <= 12
                or not minimum_calendar_year <= calendar_year <= maximum_calendar_year):
            abort(400)
        calendar_year_options = range(minimum_calendar_year, maximum_calendar_year + 1)
        if calendar_month == 1:
            previous_month, previous_year = 12, calendar_year - 1
        else:
            previous_month, previous_year = calendar_month - 1, calendar_year
        if calendar_month == 12:
            next_month, next_year = 1, calendar_year + 1
        else:
            next_month, next_year = calendar_month + 1, calendar_year
        calendar_has_previous = previous_year >= minimum_calendar_year
        calendar_has_next = next_year <= maximum_calendar_year
        calendar_start = date(calendar_year, calendar_month, 1)
        calendar_end = date(calendar_year + (calendar_month == 12),
                            calendar_month % 12 + 1, 1)
        month_events = [dict(row) for row in db.execute(
            'SELECT id,title,event_date,recipe_id,created_by,description,all_day,start_time,end_time,visibility '
            'FROM events '
            'WHERE event_date>=? AND event_date<? ORDER BY event_date,id',
            (calendar_start.isoformat(), calendar_end.isoformat())
        ).fetchall()]
        viewer_participant_ids = {row['event_id'] for row in db.execute(
            'SELECT event_id FROM event_participants WHERE user_id=?', (g.user['id'],)
        ).fetchall()}
        for event in month_events:
            event['can_view_details'] = (
                event['visibility'] == 'shared' or g.user['role'] == 'admin'
                or event['created_by'] == g.user['id']
                or event['id'] in viewer_participant_ids
            )
            if not event['can_view_details']:
                event['title'] = 'Busy'
                event['description'] = ''
                event['recipe_id'] = None
            event['display_start_time'] = event_time_label(event['start_time'])
        weekend_start = (today if today.weekday() >= 4 else
                         today + timedelta(days=4 - today.weekday()))
        weekend_end = weekend_start + timedelta(days=6 - weekend_start.weekday())
        just_my_events = request.args.get('just_my_events') == '1'
        weekend_rows = db.execute(
            'SELECT events.id,events.title,events.event_date,events.created_by,events.description,'
            'events.all_day,events.start_time,events.end_time,events.visibility,'
            'users.id AS participant_id,users.display_name AS participant_name '
            'FROM events LEFT JOIN event_participants ON event_participants.event_id=events.id '
            'LEFT JOIN users ON users.id=event_participants.user_id '
            'WHERE events.event_date>=? AND events.event_date<=? '
            'ORDER BY events.event_date,events.all_day DESC,events.start_time,events.id,'
            'users.display_name COLLATE NOCASE,users.id',
            (weekend_start.isoformat(), weekend_end.isoformat()),
        ).fetchall()
        weekend_by_id = {}
        for row in weekend_rows:
            event = weekend_by_id.setdefault(row['id'], {
                key: row[key] for key in ('id', 'title', 'event_date', 'created_by',
                                          'description', 'all_day', 'start_time',
                                          'end_time', 'visibility')
            } | {'participants': []})
            if row['participant_id'] is not None:
                event['participants'].append({
                    'id': row['participant_id'], 'display_name': row['participant_name'],
                })
        weekend_groups = []
        for event in weekend_by_id.values():
            participants = event['participants']
            participant_ids = {person['id'] for person in participants}
            is_mine = event['created_by'] == g.user['id'] or g.user['id'] in participant_ids
            if just_my_events and not is_mine:
                continue
            event['can_view_details'] = (
                event['visibility'] == 'shared' or g.user['role'] == 'admin' or is_mine
            )
            if not event['can_view_details']:
                event['title'] = 'Busy'
                event['description'] = ''
                participants = []
            event['participants'] = participants
            event['display_start_time'] = event_time_label(event['start_time'])
            event['display_end_time'] = event_time_label(event['end_time'])
            event_day = date.fromisoformat(event['event_date'])
            event['day_label'] = f'{event_day.strftime("%A, %B")} {event_day.day}'
            if not weekend_groups or weekend_groups[-1]['date'] != event['event_date']:
                weekend_groups.append({'date': event['event_date'],
                                      'label': event['day_label'], 'events': []})
            weekend_groups[-1]['events'].append(event)
        events_by_date = {}
        for event in month_events:
            events_by_date.setdefault(event['event_date'], []).append(event)
        calendar_weeks = []
        for week in pycalendar.Calendar(firstweekday=6).monthdayscalendar(
                calendar_year, calendar_month):
            calendar_weeks.append([
                None if day == 0 else {
                    'day': day,
                    'date': date(calendar_year, calendar_month, day).isoformat(),
                    'is_today': (calendar_year, calendar_month, day)
                    == (today.year, today.month, today.day),
                    'events': events_by_date.get(
                        date(calendar_year, calendar_month, day).isoformat(), []
                    ),
                }
                for day in week
            ])
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
        return render_template('index.html', recipes=visible_recipes(db, g.user)[:6],
          events=month_events, calendar_weeks=calendar_weeks,
          calendar_month=calendar_month, calendar_year=calendar_year,
          calendar_month_name=pycalendar.month_name[calendar_month],
          months=pycalendar.month_name,
          calendar_days=('Sunday', 'Monday', 'Tuesday', 'Wednesday',
                         'Thursday', 'Friday', 'Saturday'),
          calendar_year_options=calendar_year_options,
          calendar_has_previous=calendar_has_previous,
          calendar_has_next=calendar_has_next,
          calendar_previous_url=url_for('index', month=previous_month, year=previous_year),
          calendar_next_url=url_for('index', month=next_month, year=next_year),
          calendar_today_url=url_for('index', month=today.month, year=today.year),
          items=items, lists=lists, selected_list=selected_list, list_kind=list_kind,
          list_users=list_users, todo_assignees=todo_assignees,
          shopping_categories=SHOPPING_CATEGORIES,
          today=today.isoformat(), weekdays=weekdays,
          selected_weekday=selected_weekday, chore_filter=chore_filter,
          chore_groups=chore_groups, home_weekday=home_weekday,
          home_chore_groups=home_chore_groups, just_my_chores=just_my_chores,
          weekend_groups=weekend_groups, just_my_events=just_my_events)

    @app.get('/offline.html')
    def offline_shell():
        response = send_from_directory(app.static_folder, 'offline.html')
        response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
        return response

    @app.get('/service-worker.js')
    def service_worker():
        response = send_from_directory(app.static_folder, 'service-worker.js',
                                       mimetype='application/javascript')
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'no-cache'
        return response

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
                               events_per_day=setting(db, 'calendar_events_per_day', 8),
                               events_per_month=setting(db, 'calendar_events_per_month', 128),
                               backup_folder=str(configured_folder),
                               backup_folder_available=backup_folder_available,
                               backup_frequency=backup_frequency,
                               backup_retention=backup_retention,
                               backups=backups)

    @app.post('/admin/calendar/limits')
    @admin_required
    def update_calendar_limits():
        db = get_db()
        try:
            events_per_day = int(request.form.get('events_per_day', ''))
            events_per_month = int(request.form.get('events_per_month', ''))
        except ValueError:
            abort(400, description='Enter valid daily and monthly event limits.')
        if events_per_day < 1 or not 7 <= events_per_month <= 512:
            abort(400, description='Daily event limit must be at least 1; monthly limit must be from 7 to 512.')
        with db:
            db.executemany('INSERT INTO app_settings(key,value) VALUES(?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (('calendar_events_per_day', str(events_per_day)),
                            ('calendar_events_per_month', str(events_per_month))))
        flash('Calendar limits updated.')
        return redirect(url_for('accounts') + '#calendar-limits-section')

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
            db.execute("UPDATE shopping_items SET done=1-done,updated_at="
                       "strftime('%Y-%m-%d %H:%M:%f','now') "
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

    @app.get('/shopping/sync')
    @login_required
    def shopping_sync_snapshot():
        db = get_db()
        target = visible_list(db, g.user, request.args.get('list_id'))
        if target['kind'] != 'shopping':
            abort(404)
        items = db.execute(
            'SELECT id,text,done,updated_at FROM shopping_items WHERE list_id=? ORDER BY id',
            (target['id'],)
        ).fetchall()
        response = jsonify({
            'list': {'id': target['id'], 'title': target['title']},
            'items': [{'id': row['id'], 'text': row['text'], 'done': bool(row['done']),
                       'updated_at': row['updated_at']} for row in items],
        })
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/shopping/sync')
    @login_required
    def shopping_sync_apply():
        if g.user['account_kind'] == 'house':
            abort(403)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        list_id = payload.get('list_id')
        changes = payload.get('changes')
        if (not isinstance(list_id, int) or isinstance(list_id, bool)
                or not isinstance(changes, list) or len(changes) > 500):
            abort(400)
        target = visible_list(get_db(), g.user, list_id)
        if target['kind'] != 'shopping':
            abort(404)
        ids = set()
        for change in changes:
            if not isinstance(change, dict):
                abort(400)
            item_id = change.get('id')
            base_updated_at = change.get('base_updated_at')
            if (not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 1
                    or item_id in ids or not isinstance(change.get('done'), bool)
                    or not isinstance(change.get('base_done'), bool)
                    or (base_updated_at is not None and
                        (not isinstance(base_updated_at, str) or len(base_updated_at) > 64))):
                abort(400)
            ids.add(item_id)

        db = get_db()
        results = []
        with db:
            db.execute('BEGIN IMMEDIATE')
            for change in changes:
                item = db.execute(
                    'SELECT id,done,updated_at FROM shopping_items WHERE id=? AND list_id=?',
                    (change['id'], target['id'])
                ).fetchone()
                if not item:
                    results.append({'id': change['id'], 'status': 'deleted'})
                    continue
                requested = change['done']
                current = bool(item['done'])
                if ((item['updated_at'] != change['base_updated_at']
                     or current != change['base_done']) and current != requested):
                    results.append({'id': item['id'], 'status': 'conflict',
                                    'server_done': current, 'requested_done': requested})
                    continue
                if current == requested:
                    results.append({'id': item['id'], 'status': 'unchanged', 'done': current})
                    continue
                db.execute("UPDATE shopping_items SET done=?,updated_at="
                           "strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",
                           (int(requested), item['id']))
                results.append({'id': item['id'], 'status': 'updated', 'done': requested})
            items = db.execute(
                'SELECT id,text,done,updated_at FROM shopping_items WHERE list_id=? ORDER BY id',
                (target['id'],)
            ).fetchall()
        response = jsonify({
            'results': results,
            'items': [{'id': row['id'], 'text': row['text'], 'done': bool(row['done']),
                       'updated_at': row['updated_at']} for row in items],
        })
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/recipes')
    @login_required
    def add_recipe():
        values = recipe_form_values(request.form)
        title, description, servings, _, _ = values
        db = get_db()
        visibility, allowed_ids = recipe_sharing_values(db, request.form, g.user['id'])
        count = db.execute('SELECT COUNT(*) FROM recipes WHERE created_by=?',
                           (g.user['id'],)).fetchone()[0]
        if count >= 128:
            abort(409, description='Your recipe book is full (128 recipes).')
        with db:
            cursor = db.execute(
                'INSERT INTO recipes(title,description,default_servings,created_by,visibility) '
                'VALUES(?,?,?,?,?)', (title, description, servings, g.user['id'], visibility)
            )
            recipe_id = cursor.lastrowid
            save_recipe_content(db, recipe_id, values)
            if visibility == 'private':
                db.executemany('INSERT INTO recipe_access(recipe_id,user_id) VALUES(?,?)',
                               [(recipe_id, user_id) for user_id in allowed_ids])
        return redirect(url_for('index')+'#recipes')

    @app.get('/recipes/<int:recipe_id>')
    @login_required
    def recipe_detail(recipe_id):
        db = get_db()
        recipe = visible_recipe(db, g.user, recipe_id)
        raw_servings = request.args.get('servings')
        if raw_servings is None:
            selected_servings = recipe['default_servings']
        elif not re.fullmatch(r'[0-9]+', raw_servings):
            abort(400, description='Servings must be a whole number from 1 to 99.')
        else:
            selected_servings = int(raw_servings)
            if not RECIPE_SERVING_MIN <= selected_servings <= RECIPE_SERVING_MAX:
                abort(400, description='Servings must be a whole number from 1 to 99.')
        ingredient_rows = db.execute(
            'SELECT quantity,unit,name,note FROM recipe_ingredients WHERE recipe_id=? ORDER BY position',
            (recipe_id,)
        ).fetchall()
        ingredients = []
        has_unscaled_quantities = False
        for row in ingredient_rows:
            ingredient = dict(row)
            ingredient['scaled_quantity'], scalable = scale_recipe_quantity(
                row['quantity'], recipe['default_servings'], selected_servings
            )
            if (selected_servings != recipe['default_servings'] and row['quantity']
                    and not scalable):
                has_unscaled_quantities = True
            ingredients.append(ingredient)
        steps = db.execute(
            'SELECT text FROM recipe_steps WHERE recipe_id=? ORDER BY position', (recipe_id,)
        ).fetchall()
        legacy_ingredients = not ingredients and bool(recipe['ingredients'])
        legacy_steps = not steps and bool(recipe['instructions'])
        notes = db.execute(
            'SELECT recipe_notes.*,users.display_name,users.username FROM recipe_notes '
            'JOIN users ON users.id=recipe_notes.user_id WHERE recipe_notes.recipe_id=? '
            'ORDER BY users.display_name COLLATE NOCASE,users.username COLLATE NOCASE',
            (recipe_id,)
        ).fetchall()
        shopping_lists = visible_lists(db, g.user, 'shopping')
        return render_template('recipe.html', recipe=recipe, ingredients=ingredients,
                               selected_servings=selected_servings,
                               shopping_lists=shopping_lists,
                               has_unscaled_quantities=has_unscaled_quantities,
                               steps=steps, legacy_ingredients=legacy_ingredients,
                               legacy_steps=legacy_steps,
                               notes=notes,
                               can_edit=recipe['created_by'] == g.user['id'],
                               can_delete=(recipe['created_by'] == g.user['id']
                                           or g.user['role'] == 'admin'))

    @app.post('/recipes/<int:recipe_id>/shopping')
    @login_required
    def add_recipe_to_shopping(recipe_id):
        if g.user['account_kind'] == 'house':
            abort(403)
        db = get_db()
        recipe = visible_recipe(db, g.user, recipe_id)
        raw_servings = request.form.get('servings', str(recipe['default_servings']))
        if not re.fullmatch(r'[0-9]+', raw_servings):
            abort(400, description='Servings must be a whole number from 1 to 99.')
        servings = int(raw_servings)
        if not RECIPE_SERVING_MIN <= servings <= RECIPE_SERVING_MAX:
            abort(400, description='Servings must be a whole number from 1 to 99.')
        target = visible_list(db, g.user, request.form.get('list_id'))
        if target['kind'] != 'shopping':
            abort(404)
        ingredients = db.execute(
            'SELECT quantity,unit,name,note FROM recipe_ingredients '
            'WHERE recipe_id=? ORDER BY position', (recipe_id,)
        ).fetchall()
        if ingredients:
            snapshots = recipe_item_snapshots(
                ingredients, recipe['default_servings'], servings
            )
        else:
            snapshots = [line.strip() for line in recipe['ingredients'].splitlines()
                         if line.strip()]
        if not snapshots or any(len(text) > 240 for text in snapshots):
            abort(400, description='Recipe ingredients must each fit within 240 characters to copy.')
        with db:
            db.executemany(
                'INSERT INTO shopping_items(text,created_by,list_id,updated_at) '
                'VALUES(?,?,?,CURRENT_TIMESTAMP)',
                ((text, g.user['id'], target['id']) for text in snapshots),
            )
        flash(f'{len(snapshots)} ingredient items copied to {target["title"]}.')
        return redirect(url_for('recipe_detail', recipe_id=recipe_id, servings=servings))

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
            visibility, allowed_ids = recipe_sharing_values(db, request.form, g.user['id'])
            losing_notes = recipe_notes_losing_access(
                db, recipe_id, g.user['id'], visibility, allowed_ids
            )
            if losing_notes and request.form.get('confirm_note_removal') != 'yes':
                return render_template('confirm_recipe_sharing.html', recipe=recipe,
                                       fields=request.form, losing_notes=losing_notes)
            with db:
                save_recipe_content(db, recipe_id, values)
                apply_recipe_sharing(db, recipe_id, visibility, allowed_ids, losing_notes)
                if recipe['visibility'] != 'private' and visibility == 'private':
                    db.execute(
                        "UPDATE events SET title='Meal prep' WHERE recipe_id=? AND recipe_title_auto=1",
                        (recipe_id,),
                    )
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
        access_ids = {row['user_id'] for row in db.execute(
            'SELECT user_id FROM recipe_access WHERE recipe_id=?', (recipe_id,)
        ).fetchall()}
        sharing_users = db.execute(
            "SELECT id,display_name,username FROM users WHERE active=1 AND account_kind='person' "
            'AND id!=? ORDER BY display_name COLLATE NOCASE,username COLLATE NOCASE',
            (recipe['created_by'],)
        ).fetchall()
        return render_template('edit_recipe.html', recipe=recipe,
                               ingredients=ingredients or [{'quantity': '', 'unit': '', 'name': '', 'note': ''}],
                               steps=steps or [{'text': ''}], access_ids=access_ids,
                               sharing_users=sharing_users)

    @app.post('/recipes/<int:recipe_id>/notes')
    @login_required
    def save_recipe_note(recipe_id):
        if g.user['account_kind'] == 'house':
            abort(403)
        db = get_db()
        visible_recipe(db, g.user, recipe_id)
        note = request.form.get('text', '').strip()
        if not note or len(note) > 128:
            abort(400)
        with db:
            db.execute(
                'INSERT INTO recipe_notes(recipe_id,user_id,note) VALUES(?,?,?) '
                'ON CONFLICT(recipe_id,user_id) DO UPDATE SET note=excluded.note,updated_at=CURRENT_TIMESTAMP',
                (recipe_id, g.user['id'], note)
            )
        return redirect(url_for('recipe_detail', recipe_id=recipe_id))

    @app.post('/recipes/<int:recipe_id>/notes/<int:note_id>/delete')
    @login_required
    def delete_recipe_note(recipe_id, note_id):
        db = get_db()
        visible_recipe(db, g.user, recipe_id)
        note = db.execute('SELECT user_id FROM recipe_notes WHERE id=? AND recipe_id=?',
                          (note_id, recipe_id)).fetchone()
        if not note:
            abort(404)
        if note['user_id'] != g.user['id']:
            abort(403)
        with db:
            db.execute('DELETE FROM recipe_notes WHERE id=?', (note_id,))
        return redirect(url_for('recipe_detail', recipe_id=recipe_id))

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

    @app.get('/events/new')
    @login_required
    def new_event():
        if g.user['account_kind'] == 'house':
            abort(403)
        db = get_db()
        recipes = visible_recipes(db, g.user)
        recipe = None
        recipe_id = request.args.get('recipe_id', '').strip()
        if recipe_id:
            recipe = visible_recipe(db, g.user, recipe_id)
        meal_prep = request.args.get('meal_prep') == '1'
        if meal_prep and not recipe:
            abort(400, description='Meal prep events must link to one visible recipe.')
        try:
            servings = (int(request.args.get('servings', recipe['default_servings']))
                        if recipe else None)
        except ValueError:
            abort(400, description='Recipe servings must be from 1 to 99.')
        if servings is not None and not 1 <= servings <= 99:
            abort(400, description='Recipe servings must be from 1 to 99.')
        meal_slot = request.args.get('meal_slot', '')
        if meal_slot not in ('', 'breakfast', 'lunch', 'dinner'):
            abort(400, description='Choose breakfast, lunch, or dinner.')
        default_title = ('Meal prep' if recipe and recipe['visibility'] == 'private'
                         else recipe['title'] if recipe else '')
        event = {'title': '', 'event_date': household_today().isoformat(),
                 'description': '', 'all_day': 1, 'start_time': '', 'end_time': '',
                 'visibility': 'shared', 'recurrence_frequency': '', 'recurrence_until': '',
                 'recipe_id': recipe['id'] if recipe else None,
                 'recipe_servings': servings, 'meal_prep': int(meal_prep),
                 'meal_slot': meal_slot or None,
                 'recipe_title_auto': int(bool(recipe))}
        if default_title:
            event['title'] = default_title
        users = db.execute(
            'SELECT id,display_name,username,account_kind FROM users '
            'WHERE active=1 ORDER BY account_kind,display_name COLLATE NOCASE,id'
        ).fetchall()
        return render_template('event_form.html', event=event, editing=False,
                               users=users, selected_participant_ids=[], recipes=recipes)

    @app.post('/events')
    @login_required
    def add_event():
        if g.user['account_kind'] == 'house':
            abort(403)
        values = event_form_values(request.form)
        db = get_db()
        recipe_values = event_recipe_values(db, g.user, request.form, values)
        visibility, participant_ids = event_access_values(db, request.form)
        frequency, until = recurrence_values(request.form, values['event_date'])
        occurrence_dates = (recurrence_dates(values['event_date'], frequency, until)
                            if frequency else [values['event_date']])
        with db:
            db.execute('BEGIN IMMEDIATE')
            enforce_event_budget(db, g.user['id'], occurrence_dates)
            first_id = None
            occurrence_ids = []
            for occurrence_date in occurrence_dates:
                cursor = db.execute(
                    'INSERT INTO events(title,event_date,created_by,description,all_day,start_time,end_time,'
                    'series_id,recurrence_frequency,recurrence_until,visibility,recipe_servings,meal_prep,meal_slot,'
                    'recipe_title_auto,recipe_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (values['title'], occurrence_date, g.user['id'], values['description'],
                     values['all_day'], values['start_time'], values['end_time'], None,
                     frequency, until, visibility, recipe_values['recipe_servings'],
                     recipe_values['meal_prep'], recipe_values['meal_slot'],
                     recipe_values['recipe_title_auto'], recipe_values['recipe_id']),
                )
                occurrence_ids.append(cursor.lastrowid)
                set_event_participants(db, cursor.lastrowid, participant_ids)
                if first_id is None:
                    first_id = cursor.lastrowid
            if frequency:
                db.executemany('UPDATE events SET series_id=? WHERE id=?',
                               [(first_id, occurrence_id) for occurrence_id in occurrence_ids])
        return redirect(url_for('event_detail', event_id=first_id))

    @app.get('/events/<int:event_id>')
    @login_required
    def event_detail(event_id):
        db = get_db()
        event = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if not event:
            abort(404)
        can_edit = (event['created_by'] == g.user['id'] or g.user['role'] == 'admin')
        can_view_details = (
            event['visibility'] == 'shared' or can_edit or db.execute(
                'SELECT 1 FROM event_participants WHERE event_id=? AND user_id=?',
                (event_id, g.user['id']),
            ).fetchone() is not None
        )
        participants = db.execute(
            'SELECT users.id,users.display_name,users.account_kind FROM event_participants '
            'JOIN users ON users.id=event_participants.user_id '
            'WHERE event_participants.event_id=? ORDER BY users.display_name COLLATE NOCASE,users.id',
            (event_id,),
        ).fetchall() if can_view_details else []
        linked_recipe = db.execute('SELECT * FROM recipes WHERE id=?',
                                   (event['recipe_id'],)).fetchone() if (
                                       can_view_details and event['recipe_id']) else None
        recipe_is_visible = can_view_recipe(db, g.user, linked_recipe)
        return render_template(
            'event_detail.html', event=event, can_edit=can_edit,
            can_view_details=can_view_details, participants=participants,
            linked_recipe=linked_recipe if recipe_is_visible else None,
            recipe_label=(linked_recipe['title'] if recipe_is_visible else
                          'Private recipe' if linked_recipe else None),
            recipe_url=(url_for('recipe_detail', recipe_id=linked_recipe['id'],
                                servings=event['recipe_servings'] or linked_recipe['default_servings'])
                        if recipe_is_visible else None),
            recurring=bool(event['series_id'] and event['recurrence_frequency']),
            start_label=event_time_label(event['start_time']),
            end_label=event_time_label(event['end_time']),
        )

    @app.route('/events/<int:event_id>/edit', methods=['GET', 'POST'])
    @login_required
    def edit_event(event_id):
        db = get_db()
        event = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if not event:
            abort(404)
        if event['created_by'] != g.user['id'] and g.user['role'] != 'admin':
            abort(403)
        if request.method == 'GET':
            users = db.execute(
                'SELECT id,display_name,username,account_kind FROM users '
                'WHERE active=1 ORDER BY account_kind,display_name COLLATE NOCASE,id'
            ).fetchall()
            selected_participant_ids = [row['user_id'] for row in db.execute(
                'SELECT user_id FROM event_participants WHERE event_id=?', (event_id,)
            ).fetchall()]
            recipes = visible_recipes(db, g.user)
            return render_template('event_form.html', event=event, editing=True,
                                   users=users, selected_participant_ids=selected_participant_ids,
                                   recipes=recipes)
        values = event_form_values(request.form)
        recipe_values = event_recipe_values(
            db, g.user, request.form, values, existing_recipe_id=event['recipe_id']
        )
        visibility, participant_ids = event_access_values(db, request.form)
        edit_scope = request.form.get('edit_scope', 'series' if event['series_id'] else 'occurrence')
        if edit_scope not in ('occurrence', 'series'):
            abort(400)
        with db:
            db.execute('BEGIN IMMEDIATE')
            group_id = event['series_id'] or event['id']
            if edit_scope == 'series' and event['series_id']:
                frequency, until = recurrence_values(request.form, values['event_date'])
                occurrence_dates = (recurrence_dates(values['event_date'], frequency, until)
                                    if frequency else [values['event_date']])
                existing = db.execute(
                    'SELECT id FROM events WHERE series_id=? ORDER BY event_date,id',
                    (group_id,),
                ).fetchall()
                enforce_event_budget(db, event['created_by'], occurrence_dates,
                                     exclude_series_id=group_id)
                for index, occurrence_date in enumerate(occurrence_dates):
                    columns = (values['title'], occurrence_date, values['description'],
                               values['all_day'], values['start_time'], values['end_time'],
                               group_id if frequency else None, frequency, until, visibility,
                               recipe_values['recipe_servings'], recipe_values['meal_prep'],
                               recipe_values['meal_slot'], recipe_values['recipe_title_auto'],
                               recipe_values['recipe_id'])
                    if index < len(existing):
                        db.execute(
                            'UPDATE events SET title=?,event_date=?,description=?,all_day=?,start_time=?,end_time=?,'
                            'series_id=?,recurrence_frequency=?,recurrence_until=?,visibility=?,'
                            'recipe_servings=?,meal_prep=?,meal_slot=?,recipe_title_auto=?,recipe_id=? WHERE id=?',
                            (*columns, existing[index]['id']),
                        )
                        set_event_participants(db, existing[index]['id'], participant_ids)
                    else:
                        db.execute(
                            'INSERT INTO events(title,event_date,created_by,description,all_day,start_time,end_time,'
                            'series_id,recurrence_frequency,recurrence_until,visibility,recipe_servings,meal_prep,'
                            'meal_slot,recipe_title_auto,recipe_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (values['title'], occurrence_date, event['created_by'], values['description'],
                             values['all_day'], values['start_time'], values['end_time'],
                             group_id if frequency else None, frequency, until, visibility,
                             recipe_values['recipe_servings'], recipe_values['meal_prep'],
                             recipe_values['meal_slot'], recipe_values['recipe_title_auto'],
                             recipe_values['recipe_id']),
                        )
                        new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                        set_event_participants(db, new_id, participant_ids)
                for row in existing[len(occurrence_dates):]:
                    db.execute('DELETE FROM events WHERE id=?', (row['id'],))
                redirect_id = existing[0]['id']
            else:
                enforce_event_budget(db, event['created_by'], [values['event_date']],
                                     exclude_event_id=event_id)
                db.execute(
                    'UPDATE events SET title=?,event_date=?,description=?,all_day=?,start_time=?,end_time=?,'
                    'series_id=NULL,recurrence_frequency=NULL,recurrence_until=NULL,visibility=?,'
                    'recipe_servings=?,meal_prep=?,meal_slot=?,recipe_title_auto=?,recipe_id=? WHERE id=?',
                    (values['title'], values['event_date'], values['description'], values['all_day'],
                     values['start_time'], values['end_time'], visibility,
                     recipe_values['recipe_servings'], recipe_values['meal_prep'],
                     recipe_values['meal_slot'], recipe_values['recipe_title_auto'],
                     recipe_values['recipe_id'], event_id),
                )
                set_event_participants(db, event_id, participant_ids)
                redirect_id = event_id
        return redirect(url_for('event_detail', event_id=redirect_id))

    @app.post('/events/<int:event_id>/delete')
    @login_required
    def delete_event(event_id):
        db = get_db()
        event = db.execute('SELECT id,created_by,series_id,recurrence_frequency FROM events WHERE id=?',
                           (event_id,)).fetchone()
        if not event:
            abort(404)
        if event['created_by'] != g.user['id'] and g.user['role'] != 'admin':
            abort(403)
        delete_scope = request.form.get(
            'delete_scope', 'series' if event['series_id'] else 'occurrence'
        )
        if delete_scope not in ('occurrence', 'series'):
            abort(400)
        with db:
            if delete_scope == 'series' and event['series_id']:
                db.execute('DELETE FROM events WHERE series_id=?', (event['series_id'],))
            else:
                db.execute('DELETE FROM events WHERE id=?', (event_id,))
        return redirect(url_for('index') + '#calendar')

    @app.get('/calendar.ics')
    @login_required
    def export_calendar():
        db = get_db()
        events = db.execute('SELECT * FROM events ORDER BY event_date,id').fetchall()
        participant_ids = {row['event_id'] for row in db.execute(
            'SELECT event_id FROM event_participants WHERE user_id=?', (g.user['id'],)
        ).fetchall()}
        only_required = request.args.get('only_required', '') == '1'
        lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//HomeHub//Local Calendar//EN','CALSCALE:GREGORIAN']
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        for ev in events:
            required = ev['created_by'] == g.user['id'] or ev['id'] in participant_ids
            if only_required and not required:
                continue
            can_view_details = (
                ev['visibility'] == 'shared' or g.user['role'] == 'admin' or required
            )
            lines.extend(['BEGIN:VEVENT', f'UID:homehub-{ev["id"]}@local', f'DTSTAMP:{stamp}'])
            if ev['all_day']:
                lines.append(f'DTSTART;VALUE=DATE:{ev["event_date"].replace("-", "")}')
            else:
                start_value = f'{ev["event_date"].replace("-", "")}T{ev["start_time"].replace(":", "")}00'
                lines.append(f'DTSTART:{start_value}')
                if ev['end_time']:
                    end_value = f'{ev["event_date"].replace("-", "")}T{ev["end_time"].replace(":", "")}00'
                    lines.append(f'DTEND:{end_value}')
            lines.append(f'SUMMARY:{safe_ics_text(ev["title"] if can_view_details else "Busy")}')
            if can_view_details and ev['description']:
                lines.append(f'DESCRIPTION:{safe_ics_text(ev["description"])}')
            lines.append('END:VEVENT')
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
