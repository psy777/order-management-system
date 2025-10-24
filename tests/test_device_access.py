import json
import pathlib
import sys
import uuid

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as firecoast_app
import database
from database import get_db_connection
from flask import g, session


@pytest.fixture
def device_control_environment(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    settings_file = data_dir / 'settings.json'
    settings_file.write_text(json.dumps({'timezone': 'UTC'}))

    passwords_file = data_dir / 'passwords.json'
    passwords_file.write_text(json.dumps({'entries': []}))

    import data_paths

    monkeypatch.setattr(data_paths, 'DATA_ROOT', data_dir)
    monkeypatch.setattr(data_paths, 'LEGACY_DATA_ROOT', data_dir)
    monkeypatch.setattr(data_paths, 'ensure_data_root', lambda: data_dir)

    monkeypatch.setattr(database, 'DATA_ROOT', data_dir)
    monkeypatch.setattr(database, 'DATA_DIR', data_dir)
    monkeypatch.setattr(database, 'ensure_data_root', lambda: data_dir)

    monkeypatch.setattr(firecoast_app, 'DATA_ROOT', data_dir)
    monkeypatch.setattr(firecoast_app, 'DATA_DIR', data_dir)
    monkeypatch.setattr(firecoast_app, 'UPLOAD_FOLDER', data_dir)
    firecoast_app.app.config['UPLOAD_FOLDER'] = str(data_dir)
    monkeypatch.setattr(firecoast_app, 'SETTINGS_FILE', settings_file)
    monkeypatch.setattr(firecoast_app, 'PASSWORDS_FILE', passwords_file)
    monkeypatch.setattr(firecoast_app, '_db_bootstrapped', False)
    monkeypatch.setattr(firecoast_app, '_ensure_reminder_dispatcher_started', lambda: None)
    firecoast_app.app.config['TESTING'] = True
    monkeypatch.setattr(firecoast_app, 'ensure_data_root', lambda: data_dir)

    firecoast_app.init_db()

    yield firecoast_app

    firecoast_app._db_bootstrapped = False


def test_new_device_is_redirected_and_logged(device_control_environment, monkeypatch):
    firecoast_app = device_control_environment

    monkeypatch.setattr(firecoast_app, '_get_request_ip_address', lambda: '192.168.0.42')
    monkeypatch.setattr(firecoast_app, '_generate_device_token', lambda: 'token-new-device')

    original_testing = firecoast_app.app.config.get('TESTING')
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_request_context('/orders'):
            session.clear()
            response = firecoast_app._enforce_device_access_gate()
            assert response.status_code == 302
            assert response.location.endswith('/device/register') or response.location.endswith('/device/pending')
            assert session.get(firecoast_app.DEVICE_TOKEN_SESSION_KEY) == 'token-new-device'
            assert session.get(firecoast_app.PENDING_DEVICE_TOKEN_SESSION_KEY) == 'token-new-device'
            assert session.get('pending_ip') == '192.168.0.42'
    finally:
        firecoast_app.app.config['TESTING'] = original_testing

    conn = get_db_connection()
    try:
        device = conn.execute(
            "SELECT owner_name, device_name, status, access_token, last_ip FROM network_devices WHERE access_token = ?",
            ('token-new-device',),
        ).fetchone()
        assert device is not None
        assert device['access_token'] == 'token-new-device'
        assert device['status'] == firecoast_app.DEVICE_STATUS_PENDING
        assert device['owner_name'] is None
        assert device['device_name'] is None
        assert device['last_ip'] == '192.168.0.42'
        row = conn.execute(
            "SELECT device_token, ip_address, status FROM device_access_logs WHERE device_token = ? ORDER BY id DESC LIMIT 1",
            ('token-new-device',),
        ).fetchone()
        assert row['device_token'] == 'token-new-device'
        assert row['ip_address'] == '192.168.0.42'
        assert row['status'] in {'new', firecoast_app.DEVICE_STATUS_PENDING}
    finally:
        conn.close()


def test_pending_device_without_details_can_submit_request(device_control_environment, monkeypatch):
    firecoast_app = device_control_environment

    device_token = f'token-form-flow-{uuid.uuid4()}'

    monkeypatch.setattr(firecoast_app, '_get_request_ip_address', lambda: '192.168.0.77')
    monkeypatch.setattr(firecoast_app, '_generate_device_token', lambda: device_token)

    original_testing = firecoast_app.app.config.get('TESTING')
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_client() as client:
            response = client.get('/orders')
            assert response.status_code == 302
            assert response.headers['Location']
            assert response.headers['Location'].endswith('/device/register')

            response = client.get('/device/register')
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            assert 'name="owner_name"' in html
            assert 'name="device_name"' in html

            post_response = client.post(
                '/device/register',
                data={'owner_name': 'Jordan', 'device_name': 'Warehouse Tablet'},
                follow_redirects=False,
            )
            assert post_response.status_code == 302
            assert post_response.headers['Location'].endswith('/device/pending')

            # Once the details are recorded, subsequent visits should go to the pending page.
            response = client.get('/device/register')
            assert response.status_code == 302
            assert response.headers['Location'].endswith('/device/pending')
    finally:
        firecoast_app.app.config['TESTING'] = original_testing

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT owner_name, device_name, status FROM network_devices WHERE access_token = ?",
            (device_token,),
        ).fetchone()
        assert row is not None
        assert row['owner_name'] == 'Jordan'
        assert row['device_name'] == 'Warehouse Tablet'
        assert row['status'] == firecoast_app.DEVICE_STATUS_PENDING
    finally:
        conn.close()


