import io
import json
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as firenotes_app
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

    monkeypatch.setattr(firenotes_app, 'DATA_DIR', data_dir)
    monkeypatch.setattr(firenotes_app, 'UPLOAD_FOLDER', data_dir)
    firenotes_app.app.config['UPLOAD_FOLDER'] = str(data_dir)
    monkeypatch.setattr(firenotes_app, 'SETTINGS_FILE', settings_file)
    monkeypatch.setattr(firenotes_app, 'PASSWORDS_FILE', passwords_file)
    monkeypatch.setattr(firenotes_app, 'DATA_ROOT', data_dir)
    monkeypatch.setattr(firenotes_app, '_db_bootstrapped', False)
    firenotes_app.app.config['TESTING'] = True

    import data_paths

    monkeypatch.setattr(data_paths, 'DATA_ROOT', data_dir)
    monkeypatch.setattr(data_paths, 'LEGACY_DATA_ROOT', data_dir)
    monkeypatch.setattr(firenotes_app, 'ensure_data_root', lambda: data_dir)
    monkeypatch.setattr(data_paths, 'ensure_data_root', lambda: data_dir)

    firenotes_app.init_db()

    yield


def _create_note(client, title='New note'):
    response = client.post('/api/firenotes/notes', json={'title': title})
    assert response.status_code == 201
    payload = response.get_json()
    assert 'note' in payload
    return payload['note']


