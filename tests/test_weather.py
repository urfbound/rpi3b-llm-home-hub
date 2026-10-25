import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from homehub.constants import WEATHER_FORECAST_DAYS_DEFAULT, WEATHER_FORECAST_DAYS_MAXIMUM
from homehub.weather import (
    WeatherError, fetch_forecast, format_weather_time, forecast_request_url, geocode_location,
    get_cached_forecast, moon_illumination_percent, pressure_trend, weather_code_label,
    next_named_full_moon,
)
from homehub.app import create_app


LOCATION = {
    'name': 'Sampleville', 'latitude': 44.6488, 'longitude': -63.5752,
    'timezone': 'America/Halifax',
}
NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
PAYLOAD = {
    'current': {'temperature_2m': 10},
    'hourly': {'time': ['2026-10-23T09:00'], 'temperature_2m': [10]},
    'daily': {'time': ['2026-10-23'], 'moon_phase': [0.5]},
}


def cache_db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE weather_cache(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, '
               'fetched_at TEXT NOT NULL)')
    return db


def test_forecast_request_includes_approved_current_hourly_daily_data_and_units():
    url = forecast_request_url(LOCATION, 5, 'imperial')
    params = parse_qs(urlparse(url).query)

    assert url.startswith('https://api.open-meteo.com/v1/forecast?')
    assert params['latitude'] == ['44.6488']
    assert params['longitude'] == ['-63.5752']
    assert params['timezone'] == ['America/Halifax']
    assert params['forecast_days'] == [str(WEATHER_FORECAST_DAYS_DEFAULT)]
    assert params['temperature_unit'] == ['fahrenheit']
    assert params['wind_speed_unit'] == ['mph']
    assert 'pressure_msl' in params['current'][0].split(',')
    assert {'wind_speed_10m', 'wind_direction_10m', 'pressure_msl'} <= set(params['hourly'][0].split(','))
    assert {'moon_phase', 'sunrise', 'sunset', 'precipitation_sum'} <= set(params['daily'][0].split(','))


@pytest.mark.parametrize('days,units', [
    (0, 'metric'), (WEATHER_FORECAST_DAYS_MAXIMUM + 1, 'metric'),
    (WEATHER_FORECAST_DAYS_DEFAULT, 'kelvin'),
])
def test_forecast_request_rejects_invalid_settings(days, units):
    with pytest.raises(WeatherError):
        forecast_request_url(LOCATION, days, units)


def test_geocoder_uses_town_or_postcode_and_returns_candidates():
    calls = []
    def transport(url):
        calls.append(url)
        return {'results': [LOCATION]}

    results = geocode_location('Sampleville', transport=transport)

    assert results == [LOCATION]
    assert parse_qs(urlparse(calls[0]).query)['name'] == ['Sampleville']
    assert calls[0].startswith('https://geocoding-api.open-meteo.com/v1/search?')


def test_geocoder_rejects_empty_or_too_short_queries_without_network_call():
    def forbidden(_url):
        pytest.fail('invalid location must not trigger a request')

    for query in ('', 'A'):
        with pytest.raises(WeatherError):
            geocode_location(query, transport=forbidden)


def test_forecast_fetch_surfaces_bad_provider_response_as_weather_error():
    with pytest.raises(WeatherError):
        fetch_forecast(LOCATION, 5, 'metric', transport=lambda _url: {'error': True})


def test_cache_returns_fresh_shared_data_without_another_provider_call():
    db = cache_db()
    calls = []
    fetcher = lambda *_args: calls.append(True) or PAYLOAD
    first = get_cached_forecast(db, LOCATION, 5, 'metric', True, NOW, fetcher=fetcher)
    second = get_cached_forecast(db, LOCATION, 5, 'metric', True,
                                 NOW + timedelta(minutes=29), fetcher=fetcher)

    assert first['data'] == PAYLOAD and not first['stale']
    assert second['data'] == PAYLOAD and not second['stale']
    assert len(calls) == 1


def test_cache_refreshes_after_30_minutes_and_keeps_only_one_location_record():
    db = cache_db()
    calls = []
    def fetcher(*_args):
        calls.append(len(calls) + 1)
        return {'current': {'temperature_2m': calls[-1]}}

    get_cached_forecast(db, LOCATION, 5, 'metric', True, NOW, fetcher=fetcher)
    refreshed = get_cached_forecast(db, LOCATION, 5, 'metric', True,
                                    NOW + timedelta(minutes=30), fetcher=fetcher)

    assert refreshed['data']['current']['temperature_2m'] == 2
    assert len(calls) == 2
    assert db.execute('SELECT COUNT(*) FROM weather_cache').fetchone()[0] == 1


def test_disabled_outbound_uses_stale_shared_cache_without_fetching():
    db = cache_db()
    get_cached_forecast(db, LOCATION, 5, 'metric', True, NOW, fetcher=lambda *_: PAYLOAD)

    result = get_cached_forecast(
        db, LOCATION, 5, 'metric', False, NOW + timedelta(hours=1),
        fetcher=lambda *_: pytest.fail('outbound access is disabled'),
    )

    assert result['data'] == PAYLOAD
    assert result['stale']
    assert result['age_seconds'] == 3600


def test_failed_refresh_keeps_stale_data_and_reports_fetch_error():
    db = cache_db()
    get_cached_forecast(db, LOCATION, 5, 'metric', True, NOW, fetcher=lambda *_: PAYLOAD)

    result = get_cached_forecast(
        db, LOCATION, 5, 'metric', True, NOW + timedelta(hours=1),
        fetcher=lambda *_: (_ for _ in ()).throw(WeatherError('offline')),
    )

    assert result['data'] == PAYLOAD
    assert result['stale']
    assert result['error'] == 'offline'


def test_disabled_outbound_without_cached_data_returns_empty_result():
    result = get_cached_forecast(cache_db(), LOCATION, 5, 'metric', False, NOW)

    assert result['data'] is None
    assert result['stale'] is False


def test_app_database_initializes_weather_cache_table(tmp_path):
    path = tmp_path / 'weather-app.sqlite3'
    create_app({'TESTING': True, 'SECRET_KEY': 'weather-app-secret', 'DATABASE': str(path)})
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='weather_cache'"
        ).fetchone() == ('weather_cache',)


@pytest.mark.parametrize(('phase', 'expected'), [
    (0, 0), (0.25, 50), (0.5, 100), (0.75, 50), (1, 0),
])
def test_moon_illumination_approximates_open_meteo_phase(phase, expected):
    assert moon_illumination_percent(phase) == expected


@pytest.mark.parametrize(('change', 'expected'), [
    (2.0, 'Rising quickly'), (0.8, 'Rising slowly'), (0.2, 'Steady'),
    (-0.8, 'Falling slowly'), (-2.0, 'Falling quickly'),
])
def test_pressure_trend_labels_hourly_change(change, expected):
    result = pressure_trend(1012.0, 1012.0 - change)

    assert result['change_hpa'] == change
    assert result['label'] == expected


def test_weather_code_label_has_fallback_for_unknown_provider_code():
    assert weather_code_label(0) == 'Clear sky'
    assert weather_code_label(999) == 'Unknown conditions'


def test_next_named_full_moon_uses_approximate_open_meteo_phase():
    result = next_named_full_moon('2026-06-15', 0.25)

    assert result['name'] == 'Strawberry Moon'
    assert result['date'].isoformat() == '2026-06-22'


def test_sunrise_and_sunset_display_as_local_clock_times():
    assert format_weather_time('2026-10-23T07:21') == '7:21 AM'
    assert format_weather_time('2026-10-23T18:12-02:30') == '6:12 PM'
