"""Bounded, read-only database tools for an individual Chat requester."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from . import constants as limits


class ChatToolError(ValueError):
    """A tool name or argument is outside the supported contract."""


_TOOL_DEFINITIONS = [
    {
        'type': 'function',
        'function': {
            'name': 'get_household_lists',
            'description': 'Read shopping lists, to-do lists, and chores visible to this signed-in user.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_household_recipes',
            'description': 'Read recipes, ingredients, steps, and notes visible to this signed-in user.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_weather_forecast',
            'description': 'Read the cached household weather forecast without making a network request.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_saved_articles',
            'description': 'Read saved household articles and this signed-in user’s personal-feed articles.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_system_status',
            'description': 'Read the latest system performance sample if household visibility allows it.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_admin_overview',
            'description': 'For admins only: read a small overview of household configuration, without secrets.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_calendar_events',
            'description': 'Read events visible to this signed-in user in a date range.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start_date': {'type': 'string', 'format': 'date'},
                    'end_date': {'type': 'string', 'format': 'date'},
                },
                'required': ['start_date', 'end_date'],
                'additionalProperties': False,
            },
        },
    },
]


def _serialized_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


class ChatDataTools:
    """Expose only permission-filtered reads through a SQLite read-only handle."""

    def __init__(self, database_path, user_id):
        self.database_path = Path(database_path)
        self.user_id = int(user_id)

    def definitions(self):
        return json.loads(json.dumps(_TOOL_DEFINITIONS))

    def call(self, name, arguments):
        if not isinstance(name, str) or name not in {
                'get_household_lists', 'get_calendar_events', 'get_household_recipes',
                'get_weather_forecast', 'get_saved_articles', 'get_system_status',
                'get_admin_overview'}:
            raise ChatToolError('That read-only tool is not available.')
        if not isinstance(arguments, dict):
            raise ChatToolError('Tool arguments must be an object.')
        uri = self.database_path.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            requester = db.execute(
                'SELECT id,role,account_kind FROM users WHERE id=? AND active=1',
                (self.user_id,),
            ).fetchone()
            if requester is None or requester['account_kind'] == 'house':
                raise ChatToolError('This account cannot use Chat data tools.')
            if name == 'get_admin_overview' and requester['role'] != 'admin':
                raise ChatToolError('This read-only overview is available to administrators only.')
            if name != 'get_calendar_events' and arguments:
                raise ChatToolError('This tool does not accept arguments.')
            if name == 'get_household_lists':
                return self._lists(db, requester)
            if name == 'get_calendar_events':
                return self._calendar(db, requester, arguments)
            if name == 'get_household_recipes':
                return self._recipes(db, requester)
            if name == 'get_weather_forecast':
                return self._weather(db)
            if name == 'get_saved_articles':
                return self._articles(db, requester)
            if name == 'get_system_status':
                return self._system_status(db, requester)
            return self._admin_overview(db)

    def _lists(self, db, requester):
        if requester['role'] == 'admin':
            lists = db.execute(
                'SELECT id,title,kind,category,visibility FROM household_lists '
                'ORDER BY is_default DESC,created_at,id LIMIT ?',
                (limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM + 1,),
            ).fetchall()
        else:
            lists = db.execute(
                'SELECT l.id,l.title,l.kind,l.category,l.visibility FROM household_lists l '
                'WHERE l.visibility=? OR l.created_by=? OR EXISTS '
                '(SELECT 1 FROM list_access a WHERE a.list_id=l.id AND a.user_id=?) '
                'ORDER BY l.is_default DESC,l.created_at,l.id LIMIT ?',
                ('shared', requester['id'], requester['id'],
                 limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM + 1),
            ).fetchall()
        result = {'lists': [], 'chores': [], 'row_count': 0, 'truncated': False}
        if len(lists) > limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM:
            result['truncated'] = True
            lists = lists[:limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM]
        list_ids = []
        records_by_id = {}
        for list_row in lists:
            record = {
                'title': list_row['title'][:limits.LIST_TITLE_MAXIMUM],
                'kind': list_row['kind'], 'category': list_row['category'][:80],
                'visibility': list_row['visibility'], 'items': [],
            }
            result['lists'].append(record)
            result['row_count'] += 1
            if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
                result['lists'].pop()
                result['row_count'] -= 1
                result['truncated'] = True
                break
            list_ids.append(list_row['id'])
            records_by_id[list_row['id']] = record
        remaining = limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM - result['row_count']
        if list_ids and remaining:
            placeholders = ','.join('?' for _ in list_ids)
            items = db.execute(
                'SELECT list_id,text,done,due_date,assignee_id FROM shopping_items '
                f'WHERE list_id IN ({placeholders}) ORDER BY list_id,id LIMIT ?',
                [*list_ids, remaining + 1],
            ).fetchall()
            if len(items) > remaining:
                result['truncated'] = True
                items = items[:remaining]
            for item in items:
                records_by_id[item['list_id']]['items'].append({
                    'text': item['text'][:limits.LIST_ITEM_TEXT_MAXIMUM],
                    'done': bool(item['done']), 'due_date': item['due_date'],
                    'assigned_to_requester': item['assignee_id'] == requester['id'],
                })
                result['row_count'] += 1
                if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
                    records_by_id[item['list_id']]['items'].pop()
                    result['row_count'] -= 1
                    result['truncated'] = True
                    break
        remaining = limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM - result['row_count']
        if remaining:
            chores = db.execute(
                'SELECT c.weekday,c.text,c.assigned_everyone,u.display_name '
                'FROM chores c LEFT JOIN users u ON u.id=c.assignee_id '
                'ORDER BY c.weekday,lower(COALESCE(u.display_name,\'\')),c.id LIMIT ?',
                (remaining + 1,),
            ).fetchall()
            if len(chores) > remaining:
                result['truncated'] = True
                chores = chores[:remaining]
            for chore in chores:
                result['chores'].append({
                    'weekday': chore['weekday'], 'text': chore['text'][:limits.CHORE_TEXT_MAXIMUM],
                    'assigned_to': ('Everyone' if chore['assigned_everyone']
                                    else chore['display_name']),
                })
                result['row_count'] += 1
                if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
                    result['chores'].pop()
                    result['row_count'] -= 1
                    result['truncated'] = True
                    break
        return result

    def _recipes(self, db, requester):
        if requester['role'] == 'admin':
            recipes = db.execute(
                'SELECT id,title,description,default_servings,visibility FROM recipes '
                'ORDER BY title COLLATE NOCASE,id LIMIT ?',
                (limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM + 1,),
            ).fetchall()
        else:
            recipes = db.execute(
                'SELECT r.id,r.title,r.description,r.default_servings,r.visibility '
                'FROM recipes r WHERE r.visibility=? OR r.created_by=? OR EXISTS '
                '(SELECT 1 FROM recipe_access a WHERE a.recipe_id=r.id AND a.user_id=?) '
                'ORDER BY r.title COLLATE NOCASE,r.id LIMIT ?',
                ('shared', requester['id'], requester['id'],
                 limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM + 1),
            ).fetchall()
        result = {'recipes': [], 'row_count': 0, 'truncated': False}
        if len(recipes) > limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM:
            result['truncated'] = True
            recipes = recipes[:limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM]
        for recipe in recipes:
            remaining = limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM - result['row_count'] - 1
            if remaining < 0:
                result['truncated'] = True
                break
            ingredients = db.execute(
                'SELECT quantity,unit,name,note FROM recipe_ingredients '
                'WHERE recipe_id=? ORDER BY position,id LIMIT ?',
                (recipe['id'], remaining + 1),
            ).fetchall()
            if len(ingredients) > remaining:
                ingredients = ingredients[:remaining]
                result['truncated'] = True
            remaining -= len(ingredients)
            steps = db.execute(
                'SELECT text FROM recipe_steps WHERE recipe_id=? '
                'ORDER BY position,id LIMIT ?', (recipe['id'], remaining + 1),
            ).fetchall()
            if len(steps) > remaining:
                steps = steps[:remaining]
                result['truncated'] = True
            remaining -= len(steps)
            notes = db.execute(
                'SELECT u.display_name,n.note FROM recipe_notes n '
                'JOIN users u ON u.id=n.user_id WHERE n.recipe_id=? '
                'ORDER BY u.display_name COLLATE NOCASE,u.id LIMIT ?',
                (recipe['id'], remaining + 1),
            ).fetchall()
            if len(notes) > remaining:
                notes = notes[:remaining]
                result['truncated'] = True
            record = {
                'title': recipe['title'][:limits.RECIPE_TITLE_MAXIMUM],
                'description': recipe['description'][:limits.RECIPE_DESCRIPTION_MAXIMUM],
                'default_servings': recipe['default_servings'],
                'ingredients': [{
                    'quantity': row['quantity'], 'unit': row['unit'],
                    'name': row['name'], 'note': row['note'],
                } for row in ingredients],
                'steps': [row['text'] for row in steps],
                'notes': [{'author': row['display_name'], 'note': row['note']}
                          for row in notes],
            }
            result['recipes'].append(record)
            added_rows = 1 + len(ingredients) + len(steps) + len(notes)
            result['row_count'] += added_rows
            if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
                result['recipes'].pop()
                result['row_count'] -= added_rows
                result['truncated'] = True
                break
        return result

    def _weather(self, db):
        def setting(name, default=''):
            row = db.execute('SELECT value FROM app_settings WHERE key=?', (name,)).fetchone()
            return row['value'] if row else default

        try:
            latitude = float(setting('weather_latitude'))
            longitude = float(setting('weather_longitude'))
            timezone_name = setting('weather_timezone')
            forecast_days = int(setting('weather_forecast_days',
                                        str(limits.WEATHER_FORECAST_DAYS_DEFAULT)))
            units = setting('weather_units', 'metric')
            if not timezone_name or units not in ('metric', 'imperial'):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            return {'configured': False, 'row_count': 0, 'truncated': False}
        cache_key = json.dumps(
            [latitude, longitude, timezone_name, forecast_days, units],
            separators=(',', ':'), ensure_ascii=True,
        )
        cached = db.execute(
            'SELECT payload,fetched_at FROM weather_cache WHERE cache_key=?', (cache_key,)
        ).fetchone()
        if cached is None:
            return {'configured': True, 'available': False, 'row_count': 0,
                    'truncated': False}
        try:
            payload = json.loads(cached['payload'])
        except (TypeError, ValueError, json.JSONDecodeError):
            return {'configured': True, 'available': False, 'row_count': 0,
                    'truncated': False}
        if not isinstance(payload, dict):
            return {'configured': True, 'available': False, 'row_count': 0,
                    'truncated': False}
        current = payload.get('current') if isinstance(payload.get('current'), dict) else {}
        hourly_source = payload.get('hourly') if isinstance(payload.get('hourly'), dict) else {}
        daily_source = payload.get('daily') if isinstance(payload.get('daily'), dict) else {}
        hourly_fields = ('time', 'temperature_2m', 'apparent_temperature',
                         'precipitation_probability', 'precipitation', 'wind_speed_10m',
                         'wind_direction_10m', 'pressure_msl', 'weather_code')
        daily_fields = ('time', 'weather_code', 'temperature_2m_max', 'temperature_2m_min',
                        'precipitation_probability_max', 'precipitation_sum', 'sunrise', 'sunset')
        hourly = {key: value[:6] for key in hourly_fields
                  if isinstance((value := hourly_source.get(key)), list)}
        daily = {key: value[:7] for key in daily_fields
                 if isinstance((value := daily_source.get(key)), list)}
        result = {
            'configured': True, 'available': True,
            'location': setting('weather_location_name')[:limits.LOCATION_QUERY_MAXIMUM],
            'fetched_at': cached['fetched_at'],
            'stale': False,
            'current': {key: value for key, value in current.items()
                        if key in ('temperature_2m', 'relative_humidity_2m',
                                   'apparent_temperature', 'weather_code', 'wind_speed_10m',
                                   'wind_direction_10m', 'pressure_msl', 'precipitation')},
            'hourly': hourly, 'daily': daily,
            'units': payload.get('current_units', {}),
            'row_count': 1 + min(6, len(hourly.get('time', [])))
            + min(7, len(daily.get('time', []))),
            'truncated': False,
        }
        try:
            fetched_at = datetime.fromisoformat(cached['fetched_at'])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            result['stale'] = (
                datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)
            ).total_seconds() >= 1800
        except (TypeError, ValueError):
            result['stale'] = True
        while _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
            if daily.get('time'):
                for values in daily.values():
                    if values:
                        values.pop()
                result['row_count'] -= 1
                result['truncated'] = True
            elif hourly.get('time'):
                for values in hourly.values():
                    if values:
                        values.pop()
                result['row_count'] -= 1
                result['truncated'] = True
            else:
                result['current'] = {}
                result['truncated'] = True
                break
        return result

    def _articles(self, db, requester):
        articles = db.execute(
            'SELECT a.title,a.url,a.summary,a.published,f.title AS feed_title,'
            'COALESCE(s.is_read,0) AS is_read,COALESCE(s.is_favorite,0) AS is_favorite '
            'FROM rss_articles a JOIN rss_feeds f ON f.id=a.feed_id '
            'LEFT JOIN rss_article_states s ON s.article_id=a.id AND s.user_id=? '
            'WHERE s.deleted_at IS NULL AND (f.scope=? OR (f.scope=? AND f.owner_user_id=?)) '
            'ORDER BY f.sort_order,f.id,a.first_seen_at DESC,a.id DESC LIMIT ?',
            (requester['id'], 'household', 'personal', requester['id'],
             limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM + 1),
        ).fetchall()
        result = {'articles': [], 'row_count': 0, 'truncated': False}
        if len(articles) > limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM:
            result['truncated'] = True
            articles = articles[:limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM]
        for article in articles:
            result['articles'].append({
                'feed': article['feed_title'][:limits.RSS_FEED_TITLE_MAXIMUM],
                'title': article['title'][:limits.RSS_ARTICLE_TITLE_MAXIMUM],
                'url': (article['url'] or '')[:limits.RSS_ARTICLE_URL_MAXIMUM],
                'summary': article['summary'][:limits.RSS_ARTICLE_BODY_MAXIMUM],
                'published': article['published'][:limits.RSS_ARTICLE_DATE_MAXIMUM],
                'read': bool(article['is_read']), 'favorite': bool(article['is_favorite']),
            })
            result['row_count'] += 1
            if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
                result['articles'].pop()
                result['row_count'] -= 1
                result['truncated'] = True
                break
        return result

    def _system_status(self, db, requester):
        visible = db.execute(
            "SELECT value FROM app_settings WHERE key='system_metrics_visible_to_all'"
        ).fetchone()
        if requester['role'] != 'admin' and visible and visible['value'] == '0':
            raise ChatToolError('System status is not available to this account.')
        row = db.execute(
            'SELECT sampled_at,cpu_percent,ram_percent,temperature_c,disk_used_bytes,'
            'disk_total_bytes,app_rss_bytes FROM system_metrics ORDER BY sampled_at DESC LIMIT 1'
        ).fetchone()
        if row is None:
            return {'available': False, 'row_count': 0, 'truncated': False}
        result = {'available': True, **dict(row), 'row_count': 1, 'truncated': False}
        if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
            raise ChatToolError('The system status result exceeds the size limit.')
        return result

    def _admin_overview(self, db):
        accounts = db.execute(
            'SELECT COUNT(*) AS total,COALESCE(SUM(active=1),0) AS active,'
            "COALESCE(SUM(role='admin' AND active=1),0) AS admins FROM users"
        ).fetchone()
        allowed_settings = (
            'chat_enabled', 'chat_model', 'weather_outbound_enabled',
            'backup_interval_days', 'calendar_timezone',
        )
        settings = {}
        for key in allowed_settings:
            row = db.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
            if row:
                settings[key] = row['value'][:128]
        result = {
            'accounts': {'total': accounts['total'], 'active': accounts['active'],
                         'active_admins': accounts['admins']},
            'settings': settings, 'row_count': 1, 'truncated': False,
        }
        if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
            raise ChatToolError('The admin overview exceeds the size limit.')
        return result

    def _calendar(self, db, requester, arguments):
        if set(arguments) != {'start_date', 'end_date'}:
            raise ChatToolError('Provide only start_date and end_date.')
        try:
            if (not isinstance(arguments['start_date'], str)
                    or not isinstance(arguments['end_date'], str)
                    or len(arguments['start_date']) != 10
                    or len(arguments['end_date']) != 10):
                raise ValueError
            start = date.fromisoformat(arguments['start_date'])
            end = date.fromisoformat(arguments['end_date'])
        except (TypeError, ValueError):
            raise ChatToolError('Dates must use YYYY-MM-DD format.') from None
        if start > end or (end - start).days >= limits.CHAT_TOOL_CALENDAR_DAYS_MAXIMUM:
            raise ChatToolError('Choose a date range of at most 31 days.')
        sql = (
            'SELECT e.id,e.title,e.event_date,e.description,e.all_day,e.start_time,e.end_time '
            'FROM events e WHERE e.event_date BETWEEN ? AND ? '
        )
        params = [start.isoformat(), end.isoformat()]
        if requester['role'] != 'admin':
            sql += ('AND (e.visibility=? OR e.created_by=? OR EXISTS '
                    '(SELECT 1 FROM event_participants p WHERE p.event_id=e.id AND p.user_id=?)) ')
            params.extend(('shared', requester['id'], requester['id']))
        sql += 'ORDER BY e.event_date,e.start_time,e.id LIMIT ?'
        params.append(limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM + 1)
        events = db.execute(sql, params).fetchall()
        result = {'events': [], 'row_count': 0, 'truncated': False}
        if len(events) > limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM:
            result['truncated'] = True
            events = events[:limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM]
        for event in events:
            if result['row_count'] >= limits.CHAT_TOOL_RESULT_ROWS_MAXIMUM:
                result['truncated'] = True
                break
            row = {
                'title': event['title'][:limits.EVENT_TITLE_MAXIMUM],
                'date': event['event_date'],
                'description': event['description'][:limits.EVENT_DESCRIPTION_MAXIMUM],
                'all_day': bool(event['all_day']),
                'start_time': event['start_time'], 'end_time': event['end_time'],
            }
            result['events'].append(row)
            result['row_count'] += 1
            if _serialized_size(result) > limits.CHAT_TOOL_RESULT_BYTES_MAXIMUM:
                result['events'].pop()
                result['row_count'] -= 1
                result['truncated'] = True
                break
        return result