def test_chat_creates_reminder_entry(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Ops note')

    response = client.post(
        '/api/firenotes/chat',
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
    firenotes_app.write_password_entries([
        {
            'id': 'pw-1',
            'service': 'Example CRM',
            'username': 'ops@example.com',
            'password': 'super-secret',
            'notes': '',
        }
    ])
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Vault note')

    response = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': "@firenotes what's my password for example"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assistant = data['messages'][-1]
    assert assistant['metadata']['action'] == 'password_lookup'
    matches = assistant['metadata']['matches']
    assert any(entry['password'] == 'super-secret' for entry in matches)


def test_chat_history_returns_messages_in_order(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Chrono note')

    client.post('/api/firenotes/chat', json={'note_id': note['id'], 'content': 'First note'})
    client.post('/api/firenotes/chat', json={'note_id': note['id'], 'content': 'Second note'})

    response = client.get(f"/api/firenotes/chat?noteId={note['id']}&limit=5")
    assert response.status_code == 200
    data = response.get_json()
    messages = data['messages']
    user_messages = [msg for msg in messages if msg['author'] == 'user']
    assert len(user_messages) >= 2
    assert user_messages[-2]['content'] == 'First note'
    assert user_messages[-1]['content'] == 'Second note'


def test_attachments_are_persisted(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Files note')

    data = {
        'note_id': note['id'],
        'content': 'Uploads included',
        'attachments': [
            (io.BytesIO(b'hello world'), 'hello.txt'),
            (io.BytesIO(b'\x89PNG\r\n\x1a\nPNGDATA'), 'preview.png', 'image/png'),
        ],
    }

    response = client.post('/api/firenotes/chat', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    payload = response.get_json()
    message = payload['messages'][0]
    attachments = message['attachments']
    assert len(attachments) == 2
    names = {attachment['filename'] for attachment in attachments}
    assert {'hello.txt', 'preview.png'} <= names
    assert any(attachment['is_image'] for attachment in attachments)


def test_message_reactions_toggle(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Reactable note')

    message_response = client.post('/api/firenotes/chat', json={'note_id': note['id'], 'content': 'React here'})
    assert message_response.status_code == 200
    initial_payload = message_response.get_json()
    message = initial_payload['messages'][0]
    assert message['reactions'] == []

    add_response = client.post(
        '/api/firenotes/chat/reactions',
        json={'message_id': message['id'], 'emoji': '👍'},
    )
    assert add_response.status_code == 200
    add_payload = add_response.get_json()
    updated = add_payload['message']
    assert any(reaction['emoji'] == '👍' and reaction['reacted'] for reaction in updated['reactions'])

    history_response = client.get(f"/api/firenotes/chat?noteId={note['id']}&limit=10")
    assert history_response.status_code == 200
    history_messages = history_response.get_json()['messages']
    stored_reactions = {
        entry['emoji']
        for message_entry in history_messages
        if message_entry['id'] == message['id']
        for entry in message_entry.get('reactions', [])
    }
    assert '👍' in stored_reactions

    remove_response = client.post(
        '/api/firenotes/chat/reactions',
        json={'message_id': message['id'], 'emoji': '👍'},
    )
    assert remove_response.status_code == 200
    removed_payload = remove_response.get_json()
    removed_message = removed_payload['message']
    assert not any(reaction['emoji'] == '👍' for reaction in removed_message.get('reactions', []))


def test_message_edit_updates_content_and_metadata(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Editable note')

    create_response = client.post('/api/firenotes/chat', json={'note_id': note['id'], 'content': 'Initial content'})
    assert create_response.status_code == 200
    created = create_response.get_json()['messages'][0]

    edit_response = client.patch(
        f"/api/firenotes/chat/messages/{created['id']}",
        json={'content': 'Updated body'},
    )
    assert edit_response.status_code == 200
    payload = edit_response.get_json()
    updated = payload['message']
    assert updated['content'] == 'Updated body'
    assert updated['metadata']['edited_at']

    history_response = client.get(f"/api/firenotes/chat?noteId={note['id']}&limit=5")
    history = history_response.get_json()['messages']
    stored = next(entry for entry in history if entry['id'] == created['id'])
    assert stored['content'] == 'Updated body'
    assert stored['metadata']['edited_at']


def test_message_forward_creates_new_entry(configure_chat_environment):
    client = firenotes_app.app.test_client()
    source_note = _create_note(client, 'Source note')
    target_note = _create_note(client, 'Target note')

    create_response = client.post('/api/firenotes/chat', json={'note_id': source_note['id'], 'content': 'Forward me'})
    original = create_response.get_json()['messages'][0]

    forward_response = client.post(
        '/api/firenotes/chat/forward',
        json={'message_id': original['id'], 'target_note_id': target_note['id']},
    )
    assert forward_response.status_code == 201
    payload = forward_response.get_json()
    forwarded = payload['message']
    assert forwarded['note_id'] == target_note['id']
    assert forwarded['metadata']['forwarded_from']['id'] == original['id']
    assert forwarded['content'].startswith('Forwarded from')

    dest_history = client.get(f"/api/firenotes/chat?noteId={target_note['id']}&limit=5").get_json()['messages']
    assert any(entry['id'] == forwarded['id'] for entry in dest_history)


def test_message_delete_removes_entry(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Disposable note')

    create_response = client.post('/api/firenotes/chat', json={'note_id': note['id'], 'content': 'Delete me'})
    message = create_response.get_json()['messages'][0]

    delete_response = client.delete(f"/api/firenotes/chat/messages/{message['id']}")
    assert delete_response.status_code == 200

    history = client.get(f"/api/firenotes/chat?noteId={note['id']}&limit=5").get_json()['messages']
    assert all(entry['id'] != message['id'] for entry in history)


def test_notes_endpoint_updates_titles_and_handles(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Initial name')

    list_response = client.get('/api/firenotes/notes')
    assert list_response.status_code == 200
    listed_ids = {entry['id'] for entry in list_response.get_json()['notes']}
    assert note['id'] in listed_ids

    update_response = client.patch('/api/firenotes/notes', json={'id': note['id'], 'title': 'Renamed note'})
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
    client = firenotes_app.app.test_client()
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
        '/api/firenotes/chat',
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
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Directory note')

    response = client.get('/api/records/handles?entity_types=firecoast_note')
    assert response.status_code == 200
    payload = response.get_json()
    handles = {entry['handle'] for entry in payload.get('handles', [])}
    assert note.get('handle') in handles


def test_delete_note_removes_history_and_files(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Disposable note')

    data = {
        'note_id': note['id'],
        'content': 'Attach for deletion',
        'attachments': [
            (io.BytesIO(b'temporary'), 'temp.txt'),
        ],
    }
    response = client.post('/api/firenotes/chat', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    payload = response.get_json()
    attachments = payload['messages'][0]['attachments']
    assert attachments
    attachment_path = attachments[0]['path']
    file_path = pathlib.Path(firenotes_app.app.config['UPLOAD_FOLDER']) / attachment_path
    assert file_path.exists()

    delete_response = client.delete('/api/firenotes/notes', json={'id': note['id']})
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