def test_trusted_device_gains_access_without_login(device_control_environment, monkeypatch):
    firecoast_app = device_control_environment

    trusted_token = 'trusted-token'
    placeholder_mac = firecoast_app._derive_device_identifier_from_token(trusted_token)

    initial_testing = firecoast_app.app.config.get('TESTING')
    monkeypatch.setattr(firecoast_app, '_generate_device_token', lambda: trusted_token)
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_request_context('/orders'):
            session.clear()
            firecoast_app._enforce_device_access_gate()
    finally:
        firecoast_app.app.config['TESTING'] = initial_testing

    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE network_devices
            SET owner_name = ?, device_name = ?, status = ?, permissions = ?, last_ip = ?, last_seen = CURRENT_TIMESTAMP
            WHERE access_token = ?
            """,
            (
                'Jordan',
                'Warehouse Tablet',
                firecoast_app.DEVICE_STATUS_TRUSTED,
                json.dumps(['orders']),
                '192.168.0.99',
                trusted_token,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(firecoast_app, '_get_request_ip_address', lambda: '192.168.0.99')

    original_testing = firecoast_app.app.config.get('TESTING')
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_request_context('/orders'):
            session[firecoast_app.DEVICE_TOKEN_SESSION_KEY] = trusted_token
            response = firecoast_app._enforce_device_access_gate()
            assert response is None
            assert session.get(firecoast_app.PENDING_DEVICE_TOKEN_SESSION_KEY) == trusted_token
            assert g.current_device['status'] == firecoast_app.DEVICE_STATUS_TRUSTED
            assert g.current_device['display_name'] == 'Jordan'
            assert g.current_device['device_token'] == trusted_token
    finally:
        firecoast_app.app.config['TESTING'] = original_testing

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT status, device_token FROM device_access_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row['status'] == firecoast_app.DEVICE_STATUS_TRUSTED
        assert row['device_token'] == trusted_token
    finally:
        conn.close()


def test_blocked_device_receives_blocked_page(device_control_environment, monkeypatch):
    firecoast_app = device_control_environment

    blocked_token = 'blocked-token'
    placeholder_mac = firecoast_app._derive_device_identifier_from_token(blocked_token)

    initial_testing = firecoast_app.app.config.get('TESTING')
    monkeypatch.setattr(firecoast_app, '_generate_device_token', lambda: blocked_token)
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_request_context('/orders'):
            session.clear()
            firecoast_app._enforce_device_access_gate()
    finally:
        firecoast_app.app.config['TESTING'] = initial_testing

    conn = get_db_connection()
    try:
        conn.execute(
            """
            UPDATE network_devices
            SET owner_name = ?, device_name = ?, status = ?, permissions = ?, last_ip = ?, last_seen = CURRENT_TIMESTAMP
            WHERE access_token = ?
            """,
            (
                'Jamie',
                'Blocked Tablet',
                firecoast_app.DEVICE_STATUS_BLOCKED,
                json.dumps([]),
                '192.168.0.55',
                blocked_token,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(firecoast_app, '_get_request_ip_address', lambda: '192.168.0.55')

    original_testing = firecoast_app.app.config.get('TESTING')
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_request_context('/orders'):
            session[firecoast_app.DEVICE_TOKEN_SESSION_KEY] = blocked_token
            response, status_code = firecoast_app._enforce_device_access_gate()
            assert status_code == 403
            assert session.get(firecoast_app.PENDING_DEVICE_TOKEN_SESSION_KEY) == blocked_token
    finally:
        firecoast_app.app.config['TESTING'] = original_testing
