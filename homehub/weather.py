"""Small Open-Meteo client and shared SQLite forecast cache."""

from datetime import date as date_type, datetime, timedelta, timezone
import json
from math import cos, pi
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from . import constants as limits


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
WEATHER_CODES = {
    0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Rime fog', 51: 'Light drizzle', 53: 'Drizzle',
    55: 'Heavy drizzle', 56: 'Light freezing drizzle', 57: 'Freezing drizzle',
    61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 66: 'Light freezing rain',
    67: 'Freezing rain', 71: 'Light snow', 73: 'Snow', 75: 'Heavy snow',
    77: 'Snow grains', 80: 'Light rain showers', 81: 'Rain showers',
    82: 'Heavy rain showers', 85: 'Light snow showers', 86: 'Heavy snow showers',
    95: 'Thunderstorm', 96: 'Thunderstorm with hail', 99: 'Heavy thunderstorm with hail',
}


class WeatherError(Exception):
    """A weather-provider or configuration error safe to report to users."""


def weather_code_label(code):
    try:
        return WEATHER_CODES.get(int(code), 'Unknown conditions')
    except (TypeError, ValueError):
        return 'Unknown conditions'


def moon_illumination_percent(phase):
    """Approximate illumination from Open-Meteo's 0..1 phase fraction."""
    try:
        phase = float(phase)
    except (TypeError, ValueError) as exc:
        raise WeatherError('Moon phase must be a number from 0 to 1.') from exc
    if not 0 <= phase <= 1:
        raise WeatherError('Moon phase must be a number from 0 to 1.')
    return round((1 - cos(2 * pi * phase)) * 50)


def moon_phase_path(phase):
    """Build an SVG terminator shape for the illuminated side of the moon."""
    try:
        phase = float(phase)
    except (TypeError, ValueError) as exc:
        raise WeatherError('Moon phase must be a number from 0 to 1.') from exc
    if not 0 <= phase <= 1:
        raise WeatherError('Moon phase must be a number from 0 to 1.')
    radius = 44
    terminator = round(radius * abs(cos(2 * pi * phase)), 2)
    if phase < 0.5:
        outer_sweep = 1
        terminator_sweep = 1 if phase < 0.25 else 0
    else:
        outer_sweep = 0
        terminator_sweep = 1 if phase < 0.75 else 0
    return (f'M 50 6 A {radius} {radius} 0 0 {outer_sweep} 50 94 '
            f'A {terminator} {radius} 0 0 {terminator_sweep} 50 6 Z')


def next_named_full_moon(day, phase):
    try:
        day = date_type.fromisoformat(day)
        phase = float(phase)
    except (TypeError, ValueError) as exc:
        raise WeatherError('A valid date and moon phase are required.') from exc
    if not 0 <= phase <= 1:
        raise WeatherError('Moon phase must be a number from 0 to 1.')
    synodic_month = 29.530588853
    days_until_full = ((0.5 - phase) % 1) * synodic_month
    full_moon_date = day + timedelta(days=round(days_until_full))
    names = {
        1: 'Wolf Moon', 2: 'Snow Moon', 3: 'Worm Moon', 4: 'Pink Moon',
        5: 'Flower Moon', 6: 'Strawberry Moon', 7: 'Buck Moon',
        8: 'Sturgeon Moon', 11: 'Beaver Moon', 12: 'Cold Moon',
    }
    if full_moon_date.month in (9, 10):
        epoch = date_type(2000, 1, 21)
        equinox = date_type(full_moon_date.year, 9, 22)
        cycle = round((equinox - epoch).days / synodic_month)
        harvest_date = epoch + timedelta(days=round(cycle * synodic_month))
        if full_moon_date == harvest_date:
            name = 'Harvest Moon'
        elif full_moon_date.month == 10:
            name = "Hunter's Moon"
        else:
            name = 'Corn Moon'
    else:
        name = names[full_moon_date.month]
    return {'name': name, 'date': full_moon_date}


def format_weather_time(value):
    if not value:
        return 'Unavailable'
    try:
        return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')
    except (TypeError, ValueError):
        return 'Unavailable'


def pressure_trend(current_pressure, previous_pressure):
    if current_pressure is None or previous_pressure is None:
        return {'change_hpa': None, 'label': 'Unavailable'}
    try:
        change = round(float(current_pressure) - float(previous_pressure), 1)
    except (TypeError, ValueError) as exc:
        raise WeatherError('Pressure readings must be numbers.') from exc
    if change >= 1.5:
        label = 'Rising quickly'
    elif change >= 0.5:
        label = 'Rising slowly'
    elif change <= -1.5:
        label = 'Falling quickly'
    elif change <= -0.5:
        label = 'Falling slowly'
    else:
        label = 'Steady'
    return {'change_hpa': change, 'label': label}


