import io
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

from dateutil.parser import isoparse

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


def _list_note_messages(client, note_id, limit=100):
    response = client.get(f'/api/firenotes/chat?noteId={note_id}&limit={limit}')
    assert response.status_code == 200
    payload = response.get_json()
    return payload['messages']


def test_init_db_upgrades_record_handles_schema(configure_chat_environment):
    conn = get_db_connection()
    try:
        conn.execute('DROP TABLE record_handles')
        conn.execute(
            'CREATE TABLE record_handles (handle TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL)'
        )
        conn.commit()
    finally:
        conn.close()

    firenotes_app.init_db()

    conn = get_db_connection()
    try:
        cursor = conn.execute('PRAGMA table_info(record_handles)')
        columns = {row[1] for row in cursor.fetchall()}
        assert {'display_name', 'search_blob', 'created_at', 'updated_at'} <= columns
    finally:
        conn.close()

    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Schema upgrade note')
    assert note['title'] == 'Schema upgrade note'


def test_note_creation_self_heals_record_handles(configure_chat_environment, monkeypatch):
    conn = get_db_connection()
    try:
        conn.execute('DROP TABLE record_handles')
        conn.execute(
            'CREATE TABLE record_handles (handle TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL)'
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(firenotes_app, '_db_bootstrapped', True)

    client = firenotes_app.app.test_client()
    response = client.post('/api/firenotes/notes', json={'title': 'Self-healing note'})
    assert response.status_code == 201
    payload = response.get_json()
    assert payload['note']['title'] == 'Self-healing note'

    conn = get_db_connection()
    try:
        cursor = conn.execute('PRAGMA table_info(record_handles)')
        columns = {row[1] for row in cursor.fetchall()}
        assert {'display_name', 'search_blob', 'created_at', 'updated_at'} <= columns
    finally:
        conn.close()


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
    assert reminder['kind'] == 'reminder'
    assert reminder['context_note_id'] == note['id']

    conn = get_db_connection()
    try:
        reminders = get_record_service().list_records(conn, 'reminder')
        kinds = {entry.get('title'): entry.get('kind') for entry in reminders}
        assert kinds.get('Follow up') == 'reminder'
    finally:
        conn.close()


def test_chat_creates_task_entry(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Task note')

    response = client.post(
        '/api/firenotes/chat',
        json={
            'note_id': note['id'],
            'content': '.task Call @wes | 2030-02-01 15:00 | confirm availability',
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assistant = payload['messages'][1]
    assert assistant['metadata']['action'] == 'task_created'
    task = assistant['metadata']['task']
    assert task['title'] == 'Call @wes'
    assert task['kind'] == 'task'
    assert not task['completed']

    conn = get_db_connection()
    try:
        reminders = get_record_service().list_records(conn, 'reminder')
        matches = [entry for entry in reminders if entry.get('title') == 'Call @wes']
        assert matches and matches[0].get('kind') == 'task'
    finally:
        conn.close()


def test_chat_creates_timer_reminder_from_short_command(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Timers note')

    before = datetime.now(timezone.utc)
    response = client.post(
        '/api/firenotes/chat',
        json={
            'note_id': note['id'],
            'content': '.reminder 3h20m call @wes',
        },
    )
    after = datetime.now(timezone.utc)

    assert response.status_code == 200
    payload = response.get_json()
    assistant = payload['messages'][1]
    reminder = assistant['metadata']['reminder']
    assert reminder['timer_seconds'] == 12000
    assert reminder['due_at'] is not None
    due_dt = isoparse(reminder['due_at'])
    baseline = due_dt - timedelta(seconds=reminder['timer_seconds'])
    assert before <= baseline <= after


def test_clear_command_removes_firecoast_messages_by_count(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Clear count note')

    create_response = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': '.task Send recap | 2030-01-01 09:00'},
    )
    assert create_response.status_code == 200
    assistant_task = create_response.get_json()['messages'][-1]
    assert assistant_task['metadata']['action'] == 'task_created'
    history = _list_note_messages(client, note['id'])
    assert any((msg.get('metadata') or {}).get('action') == 'task_created' for msg in history)

    clear_response = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': '.clear 1 @firecoast'},
    )
    assert clear_response.status_code == 200
    payload = clear_response.get_json()
    clear_meta = payload['clear']
    assert clear_meta['status'] == 'success'
    assert clear_meta['criteria']['author'] == 'assistant'
    deleted_ids = clear_meta['deleted_message_ids']
    assert assistant_task['id'] in deleted_ids
    assert len(deleted_ids) >= 2
    assert not any((msg.get('metadata') or {}).get('action') == 'clear_result' for msg in payload['messages'])

    remaining = _list_note_messages(client, note['id'])
    assert not any((msg.get('metadata') or {}).get('action') == 'task_created' for msg in remaining)


def test_clear_command_without_target_clears_recent_messages(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Clear latest note')

    for label in ['first message', 'second message', 'third message']:
        response = client.post(
            '/api/firenotes/chat',
            json={'note_id': note['id'], 'content': label},
        )
        assert response.status_code == 200

    clear_response = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': '.clear 2'},
    )
    assert clear_response.status_code == 200
    payload = clear_response.get_json()
    clear_meta = payload['clear']
    assert clear_meta['status'] == 'success'
    assert clear_meta['cleared_target_count'] == 1
    deleted_ids = clear_meta['deleted_message_ids']
    assert len(deleted_ids) == 2

    remaining = _list_note_messages(client, note['id'])
    contents = [msg['content'] for msg in remaining]
    assert contents == ['first message', 'second message']


def test_clear_command_category_tasks(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Clear tasks note')

    for index, title in enumerate(['Check invoices', 'Draft follow-up'], start=1):
        response = client.post(
            '/api/firenotes/chat',
            json={
                'note_id': note['id'],
                'content': f'.task {title} | 2030-01-0{index} 10:00',
            },
        )
        assert response.status_code == 200

    clear_response = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': '.clear tasks'},
    )
    assert clear_response.status_code == 200
    payload = clear_response.get_json()
    clear_meta = payload['clear']
    assert clear_meta['criteria']['category'] == 'tasks'
    assert clear_meta['status'] == 'success'
    assert clear_meta['cleared_target_count'] == 2
    assert not payload['messages']

    remaining = _list_note_messages(client, note['id'])
    assert not any((msg.get('metadata') or {}).get('action') == 'task_created' for msg in remaining)


def test_clear_command_prunes_user_commands(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Clear commands note')

    first = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': '.task Prepare agenda'},
    )
    assert first.status_code == 200

    clear_response = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': '.clear commands'},
    )
    assert clear_response.status_code == 200
    payload = clear_response.get_json()
    clear_meta = payload['clear']
    assert clear_meta['criteria']['category'] == 'commands'
    assert clear_meta['cleared_target_count'] >= 1
    deleted_ids = set(clear_meta['deleted_message_ids'])
    assert deleted_ids

    remaining = _list_note_messages(client, note['id'])
    remaining_commands = [
        msg
        for msg in remaining
        if msg.get('author') == 'user' and (msg.get('content') or '').lstrip().startswith('.')
    ]
    assert not remaining_commands


def test_task_completion_toggled_with_checkmark_reaction(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Reaction note')

    response = client.post(
        '/api/firenotes/chat',
        json={
            'note_id': note['id'],
            'content': '.task 45m follow up with @client',
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assistant = payload['messages'][1]
    task_metadata = assistant['metadata']['task']
    assert task_metadata['timer_seconds'] == 2700
    assert not task_metadata['completed']

    message_id = assistant['id']

    add_response = client.post(
        '/api/firenotes/chat/reactions',
        json={'message_id': message_id, 'emoji': '✅'},
    )
    assert add_response.status_code == 200
    add_payload = add_response.get_json()
    updated_message = add_payload['message']
    assert updated_message['metadata']['task']['completed']

    remove_response = client.post(
        '/api/firenotes/chat/reactions',
        json={'message_id': message_id, 'emoji': '✅'},
    )
    assert remove_response.status_code == 200
    remove_payload = remove_response.get_json()
    reverted_message = remove_payload['message']
    assert not reverted_message['metadata']['task']['completed']

    task_id = reverted_message['metadata']['task']['id']
    conn = get_db_connection()
    try:
        records = get_record_service().list_records(conn, 'reminder')
        stored = next(entry for entry in records if entry['id'] == task_id)
        assert stored['completed'] is False
    finally:
        conn.close()


def test_reminders_endpoint_returns_tasks_and_reminders(configure_chat_environment):
    client = firenotes_app.app.test_client()

    first = client.post('/api/reminders', json={'title': 'Prep briefing', 'kind': 'reminder'})
    assert first.status_code == 201

    second = client.post(
        '/api/reminders',
        json={'title': 'Call vendor', 'kind': 'task', 'timer_seconds': 600},
    )
    assert second.status_code == 201

    response = client.get('/api/reminders?status=all')
    assert response.status_code == 200
    payload = response.get_json()
    kinds = {item['title']: item['kind'] for item in payload['reminders']}
    assert kinds['Prep briefing'] == 'reminder'
    assert kinds['Call vendor'] == 'task'

    tasks_only = client.get('/api/reminders?status=all&kind=task')
    assert tasks_only.status_code == 200
    task_payload = tasks_only.get_json()
    assert all(item['kind'] == 'task' for item in task_payload['reminders'])

    reminders_only = client.get('/api/reminders?status=all&kind=reminder')
    assert reminders_only.status_code == 200
    reminder_payload = reminders_only.get_json()
    assert all(item['kind'] == 'reminder' for item in reminder_payload['reminders'])


def test_tasks_page_renders(configure_chat_environment):
    client = firenotes_app.app.test_client()
    response = client.get('/tasks')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="tasks-root"' in html


def test_reminders_page_renders(configure_chat_environment):
    client = firenotes_app.app.test_client()
    response = client.get('/reminders')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="reminders-root"' in html


def test_due_reminder_dispatch_cycle_posts_chat_message(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Dispatch note')
    now = datetime.now(timezone.utc)

    conn = get_db_connection()
    try:
        service = get_record_service()
        payload = {
            'title': 'Dispatch me',
            'notes': 'Check the timer',
            'timezone': 'UTC',
            'kind': 'reminder',
            'due_at': (now - timedelta(minutes=5)).isoformat(),
            'remind_at': (now - timedelta(minutes=5)).isoformat(),
            'context_note_id': note['id'],
        }
        normalized = firenotes_app._normalize_reminder_payload(conn, payload)
        created = service.create_record(conn, 'reminder', normalized, actor='pytest')
        reminder_id = created['id']
        conn.commit()
    finally:
        conn.close()

    fired = firenotes_app.run_reminder_dispatch_cycle(now=now)
    assert any(entry['id'] == reminder_id for entry in fired)

    reminder_response = client.get(f'/api/reminders/{reminder_id}')
    assert reminder_response.status_code == 200
    reminder_payload = reminder_response.get_json()['reminder']
    assert reminder_payload['completed'] is True
    assert reminder_payload['last_notified_at']

    chat_history = client.get(f"/api/firenotes/chat?noteId={note['id']}&limit=5").get_json()['messages']
    fired_messages = [msg for msg in chat_history if (msg.get('metadata') or {}).get('action') == 'reminder_fired']
    assert fired_messages
    assert 'Dispatch me' in fired_messages[-1]['content']


def test_persistent_reminder_dispatch_cycle_stays_active(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Persistent dispatch')
    now = datetime.now(timezone.utc)

    conn = get_db_connection()
    try:
        service = get_record_service()
        payload = {
            'title': 'Standup ping',
            'timezone': 'UTC',
            'kind': 'reminder',
            'due_at': (now - timedelta(minutes=10)).isoformat(),
            'remind_at': (now - timedelta(minutes=10)).isoformat(),
            'context_note_id': note['id'],
            'persistent': True,
        }
        normalized = firenotes_app._normalize_reminder_payload(conn, payload)
        created = service.create_record(conn, 'reminder', normalized, actor='pytest')
        reminder_id = created['id']
        conn.commit()
    finally:
        conn.close()

    first_cycle = firenotes_app.run_reminder_dispatch_cycle(now=now)
    assert any(entry['id'] == reminder_id for entry in first_cycle)

    reminder_response = client.get(f'/api/reminders/{reminder_id}')
    reminder_payload = reminder_response.get_json()['reminder']
    assert reminder_payload['persistent'] is True
    assert reminder_payload['completed'] is False
    assert reminder_payload['last_notified_at']

    history_payload = client.get(f"/api/firenotes/chat?noteId={note['id']}&limit=5").get_json()
    initial_fired = [msg for msg in history_payload['messages'] if (msg.get('metadata') or {}).get('action') == 'reminder_fired']
    assert len(initial_fired) == 1

    second_cycle = firenotes_app.run_reminder_dispatch_cycle(now=now + timedelta(minutes=1))
    assert all(entry['id'] != reminder_id for entry in second_cycle)

    history_after = client.get(f"/api/firenotes/chat?noteId={note['id']}&limit=5").get_json()['messages']
    fired_after = [msg for msg in history_after if (msg.get('metadata') or {}).get('action') == 'reminder_fired']
    assert len(fired_after) == 1


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
    forwarded_meta = forwarded['metadata']['forwarded_from']
    assert forwarded_meta['id'] == original['id']
    assert forwarded_meta['preview'] == 'Forward me'
    assert forwarded_meta['note_title'] == source_note['title']
    assert forwarded['content'] == original['content']

    dest_history = client.get(f"/api/firenotes/chat?noteId={target_note['id']}&limit=5").get_json()['messages']
    assert any(entry['id'] == forwarded['id'] for entry in dest_history)


def test_message_reply_metadata_links_original(configure_chat_environment):
    client = firenotes_app.app.test_client()
    note = _create_note(client, 'Reply note')

    original_response = client.post(
        '/api/firenotes/chat',
        json={'note_id': note['id'], 'content': 'What time is the meeting?'}
    )
    assert original_response.status_code == 200
    original = original_response.get_json()['messages'][0]

    reply_response = client.post(
        '/api/firenotes/chat',
        json={
            'note_id': note['id'],
            'content': 'It starts at 3pm.',
            'reply_to': {'id': original['id']},
        },
    )
    assert reply_response.status_code == 200
    reply_message = reply_response.get_json()['messages'][0]
    reply_meta = reply_message['metadata']['reply_to']
    assert reply_meta['id'] == original['id']
    assert reply_meta['note_id'] == note['id']
    assert 'meeting' in reply_meta['preview']

    history = _list_note_messages(client, note['id'])
    stored_reply = next(entry for entry in history if entry['id'] == reply_message['id'])
    stored_meta = stored_reply['metadata']['reply_to']
    assert stored_meta['id'] == original['id']
    assert stored_meta['note_title'] == note['title']


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


def test_contact_handles_include_contact_details(configure_chat_environment):
    client = firenotes_app.app.test_client()
    response = client.post(
        '/api/contacts',
        json={
            'companyName': 'Fire Coast Logistics',
            'contactName': 'David Tucker',
            'email': 'david@example.com',
            'phone': '555-0100',
            'handle': 'davidtucker',
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    created_contact = payload['contact']
    contact_id = created_contact['id']

    handles_response = client.get('/api/records/handles?entity_types=contact')
    assert handles_response.status_code == 200
    handles_payload = handles_response.get_json()
    directory = handles_payload.get('handles', [])
    match = next((entry for entry in directory if entry['entityId'] == contact_id), None)
    assert match is not None
    assert match['handle'] == 'davidtucker'
    assert match['contact']['contactName'] == 'David Tucker'
    assert match['contact']['companyName'] == 'Fire Coast Logistics'
    assert match['contact']['email'] == 'david@example.com'
    assert match['contact']['phone'] == '5550100'


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
