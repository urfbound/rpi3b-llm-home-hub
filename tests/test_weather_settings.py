from http import HTTPStatus
import sqlite3

import pytest

from homehub.constants import WEATHER_FORECAST_DAYS_DEFAULT, WEATHER_FORECAST_DAYS_MAXIMUM
import homehub.app as app_module
from homehub.app import PH, create_app


LOCATION_RESULTS = [{
    'name': 'Sampleville', 'latitude': 44.6488, 'longitude': -63.5752,
    'timezone': 'America/Halifax', 'admin1': 'Nova Scotia',
    'country': 'Canada', 'country_code': 'CA',
}]


@pytest.fixture
def app(tmp_path):
    app = create_app({
        'TESTING': True, 'SECRET_KEY': 'weather-settings-test-secret',
        'DATABASE': str(tmp_path / 'weather-settings.sqlite3'),
    })
    with app.app_context():
        from flask import g
        from homehub.app import get_db
        g.app_db_path = app.config['DATABASE']
        db = get_db()
        db.executemany(
            'INSERT INTO users(username,display_name,password_hash,role,account_kind) '
            'VALUES(?,?,?,?,?)',
            [('admin', 'Admin', PH.hash('admin weather password'), 'admin', 'person'),
             ('bob', 'Bob', PH.hash('bob weather password'), 'user', 'person')],
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


def csrf(client):
    with client.session_transaction() as session:
        return session['csrf_token']


def settings(app):
    with sqlite3.connect(app.config['DATABASE']) as db:
        return dict(db.execute('SELECT key,value FROM app_settings WHERE key LIKE "weather_%"'))


def test_weather_settings_are_admin_configurable_with_approved_defaults(app):
    admin = sign_in(app, 'admin', 'admin weather password')

    page = admin.get('/admin/accounts')

    assert b'Weather settings' in page.data
    assert b'value="5"' in page.data
    assert b'value="metric" selected' in page.data
    assert b'name="outbound_enabled" value="1" checked' in page.data
    assert b'No household location selected' in page.data
    assert b'placeholder="Town or postal code"' in page.data


def test_weather_settings_reject_invalid_units_and_forecast_range(app):
    admin = sign_in(app, 'admin', 'admin weather password')
    token = csrf(admin)

    assert admin.post('/admin/weather/settings', data={
        '_csrf': token, 'forecast_days': '0', 'units': 'metric', 'outbound_enabled': '1',
    }).status_code == HTTPStatus.BAD_REQUEST
    assert admin.post('/admin/weather/settings', data={
        '_csrf': token, 'forecast_days': str(WEATHER_FORECAST_DAYS_MAXIMUM + 1), 'units': 'metric', 'outbound_enabled': '1',
    }).status_code == HTTPStatus.BAD_REQUEST
    assert admin.post('/admin/weather/settings', data={
        '_csrf': token, 'forecast_days': '5', 'units': 'kelvin', 'outbound_enabled': '1',
    }).status_code == HTTPStatus.BAD_REQUEST
    assert settings(app) == {}


def test_admin_can_save_units_forecast_length_and_disable_outbound(app):
    admin = sign_in(app, 'admin', 'admin weather password')

    response = admin.post('/admin/weather/settings', data={
        '_csrf': csrf(admin), 'forecast_days': str(WEATHER_FORECAST_DAYS_MAXIMUM), 'units': 'imperial',
    })

    assert response.status_code == HTTPStatus.FOUND
    assert settings(app) == {
        'weather_forecast_days': str(WEATHER_FORECAST_DAYS_MAXIMUM), 'weather_units': 'imperial',
        'weather_outbound_enabled': '0',
    }


def test_only_admins_can_change_weather_settings_or_search_locations(app, monkeypatch):
    bob = sign_in(app, 'bob', 'bob weather password')
    monkeypatch.setattr(app_module, 'geocode_location',
                        lambda *_args: pytest.fail('non-admin triggered geocoding'))

    update = bob.post('/admin/weather/settings', data={
        '_csrf': csrf(bob), 'forecast_days': '5', 'units': 'metric',
    })
    search = bob.post('/admin/weather/location/search', data={
        '_csrf': csrf(bob), 'location_query': 'Sampleville',
    })

    assert update.status_code == HTTPStatus.FORBIDDEN
    assert search.status_code == HTTPStatus.FORBIDDEN
    assert settings(app) == {}


def test_location_search_requires_outbound_access_and_does_not_save_unselected_candidate(app, monkeypatch):
    admin = sign_in(app, 'admin', 'admin weather password')
    calls = []
    monkeypatch.setattr(app_module, 'geocode_location',
                        lambda query: calls.append(query) or LOCATION_RESULTS)

    response = admin.post('/admin/weather/location/search', data={
        '_csrf': csrf(admin), 'location_query': 'Sampleville',
    }, follow_redirects=True)

    assert response.status_code == HTTPStatus.OK
    assert b'Sampleville, Nova Scotia, Canada' in response.data
    assert calls == ['Sampleville']
    assert 'weather_latitude' not in settings(app)

    admin.post('/admin/weather/settings', data={
        '_csrf': csrf(admin), 'forecast_days': '5', 'units': 'metric',
    })
    blocked = admin.post('/admin/weather/location/search', data={
        '_csrf': csrf(admin), 'location_query': 'Sampleville',
    })
    assert blocked.status_code == HTTPStatus.BAD_REQUEST
    assert calls == ['Sampleville']


def test_admin_selects_a_geocoded_location_for_the_household(app, monkeypatch):
    admin = sign_in(app, 'admin', 'admin weather password')
    monkeypatch.setattr(app_module, 'geocode_location', lambda _query: LOCATION_RESULTS)
    admin.post('/admin/weather/location/search', data={
        '_csrf': csrf(admin), 'location_query': 'Sampleville',
    })

    response = admin.post('/admin/weather/location/select', data={
        '_csrf': csrf(admin), 'candidate_index': '0',
    })

    assert response.status_code == HTTPStatus.FOUND
    assert settings(app) == {
        'weather_location_name': 'Sampleville, Nova Scotia, Canada',
        'weather_latitude': '44.6488', 'weather_longitude': '-63.5752',
        'weather_timezone': 'America/Halifax',
    }


def test_invalid_location_candidate_cannot_be_selected(app):
    admin = sign_in(app, 'admin', 'admin weather password')

    response = admin.post('/admin/weather/location/select', data={
        '_csrf': csrf(admin), 'candidate_index': '0',
    })

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert settings(app) == {}
