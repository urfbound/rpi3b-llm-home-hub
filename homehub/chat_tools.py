"""Bounded, read-only database tools for an individual Chat requester."""
from datetime import date
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
            'description': 'Read shopping and to-do lists visible to this signed-in user.',
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
                'get_household_lists', 'get_calendar_events'}:
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
            if name == 'get_household_lists':
                if arguments:
                    raise ChatToolError('This tool does not accept arguments.')
                return self._lists(db, requester)
            return self._calendar(db, requester, arguments)

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
        result = {'lists': [], 'row_count': 0, 'truncated': False}
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
