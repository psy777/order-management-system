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
    monkeypatch.setattr(firecoast_app, '_resolve_mac_address_for_ip', lambda ip: 'aa:bb:cc:dd:ee:ff')

    original_testing = firecoast_app.app.config.get('TESTING')
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_request_context('/orders'):
            response = firecoast_app._enforce_device_access_gate()
            assert response.status_code == 302
            assert response.location.endswith('/device/register')
            assert session.get('pending_mac') == 'aa:bb:cc:dd:ee:ff'
            assert session.get('pending_ip') == '192.168.0.42'
    finally:
        firecoast_app.app.config['TESTING'] = original_testing

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT mac_address, ip_address, status FROM device_access_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row['mac_address'] == 'aa:bb:cc:dd:ee:ff'
        assert row['ip_address'] == '192.168.0.42'
        assert row['status'] == 'new'
    finally:
        conn.close()


def test_trusted_device_gains_access_without_login(device_control_environment, monkeypatch):
    firecoast_app = device_control_environment

    conn = get_db_connection()
    try:
        device_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO network_devices (
                id,
                mac_address,
                owner_name,
                device_name,
                status,
                permissions,
                last_ip,
                last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                device_id,
                '11:22:33:44:55:66',
                'Jordan',
                'Warehouse Tablet',
                firecoast_app.DEVICE_STATUS_TRUSTED,
                json.dumps(['orders']),
                '192.168.0.99',
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(firecoast_app, '_get_request_ip_address', lambda: '192.168.0.99')
    monkeypatch.setattr(firecoast_app, '_resolve_mac_address_for_ip', lambda ip: '11:22:33:44:55:66')

    original_testing = firecoast_app.app.config.get('TESTING')
    firecoast_app.app.config['TESTING'] = False
    try:
        with firecoast_app.app.test_request_context('/orders'):
            response = firecoast_app._enforce_device_access_gate()
            assert response is None
            assert session.get('pending_mac') == '11:22:33:44:55:66'
            assert g.current_device['status'] == firecoast_app.DEVICE_STATUS_TRUSTED
            assert g.current_device['display_name'] == 'Jordan'
    finally:
        firecoast_app.app.config['TESTING'] = original_testing

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT status FROM device_access_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row['status'] == firecoast_app.DEVICE_STATUS_TRUSTED
    finally:
        conn.close()
