import json
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as firecoast_app
from database import get_db_connection
from services.records import get_record_service


@pytest.fixture(autouse=True)
def configure_chat_environment(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    settings_file = data_dir / 'settings.json'
    settings_file.write_text(json.dumps({'timezone': 'UTC'}))
    passwords_file = data_dir / 'passwords.json'
    passwords_file.write_text(json.dumps({'entries': []}))

    monkeypatch.setattr(firecoast_app, 'DATA_DIR', data_dir)
    monkeypatch.setattr(firecoast_app, 'UPLOAD_FOLDER', data_dir)
    firecoast_app.app.config['UPLOAD_FOLDER'] = str(data_dir)
    monkeypatch.setattr(firecoast_app, 'SETTINGS_FILE', settings_file)
    monkeypatch.setattr(firecoast_app, 'PASSWORDS_FILE', passwords_file)
    monkeypatch.setattr(firecoast_app, 'DATA_ROOT', data_dir)
    monkeypatch.setattr(firecoast_app, '_db_bootstrapped', False)
    firecoast_app.app.config['TESTING'] = True

    import data_paths

    monkeypatch.setattr(data_paths, 'DATA_ROOT', data_dir)
    monkeypatch.setattr(data_paths, 'LEGACY_DATA_ROOT', data_dir)
    monkeypatch.setattr(firecoast_app, 'ensure_data_root', lambda: data_dir)
    monkeypatch.setattr(data_paths, 'ensure_data_root', lambda: data_dir)

    firecoast_app.init_db()

    yield


def test_chat_creates_reminder_entry(configure_chat_environment):
    client = firecoast_app.app.test_client()

    response = client.post(
        '/api/firecoast/chat',
        json={'content': '.reminder Follow up | 2030-01-01 09:00 | send weekly report'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert 'messages' in payload
    assert len(payload['messages']) == 2
    assistant = payload['messages'][1]
    assert assistant['author'] == 'assistant'
    assert assistant['metadata']['action'] == 'reminder_created'
    reminder = assistant['metadata']['reminder']
    assert reminder['title'] == 'Follow up'

    conn = get_db_connection()
    try:
        reminders = get_record_service().list_records(conn, 'reminder')
        titles = [entry.get('title') for entry in reminders]
        assert 'Follow up' in titles
    finally:
        conn.close()


def test_password_lookup_responds_with_matches(configure_chat_environment):
    firecoast_app.write_password_entries([
        {
            'id': 'pw-1',
            'service': 'Example CRM',
            'username': 'ops@example.com',
            'password': 'super-secret',
            'notes': '',
        }
    ])
    client = firecoast_app.app.test_client()

    response = client.post(
        '/api/firecoast/chat',
        json={'content': "@firecoast what's my password for example"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assistant = data['messages'][-1]
    assert assistant['metadata']['action'] == 'password_lookup'
    matches = assistant['metadata']['matches']
    assert any(entry['password'] == 'super-secret' for entry in matches)


def test_chat_history_returns_messages_in_order(configure_chat_environment):
    client = firecoast_app.app.test_client()

    client.post('/api/firecoast/chat', json={'content': 'First note'})
    client.post('/api/firecoast/chat', json={'content': 'Second note'})

    response = client.get('/api/firecoast/chat?limit=5')
    assert response.status_code == 200
    data = response.get_json()
    messages = data['messages']
    user_messages = [msg for msg in messages if msg['author'] == 'user']
    assert len(user_messages) >= 2
    assert user_messages[-2]['content'] == 'First note'
    assert user_messages[-1]['content'] == 'Second note'
