import io
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


def _create_note(client, title='New note'):
    response = client.post('/api/firecoast/notes', json={'title': title})
    assert response.status_code == 201
    payload = response.get_json()
    assert 'note' in payload
    return payload['note']


def test_chat_creates_reminder_entry(configure_chat_environment):
    client = firecoast_app.app.test_client()
    note = _create_note(client, 'Ops note')

    response = client.post(
        '/api/firecoast/chat',
        json={
            'note_id': note['id'],
            'content': '.reminder Follow up | 2030-01-01 09:00 | send weekly report',
        },
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
    note = _create_note(client, 'Vault note')

    response = client.post(
        '/api/firecoast/chat',
        json={'note_id': note['id'], 'content': "@firecoast what's my password for example"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assistant = data['messages'][-1]
    assert assistant['metadata']['action'] == 'password_lookup'
    matches = assistant['metadata']['matches']
    assert any(entry['password'] == 'super-secret' for entry in matches)


def test_chat_history_returns_messages_in_order(configure_chat_environment):
    client = firecoast_app.app.test_client()
    note = _create_note(client, 'Chrono note')

    client.post('/api/firecoast/chat', json={'note_id': note['id'], 'content': 'First note'})
    client.post('/api/firecoast/chat', json={'note_id': note['id'], 'content': 'Second note'})

    response = client.get(f"/api/firecoast/chat?noteId={note['id']}&limit=5")
    assert response.status_code == 200
    data = response.get_json()
    messages = data['messages']
    user_messages = [msg for msg in messages if msg['author'] == 'user']
    assert len(user_messages) >= 2
    assert user_messages[-2]['content'] == 'First note'
    assert user_messages[-1]['content'] == 'Second note'


def test_attachments_are_persisted(configure_chat_environment):
    client = firecoast_app.app.test_client()
    note = _create_note(client, 'Files note')

    data = {
        'note_id': note['id'],
        'content': 'Uploads included',
        'attachments': [
            (io.BytesIO(b'hello world'), 'hello.txt'),
            (io.BytesIO(b'\x89PNG\r\n\x1a\nPNGDATA'), 'preview.png', 'image/png'),
        ],
    }

    response = client.post('/api/firecoast/chat', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    payload = response.get_json()
    message = payload['messages'][0]
    attachments = message['attachments']
    assert len(attachments) == 2
    names = {attachment['filename'] for attachment in attachments}
    assert {'hello.txt', 'preview.png'} <= names
    assert any(attachment['is_image'] for attachment in attachments)


def test_notes_endpoint_updates_titles_and_handles(configure_chat_environment):
    client = firecoast_app.app.test_client()
    note = _create_note(client, 'Initial name')

    list_response = client.get('/api/firecoast/notes')
    assert list_response.status_code == 200
    listed_ids = {entry['id'] for entry in list_response.get_json()['notes']}
    assert note['id'] in listed_ids

    update_response = client.patch('/api/firecoast/notes', json={'id': note['id'], 'title': 'Renamed note'})
    assert update_response.status_code == 200
    updated = update_response.get_json()['note']
    assert updated['title'] == 'Renamed note'

    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT handle FROM record_handles WHERE entity_type = 'firecoast_note' AND entity_id = ?",
            (note['id'],),
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_note_mentions_are_synced(configure_chat_environment):
    client = firecoast_app.app.test_client()
    note = _create_note(client, 'Mention note')

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO record_handles (handle, entity_type, entity_id, display_name, search_blob)
            VALUES (?, 'contact', ?, ?, ?)
            """,
            ('ops-team', 'contact-1', 'Ops Team', 'ops team ops-team'),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        '/api/firecoast/chat',
        json={'note_id': note['id'], 'content': 'Loop in @ops-team for the review.'},
    )
    assert response.status_code == 200

    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT mentioned_handle FROM record_mentions WHERE context_entity_type = 'firecoast_note' AND context_entity_id = ?",
            (note['id'],),
        ).fetchall()
        handles = {row['mentioned_handle'] for row in rows}
        assert 'ops-team' in handles
    finally:
        conn.close()


def test_note_handles_available_in_directory(configure_chat_environment):
    client = firecoast_app.app.test_client()
    note = _create_note(client, 'Directory note')

    response = client.get('/api/records/handles?entity_types=firecoast_note')
    assert response.status_code == 200
    payload = response.get_json()
    handles = {entry['handle'] for entry in payload.get('handles', [])}
    assert note.get('handle') in handles


def test_delete_note_removes_history_and_files(configure_chat_environment):
    client = firecoast_app.app.test_client()
    note = _create_note(client, 'Disposable note')

    data = {
        'note_id': note['id'],
        'content': 'Attach for deletion',
        'attachments': [
            (io.BytesIO(b'temporary'), 'temp.txt'),
        ],
    }
    response = client.post('/api/firecoast/chat', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    payload = response.get_json()
    attachments = payload['messages'][0]['attachments']
    assert attachments
    attachment_path = attachments[0]['path']
    file_path = pathlib.Path(firecoast_app.app.config['UPLOAD_FOLDER']) / attachment_path
    assert file_path.exists()

    delete_response = client.delete('/api/firecoast/notes', json={'id': note['id']})
    assert delete_response.status_code == 200

    conn = get_db_connection()
    try:
        assert conn.execute('SELECT 1 FROM firecoast_notes WHERE id = ?', (note['id'],)).fetchone() is None
        assert conn.execute('SELECT 1 FROM firecoast_chat_messages WHERE note_id = ?', (note['id'],)).fetchone() is None
        assert (
            conn.execute(
                "SELECT 1 FROM record_handles WHERE entity_type = 'firecoast_note' AND entity_id = ?",
                (note['id'],),
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM record_mentions WHERE context_entity_type = 'firecoast_note' AND context_entity_id = ?",
                (note['id'],),
            ).fetchone()
            is None
        )
    finally:
        conn.close()

    assert not file_path.exists()