def _at(values, index):
    return values[index] if isinstance(values, list) and index < len(values) else None


def present_forecast(payload):
    """Prepare the API response for the Home and Weather templates."""
    current = payload.get('current', {})
    hourly = payload.get('hourly', {})
    daily = payload.get('daily', {})
    current_units = payload.get('current_units', {})
    hourly_units = payload.get('hourly_units', {})
    daily_units = payload.get('daily_units', {})
    hour_times = hourly.get('time', [])
    current_time = current.get('time', '')
    start_index = next((i for i, value in enumerate(hour_times)
                        if current_time and value >= current_time[:16]), 0)
    prior_index = start_index - 1
    pressure = current.get('pressure_msl')
    previous_pressure = _at(hourly.get('pressure_msl', []), prior_index)
    trend = pressure_trend(pressure, previous_pressure)
    hour_rows = []
    for index in range(start_index, min(start_index + 24, len(hour_times))):
        time_value = hour_times[index]
        previous = (pressure if index == start_index else
                    _at(hourly.get('pressure_msl', []), index - 1))
        hourly_pressure = _at(hourly.get('pressure_msl', []), index)
        hour_rows.append({
            'time': time_value[11:16] if len(time_value) >= 16 else time_value,
            'temperature': _at(hourly.get('temperature_2m', []), index),
            'apparent_temperature': _at(hourly.get('apparent_temperature', []), index),
            'precipitation_probability': _at(hourly.get('precipitation_probability', []), index),
            'precipitation': _at(hourly.get('precipitation', []), index),
            'wind_speed': _at(hourly.get('wind_speed_10m', []), index),
            'wind_direction': _at(hourly.get('wind_direction_10m', []), index),
            'pressure': hourly_pressure,
            'pressure_trend': pressure_trend(hourly_pressure, previous),
            'weather': weather_code_label(_at(hourly.get('weather_code', []), index)),
        })
    daily_rows = []
    day_times = daily.get('time', [])
    for index, day in enumerate(day_times):
        daily_rows.append({
            'date': day,
            'weather': weather_code_label(_at(daily.get('weather_code', []), index)),
            'high': _at(daily.get('temperature_2m_max', []), index),
            'low': _at(daily.get('temperature_2m_min', []), index),
            'sunrise': format_weather_time(_at(daily.get('sunrise', []), index)),
            'sunset': format_weather_time(_at(daily.get('sunset', []), index)),
            'moon_phase': _at(daily.get('moon_phase', []), index),
            'precipitation_chance': _at(daily.get('precipitation_probability_max', []), index),
            'precipitation_sum': _at(daily.get('precipitation_sum', []), index),
            'wind_speed': _at(daily.get('wind_speed_10m_max', []), index),
            'wind_direction': _at(daily.get('wind_direction_10m_dominant', []), index),
        })
    today = daily_rows[0] if daily_rows else {}
    phase = today.get('moon_phase')
    phase_name = 'Unavailable'
    illumination = None
    named_full_moon = None
    if phase is not None:
        illumination = moon_illumination_percent(phase)
        phase = float(phase)
        named_full_moon = next_named_full_moon(today['date'], phase)
        if phase < 0.03 or phase > 0.97:
            phase_name = 'New moon'
        elif phase < 0.22:
            phase_name = 'Waxing crescent'
        elif phase < 0.28:
            phase_name = 'First quarter'
        elif phase < 0.47:
            phase_name = 'Waxing gibbous'
        elif phase < 0.53:
            phase_name = 'Full moon'
        elif phase < 0.72:
            phase_name = 'Waning gibbous'
        elif phase < 0.78:
            phase_name = 'Last quarter'
        else:
            phase_name = 'Waning crescent'
    return {
        'current': current, 'current_units': current_units,
        'hourly_units': hourly_units, 'daily_units': daily_units,
        'pressure_trend': trend, 'hours': hour_rows, 'days': daily_rows,
        'today': today, 'moon_phase_name': phase_name,
        'moon_illumination': illumination,
        'moon_path': moon_phase_path(today['moon_phase']) if phase is not None else '',
        'next_named_full_moon': named_full_moon,
        'condition': weather_code_label(current.get('weather_code')),
    }


def request_json(url, timeout=8):
    request = Request(url, headers={'User-Agent': 'HomeHub/1.0 (household weather)'})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise WeatherError('The weather service could not be reached.') from exc


def geocode_location(query, *, transport=request_json):
    query = str(query or '').strip()
    if len(query) < 2 or len(query) > limits.LOCATION_QUERY_MAXIMUM:
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
    if (not isinstance(forecast_days, int)
            or not limits.WEATHER_FORECAST_DAYS_MINIMUM <= forecast_days <= limits.WEATHER_FORECAST_DAYS_MAXIMUM):
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
