"""Small Open-Meteo client and shared SQLite forecast cache."""

from datetime import datetime, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GEOCODING_URL = 'https://geocoding-api.open-meteo.com/v1/search'
FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
CACHE_SECONDS = 30 * 60
CURRENT_FIELDS = (
    'temperature_2m', 'relative_humidity_2m', 'apparent_temperature',
    'weather_code', 'wind_speed_10m', 'wind_direction_10m', 'pressure_msl',
    'precipitation',
)
HOURLY_FIELDS = (
    'temperature_2m', 'apparent_temperature', 'precipitation_probability',
    'precipitation', 'wind_speed_10m', 'wind_direction_10m', 'pressure_msl',
    'weather_code',
)
DAILY_FIELDS = (
    'weather_code', 'temperature_2m_max', 'temperature_2m_min', 'sunrise',
    'sunset', 'moon_phase', 'precipitation_probability_max', 'precipitation_sum',
    'wind_speed_10m_max', 'wind_direction_10m_dominant',
)


class WeatherError(Exception):
    """A weather-provider or configuration error safe to report to users."""


def request_json(url, timeout=8):
    request = Request(url, headers={'User-Agent': 'HomeHub/1.0 (household weather)'})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise WeatherError('The weather service could not be reached.') from exc


def geocode_location(query, *, transport=request_json):
    query = str(query or '').strip()
    if len(query) < 2 or len(query) > 100:
        raise WeatherError('Enter a town or postcode between 2 and 100 characters.')
    url = GEOCODING_URL + '?' + urlencode({
        'name': query, 'count': 5, 'language': 'en', 'format': 'json',
    })
    response = transport(url)
    if not isinstance(response, dict) or response.get('error'):
        raise WeatherError('Location lookup failed.')
    results = response.get('results', [])
    if not isinstance(results, list):
        raise WeatherError('Location lookup returned an invalid response.')
    return results


def forecast_request_url(location, forecast_days, units):
    if not isinstance(forecast_days, int) or not 1 <= forecast_days <= 15:
        raise WeatherError('Forecast length must be from 1 to 15 days.')
    unit_settings = {
        'metric': ('celsius', 'kmh', 'mm'),
        'imperial': ('fahrenheit', 'mph', 'inch'),
    }
    if units not in unit_settings:
        raise WeatherError('Choose metric or imperial units.')
    try:
        latitude = float(location['latitude'])
        longitude = float(location['longitude'])
        timezone_name = str(location['timezone']).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError('Choose a valid weather location.') from exc
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or not timezone_name:
        raise WeatherError('Choose a valid weather location.')
    temperature_unit, wind_speed_unit, precipitation_unit = unit_settings[units]
    params = {
        'latitude': latitude,
        'longitude': longitude,
        'current': ','.join(CURRENT_FIELDS),
        'hourly': ','.join(HOURLY_FIELDS),
        'daily': ','.join(DAILY_FIELDS),
        'timezone': timezone_name,
        'forecast_days': forecast_days,
        'temperature_unit': temperature_unit,
        'wind_speed_unit': wind_speed_unit,
        'precipitation_unit': precipitation_unit,
    }
    return FORECAST_URL + '?' + urlencode(params)


def fetch_forecast(location, forecast_days, units, *, transport=request_json):
    payload = transport(forecast_request_url(location, forecast_days, units))
    if (not isinstance(payload, dict) or payload.get('error')
            or not all(isinstance(payload.get(key), dict) for key in ('current', 'hourly', 'daily'))):
        raise WeatherError('The weather service returned an incomplete forecast.')
    return payload


def get_cached_forecast(db, location, forecast_days, units, outbound_enabled, now,
                        *, fetcher=fetch_forecast):
    """Return the shared cached forecast, fetching only when allowed and stale."""
    cache_key = json.dumps(
        [location.get('latitude'), location.get('longitude'), location.get('timezone'),
         forecast_days, units], separators=(',', ':'), ensure_ascii=True,
    )
    cached = db.execute(
        'SELECT payload,fetched_at FROM weather_cache WHERE cache_key=?', (cache_key,)
    ).fetchone()
    cached_data = None
    fetched_at = None
    age_seconds = None
    if cached:
        try:
            cached_data = json.loads(cached['payload'])
            fetched_at = datetime.fromisoformat(cached['fetched_at'])
            age_seconds = max(0, int((now - fetched_at).total_seconds()))
        except (TypeError, ValueError, json.JSONDecodeError):
            cached_data = None
            fetched_at = None
            age_seconds = None
    if cached_data is not None and age_seconds < CACHE_SECONDS:
        return {'data': cached_data, 'fetched_at': fetched_at, 'stale': False,
                'age_seconds': age_seconds, 'error': None}
    if not outbound_enabled:
        return {'data': cached_data, 'fetched_at': fetched_at,
                'stale': cached_data is not None, 'age_seconds': age_seconds,
                'error': None}
    try:
        payload = fetcher(location, forecast_days, units)
        if not isinstance(payload, dict):
            raise WeatherError('The weather service returned an invalid forecast.')
    except (WeatherError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {'data': cached_data, 'fetched_at': fetched_at,
                'stale': cached_data is not None, 'age_seconds': age_seconds,
                'error': str(exc)}
    fetched_at = now.astimezone(timezone.utc)
    db.execute(
        'INSERT INTO weather_cache(cache_key,payload,fetched_at) VALUES(?,?,?) '
        'ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at',
        (cache_key, json.dumps(payload, separators=(',', ':')), fetched_at.isoformat()),
    )
    db.commit()
    return {'data': payload, 'fetched_at': fetched_at, 'stale': False,
            'age_seconds': 0, 'error': None}
