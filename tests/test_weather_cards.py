from http import HTTPStatus
import sqlite3

import pytest

import homehub.app as app_module
from homehub.app import PH, create_app


FORECAST = {
    'current': {
        'time': '2026-10-23T12:00', 'temperature_2m': 11.2,
        'relative_humidity_2m': 72, 'apparent_temperature': 8.5,
        'weather_code': 3, 'wind_speed_10m': 15, 'wind_direction_10m': 225,
        'pressure_msl': 1008.0, 'precipitation': 0,
    },
    'current_units': {'temperature_2m': '°C', 'apparent_temperature': '°C',
                      'pressure_msl': 'hPa', 'wind_speed_10m': 'km/h'},
    'hourly': {
        'time': ['2026-10-23T10:00', '2026-10-23T11:00', '2026-10-23T12:00',
                 '2026-10-23T13:00', '2026-10-23T14:00'],
        'temperature_2m': [9, 10, 11, 12, 12],
        'apparent_temperature': [7, 8, 9, 10, 10],
        'precipitation_probability': [5, 10, 20, 30, 35],
        'precipitation': [0, 0, 0, 0.3, 0.5],
        'wind_speed_10m': [10, 12, 15, 18, 20],
        'wind_direction_10m': [200, 210, 225, 230, 240],
        'pressure_msl': [1004, 1006, 1008, 1009, 1010],
        'weather_code': [2, 2, 3, 61, 61],
    },
    'hourly_units': {'temperature_2m': '°C', 'pressure_msl': 'hPa'},
    'daily': {
        'time': ['2026-10-23', '2026-10-24'], 'weather_code': [3, 61],
        'temperature_2m_max': [13, 12], 'temperature_2m_min': [5, 6],
        'sunrise': ['2026-10-23T07:21', '2026-10-24T07:23'],
        'sunset': ['2026-10-23T18:12', '2026-10-24T18:10'],
        'moon_phase': [0.25, 0.28], 'precipitation_probability_max': [35, 80],
        'precipitation_sum': [0, 4.2], 'wind_speed_10m_max': [20, 25],
        'wind_direction_10m_dominant': [225, 250],
    },
    'daily_units': {'temperature_2m_max': '°C', 'temperature_2m_min': '°C',
                    'precipitation_sum': 'mm'},
}


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'weather-cards-test-secret',
        'DATABASE': str(tmp_path / 'weather-cards.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin card password'), 'admin', 'person'),
             ('bob', 'Bob', PH.hash('bob card password'), 'user', 'person')],
        )
        db.commit()
    return app


def sign_in(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    response = client.post('/login', data={
        'username': username, 'password': password, '_csrf': csrf,
    })
    assert response.status_code == HTTPStatus.FOUND
    return client


def configure_location(app, outbound='1'):
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO app_settings(key,value) VALUES(?,?)',
            [('weather_location_name', 'Brigus, Newfoundland and Labrador, Canada'),
             ('weather_latitude', '47.533'), ('weather_longitude', '-53.55'),
             ('weather_timezone', 'America/St_Johns'), ('weather_forecast_days', '5'),
             ('weather_units', 'metric'), ('weather_outbound_enabled', outbound)],
        )


def test_weather_page_requires_login(app):
    assert app.test_client().get('/weather').status_code == HTTPStatus.FOUND


def test_weather_home_summary_and_detail_use_shared_forecast(app, monkeypatch):
    configure_location(app)
    requests = []
    def cached(db, location, days, units, outbound, now):
        requests.append((location, days, units, outbound))
        return {'data': FORECAST, 'fetched_at': now, 'stale': False,
                'age_seconds': 0, 'error': None}
    monkeypatch.setattr(app_module, 'get_cached_forecast', cached)
    bob = sign_in(app, 'bob', 'bob card password')

    home = bob.get('/')
    detail = bob.get('/weather')

    assert b'Current weather' in home.data
    assert b'Brigus, Newfoundland and Labrador, Canada' in home.data
    assert b'11.2' in home.data
    assert b'Weather details' in home.data
    assert b'Right now' in detail.data
    assert b'Feels like' in detail.data
    assert b'At a glance' in detail.data
    assert b'Rising quickly' in detail.data
    assert b'Pressure: 1008.0 hPa' in detail.data
    assert b'Sunrise' in detail.data and b'Sunset' in detail.data
    assert b'High / Low' in detail.data and b'Wind' in detail.data
    assert b'Precipitation chance' in detail.data
    assert b'Precipitation accumulation' in detail.data
    assert b'None' in detail.data
    assert b'Hour by hour' in detail.data
    assert b'Pressure trend' in detail.data
    assert b'Daily forecast' in detail.data
    assert b'Moon phase' in detail.data
    assert b'50% illuminated' in detail.data
    assert b'Next named full moon' in detail.data
    assert b'<path d="M 50 6' in detail.data
    assert b'7:21 AM' in detail.data and b'6:12 PM' in detail.data
    assert len(requests) == 2
    assert requests[0][0] == {'name': 'Brigus, Newfoundland and Labrador, Canada',
                              'latitude': 47.533, 'longitude': -53.55,
                              'timezone': 'America/St_Johns'}
    assert requests[0][1:] == (5, 'metric', True)


def test_weather_page_reports_unconfigured_location_without_fetching(app, monkeypatch):
    monkeypatch.setattr(app_module, 'get_cached_forecast',
                        lambda *_args: pytest.fail('must not fetch without location'))
    bob = sign_in(app, 'bob', 'bob card password')

    page = bob.get('/weather')

    assert page.status_code == HTTPStatus.OK
    assert b'Weather location is not set up yet' in page.data


def test_weather_page_shows_stale_cache_age_and_does_not_hide_old_forecast(app, monkeypatch):
    configure_location(app, outbound='0')
    monkeypatch.setattr(app_module, 'get_cached_forecast', lambda *_args: {
        'data': FORECAST, 'fetched_at': None, 'stale': True,
        'age_seconds': 3660, 'error': None,
    })
    bob = sign_in(app, 'bob', 'bob card password')

    page = bob.get('/weather')

    assert b'Out of date' in page.data
    assert b'1 hour' in page.data
    assert b'11.2' in page.data
