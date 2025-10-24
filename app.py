import os
import shutil
from pathlib import Path
import uuid
import webbrowser
from threading import Event, Lock, Thread, Timer
import socket
import sqlite3
import sys
import smtplib
import subprocess
import re
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from werkzeug.utils import secure_filename
from datetime import datetime, timezone, timedelta
from dateutil.parser import parse as dateutil_parse
import traceback
import time
import json
import csv
import pytz
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
    redirect,
    flash,
    url_for,
    send_file,
)
from database import (
    get_db_connection,
    init_db,
    ensure_contact_handle,
    ensure_order_record_handle,
    ensure_record_handle_schema,
    generate_unique_contact_handle,
)
from data_paths import DATA_ROOT, ensure_data_root
from services.analytics import get_analytics_engine
from services.backup import BackupError, create_backup_archive, restore_backup_from_stream
from services.upgrade import UpgradeError, perform_upgrade
from services.records import (
    RecordValidationError,
    bootstrap_record_service,
    extract_mentions,
    get_record_service,
    reset_record_service,
    sync_record_mentions,
)

# Load environment variables from .env file
load_dotenv()

# --- App Initialization ---
RESTART_DELAY_SECONDS = 1.0

app = Flask(__name__, template_folder='templates')
app.config['JSON_SORT_KEYS'] = False
app.secret_key = os.urandom(24)

_db_bootstrapped = False


def _schedule_post_upgrade_restart(delay: float = RESTART_DELAY_SECONDS) -> None:
    """Launch a fresh process after a short delay and exit the current one."""

    if app.config.get('TESTING'):
        app.logger.info('Skipping restart scheduling while running tests')
        return

    python_executable = sys.executable or ""
    argv = list(sys.argv)
    if not argv:
        argv = [str(Path(__file__).resolve())]

    script_arg = argv[0] if argv else ""
    if not script_arg:
        script_arg = str(Path(__file__).resolve())
        argv[0] = script_arg
    else:
        candidate = Path(script_arg)
        if not candidate.is_absolute():
            resolved = (Path.cwd() / candidate).resolve()
            if resolved.exists():
                script_arg = str(resolved)
                argv[0] = script_arg

    if not python_executable:
        python_executable = script_arg
        launch_args = argv[1:]
    else:
        launch_args = argv
        try:
            exec_path = Path(python_executable)
            script_path = Path(script_arg)
            same_target = False
            if exec_path.exists() and script_path.exists():
                same_target = exec_path.resolve() == script_path.resolve()
            else:
                same_target = os.path.normcase(python_executable) == os.path.normcase(script_arg)
            if same_target:
                launch_args = argv[1:]
        except Exception:  # pragma: no cover - best effort comparison
            if python_executable == script_arg:
                launch_args = argv[1:]
    current_env = os.environ.copy()
    working_dir = Path.cwd()

    def _perform_restart() -> None:
        try:
            app.logger.info('Restarting process to finalize upgrade')
        except Exception:  # pragma: no cover - logging best effort
            pass

        try:
            subprocess.Popen(
                [python_executable, *launch_args],
                cwd=working_dir,
                env=current_env,
            )
        except Exception:  # pragma: no cover - process spawn best effort
            app.logger.exception('Failed to launch replacement process')
        finally:
            os._exit(0)

    Timer(delay, _perform_restart).start()


@app.before_request
def _ensure_database_initialized():
    """Guarantee the SQLite schema exists before serving any request."""
    global _db_bootstrapped
    if _db_bootstrapped:
        return
    try:
        init_db()
        bootstrap_conn = get_db_connection()
        try:
            bootstrap_record_service(bootstrap_conn)
        finally:
            bootstrap_conn.close()
        _db_bootstrapped = True
        if not app.config.get('TESTING'):
            _ensure_reminder_dispatcher_started()
    except Exception as exc:  # pragma: no cover - defensive logging
        app.logger.exception("Failed to initialize database before request: %s", exc)

ensure_data_root()

DATA_DIR = DATA_ROOT
SETTINGS_FILE = DATA_DIR / 'settings.json'
PASSWORDS_FILE = DATA_DIR / 'passwords.json'

NAV_SHORTCUT_CATALOG = [
    (
        'orders',
        {
            'label': 'Orders',
            'href': '/orders',
        },
    ),
    (
        'contacts',
        {
            'label': 'Contacts',
            'href': '/contacts',
        },
    ),
    (
        'analytics',
        {
            'label': 'Analytics',
            'href': '/analytics',
        },
    ),
    (
        'tasks',
        {
            'label': 'Tasks',
            'href': '/tasks',
        },
    ),
    (
        'reminders',
        {
            'label': 'Reminders',
            'href': '/reminders',
        },
    ),
    (
        'calendar',
        {
            'label': 'Calendar',
            'href': '/calendar',
        },
    ),
    (
        'passwords',
        {
            'label': 'Password Manager',
            'href': '/passwords',
        },
    ),
    (
        'firenotes',
        {
            'label': 'FireNotes',
            'href': '/firenotes',
        },
    ),
]

NAV_SHORTCUT_REGISTRY = {key: dict(value, id=key) for key, value in NAV_SHORTCUT_CATALOG}
DEFAULT_NAV_SHORTCUT_IDS = ['orders', 'contacts', 'analytics', 'tasks', 'reminders', 'calendar', 'passwords']

UPLOAD_FOLDER = DATA_DIR
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)

_REMINDER_DISPATCH_INTERVAL_SECONDS = 30
_reminder_dispatcher_lock = Lock()
_reminder_dispatcher_thread: Optional[Thread] = None
_reminder_dispatcher_started = False
_reminder_dispatcher_stop_event = Event()

def read_json_file(file_path):
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return {}
    with open(file_path, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            app.logger.error(f"JSONDecodeError for {file_path}")
            return {}

def write_json_file(file_path, data):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)


def read_password_entries():
    entries_blob = read_json_file(PASSWORDS_FILE)
    if isinstance(entries_blob, dict):
        return entries_blob.get('entries', [])
    if isinstance(entries_blob, list):
        return entries_blob
    return []


def write_password_entries(entries):
    write_json_file(PASSWORDS_FILE, {"entries": entries})


def _load_settings_dict() -> Dict[str, Any]:
    settings_blob = read_json_file(SETTINGS_FILE)
    return settings_blob if isinstance(settings_blob, dict) else {}


def _coerce_nav_shortcut_ids(candidate: Any) -> List[str]:
    if not isinstance(candidate, list):
        return []
    filtered: List[str] = []
    seen = set()
    for raw_value in candidate:
        if not isinstance(raw_value, str):
            continue
        shortcut_id = raw_value.strip()
        if shortcut_id in NAV_SHORTCUT_REGISTRY and shortcut_id not in seen:
            filtered.append(shortcut_id)
            seen.add(shortcut_id)
    return filtered


def get_selected_nav_shortcut_ids(settings: Optional[Dict[str, Any]] = None) -> List[str]:
    settings_dict = settings or _load_settings_dict()
    saved = _coerce_nav_shortcut_ids(settings_dict.get('nav_shortcuts'))
    return saved if saved else list(DEFAULT_NAV_SHORTCUT_IDS)


def get_navigation_shortcuts(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    shortcut_ids = get_selected_nav_shortcut_ids(settings=settings)
    return [dict(NAV_SHORTCUT_REGISTRY[shortcut_id]) for shortcut_id in shortcut_ids if shortcut_id in NAV_SHORTCUT_REGISTRY]


def get_available_nav_shortcuts() -> List[Dict[str, Any]]:
    return [dict(NAV_SHORTCUT_REGISTRY[key]) for key, _ in NAV_SHORTCUT_CATALOG]


def _fetch_attachments_for_logs(cursor, log_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not log_ids:
        return {}
    placeholders = ",".join(["?"] * len(log_ids))
    cursor.execute(
        f"""
        SELECT log_id, attachment_id, file_path, original_filename
        FROM order_log_attachments
        WHERE log_id IN ({placeholders})
        ORDER BY attachment_id ASC
        """,
        log_ids,
    )
    attachments_by_log: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in cursor.fetchall():
        file_path = row["file_path"]
        original_name = row["original_filename"]
        attachments_by_log[row["log_id"]].append(
            {
                "id": row["attachment_id"],
                "path": file_path,
                "name": original_name or os.path.basename(file_path),
            }
        )
    return dict(attachments_by_log)


def _remove_saved_files(file_paths: List[str]) -> None:
    for relative_path in file_paths:
        if not relative_path:
            continue
        absolute_path = os.path.join(app.config['UPLOAD_FOLDER'], relative_path)
        try:
            if os.path.exists(absolute_path):
                os.remove(absolute_path)
        except OSError:
            app.logger.warning("Failed to remove attachment file %s", absolute_path)
PASSWORD_SUBJECT_CLEAN_RE = re.compile(r"^(?:my|the)\s+", re.IGNORECASE)
DATE_FROM_RE = re.compile(r"\bfrom\s+(.+?)(?=\s+\b(?:to|through|until|till|by)\b|$)", re.IGNORECASE)
DATE_TO_RE = re.compile(r"\b(?:to|through|until|till|by)\s+(.+)", re.IGNORECASE)
REPORT_ID_RE = re.compile(r"run\s+(?:the\s+)?report\s+(?P<report>[A-Za-z0-9_.-]+)", re.IGNORECASE)
REPORT_LIST_RE = re.compile(r"\b(list|show)\s+(?:all\s+)?reports\b", re.IGNORECASE)
MAX_CHAT_HISTORY = 250
DEFAULT_CHAT_REACTOR = 'workspace-user'
FIRENOTES_MENTION = '@firenotes'

CLEAR_CATEGORY_ACTIONS = {
    'tasks': {'task_created'},
    'reminders': {'reminder_created', 'reminder_fired'},
    'events': {'calendar_event_created'},
}

CLEAR_CATEGORY_LABELS = {
    'tasks': ('task update', 'task updates'),
    'reminders': ('reminder update', 'reminder updates'),
    'events': ('event update', 'event updates'),
    'commands': ('command message', 'command messages'),
}

CHAT_CLEAR_SCAN_LIMIT = 500


def _resolve_timezone_setting() -> str:
    settings = read_json_file(SETTINGS_FILE)
    if isinstance(settings, dict):
        tz_value = (settings.get('timezone') or 'UTC').strip() or 'UTC'
    else:
        tz_value = 'UTC'
    try:
        pytz.timezone(tz_value)
    except Exception:
        tz_value = 'UTC'
    return tz_value


def _normalize_password_subject(subject: str) -> str:
    cleaned = subject.strip().strip("?!.,")
    cleaned = PASSWORD_SUBJECT_CLEAN_RE.sub('', cleaned)
    return cleaned.strip()


def _infer_password_subject(text: str) -> str:
    if not text:
        return ''
    lowered = text.strip()
    match = re.search(
        r"password(?:\s+(?:for|to|on|about|for the))?\s+(?P<subject>.+)",
        lowered,
        re.IGNORECASE,
    )
    if match:
        return _normalize_password_subject(match.group('subject'))
    alt = re.search(r"(?P<subject>.+?)\s+password\b", lowered, re.IGNORECASE)
    if alt:
        return _normalize_password_subject(alt.group('subject'))
    return _normalize_password_subject(lowered)


def _parse_json_column(value: Optional[str]) -> Optional[Any]:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _normalize_clear_author_handle(handle: str) -> Optional[str]:
    if not handle:
        return None
    cleaned = handle.strip()
    if cleaned.startswith('@'):
        cleaned = cleaned[1:]
    lowered = cleaned.lower()
    if lowered in {'you', 'me', 'user'}:
        return 'user'
    if lowered in {'firecoast', 'firenotes', 'assistant'}:
        return 'assistant'
    if lowered:
        return lowered
    return None


def _format_clear_target_label(author: Optional[str]) -> str:
    if not author:
        return 'messages'
    normalized = str(author).strip().lower()
    if normalized in {'user', 'you', 'me'}:
        return '@you'
    if normalized in {'assistant', 'firecoast', 'firenotes'}:
        return '@firecoast'
    if normalized.startswith('@'):
        return normalized
    return f"@{normalized}"


def _format_clear_category_label(category: str, count: int) -> str:
    singular, plural = CLEAR_CATEGORY_LABELS.get(category, (category, f"{category}s"))
    return singular if count == 1 else plural


def _collect_messages_for_clear(
    conn: sqlite3.Connection,
    note_id: str,
    *,
    author: Optional[str] = None,
    category: Optional[str] = None,
    count: Optional[int] = None,
    skip_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not note_id:
        return []
    normalized_skip = {str(value) for value in (skip_ids or []) if value}
    params: List[Any] = [note_id]
    query = (
        "SELECT id, note_id, author, content, metadata_json, attachments_json, created_at "
        "FROM firecoast_chat_messages WHERE note_id = ?"
    )
    if author:
        query += " AND LOWER(author) = ?"
        params.append(author.lower())
    query += " ORDER BY datetime(created_at) DESC, rowid DESC LIMIT ?"
    scan_limit = CHAT_CLEAR_SCAN_LIMIT
    if count is not None:
        scan_limit = max(25, min(CHAT_CLEAR_SCAN_LIMIT, count * 5))
    params.append(scan_limit)
    cursor = conn.execute(query, params)
    matches: List[Dict[str, Any]] = []
    allowed_actions = CLEAR_CATEGORY_ACTIONS.get(category or '', set())
    for row in cursor.fetchall():
        record = dict(row)
        message_id = str(record.get('id') or '')
        if not message_id or message_id in normalized_skip:
            continue
        metadata = _parse_json_column(record.get('metadata_json')) or {}
        content = (record.get('content') or '')
        normalized_content = content.lstrip()
        if category:
            if category == 'commands':
                if not normalized_content.startswith('.'):
                    continue
            else:
                action = str(metadata.get('action') or '').lower()
                if action not in allowed_actions:
                    continue
        matches.append(
            {
                'id': message_id,
                'author': record.get('author'),
                'metadata': metadata,
                'content': content,
            }
        )
        if count is not None and len(matches) >= count:
            break
    return matches


def _serialize_chat_row(
    row: sqlite3.Row,
    reaction_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    elif isinstance(row, tuple):
        row = {
            'id': row[0],
            'author': row[1],
            'content': row[2],
            'metadata_json': row[3],
            'created_at': row[4],
        }
    metadata = _parse_json_column(row.get('metadata_json'))
    attachments = _parse_json_column(row.get('attachments_json')) or []
    message = {
        'id': row.get('id'),
        'note_id': row.get('note_id'),
        'author': row.get('author'),
        'content': row.get('content'),
        'metadata': metadata,
        'attachments': attachments,
        'created_at': row.get('created_at'),
    }
    if reaction_map and message['id']:
        message['reactions'] = reaction_map.get(message['id'], [])
    else:
        message['reactions'] = []
    return message


def _summarize_chat_preview(content: Optional[str], limit: int = 140) -> str:
    if not content:
        return ''
    normalized = re.sub(r"\s+", ' ', str(content)).strip()
    if not normalized:
        return ''
    if len(normalized) <= limit:
        return normalized
    truncated = normalized[: max(1, limit - 1)].rstrip()
    return f"{truncated}…"


def _build_message_reference(
    conn: sqlite3.Connection,
    message_id: str,
) -> Optional[Dict[str, Any]]:
    normalized = (message_id or '').strip()
    if not normalized:
        return None
    row = _get_chat_message_row(conn, normalized)
    if not row:
        return None
    if isinstance(row, sqlite3.Row):
        data = dict(row)
    else:
        data = {
            'id': row[0] if len(row) > 0 else None,
            'note_id': row[1] if len(row) > 1 else None,
            'author': row[2] if len(row) > 2 else None,
            'content': row[3] if len(row) > 3 else None,
            'metadata_json': row[4] if len(row) > 4 else None,
            'attachments_json': row[5] if len(row) > 5 else None,
            'created_at': row[6] if len(row) > 6 else None,
        }
    reference: Dict[str, Any] = {
        'id': str(data.get('id') or normalized),
        'note_id': str(data.get('note_id') or ''),
        'author': data.get('author'),
        'created_at': data.get('created_at'),
        'preview': _summarize_chat_preview(data.get('content')),
    }
    if not reference['preview']:
        attachments = _parse_json_column(data.get('attachments_json'))
        if isinstance(attachments, list) and attachments:
            if len(attachments) == 1:
                attachment = attachments[0] or {}
                reference['preview'] = attachment.get('filename') or (
                    'Image attachment' if attachment.get('is_image') else 'Attachment'
                )
            else:
                reference['preview'] = f"{len(attachments)} attachments"
    note_id = reference.get('note_id')
    if note_id:
        note = _get_note(conn, note_id)
        if note and note.get('title'):
            reference['note_title'] = note.get('title')
    return reference


def _collect_chat_reactions(
    conn: sqlite3.Connection,
    message_ids: List[str],
    actor: str = DEFAULT_CHAT_REACTOR,
) -> Dict[str, List[Dict[str, Any]]]:
    normalized_ids = [str(message_id) for message_id in message_ids if message_id]
    if not normalized_ids:
        return {}
    placeholders = ','.join('?' for _ in normalized_ids)
    cursor = conn.execute(
        f"""
        SELECT message_id, emoji, reactor
        FROM firecoast_chat_reactions
        WHERE message_id IN ({placeholders})
        """,
        normalized_ids,
    )
    aggregated: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in cursor.fetchall():
        if isinstance(row, sqlite3.Row):
            message_id = row['message_id']
            emoji = row['emoji']
            reactor = row['reactor']
        else:
            message_id, emoji, reactor = row[:3]
        if not message_id or not emoji:
            continue
        message_bucket = aggregated.setdefault(str(message_id), {})
        entry = message_bucket.setdefault(
            emoji,
            {'emoji': emoji, 'count': 0, 'reacted': False, 'reactors': []},
        )
        entry['count'] += 1
        if reactor:
            entry['reactors'].append('You' if reactor == actor else str(reactor))
        if reactor == actor:
            entry['reacted'] = True
    summarized: Dict[str, List[Dict[str, Any]]] = {}
    for message_id, emoji_map in aggregated.items():
        sorted_entries = sorted(
            emoji_map.values(),
            key=lambda item: (-int(item.get('count') or 0), item.get('emoji') or ''),
        )
        summarized[message_id] = sorted_entries
    return summarized


def _store_chat_message(
    conn: sqlite3.Connection,
    note_id: str,
    author: str,
    content: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not note_id:
        raise ValueError('note_id is required for chat messages')
    metadata_json = json.dumps(metadata) if metadata else None
    attachments_json = json.dumps(attachments) if attachments else None
    message_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO firecoast_chat_messages (id, note_id, author, content, metadata_json, attachments_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (message_id, note_id, author, content, metadata_json, attachments_json),
    )
    conn.execute(
        "UPDATE firecoast_notes SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (note_id,),
    )
    cursor = conn.execute(
        """
        SELECT id, note_id, author, content, metadata_json, attachments_json, created_at
        FROM firecoast_chat_messages
        WHERE id = ?
        """,
        (message_id,),
    )
    row = cursor.fetchone()
    _refresh_note_mentions(conn, note_id)
    reaction_map = _collect_chat_reactions(conn, [message_id], DEFAULT_CHAT_REACTOR)
    return _serialize_chat_row(row, reaction_map)


def _list_chat_messages(conn: sqlite3.Connection, note_id: str, limit: int) -> List[Dict[str, Any]]:
    limit = max(1, min(MAX_CHAT_HISTORY, limit))
    cursor = conn.execute(
        """
        SELECT id, note_id, author, content, metadata_json, attachments_json, created_at
        FROM firecoast_chat_messages
        WHERE note_id = ?
        ORDER BY datetime(created_at) ASC, rowid ASC
        LIMIT ?
        """,
        (note_id, limit),
    )
    rows = cursor.fetchall()
    message_ids: List[str] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            message_ids.append(str(row['id']))
        else:
            message_ids.append(str(row[0]))
    reaction_map = _collect_chat_reactions(conn, message_ids, DEFAULT_CHAT_REACTOR)
    return [_serialize_chat_row(row, reaction_map) for row in rows]


def _toggle_chat_reaction(
    conn: sqlite3.Connection,
    message_id: str,
    emoji: str,
    reactor: str = DEFAULT_CHAT_REACTOR,
) -> Tuple[Dict[str, Any], str]:
    if not message_id:
        raise ValueError('message_id is required')
    normalized_emoji = (emoji or '').strip()
    if not normalized_emoji:
        raise ValueError('emoji is required')
    message_row = conn.execute(
        """
        SELECT id, note_id, author, content, metadata_json, attachments_json, created_at
        FROM firecoast_chat_messages
        WHERE id = ?
        """,
        (message_id,),
    ).fetchone()
    if not message_row:
        raise ValueError('Message not found.')
    existing = conn.execute(
        """
        SELECT id
        FROM firecoast_chat_reactions
        WHERE message_id = ? AND emoji = ? AND reactor = ?
        """,
        (message_id, normalized_emoji, reactor),
    ).fetchone()
    if existing:
        reaction_id = existing['id'] if isinstance(existing, sqlite3.Row) else existing[0]
        conn.execute("DELETE FROM firecoast_chat_reactions WHERE id = ?", (reaction_id,))
        action = 'removed'
    else:
        reaction_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO firecoast_chat_reactions (id, message_id, emoji, reactor)
            VALUES (?, ?, ?, ?)
            """,
            (reaction_id, message_id, normalized_emoji, reactor),
        )
        action = 'added'

    if emoji == '✅' and message_row:
        metadata = _parse_json_column(message_row['metadata_json']) if 'metadata_json' in message_row.keys() else None
        if isinstance(metadata, dict) and metadata.get('action') == 'task_created':
            task_payload = metadata.get('task') or {}
            task_id = task_payload.get('id') or metadata.get('task_id')
            if task_id:
                try:
                    service = get_record_service()
                    updated_payload = _normalize_reminder_payload(
                        conn,
                        {'completed': action == 'added'},
                        existing_id=str(task_id),
                    )
                    updated_record = service.update_record(
                        conn,
                        'reminder',
                        str(task_id),
                        updated_payload,
                        actor='firenotes-chat',
                    )
                    serialized_task = _serialize_reminder(updated_record['data'])
                    metadata['task'] = serialized_task
                    metadata['task_id'] = serialized_task.get('id')
                    try:
                        conn.execute(
                            """
                            UPDATE firecoast_chat_messages
                            SET metadata_json = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (json.dumps(metadata), message_id),
                        )
                    except sqlite3.OperationalError as exc:
                        if 'updated_at' in str(exc).lower():
                            conn.execute(
                                """
                                UPDATE firecoast_chat_messages
                                SET metadata_json = ?
                                WHERE id = ?
                                """,
                                (json.dumps(metadata), message_id),
                            )
                        else:
                            raise
                    message_row = _get_chat_message_row(conn, message_id) or message_row
                except Exception as exc:  # pragma: no cover - defensive logging
                    app.logger.exception("Failed to synchronise task completion: %s", exc)

    reaction_map = _collect_chat_reactions(conn, [message_id], reactor)
    serialized = _serialize_chat_row(message_row, reaction_map)
    return serialized, action


def _get_chat_message_row(conn: sqlite3.Connection, message_id: str) -> Optional[sqlite3.Row]:
    if not message_id:
        return None
    cursor = conn.execute(
        """
        SELECT id, note_id, author, content, metadata_json, attachments_json, created_at
        FROM firecoast_chat_messages
        WHERE id = ?
        """,
        (message_id,),
    )
    return cursor.fetchone()


def _serialize_chat_message(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    include_reactions: bool = True,
) -> Dict[str, Any]:
    if not row:
        raise ValueError('Message not found.')
    reaction_map: Optional[Dict[str, List[Dict[str, Any]]]] = None
    if include_reactions:
        message_id = row['id'] if isinstance(row, sqlite3.Row) else None
        if message_id:
            reaction_map = _collect_chat_reactions(conn, [message_id], DEFAULT_CHAT_REACTOR)
    return _serialize_chat_row(row, reaction_map)


def _clone_chat_attachments(original: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if not original:
        return []
    clones: List[Dict[str, Any]] = []
    base_dir = Path(app.config['UPLOAD_FOLDER'])
    for attachment in original:
        if not isinstance(attachment, dict):
            continue
        source_path = attachment.get('path')
        filename = attachment.get('filename') or 'attachment'
        content_type = attachment.get('content_type') or 'application/octet-stream'
        is_image = bool(attachment.get('is_image'))
        if source_path:
            source_file = base_dir / source_path
        else:
            source_file = None
        cloned: Dict[str, Any] = {
            'id': str(uuid.uuid4()),
            'filename': filename,
            'content_type': content_type,
            'is_image': is_image,
        }
        if source_file and source_file.exists():
            sanitized = secure_filename(filename) or 'attachment'
            unique_name = f"{uuid.uuid4().hex}_{sanitized}"
            relative_path = os.path.join('firecoast', unique_name)
            destination = base_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            cloned['path'] = relative_path.replace('\\', '/')
            cloned['url'] = f"/data/{cloned['path']}"
            cloned['size'] = destination.stat().st_size
            cloned['is_image'] = content_type.startswith('image/') if content_type else is_image
        else:
            for key in ('path', 'url', 'size'):
                if key in attachment:
                    cloned[key] = attachment[key]
        clones.append(cloned)
    return clones


def _edit_chat_message(conn: sqlite3.Connection, message_id: str, content: str) -> Dict[str, Any]:
    if not message_id:
        raise ValueError('message_id is required.')
    trimmed = (content or '').strip()
    if not trimmed:
        raise ValueError('content is required.')
    row = _get_chat_message_row(conn, message_id)
    if not row:
        raise ValueError('Message not found.')
    note_id = row['note_id'] if isinstance(row, sqlite3.Row) else None
    metadata = _parse_json_column(row['metadata_json']) or {}
    metadata['edited_at'] = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(metadata)
    conn.execute(
        """
        UPDATE firecoast_chat_messages
        SET content = ?, metadata_json = ?
        WHERE id = ?
        """,
        (trimmed, metadata_json, message_id),
    )
    if note_id:
        conn.execute(
            "UPDATE firecoast_notes SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (note_id,),
        )
        _refresh_note_mentions(conn, note_id)
    refreshed = _get_chat_message_row(conn, message_id)
    return _serialize_chat_message(conn, refreshed)


def _delete_chat_message(conn: sqlite3.Connection, message_id: str) -> Dict[str, Any]:
    if not message_id:
        raise ValueError('message_id is required.')
    row = _get_chat_message_row(conn, message_id)
    if not row:
        raise ValueError('Message not found.')
    note_id = row['note_id'] if isinstance(row, sqlite3.Row) else None
    attachments = _parse_json_column(row['attachments_json']) or []
    base_dir = Path(app.config['UPLOAD_FOLDER'])
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        rel_path = attachment.get('path')
        if not rel_path:
            continue
        file_path = base_dir / rel_path
        try:
            if file_path.exists() and file_path.is_file():
                file_path.unlink()
        except OSError:
            app.logger.warning('Failed to remove attachment %s for message %s', rel_path, message_id)
    conn.execute("DELETE FROM firecoast_chat_reactions WHERE message_id = ?", (message_id,))
    conn.execute("DELETE FROM firecoast_chat_messages WHERE id = ?", (message_id,))
    if note_id:
        conn.execute(
            "UPDATE firecoast_notes SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (note_id,),
        )
        _refresh_note_mentions(conn, note_id)
    return {'id': message_id, 'note_id': note_id, 'deleted': True}


def _forward_chat_message(
    conn: sqlite3.Connection,
    message_id: str,
    target_note_id: str,
    *,
    actor: str = 'user',
) -> Dict[str, Any]:
    if not message_id:
        raise ValueError('message_id is required.')
    if not target_note_id:
        raise ValueError('target_note_id is required.')
    original_row = _get_chat_message_row(conn, message_id)
    if not original_row:
        raise ValueError('Message not found.')
    note = _get_note(conn, target_note_id)
    if not note:
        raise ValueError('Target note not found.')
    original = _serialize_chat_row(original_row, None)
    attachments = _clone_chat_attachments(original.get('attachments'))
    forwarded_reference = _build_message_reference(conn, str(original.get('id') or ''))
    if forwarded_reference:
        forwarded_metadata = {'forwarded_from': forwarded_reference}
    else:
        forwarded_metadata = {
            'forwarded_from': {
                'id': original.get('id'),
                'note_id': original.get('note_id'),
                'author': original.get('author'),
                'created_at': original.get('created_at'),
            }
        }
    forwarded_body = original.get('content') or ''
    stored = _store_chat_message(
        conn,
        target_note_id,
        actor,
        forwarded_body,
        metadata=forwarded_metadata,
        attachments=attachments,
    )
    return stored


NOTE_HANDLE_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def _refresh_note_mentions(conn: sqlite3.Connection, note_id: str) -> None:
    if not note_id:
        return
    cursor = conn.execute(
        """
        SELECT content
        FROM firecoast_chat_messages
        WHERE note_id = ?
        ORDER BY datetime(created_at) DESC, rowid DESC
        """,
        (note_id,),
    )
    handles: List[str] = []
    snippet_source: Optional[str] = None
    for row in cursor.fetchall():
        if isinstance(row, sqlite3.Row):
            try:
                content = row['content']
            except (KeyError, TypeError):
                content = None
        else:
            content = row[0] if row and len(row) else None
        if not content:
            continue
        extracted = extract_mentions(content)
        if not extracted:
            continue
        handles.extend(extracted)
        if not snippet_source:
            snippet_source = content
    unique_handles = sorted({handle.lower() for handle in handles})
    sync_record_mentions(conn, unique_handles, 'firecoast_note', str(note_id), snippet_source or '')


def _normalize_note_title(value: Optional[str]) -> str:
    if value is None:
        return 'Untitled note'
    text = value.strip()
    return text or 'Untitled note'


def _generate_note_handle(conn: sqlite3.Connection, note_id: str, title: str) -> str:
    base_slug = NOTE_HANDLE_SANITIZE_RE.sub('-', title.strip().lower()).strip('-')
    if not base_slug:
        base_slug = f'note-{note_id.split("-")[0]}'
    candidate = f'firecoast-{base_slug}'
    suffix = 2
    while True:
        row = conn.execute(
            "SELECT entity_id FROM record_handles WHERE handle = ?",
            (candidate,),
        ).fetchone()
        if not row or row['entity_id'] == note_id:
            return candidate
        candidate = f'firecoast-{base_slug}-{suffix}'
        suffix += 1


def _upsert_note_handle(conn: sqlite3.Connection, note_id: str, title: str) -> str:
    ensure_record_handle_schema(conn)
    handle = _generate_note_handle(conn, note_id, title)
    search_blob = title.strip().lower()
    conn.execute(
        "DELETE FROM record_handles WHERE entity_type = 'firecoast_note' AND entity_id = ?",
        (note_id,),
    )
    conn.execute(
        """
        INSERT INTO record_handles (handle, entity_type, entity_id, display_name, search_blob)
        VALUES (?, 'firecoast_note', ?, ?, ?)
        """,
        (handle, note_id, title, search_blob),
    )
    return handle


def _serialize_note_row(row: sqlite3.Row) -> Dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    return {
        'id': row.get('id'),
        'title': row.get('title'),
        'handle': row.get('handle'),
        'created_at': row.get('created_at'),
        'updated_at': row.get('updated_at'),
        'last_message_preview': row.get('last_message_preview'),
        'last_message_at': row.get('last_message_at'),
    }


def _get_note(conn: sqlite3.Connection, note_id: str) -> Optional[Dict[str, Any]]:
    cursor = conn.execute(
        """
        SELECT
            n.id,
            n.title,
            rh.handle,
            n.created_at,
            n.updated_at,
            (
                SELECT content
                FROM firecoast_chat_messages m
                WHERE m.note_id = n.id
                ORDER BY datetime(m.created_at) DESC, m.rowid DESC
                LIMIT 1
            ) AS last_message_preview,
            (
                SELECT created_at
                FROM firecoast_chat_messages m
                WHERE m.note_id = n.id
                ORDER BY datetime(m.created_at) DESC, m.rowid DESC
                LIMIT 1
            ) AS last_message_at
        FROM firecoast_notes n
        LEFT JOIN record_handles rh ON rh.entity_type = 'firecoast_note' AND rh.entity_id = n.id
        WHERE n.id = ?
        """,
        (note_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return _serialize_note_row(row)


def _create_note(conn: sqlite3.Connection, title: str) -> Dict[str, Any]:
    note_id = str(uuid.uuid4())
    normalized_title = _normalize_note_title(title)
    conn.execute(
        """
        INSERT INTO firecoast_notes (id, title)
        VALUES (?, ?)
        """,
        (note_id, normalized_title),
    )
    handle = _upsert_note_handle(conn, note_id, normalized_title)
    note = _get_note(conn, note_id)
    if note is not None:
        note['handle'] = handle
    return note or {'id': note_id, 'title': normalized_title, 'handle': handle}


def _delete_note(conn: sqlite3.Connection, note_id: str) -> None:
    if not note_id:
        return
    cursor = conn.execute(
        "SELECT attachments_json FROM firecoast_chat_messages WHERE note_id = ?",
        (note_id,),
    )
    attachment_paths: List[str] = []
    for row in cursor.fetchall():
        try:
            raw = row['attachments_json'] if isinstance(row, sqlite3.Row) else row[0]
        except (KeyError, IndexError, TypeError):
            raw = None
        attachments = _parse_json_column(raw)
        if isinstance(attachments, list):
            for attachment in attachments:
                if isinstance(attachment, dict):
                    path_value = attachment.get('path') or ''
                    if path_value:
                        attachment_paths.append(str(path_value))
    conn.execute("DELETE FROM firecoast_chat_messages WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM firecoast_notes WHERE id = ?", (note_id,))
    conn.execute(
        "DELETE FROM record_handles WHERE entity_type = 'firecoast_note' AND entity_id = ?",
        (note_id,),
    )
    conn.execute(
        "DELETE FROM record_mentions WHERE context_entity_type = 'firecoast_note' AND context_entity_id = ?",
        (note_id,),
    )
    conn.execute(
        "DELETE FROM record_mentions WHERE mentioned_entity_type = 'firecoast_note' AND mentioned_entity_id = ?",
        (note_id,),
    )
    base_dir = Path(app.config['UPLOAD_FOLDER']).resolve()
    for relative_path in attachment_paths:
        normalized = str(relative_path).replace('\\', '/').lstrip('/')
        candidate = (base_dir / normalized).resolve()
        try:
            if candidate.exists() and candidate.is_file() and base_dir in candidate.parents:
                candidate.unlink()
        except Exception:
            app.logger.debug('Failed to remove attachment for note %s: %s', note_id, candidate)


def _list_notes(conn: sqlite3.Connection, query: Optional[str], limit: int = 200) -> List[Dict[str, Any]]:
    search_text = (query or '').strip().lower()
    params: List[Any] = []
    sql = [
        """
        SELECT
            n.id,
            n.title,
            rh.handle,
            n.created_at,
            n.updated_at,
            (
                SELECT content
                FROM firecoast_chat_messages m
                WHERE m.note_id = n.id
                ORDER BY datetime(m.created_at) DESC, m.rowid DESC
                LIMIT 1
            ) AS last_message_preview,
            (
                SELECT created_at
                FROM firecoast_chat_messages m
                WHERE m.note_id = n.id
                ORDER BY datetime(m.created_at) DESC, m.rowid DESC
                LIMIT 1
            ) AS last_message_at
        FROM firecoast_notes n
        LEFT JOIN record_handles rh ON rh.entity_type = 'firecoast_note' AND rh.entity_id = n.id
        """
    ]
    if search_text:
        sql.append(
            "WHERE (")
        sql.append("lower(n.title) LIKE ?")
        params.append(f'%{search_text}%')
        sql.append(" OR lower(rh.handle) LIKE ?")
        params.append(f'%{search_text}%')
        sql.append(")")
    sql.append("ORDER BY datetime(n.updated_at) DESC, datetime(n.created_at) DESC LIMIT ?")
    params.append(max(1, limit))
    cursor = conn.execute("\n".join(sql), params)
    return [_serialize_note_row(row) for row in cursor.fetchall()]


def _save_note_attachments(files: List[Any]) -> List[Dict[str, Any]]:
    if not files:
        return []
    saved: List[Dict[str, Any]] = []
    base_dir = Path(app.config['UPLOAD_FOLDER']) / 'firecoast'
    base_dir.mkdir(parents=True, exist_ok=True)
    for storage in files:
        if not storage or not getattr(storage, 'filename', None):
            continue
        original_name = storage.filename
        sanitized = secure_filename(original_name) or 'attachment'
        unique_name = f"{uuid.uuid4().hex}_{sanitized}"
        relative_path = os.path.join('firecoast', unique_name)
        full_path = base_dir / unique_name
        storage.save(full_path)
        size = full_path.stat().st_size if full_path.exists() else None
        content_type = storage.mimetype or 'application/octet-stream'
        attachment_id = str(uuid.uuid4())
        normalized_path = relative_path.replace('\\', '/')
        saved.append(
            {
                'id': attachment_id,
                'filename': original_name,
                'path': normalized_path,
                'url': f"/data/{normalized_path}",
                'content_type': content_type,
                'size': size,
                'is_image': content_type.startswith('image/'),
            }
        )
    return saved


def _format_datetime_for_display(
    value: Optional[str],
    timezone_name: str,
    *,
    include_time: bool = True,
) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = dateutil_parse(value)
    except (TypeError, ValueError):
        return value
    try:
        tz = pytz.timezone(timezone_name)
        parsed = parsed.astimezone(tz)
    except Exception:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    if include_time:
        return parsed.strftime('%b %d, %Y %I:%M %p %Z')
    return parsed.strftime('%b %d, %Y')


def _format_event_window(event_payload: Dict[str, Any], timezone_name: str) -> str:
    start_value = event_payload.get('start_at')
    end_value = event_payload.get('end_at') or start_value
    all_day = bool(event_payload.get('all_day'))
    try:
        tz = pytz.timezone(timezone_name)
    except Exception:
        tz = timezone.utc
    try:
        start_dt = dateutil_parse(start_value)
        end_dt = dateutil_parse(end_value)
    except (TypeError, ValueError):
        return start_value or ''
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    else:
        start_dt = start_dt.astimezone(tz)
    if end_dt.tzinfo is None:
        end_dt = tz.localize(end_dt)
    else:
        end_dt = end_dt.astimezone(tz)
    if all_day:
        if start_dt.date() == end_dt.date():
            return start_dt.strftime('%b %d, %Y')
        return f"{start_dt.strftime('%b %d, %Y')} – {end_dt.strftime('%b %d, %Y')}"
    if start_dt.date() == end_dt.date():
        return f"{start_dt.strftime('%b %d, %Y %I:%M %p')} – {end_dt.strftime('%I:%M %p %Z')}"
    return f"{start_dt.strftime('%b %d, %Y %I:%M %p')} – {end_dt.strftime('%b %d, %Y %I:%M %p %Z')}"

def _handle_clear_command(
    conn: sqlite3.Connection, message: Dict[str, Any]
) -> Dict[str, Any]:
    note_id = (message.get('note_id') or '').strip()
    message_id = str(message.get('id') or '')
    content = (message.get('content') or '')
    result: Dict[str, Any] = {
        'status': 'error',
        'reason': 'missing_note',
        'criteria': {},
        'deleted_message_ids': [],
        'cleared_target_count': 0,
        'message': 'Note not found for clear command.' if not note_id else None,
        'command_message_id': message_id or None,
    }
    if not note_id or not message_id:
        return result

    body = content[len('.clear'):].strip()
    criteria: Dict[str, Any] = {}
    if not body:
        result.update(
            {
                'status': 'error',
                'reason': 'missing_target',
                'message': "Tell me what to clear. Try `.clear 4`, `.clear 4 @firecoast`, or `.clear tasks`.",
                'criteria': criteria,
            }
        )
        return result

    tokens = body.split()
    category: Optional[str] = None
    author: Optional[str] = None
    count: Optional[int] = None
    pointer = 0

    keyword = tokens[0].lower() if tokens else ''
    if keyword in CLEAR_CATEGORY_ACTIONS or keyword == 'commands':
        category = keyword
        pointer = 1
        if len(tokens) > 1:
            head = tokens[1].lower()
            if head == 'all':
                count = None
            elif head.isdigit():
                count = max(1, int(head))
    else:
        if keyword == 'all':
            count = None
            pointer += 1
        elif keyword.isdigit():
            count = max(1, int(keyword))
            pointer += 1
        if pointer < len(tokens):
            handle_token = tokens[pointer]
            normalized_author = _normalize_clear_author_handle(handle_token)
            if not normalized_author:
                result.update(
                    {
                        'status': 'error',
                        'reason': 'unknown_author',
                        'message': f"I don't recognize {handle_token}. Try @you or @firecoast.",
                        'criteria': criteria,
                    }
                )
                return result
            author = normalized_author
            pointer += 1
        elif count is None:
            # A lone token that isn't a category must be an author shorthand.
            normalized_author = _normalize_clear_author_handle(tokens[0]) if tokens else None
            if normalized_author:
                author = normalized_author
            else:
                result.update(
                    {
                        'status': 'error',
                        'reason': 'missing_target',
                        'message': "I couldn't understand that clear command. Try `.clear 4` or `.clear tasks`.",
                        'criteria': criteria,
                    }
                )
                return result
        if pointer < len(tokens):
            tail = tokens[pointer].lower()
            if tail == 'all':
                count = None
            elif tail.isdigit():
                count = max(1, int(tail))

    if category:
        criteria['category'] = category
        criteria['count'] = 'all' if count is None else count
    elif author is not None or count is not None:
        if author is not None:
            criteria['author'] = author
        if count is not None:
            criteria['count'] = count
    else:
        result.update(
            {
                'status': 'error',
                'reason': 'missing_target',
                'message': "I couldn't understand that clear command. Try `.clear 4` or `.clear tasks`.",
                'criteria': criteria,
            }
        )
        return result

    targets = _collect_messages_for_clear(
        conn,
        note_id,
        author=author,
        category=category,
        count=count,
    )

    seen_ids: Set[str] = set()
    filtered_targets: List[Dict[str, Any]] = []
    for target in targets:
        target_id = str(target.get('id') or '')
        if not target_id or target_id in seen_ids:
            continue
        seen_ids.add(target_id)
        filtered_targets.append(target)

    cleared_target_count = len([entry for entry in filtered_targets if str(entry.get('id') or '') != message_id])
    status = 'success'
    reason = None
    message_text = None

    if not filtered_targets:
        status = 'noop'
        reason = 'no_matches'
        message_text = "I couldn't find any matching messages to clear."

    delete_ids: List[str] = []
    if status in {'success', 'noop'}:
        if message_id not in seen_ids:
            filtered_targets.insert(0, {'id': message_id, 'author': message.get('author')})
            seen_ids.add(message_id)
        for target in filtered_targets:
            target_id = str(target.get('id') or '')
            if not target_id:
                continue
            try:
                _delete_chat_message(conn, target_id)
            except ValueError:
                continue
            delete_ids.append(target_id)
    else:
        cleared_target_count = 0

    result.update(
        {
            'status': status,
            'reason': reason,
            'message': message_text,
            'criteria': criteria,
            'deleted_message_ids': delete_ids,
            'cleared_target_count': cleared_target_count,
        }
    )
    return result

def _handle_event_command(conn: sqlite3.Connection, note_id: str, content: str) -> List[Dict[str, Any]]:
    body = content[len('.event'):].strip()
    if not body:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            "I need event details. Try `.event Team sync | tomorrow 3pm | tomorrow 4pm | HQ | bring deck`.",
            metadata={'action': 'error', 'context': 'event', 'reason': 'missing_details'},
        )
        return [message]
    parts = [segment.strip() for segment in body.split('|')]
    title = parts[0] if parts else ''
    start_part = parts[1] if len(parts) > 1 else ''
    end_part = parts[2] if len(parts) > 2 else ''
    location_part = parts[3] if len(parts) > 3 else ''
    notes_part = parts[4] if len(parts) > 4 else ''
    timezone_name = _resolve_timezone_setting()
    if not title:
        title = 'Untitled event'
    if not start_part:
        try:
            tz = pytz.timezone(timezone_name)
        except Exception:
            tz = timezone.utc
        start_part = datetime.now(tz).isoformat()
    payload = {
        'title': title,
        'start_at': start_part,
        'timezone': timezone_name,
        'location': location_part,
        'notes': notes_part or f'Created from chat command: {body}',
    }
    if end_part:
        payload['end_at'] = end_part
    service = get_record_service()
    try:
        normalized = _normalize_calendar_event_payload(conn, payload)
        created = service.create_record(conn, 'calendar_event', normalized, actor='firenotes-chat')
        event_payload = _serialize_calendar_event(created['data'])
        window_label = _format_event_window(event_payload, event_payload.get('timezone') or timezone_name)
        message_text = f"Scheduled \"{event_payload['title']}\" for {window_label}."
        metadata = {
            'action': 'calendar_event_created',
            'event': event_payload,
        }
        return [_store_chat_message(conn, note_id, 'assistant', message_text, metadata=metadata)]
    except (ValueError, RecordValidationError) as exc:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            f"I couldn't create that event: {exc}",
            metadata={'action': 'error', 'context': 'event', 'reason': str(exc)},
        )
        return [message]


def _format_reminder_due(reminder_payload: Dict[str, Any]) -> str:
    due_value = reminder_payload.get('due_at')
    timezone_name = reminder_payload.get('timezone') or _resolve_timezone_setting()
    if not due_value:
        return 'no due date'
    label = _format_datetime_for_display(
        due_value,
        timezone_name,
        include_time=bool(reminder_payload.get('due_has_time')),
    )
    return f"due {label}" if label else 'no due date'


def _handle_reminder_command(conn: sqlite3.Connection, note_id: str, content: str) -> List[Dict[str, Any]]:
    body = content[len('.reminder'):].strip()
    if not body:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            "Let's add a reminder. Try `.reminder Follow up | next Monday 9am | include tracking link`.",
            metadata={'action': 'error', 'context': 'reminder', 'reason': 'missing_details'},
        )
        return [message]
    timer_seconds = None
    due_part = ''
    notes_part = ''
    title = ''
    if '|' in body:
        parts = [segment.strip() for segment in body.split('|')]
        title = parts[0] if parts else ''
        raw_due = parts[1] if len(parts) > 1 else ''
        notes_part = parts[2] if len(parts) > 2 else ''
        timer_candidate = _parse_timer_expression(raw_due) if raw_due else None
        if timer_candidate:
            timer_seconds = timer_candidate
        else:
            due_part = raw_due
    else:
        timer_candidate, remainder = _split_timer_prefix(body)
        if timer_candidate:
            timer_seconds = timer_candidate
            title = remainder
        else:
            title = body
    if not title:
        title = 'Reminder'
    timezone_name = _resolve_timezone_setting()
    payload = {
        'title': title,
        'timezone': timezone_name,
        'kind': 'reminder',
        'context_note_id': note_id,
    }
    if notes_part:
        payload['notes'] = notes_part
    if due_part:
        payload['due_at'] = due_part
    if timer_seconds:
        payload['timer_seconds'] = timer_seconds
    service = get_record_service()
    try:
        normalized = _normalize_reminder_payload(conn, payload)
        created = service.create_record(conn, 'reminder', normalized, actor='firenotes-chat')
        reminder_payload = _serialize_reminder(created['data'])
        raw_title = (reminder_payload.get('title') or '').strip()
        base_label = f"Reminder {raw_title}" if raw_title else 'Reminder'
        timer_value = reminder_payload.get('timer_seconds')
        if timer_value:
            timer_label = _format_timer_label(int(timer_value))
            message_text = f"{base_label} in {timer_label}."
        else:
            due_value = reminder_payload.get('due_at')
            if due_value:
                timezone_name = reminder_payload.get('timezone') or _resolve_timezone_setting()
                include_time = bool(reminder_payload.get('due_has_time'))
                display_value = _format_datetime_for_display(
                    due_value,
                    timezone_name,
                    include_time=include_time,
                )
                if display_value:
                    message_text = f"{base_label} due {display_value}."
                else:
                    message_text = f"{base_label} saved."
            else:
                message_text = f"{base_label} saved."
        metadata = {
            'action': 'reminder_created',
            'reminder': reminder_payload,
        }
        return [_store_chat_message(conn, note_id, 'assistant', message_text, metadata=metadata)]
    except (ValueError, RecordValidationError) as exc:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            f"I couldn't save that reminder: {exc}",
            metadata={'action': 'error', 'context': 'reminder', 'reason': str(exc)},
        )
        return [message]


def _handle_task_command(conn: sqlite3.Connection, note_id: str, content: str) -> List[Dict[str, Any]]:
    body = content[len('.task'):].strip()
    if not body:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            "Let's capture a task. Try `.task Call @client | tomorrow 9am | include call agenda`.",
            metadata={'action': 'error', 'context': 'task', 'reason': 'missing_details'},
        )
        return [message]
    timer_seconds = None
    due_part = ''
    notes_part = ''
    title = ''
    if '|' in body:
        parts = [segment.strip() for segment in body.split('|')]
        title = parts[0] if parts else ''
        raw_due = parts[1] if len(parts) > 1 else ''
        notes_part = parts[2] if len(parts) > 2 else ''
        timer_candidate = _parse_timer_expression(raw_due) if raw_due else None
        if timer_candidate:
            timer_seconds = timer_candidate
        else:
            due_part = raw_due
    else:
        timer_candidate, remainder = _split_timer_prefix(body)
        if timer_candidate:
            timer_seconds = timer_candidate
            title = remainder
        else:
            title = body
    if not title:
        title = 'Task'
    timezone_name = _resolve_timezone_setting()
    payload = {
        'title': title,
        'timezone': timezone_name,
        'kind': 'task',
    }
    if notes_part:
        payload['notes'] = notes_part
    if due_part:
        payload['due_at'] = due_part
    if timer_seconds:
        payload['timer_seconds'] = timer_seconds
    service = get_record_service()
    try:
        normalized = _normalize_reminder_payload(conn, payload)
        created = service.create_record(conn, 'reminder', normalized, actor='firenotes-chat')
        task_payload = _serialize_reminder(created['data'])
        timer_value = task_payload.get('timer_seconds')
        if timer_value:
            timer_label = _format_timer_label(int(timer_value))
            message_text = f"Task timer set for {timer_label}. React with ✅ when complete."
        else:
            due_label = _format_reminder_due(task_payload)
            if due_label == 'no due date':
                message_text = "Task created. React with ✅ when complete."
            else:
                message_text = f"Task {due_label}. React with ✅ when complete."
        metadata = {
            'action': 'task_created',
            'task': task_payload,
            'task_id': task_payload.get('id'),
        }
        return [_store_chat_message(conn, note_id, 'assistant', message_text, metadata=metadata)]
    except (ValueError, RecordValidationError) as exc:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            f"I couldn't save that task: {exc}",
            metadata={'action': 'error', 'context': 'task', 'reason': str(exc)},
        )
        return [message]


def _normalise_date_fragment(fragment: Optional[str]) -> Optional[str]:
    if not fragment:
        return None
    cleaned = fragment.strip().strip('.,')
    if not cleaned:
        return None
    try:
        parsed = dateutil_parse(cleaned)
    except (TypeError, ValueError):
        return None
    return parsed.date().isoformat()


def _extract_date_filters(text: str) -> Tuple[Optional[str], Optional[str]]:
    start_fragment = None
    end_fragment = None
    if text:
        match_from = DATE_FROM_RE.search(text)
        if match_from:
            start_fragment = match_from.group(1)
        match_to = DATE_TO_RE.search(text)
        if match_to:
            end_fragment = match_to.group(1)
    return _normalise_date_fragment(start_fragment), _normalise_date_fragment(end_fragment)


def _summarize_report_result(result: Dict[str, Any]) -> str:
    name = result.get('name') or result.get('id') or 'report'
    summary_entries = result.get('summary') or []
    if not summary_entries:
        return f"Report \"{name}\" is ready."
    lines = [f"Report \"{name}\" is ready:"]
    for entry in summary_entries[:4]:
        label = entry.get('label') or entry.get('id')
        display = entry.get('display')
        value = display if display not in (None, '') else entry.get('value')
        lines.append(f"- {label}: {value}")
    if len(summary_entries) > 4:
        lines.append(f"…plus {len(summary_entries) - 4} more metrics.")
    return "\n".join(lines)


def _list_reports_message(conn: sqlite3.Connection, note_id: str) -> List[Dict[str, Any]]:
    engine = get_analytics_engine()
    definitions = engine.list_report_definitions(conn)
    if not definitions:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            "I couldn't find any analytics reports.",
            metadata={'action': 'report_list', 'reports': []},
        )
        return [message]
    lines = ["Available reports:"]
    for definition in definitions:
        lines.append(f"- {definition['id']}: {definition.get('name', '')}")
    metadata = {'action': 'report_list', 'reports': definitions}
    message = _store_chat_message(conn, note_id, 'assistant', "\n".join(lines), metadata=metadata)
    return [message]


def _run_report_via_chat(
    conn: sqlite3.Connection,
    report_id: str,
    context_text: str,
    *,
    note_id: str,
) -> List[Dict[str, Any]]:
    report_id = report_id.strip()
    if not report_id:
        return _list_reports_message(conn, note_id)
    params: Dict[str, Any] = {}
    start_value, end_value = _extract_date_filters(context_text)
    if start_value:
        params['start_date'] = start_value
    if end_value:
        params['end_date'] = end_value
    engine = get_analytics_engine()
    timezone_name = _resolve_timezone_setting()
    try:
        result = engine.run_report(conn, report_id, params, timezone_name=timezone_name)
    except KeyError:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            f"I don't know a report named '{report_id}'. Try `.report list` for options.",
            metadata={'action': 'error', 'context': 'report', 'requested': report_id},
        )
        return [message]
    except ValueError as exc:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            f"I couldn't run that report: {exc}",
            metadata={'action': 'error', 'context': 'report', 'requested': report_id},
        )
        return [message]
    summary_text = _summarize_report_result(result)
    metadata = {'action': 'report_run', 'report': result, 'params': params}
    message = _store_chat_message(conn, note_id, 'assistant', summary_text, metadata=metadata)
    return [message]


def _handle_report_command(conn: sqlite3.Connection, note_id: str, content: str) -> List[Dict[str, Any]]:
    body = content[len('.report'):].strip()
    if not body or body.lower() in {'list', 'help'}:
        return _list_reports_message(conn, note_id)
    tokens = body.split(None, 1)
    report_id = tokens[0]
    remainder = tokens[1] if len(tokens) > 1 else ''
    return _run_report_via_chat(conn, report_id, remainder, note_id=note_id)


def _handle_password_lookup(conn: sqlite3.Connection, note_id: str, text: str) -> List[Dict[str, Any]]:
    subject = _infer_password_subject(text)
    if not subject:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            "Let me know which service you'd like me to look up.",
            metadata={'action': 'password_lookup', 'query': '', 'matches': []},
        )
        return [message]
    entries = read_password_entries()
    needle = subject.lower()
    matches = []
    for entry in entries:
        haystack = " ".join(
            [
                str(entry.get('service', '')),
                str(entry.get('username', '')),
                str(entry.get('notes', '')),
            ]
        ).lower()
        if needle in haystack:
            matches.append(entry)
    matches.sort(key=lambda item: (item.get('service') or '').lower())
    if not matches:
        message = _store_chat_message(
            conn,
            note_id,
            'assistant',
            f"I couldn't find anything for \"{subject}\" in the password vault.",
            metadata={'action': 'password_lookup', 'query': subject, 'matches': []},
        )
        return [message]
    preview_lines = [
        f"Here {'are' if len(matches) > 1 else 'is'} what I found for \"{subject}\":"
    ]
    for entry in matches[:5]:
        service = entry.get('service') or '(unknown)'
        username = entry.get('username') or '—'
        password_value = entry.get('password') or '—'
        preview_lines.append(f"- {service}: {username} / {password_value}")
    if len(matches) > 5:
        preview_lines.append(f"…and {len(matches) - 5} more saved credentials.")
    metadata = {
        'action': 'password_lookup',
        'query': subject,
        'matches': matches,
    }
    message = _store_chat_message(conn, note_id, 'assistant', "\n".join(preview_lines), metadata=metadata)
    return [message]


def _firenotes_help(conn: sqlite3.Connection, note_id: str) -> List[Dict[str, Any]]:
    help_text = "\n".join(
        [
            "Here's what I can do:",
            "• `.reminder Title | tomorrow 9am | notes` to capture a follow-up.",
            "• `.task Call client | Friday 10am | confirm details` to create a task you can check off with ✅.",
            "• `.event Planning session | Friday 2pm | 3pm | HQ | bring slides` to schedule a calendar block.",
            "• `.report list` or `@firenotes run report orders_overview from 2024-01-01 to 2024-03-31` to pull analytics.",
            "• `@firenotes what's my password for Example` to retrieve saved credentials.",
        ]
    )
    message = _store_chat_message(conn, note_id, 'assistant', help_text, metadata={'action': 'help'})
    return [message]


def _handle_firenotes_mention(conn: sqlite3.Connection, note_id: str, content: str) -> List[Dict[str, Any]]:
    text = content[len(FIRENOTES_MENTION):].strip()
    if not text:
        return _firenotes_help(conn, note_id)
    lowered = text.lower()
    if 'password' in lowered:
        return _handle_password_lookup(conn, note_id, text)
    if REPORT_LIST_RE.search(text):
        return _list_reports_message(conn, note_id)
    report_match = REPORT_ID_RE.search(text)
    if report_match:
        report_id = report_match.group('report')
        remainder = text[report_match.end():]
        return _run_report_via_chat(conn, report_id, remainder, note_id=note_id)
    if lowered.startswith('run '):
        tokens = text.split(None, 2)
        if len(tokens) >= 2:
            report_id = tokens[1]
            remainder = tokens[2] if len(tokens) > 2 else ''
            return _run_report_via_chat(conn, report_id, remainder, note_id=note_id)
    if 'help' in lowered or 'what can you do' in lowered:
        return _firenotes_help(conn, note_id)
    fallback = _store_chat_message(
        conn,
        note_id,
        'assistant',
        "I'm here! Ask for `.report list`, saved passwords, reminders, or events whenever you need them.",
        metadata={'action': 'fallback'},
    )
    return [fallback]


def _handle_chat_message(conn: sqlite3.Connection, message: Dict[str, Any]) -> List[Dict[str, Any]]:
    author = (message.get('author') or '').lower()
    if author != 'user':
        return []
    content = (message.get('content') or '').strip()
    if not content:
        return []
    note_id = message.get('note_id') or ''
    if not note_id:
        return []
    lowered = content.lower()
    if lowered.startswith('.event'):
        return _handle_event_command(conn, note_id, content)
    if lowered.startswith('.reminder'):
        return _handle_reminder_command(conn, note_id, content)
    if lowered.startswith('.task'):
        return _handle_task_command(conn, note_id, content)
    if lowered.startswith('.report'):
        return _handle_report_command(conn, note_id, content)
    if lowered.startswith(FIRENOTES_MENTION):
        return _handle_firenotes_mention(conn, note_id, content)
    return []

PHONE_CLEAN_RE = re.compile(r"\D+")
CALENDAR_HANDLE_SANITIZE_RE = re.compile(r"[^a-z0-9.-]+")
TIMER_PREFIX_RE = re.compile(r"^\s*(?P<timer>(?:\d+\s*[smhd]\s*)+)(?=\s|$)", re.IGNORECASE)
TIMER_COMPONENT_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)


def _normalize_phone_digits(value):
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        value = str(int(value))
    digits = PHONE_CLEAN_RE.sub("", str(value))
    return digits[:20]


def _ensure_primary(entries):
    for entry in entries:
        if entry.get("isPrimary"):
            return entries
    if entries:
        entries[0]["isPrimary"] = True
    return entries


def _infer_address_kind(kind_value, label_value):
    text = (kind_value or "").strip().lower()
    label_text = (label_value or "").strip().lower()
    if "ship" in text or "ship" in label_text:
        return "shipping"
    if "bill" in text or "bill" in label_text:
        return "billing"
    return "other"


def _address_has_fields(entry):
    if not isinstance(entry, dict):
        return False
    for field in ("street", "city", "state", "postalCode"):
        value = entry.get(field)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return True
        else:
            if str(value).strip():
                return True
    return False


def _pick_address_candidate(addresses, preferred_kind, *, exclude_id=None):
    """Select a reasonable address for the preferred kind with sensible fallbacks."""
    if not addresses:
        return None

    normalized_kind = (preferred_kind or "").strip().lower()
    keyword = "bill" if normalized_kind == "billing" else "ship"

    def _filter_candidates(exclude=True):
        filtered = []
        for entry in addresses:
            if not _address_has_fields(entry):
                continue
            if exclude and exclude_id and entry.get("id") == exclude_id:
                continue
            filtered.append(entry)
        return filtered

    candidates = _filter_candidates(exclude=True)
    if not candidates and exclude_id:
        candidates = _filter_candidates(exclude=False)
    if not candidates:
        return None

    for entry in candidates:
        if (entry.get("kind") or "").lower() == normalized_kind:
            return entry
    for entry in candidates:
        if keyword in (entry.get("label") or "").strip().lower():
            return entry
    for entry in candidates:
        if entry.get("isPrimary"):
            return entry
    return candidates[0]


def _assign_address_kinds(addresses):
    if not addresses:
        return addresses

    shipping_entry = _pick_address_candidate(addresses, "shipping")
    billing_entry = _pick_address_candidate(
        addresses,
        "billing",
        exclude_id=shipping_entry.get("id") if shipping_entry else None,
    )

    for entry in addresses:
        if shipping_entry and entry.get("id") == shipping_entry.get("id"):
            entry["kind"] = "shipping"
        elif billing_entry and entry.get("id") == billing_entry.get("id"):
            entry["kind"] = "billing"
        else:
            entry["kind"] = "other"
    return addresses


def _sanitize_email_entries(entries):
    sanitized = []
    seen = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        raw_value = entry.get("value") or entry.get("email")
        if raw_value in (None, ""):
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        lower = value.lower()
        if lower in seen:
            continue
        seen.add(lower)
        sanitized.append({
            "id": str(entry.get("id") or uuid.uuid4()),
            "label": (entry.get("label") or "Email").strip() or "Email",
            "value": value,
            "isPrimary": bool(entry.get("isPrimary")),
        })
    return _ensure_primary(sanitized)


def _sanitize_phone_entries(entries):
    sanitized = []
    seen = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        raw_value = entry.get("value") or entry.get("phone") or entry.get("number")
        digits = _normalize_phone_digits(raw_value)
        if not digits or digits in seen:
            continue
        seen.add(digits)
        sanitized.append({
            "id": str(entry.get("id") or uuid.uuid4()),
            "label": (entry.get("label") or "Phone").strip() or "Phone",
            "value": digits,
            "isPrimary": bool(entry.get("isPrimary")),
        })
    return _ensure_primary(sanitized)


def _normalize_contact_display_value(value):
    """Coerce contact-related values into a clean display-friendly string."""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        if lowered in {"[contact not found]", "contact not found", "[no contact found]", "no contact found"}:
            return ""
        return cleaned
    return str(value).strip()


def _sanitize_address_entries(entries):
    sanitized = []
    seen = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        street = (entry.get("street") or entry.get("address") or entry.get("addressLine1") or "").strip()
        city = (entry.get("city") or "").strip()
        state = (entry.get("state") or "").strip()
        postal = (entry.get("postalCode") or entry.get("zip") or entry.get("zipCode") or "").strip()
        if not any([street, city, state, postal]):
            continue
        label = (entry.get("label") or "").strip()
        kind = _infer_address_kind(entry.get("kind"), label)
        if not label:
            label = "Address"

        key = (street.lower(), city.lower(), state.lower(), postal.lower())
        normalized = {
            "id": str(entry.get("id") or uuid.uuid4()),
            "label": label,
            "kind": kind,
            "street": street,
            "city": city,
            "state": state,
            "postalCode": postal,
            "isPrimary": bool(entry.get("isPrimary")),
        }

        if key in seen:
            existing_index = seen[key]
            existing = sanitized[existing_index]
            if (not existing.get("label")) or existing.get("label") == "Address":
                existing["label"] = normalized["label"]
            if existing.get("kind") == "other" and kind in {"shipping", "billing"}:
                existing["kind"] = kind
            if kind == "shipping" and existing.get("kind") == "billing":
                existing["kind"] = "shipping"
            if normalized.get("isPrimary"):
                existing["isPrimary"] = True
            continue

        seen[key] = len(sanitized)
        sanitized.append(normalized)
    if sanitized:
        # Ensure at least one address is marked primary, preferring shipping and billing entries
        kind_order = ["shipping", "billing"]
        if not any(addr.get("isPrimary") for addr in sanitized):
            assigned = False
            for preferred_kind in kind_order:
                for addr in sanitized:
                    if addr["kind"] == preferred_kind:
                        addr["isPrimary"] = True
                        assigned = True
                        break
                if assigned:
                    break
            if not assigned:
                sanitized[0]["isPrimary"] = True
    return _assign_address_kinds(sanitized)


def _prepare_contact_details_for_storage(payload, *, force=False):
    details_source = payload.get("contactDetails") or {}
    raw_addresses = []
    raw_emails = []
    raw_phones = []

    if isinstance(details_source, dict):
        raw_addresses.extend(details_source.get("addresses") or [])
        raw_emails.extend(details_source.get("emails") or [])
        raw_phones.extend(details_source.get("phones") or [])

    if isinstance(payload.get("addresses"), list):
        raw_addresses.extend(payload.get("addresses") or [])
    if isinstance(payload.get("emails"), list):
        raw_emails.extend(payload.get("emails") or [])
    if isinstance(payload.get("phones"), list):
        raw_phones.extend(payload.get("phones") or [])

    if any(key in payload for key in ("shippingAddress", "shippingCity", "shippingState", "shippingZipCode")):
        raw_addresses.append({
            "id": payload.get("shippingAddressId"),
            "label": "Address",
            "kind": "shipping",
            "street": payload.get("shippingAddress", ""),
            "city": payload.get("shippingCity", ""),
            "state": payload.get("shippingState", ""),
            "postalCode": payload.get("shippingZipCode", ""),
            "isPrimary": True,
        })
    if any(key in payload for key in ("billingAddress", "billingCity", "billingState", "billingZipCode")):
        raw_addresses.append({
            "id": payload.get("billingAddressId"),
            "label": "Address",
            "kind": "billing",
            "street": payload.get("billingAddress", ""),
            "city": payload.get("billingCity", ""),
            "state": payload.get("billingState", ""),
            "postalCode": payload.get("billingZipCode", ""),
            "isPrimary": True,
        })

    if "email" in payload:
        raw_emails.append({"value": payload.get("email"), "label": "Email", "isPrimary": True})
    if "phone" in payload:
        raw_phones.append({"value": payload.get("phone"), "label": "Phone", "isPrimary": True})

    addresses = _sanitize_address_entries(raw_addresses)
    emails = _sanitize_email_entries(raw_emails)
    phones = _sanitize_phone_entries(raw_phones)

    shipping_entry = _pick_address_candidate(addresses, "shipping")
    billing_entry = _pick_address_candidate(
        addresses,
        "billing",
        exclude_id=shipping_entry.get("id") if shipping_entry else None,
    )
    primary_email = emails[0]["value"] if emails else ""
    primary_phone = phones[0]["value"] if phones else ""

    if force or addresses or emails or phones:
        details = {
            "addresses": addresses,
            "emails": emails,
            "phones": phones,
        }
    else:
        details = {"addresses": [], "emails": [], "phones": []}

    return {
        "details": details,
        "primary_email": primary_email,
        "primary_phone": primary_phone,
        "shipping": shipping_entry,
        "billing": billing_entry,
        "had_addresses": bool(raw_addresses),
        "had_emails": bool(raw_emails),
        "had_phones": bool(raw_phones),
    }


def _deserialize_contact_details(contact_dict, raw_details):
    parsed = {}
    if isinstance(raw_details, str) and raw_details.strip():
        try:
            parsed = json.loads(raw_details)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(raw_details, dict):
        parsed = raw_details

    addresses = _sanitize_address_entries(parsed.get("addresses", []))
    emails = _sanitize_email_entries(parsed.get("emails", []))
    phones = _sanitize_phone_entries(parsed.get("phones", []))

    def _has_address_kind(kind):
        return any(addr["kind"] == kind for addr in addresses)

    shipping_fields = (
        contact_dict.get("shippingAddress"),
        contact_dict.get("shippingCity"),
        contact_dict.get("shippingState"),
        contact_dict.get("shippingZipCode"),
    )
    if any(field for field in shipping_fields) and not _has_address_kind("shipping"):
        addresses.append({
            "id": str(uuid.uuid4()),
            "label": "Address",
            "kind": "shipping",
            "street": contact_dict.get("shippingAddress", "") or "",
            "city": contact_dict.get("shippingCity", "") or "",
            "state": contact_dict.get("shippingState", "") or "",
            "postalCode": contact_dict.get("shippingZipCode", "") or "",
            "isPrimary": not _has_address_kind("shipping"),
        })

    billing_fields = (
        contact_dict.get("billingAddress"),
        contact_dict.get("billingCity"),
        contact_dict.get("billingState"),
        contact_dict.get("billingZipCode"),
    )
    if any(field for field in billing_fields) and not _has_address_kind("billing"):
        addresses.append({
            "id": str(uuid.uuid4()),
            "label": "Address",
            "kind": "billing",
            "street": contact_dict.get("billingAddress", "") or "",
            "city": contact_dict.get("billingCity", "") or "",
            "state": contact_dict.get("billingState", "") or "",
            "postalCode": contact_dict.get("billingZipCode", "") or "",
            "isPrimary": not _has_address_kind("billing"),
        })

    fallback_email = (contact_dict.get("email") or "").strip()
    if fallback_email and not any((entry.get("value") or "").lower() == fallback_email.lower() for entry in emails):
        emails.append({
            "id": str(uuid.uuid4()),
            "label": "Email",
            "value": fallback_email,
            "isPrimary": not emails,
        })

    fallback_phone = _normalize_phone_digits(contact_dict.get("phone"))
    if fallback_phone and not any(entry.get("value") == fallback_phone for entry in phones):
        phones.append({
            "id": str(uuid.uuid4()),
            "label": "Phone",
            "value": fallback_phone,
            "isPrimary": not phones,
        })

    _ensure_primary(emails)
    _ensure_primary(phones)
    if addresses:
        for preferred_kind in ("shipping", "billing"):
            candidates = [addr for addr in addresses if addr["kind"] == preferred_kind]
            if candidates and not any(addr.get("isPrimary") for addr in candidates):
                candidates[0]["isPrimary"] = True
        if not any(addr.get("isPrimary") for addr in addresses):
            addresses[0]["isPrimary"] = True
        _assign_address_kinds(addresses)

    return {
        "addresses": addresses,
        "emails": emails,
        "phones": phones,
    }


def serialize_contact_row(row):
    if row is None:
        return None
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    contact = {
        "id": row["id"],
        "companyName": row["company_name"] if "company_name" in keys else None,
        "contactName": row["contact_name"] if "contact_name" in keys else None,
        "email": row["email"] if "email" in keys else None,
        "phone": row["phone"] if "phone" in keys else None,
        "billingAddress": row["billing_address"] if "billing_address" in keys else None,
        "billingCity": row["billing_city"] if "billing_city" in keys else None,
        "billingState": row["billing_state"] if "billing_state" in keys else None,
        "billingZipCode": row["billing_zip_code"] if "billing_zip_code" in keys else None,
        "shippingAddress": row["shipping_address"] if "shipping_address" in keys else None,
        "shippingCity": row["shipping_city"] if "shipping_city" in keys else None,
        "shippingState": row["shipping_state"] if "shipping_state" in keys else None,
        "shippingZipCode": row["shipping_zip_code"] if "shipping_zip_code" in keys else None,
        "handle": row["handle"] if "handle" in keys else None,
        "notes": row["notes"] if "notes" in keys else None,
    }

    for key in (
        "companyName",
        "contactName",
        "email",
        "phone",
        "billingAddress",
        "billingCity",
        "billingState",
        "billingZipCode",
        "shippingAddress",
        "shippingCity",
        "shippingState",
        "shippingZipCode",
        "handle",
    ):
        contact[key] = _normalize_contact_display_value(contact.get(key))
    if "created_at" in keys:
        contact["createdAt"] = row["created_at"]
    if "updated_at" in keys:
        contact["updatedAt"] = row["updated_at"]

    raw_details = row["details_json"] if "details_json" in keys else None
    contact_details = _deserialize_contact_details(contact, raw_details)
    contact["contactDetails"] = contact_details

    primary_email = contact_details["emails"][0]["value"] if contact_details["emails"] else contact.get("email") or ""
    primary_phone = contact_details["phones"][0]["value"] if contact_details["phones"] else contact.get("phone") or ""

    contact["email"] = primary_email
    contact["phone"] = primary_phone

    shipping_entry = next((addr for addr in contact_details["addresses"] if addr["kind"] == "shipping"), None)
    billing_entry = next((addr for addr in contact_details["addresses"] if addr["kind"] == "billing"), None)

    if shipping_entry:
        contact["shippingAddress"] = shipping_entry["street"]
        contact["shippingCity"] = shipping_entry["city"]
        contact["shippingState"] = shipping_entry["state"]
        contact["shippingZipCode"] = shipping_entry["postalCode"]
    if billing_entry:
        contact["billingAddress"] = billing_entry["street"]
        contact["billingCity"] = billing_entry["city"]
        contact["billingState"] = billing_entry["state"]
        contact["billingZipCode"] = billing_entry["postalCode"]

    return contact


def _build_contact_display(contact_dict):
    if not contact_dict:
        return None
    display_name = (
        (contact_dict.get("contactName") or "").strip()
        or (contact_dict.get("companyName") or "").strip()
        or (contact_dict.get("email") or "").strip()
        or (contact_dict.get("handle") or "").strip()
    )
    if not display_name:
        display_name = "Unnamed contact"
    return {
        **contact_dict,
        "displayName": display_name,
    }


def _safe_parse_float(value, default=0.0):
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(value, str):
        cleaned = value.strip().replace('$', '').replace(',', '')
        if not cleaned:
            return default
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def _normalize_discount_entries(discounts_payload, line_items_payload):
    normalized_entries = []
    total_discount_cents = 0

    if not isinstance(line_items_payload, list):
        line_items_payload = []

    line_item_totals = {}
    for item in line_items_payload:
        if not isinstance(item, dict):
            continue
        raw_identifier = (
            item.get('id')
            or item.get('line_item_id')
            or item.get('client_reference_id')
        )
        if raw_identifier in (None, ''):
            continue
        key = str(raw_identifier)
        try:
            quantity = int(item.get('quantity', 0))
        except (TypeError, ValueError):
            quantity = 0
        try:
            price_cents = int(item.get('price', 0))
        except (TypeError, ValueError):
            price_cents = 0
        quantity = max(0, quantity)
        price_cents = max(0, price_cents)
        line_item_totals[key] = quantity * price_cents

    if not isinstance(discounts_payload, list):
        return normalized_entries, 0

    all_line_item_keys = list(line_item_totals.keys())

    for entry in discounts_payload:
        if not isinstance(entry, dict):
            continue

        entry_type_raw = entry.get('type', 'fixed')
        entry_type = entry_type_raw.lower() if isinstance(entry_type_raw, str) else 'fixed'
        if entry_type not in {'percentage', 'fixed'}:
            entry_type = 'fixed'

        label_raw = entry.get('label')
        label = label_raw.strip() if isinstance(label_raw, str) else ''

        applies_raw = entry.get('appliesTo') if isinstance(entry.get('appliesTo'), list) else []
        applies_clean = []
        applies_keys = []
        for candidate in applies_raw:
            candidate_key = str(candidate)
            if candidate_key in line_item_totals:
                applies_clean.append(candidate)
                applies_keys.append(candidate_key)

        if applies_keys:
            base_keys = applies_keys
        else:
            base_keys = all_line_item_keys

        base_total_cents = sum(line_item_totals.get(key, 0) for key in base_keys)
        amount_cents = 0

        if entry_type == 'percentage':
            percentage_value = max(0.0, _safe_parse_float(entry.get('value', 0.0)))
            amount_cents = int(round(base_total_cents * (percentage_value / 100.0))) if base_total_cents > 0 else 0
        else:
            fixed_value = max(0.0, _safe_parse_float(entry.get('value', 0.0)))
            fixed_cents = int(round(fixed_value * 100))
            amount_cents = min(fixed_cents, base_total_cents)

        amount_cents = max(0, amount_cents)
        total_discount_cents += amount_cents

        normalized_entries.append({
            'id': entry.get('id'),
            'label': label,
            'type': entry_type,
            'value': entry.get('value'),
            'appliesTo': applies_clean,
            'amount_cents': amount_cents,
        })

    return normalized_entries, total_discount_cents


def serialize_order(cursor, order_row, user_timezone, include_logs=False):
    order_dict = dict(order_row)

    if order_dict.get('order_date'):
        utc_date = dateutil_parse(order_dict['order_date']).replace(tzinfo=pytz.utc)
        order_dict['order_date'] = utc_date.astimezone(user_timezone).isoformat()

    contact_snapshot = {
        "id": order_dict.pop('contact_id'),
        "companyName": order_dict.pop('contact_company_name', None),
        "contactName": order_dict.pop('contact_contact_name', None),
        "email": order_dict.pop('contact_email', None),
        "phone": order_dict.pop('contact_phone', None),
        "billingAddress": order_dict.pop('contact_billing_address', None),
        "billingCity": order_dict.pop('contact_billing_city', None),
        "billingState": order_dict.pop('contact_billing_state', None),
        "billingZipCode": order_dict.pop('contact_billing_zip_code', None),
        "shippingAddress": order_dict.pop('contact_shipping_address', None),
        "shippingCity": order_dict.pop('contact_shipping_city', None),
        "shippingState": order_dict.pop('contact_shipping_state', None),
        "shippingZipCode": order_dict.pop('contact_shipping_zip_code', None),
        "handle": order_dict.pop('contact_handle', None),
        "notes": order_dict.pop('contact_notes', None),
    }

    for key in (
        "companyName",
        "contactName",
        "email",
        "phone",
        "billingAddress",
        "billingCity",
        "billingState",
        "billingZipCode",
        "shippingAddress",
        "shippingCity",
        "shippingState",
        "shippingZipCode",
        "handle",
    ):
        contact_snapshot[key] = _normalize_contact_display_value(contact_snapshot.get(key))
    contact_details_raw = order_dict.pop('contact_details_json', None)
    contact_details = _deserialize_contact_details(contact_snapshot, contact_details_raw)
    contact_snapshot['contactDetails'] = contact_details

    if contact_details['emails']:
        contact_snapshot['email'] = contact_details['emails'][0]['value']
    if contact_details['phones']:
        contact_snapshot['phone'] = contact_details['phones'][0]['value']

    shipping_entry = next((addr for addr in contact_details['addresses'] if addr['kind'] == 'shipping'), None)
    if shipping_entry:
        contact_snapshot['shippingAddress'] = shipping_entry['street']
        contact_snapshot['shippingCity'] = shipping_entry['city']
        contact_snapshot['shippingState'] = shipping_entry['state']
        contact_snapshot['shippingZipCode'] = shipping_entry['postalCode']
    billing_entry = next((addr for addr in contact_details['addresses'] if addr['kind'] == 'billing'), None)
    if billing_entry:
        contact_snapshot['billingAddress'] = billing_entry['street']
        contact_snapshot['billingCity'] = billing_entry['city']
        contact_snapshot['billingState'] = billing_entry['state']
        contact_snapshot['billingZipCode'] = billing_entry['postalCode']

    if not contact_snapshot['id']:
        contact_snapshot = {
            "id": None,
            "companyName": "",
            "contactName": "",
            "email": "",
            "phone": "",
            "billingAddress": "",
            "billingCity": "",
            "billingState": "",
            "billingZipCode": "",
            "shippingAddress": "",
            "shippingCity": "",
            "shippingState": "",
            "shippingZipCode": "",
            "handle": None,
            "notes": "",
            "contactDetails": {"addresses": [], "emails": [], "phones": []},
        }

    order_id = order_dict['order_id']

    cursor.execute(
        """
        SELECT line_item_id, catalog_item_id, name, description, quantity, price_per_unit_cents, package_id, client_reference_id
        FROM order_line_items
        WHERE order_id = ?
        ORDER BY line_item_id ASC
        """,
        (order_id,)
    )
    order_dict['lineItems'] = [
        {
            'id': li['client_reference_id'] or li['line_item_id'],
            'catalogItemId': li['catalog_item_id'],
            'name': li['name'],
            'description': li['description'] or '',
            'quantity': li['quantity'],
            'price': li['price_per_unit_cents'],
            'packageId': li['package_id'],
        }
        for li in cursor.fetchall()
    ]

    cursor.execute(
        "SELECT status, status_date FROM order_status_history WHERE order_id = ? ORDER BY status_date ASC",
        (order_id,)
    )
    status_history = []
    for history_row in cursor.fetchall():
        utc_date = dateutil_parse(history_row['status_date']).replace(tzinfo=pytz.utc)
        status_history.append({
            'status': history_row['status'],
            'date': utc_date.astimezone(user_timezone).isoformat()
        })
    order_dict['statusHistory'] = status_history

    cursor.execute(
        """
            SELECT c.id, c.company_name, c.contact_name, c.email, c.phone, c.billing_address, c.billing_city,
                   c.billing_state, c.billing_zip_code, c.shipping_address, c.shipping_city, c.shipping_state,
                   c.shipping_zip_code, c.handle, c.notes, c.created_at, c.updated_at
            FROM order_contact_links ocl
            JOIN contacts c ON ocl.contact_id = c.id
            WHERE ocl.order_id = ?
            ORDER BY LOWER(COALESCE(c.contact_name, c.company_name, c.email, c.handle, ''))
        """,
        (order_id,)
    )
    additional_contacts = [serialize_contact_row(row) for row in cursor.fetchall()]
    additional_contacts = [_build_contact_display(contact) for contact in additional_contacts]

    primary_contact_display = _build_contact_display(contact_snapshot)

    order_dict['contactInfo'] = contact_snapshot
    order_dict['primaryContact'] = primary_contact_display
    order_dict['primaryContactId'] = primary_contact_display['id'] if primary_contact_display else None
    order_dict['additionalContacts'] = additional_contacts
    order_dict['additionalContactIds'] = [contact['id'] for contact in additional_contacts if contact]

    title_value = order_dict.pop('title', None)
    order_dict['title'] = title_value or ''
    order_dict['id'] = order_dict.pop('order_id')
    order_dict['display_id'] = order_dict.pop('display_id')
    order_dict['date'] = order_dict.pop('order_date')
    order_dict['total'] = order_dict.pop('total_amount')

    shipping_cost = order_dict.pop('estimated_shipping_cost')
    try:
        shipping_value = float(shipping_cost) if shipping_cost is not None else 0.0
    except (TypeError, ValueError):
        shipping_value = 0.0
    order_dict['estimatedShipping'] = f"{shipping_value:.2f}" if shipping_value else "0.00"

    tax_amount_value = order_dict.pop('tax_amount', 0) or 0
    try:
        tax_amount_value = float(tax_amount_value)
    except (TypeError, ValueError):
        tax_amount_value = 0.0
    order_dict['taxAmount'] = f"{tax_amount_value:.2f}" if tax_amount_value else "0.00"

    raw_discounts = order_dict.pop('discounts_json', None)
    discounts_list = []
    if isinstance(raw_discounts, str) and raw_discounts.strip():
        try:
            discounts_list = json.loads(raw_discounts)
        except json.JSONDecodeError:
            discounts_list = []
    elif isinstance(raw_discounts, (list, tuple)):
        discounts_list = list(raw_discounts)
    order_dict['discounts'] = discounts_list

    discount_total_value = order_dict.pop('discount_total', 0) or 0
    try:
        discount_total_value = float(discount_total_value)
    except (TypeError, ValueError):
        discount_total_value = 0.0
    order_dict['discountTotal'] = int(round(discount_total_value * 100))

    order_dict['estimatedShippingDate'] = order_dict.pop('estimated_shipping_date')

    raw_priority = order_dict.pop('priority_level', None)
    raw_channel = order_dict.pop('fulfillment_channel', None)
    raw_reference = order_dict.pop('customer_reference', None)

    order_dict['priorityLevel'] = raw_priority.strip() if isinstance(raw_priority, str) else ''
    order_dict['fulfillmentChannel'] = raw_channel.strip() if isinstance(raw_channel, str) else ''
    order_dict['customerReference'] = raw_reference.strip() if isinstance(raw_reference, str) else ''

    order_dict.pop('scent_option', None)
    order_dict.pop('name_drop', None)
    order_dict['shippingAddress'] = order_dict.pop('shipping_address', '')
    order_dict['shippingCity'] = order_dict.pop('shipping_city', '')
    order_dict['shippingState'] = order_dict.pop('shipping_state', '')
    order_dict['shippingZipCode'] = order_dict.pop('shipping_zip_code', '')
    order_dict['billingAddress'] = order_dict.pop('billing_address', '')
    order_dict['billingCity'] = order_dict.pop('billing_city', '')
    order_dict['billingState'] = order_dict.pop('billing_state', '')
    order_dict['billingZipCode'] = order_dict.pop('billing_zip_code', '')
    order_dict['signatureDataUrl'] = order_dict.pop('signature_data_url')

    if include_logs:
        cursor.execute(
            "SELECT log_id, timestamp, user, action, details, note, attachment_path FROM order_logs WHERE order_id = ? ORDER BY timestamp DESC",
            (order_dict['id'],)
        )
        log_rows = cursor.fetchall()
        attachments_map = _fetch_attachments_for_logs(cursor, [row['log_id'] for row in log_rows])
        logs = []
        for log_row in log_rows:
            log_dict = dict(log_row)
            if log_dict.get('timestamp'):
                naive_date = dateutil_parse(log_dict['timestamp'])
                utc_date = pytz.utc.localize(naive_date)
                log_dict['timestamp'] = utc_date.astimezone(user_timezone).isoformat()
            attachments_payload = attachments_map.get(log_dict['log_id'], [])
            log_dict['attachments'] = attachments_payload
            if attachments_payload:
                log_dict['attachment_path'] = attachments_payload[0]['path']
            logs.append(log_dict)
        order_dict['orderLogs'] = logs

    return order_dict


def update_or_create_contact(cursor, contact_info_payload):
    if not contact_info_payload:
        return None

    provided_id = contact_info_payload.get("id")
    raw_company = contact_info_payload.get("companyName")
    raw_contact = contact_info_payload.get("contactName")

    if provided_id and (raw_company is None or raw_contact is None):
        cursor.execute("SELECT company_name, contact_name FROM contacts WHERE id = ?", (provided_id,))
        existing_names = cursor.fetchone()
    else:
        existing_names = None

    company_name = (raw_company if raw_company is not None else (existing_names["company_name"] if existing_names else ""))
    contact_name = (raw_contact if raw_contact is not None else (existing_names["contact_name"] if existing_names else ""))
    company_name = (company_name or "").strip()
    contact_name = (contact_name or "").strip()
    if not company_name and not contact_name:
        return provided_id

    details_info = _prepare_contact_details_for_storage(contact_info_payload, force=True)
    details_json_str = json.dumps(details_info['details'])

    email = details_info['primary_email'] or contact_info_payload.get("email", "")
    phone = details_info['primary_phone'] or _normalize_phone_digits(contact_info_payload.get("phone", ""))

    shipping_entry = details_info['shipping']
    billing_entry = details_info['billing']

    shipping_address = (shipping_entry['street'] if shipping_entry else contact_info_payload.get("shippingAddress", ""))
    shipping_city = (shipping_entry['city'] if shipping_entry else contact_info_payload.get("shippingCity", ""))
    shipping_state = (shipping_entry['state'] if shipping_entry else contact_info_payload.get("shippingState", ""))
    shipping_zip_code = (shipping_entry['postalCode'] if shipping_entry else contact_info_payload.get("shippingZipCode", ""))

    billing_address = (billing_entry['street'] if billing_entry else contact_info_payload.get("billingAddress", ""))
    billing_city = (billing_entry['city'] if billing_entry else contact_info_payload.get("billingCity", ""))
    billing_state = (billing_entry['state'] if billing_entry else contact_info_payload.get("billingState", ""))
    billing_zip_code = (billing_entry['postalCode'] if billing_entry else contact_info_payload.get("billingZipCode", ""))
    notes = contact_info_payload.get("notes")
    provided_handle = contact_info_payload.get("handle")
    if provided_handle:
        provided_handle = provided_handle.lower().lstrip('@')

    final_contact_id = provided_id
    if provided_id:
        field_values = [company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code,
                        shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json_str, provided_id]
        cursor.execute(
            "UPDATE contacts SET company_name = ?, contact_name = ?, email = ?, phone = ?, billing_address = ?, billing_city = ?, "
            "billing_state = ?, billing_zip_code = ?, shipping_address = ?, shipping_city = ?, shipping_state = ?, shipping_zip_code = ?, details_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(field_values)
        )
        if cursor.rowcount == 0:
            final_contact_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO contacts (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, "
                "billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (final_contact_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state,
                 billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json_str,
                 provided_handle or generate_unique_contact_handle(cursor, contact_name or company_name), notes)
            )
        else:
            if provided_handle:
                cursor.execute("UPDATE contacts SET handle = ? WHERE id = ?", (provided_handle, provided_id))
            if notes is not None:
                cursor.execute("UPDATE contacts SET notes = ? WHERE id = ?", (notes, provided_id))
            ensure_contact_handle(cursor, provided_id, contact_name or company_name)
    else:
        final_contact_id = str(uuid.uuid4())
        handle_to_use = provided_handle or generate_unique_contact_handle(cursor, contact_name or company_name)
        cursor.execute(
            "INSERT INTO contacts (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (final_contact_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state,
             billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json_str, handle_to_use, notes)
        )
    ensured_handle = ensure_contact_handle(cursor, final_contact_id, contact_name or company_name)
    if ensured_handle:
        service = get_record_service()
        display_value = contact_name or company_name or ensured_handle
        search_blob = ' '.join(filter(None, [contact_name, company_name, email, ensured_handle])).lower()
        service.register_handle(
            cursor.connection,
            'contact',
            final_contact_id,
            ensured_handle,
            display_name=display_value,
            search_blob=search_blob,
        )

    final_notes = notes
    if final_contact_id and notes is None:
        cursor.execute("SELECT notes FROM contacts WHERE id = ?", (final_contact_id,))
        existing_notes_row = cursor.fetchone()
        if existing_notes_row:
            final_notes = existing_notes_row['notes'] if isinstance(existing_notes_row, sqlite3.Row) else existing_notes_row[0]
    sync_record_mentions(cursor.connection, extract_mentions(final_notes), 'contact_profile_note', f'note:{final_contact_id}', final_notes)
    return final_contact_id


def update_contact_by_id(cursor, contact_id, contact_data_payload):
    if not contact_data_payload:
        cursor.execute(
            "SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes, created_at, updated_at FROM contacts WHERE id = ?",
            (contact_id,),
        )
        cv = cursor.fetchone()
        return serialize_contact_row(cv) if cv else None

    column_updates = {}

    basic_mappings = {
        "companyName": "company_name",
        "contactName": "contact_name",
        "notes": "notes",
        "handle": "handle",
    }
    for payload_key, column_name in basic_mappings.items():
        if payload_key in contact_data_payload:
            value = contact_data_payload[payload_key]
            if payload_key == "handle" and value:
                value = value.lower().lstrip('@')
            column_updates[column_name] = value

    detail_related_keys = {
        "contactDetails", "addresses", "emails", "phones",
        "email", "phone",
        "billingAddress", "billingCity", "billingState", "billingZipCode",
        "shippingAddress", "shippingCity", "shippingState", "shippingZipCode",
    }

    should_update_details = any(key in contact_data_payload for key in detail_related_keys)
    details_info = None
    if should_update_details:
        details_info = _prepare_contact_details_for_storage(contact_data_payload, force=True)
        column_updates["details_json"] = json.dumps(details_info['details'])

        if any(key in contact_data_payload for key in ("email", "contactDetails", "emails")):
            column_updates["email"] = details_info['primary_email']
        if any(key in contact_data_payload for key in ("phone", "contactDetails", "phones")):
            column_updates["phone"] = details_info['primary_phone']

        if any(key in contact_data_payload for key in ("shippingAddress", "shippingCity", "shippingState", "shippingZipCode", "contactDetails", "addresses")):
            shipping_entry = details_info['shipping']
            column_updates["shipping_address"] = shipping_entry['street'] if shipping_entry else ''
            column_updates["shipping_city"] = shipping_entry['city'] if shipping_entry else ''
            column_updates["shipping_state"] = shipping_entry['state'] if shipping_entry else ''
            column_updates["shipping_zip_code"] = shipping_entry['postalCode'] if shipping_entry else ''

        if any(key in contact_data_payload for key in ("billingAddress", "billingCity", "billingState", "billingZipCode", "contactDetails", "addresses")):
            billing_entry = details_info['billing']
            column_updates["billing_address"] = billing_entry['street'] if billing_entry else ''
            column_updates["billing_city"] = billing_entry['city'] if billing_entry else ''
            column_updates["billing_state"] = billing_entry['state'] if billing_entry else ''
            column_updates["billing_zip_code"] = billing_entry['postalCode'] if billing_entry else ''

    direct_mappings = {
        "billingAddress": "billing_address",
        "billingCity": "billing_city",
        "billingState": "billing_state",
        "billingZipCode": "billing_zip_code",
        "shippingAddress": "shipping_address",
        "shippingCity": "shipping_city",
        "shippingState": "shipping_state",
        "shippingZipCode": "shipping_zip_code",
        "email": "email",
        "phone": "phone",
    }
    for payload_key, column_name in direct_mappings.items():
        if payload_key in contact_data_payload and column_name not in column_updates:
            value = contact_data_payload[payload_key]
            if payload_key == "phone":
                value = _normalize_phone_digits(value)
            column_updates[column_name] = value

    if not column_updates:
        cursor.execute(
            "SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes, created_at, updated_at FROM contacts WHERE id = ?",
            (contact_id,),
        )
        cv = cursor.fetchone()
        return serialize_contact_row(cv) if cv else None

    set_clause = ", ".join(f"{column} = ?" for column in column_updates)
    values = list(column_updates.values())
    values.append(contact_id)

    try:
        cursor.execute(
            f"UPDATE contacts SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(values)
        )
        if cursor.rowcount == 0:
            return None

        ensured_handle = ensure_contact_handle(cursor, contact_id)
        if ensured_handle:
            service = get_record_service()
            cursor.execute(
                "SELECT contact_name, company_name, email FROM contacts WHERE id = ?",
                (contact_id,),
            )
            display_row = cursor.fetchone()
            contact_name_val = display_row['contact_name'] if display_row else None
            company_name_val = display_row['company_name'] if display_row else None
            email_val = display_row['email'] if display_row else None
            display_value = (contact_name_val or company_name_val or ensured_handle)
            search_blob = ' '.join(filter(None, [contact_name_val, company_name_val, email_val, ensured_handle])).lower()
            service.register_handle(
                cursor.connection,
                'contact',
                contact_id,
                ensured_handle,
                display_name=display_value,
                search_blob=search_blob,
            )

        cursor.execute(
            "SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes, created_at, updated_at FROM contacts WHERE id = ?",
            (contact_id,),
        )
        updated_row = cursor.fetchone()
        if not updated_row:
            return None
        updated_contact = serialize_contact_row(updated_row)
        if 'notes' in contact_data_payload:
            sync_record_mentions(cursor.connection, extract_mentions(updated_contact.get('notes')), 'contact_profile_note', f'note:{contact_id}', updated_contact.get('notes'))
        return updated_contact
    except sqlite3.Error as e:
        app.logger.error(f"DB error updating contact {contact_id}: {e}")
        raise
def refresh_order_contact_links(cursor, order_id, primary_contact_id=None):
    cursor.execute("DELETE FROM order_contact_links WHERE order_id = ?", (order_id,))
    cursor.execute(
        """
            SELECT DISTINCT mentioned_entity_id
            FROM record_mentions
            WHERE mentioned_entity_type = 'contact'
              AND (
                    (context_entity_type = 'order_note' AND context_entity_id = ?)
                 OR (context_entity_type = 'order_log' AND context_entity_id IN (
                        SELECT CAST(log_id AS TEXT) FROM order_logs WHERE order_id = ?
                    ))
              )
        """,
        (str(order_id), str(order_id))
    )
    rows = cursor.fetchall()
    for row in rows:
        contact_id = row['mentioned_entity_id'] if isinstance(row, sqlite3.Row) else row[0]
        if not contact_id:
            continue
        if primary_contact_id and str(contact_id) == str(primary_contact_id):
            continue
        cursor.execute(
            "INSERT OR IGNORE INTO order_contact_links (order_id, contact_id, relationship) VALUES (?, ?, 'secondary')",
            (order_id, contact_id)
        )

DATA_DIR.mkdir(parents=True, exist_ok=True)
if not SETTINGS_FILE.exists():
    write_json_file(SETTINGS_FILE, {"company_name": "Your Company Name", "default_shipping_zip_code": "00000"})

@app.route('/api/orders', methods=['GET'])
def get_orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    settings = read_json_file(SETTINGS_FILE)
    user_timezone_str = settings.get('timezone', 'UTC')
    user_timezone = pytz.timezone(user_timezone_str)

    cursor.execute("SELECT o.*, v.company_name as contact_company_name, v.contact_name as contact_contact_name, v.email as contact_email, v.phone as contact_phone, v.billing_address as contact_billing_address, v.billing_city as contact_billing_city, v.billing_state as contact_billing_state, v.billing_zip_code as contact_billing_zip_code, v.shipping_address as contact_shipping_address, v.shipping_city as contact_shipping_city, v.shipping_state as contact_shipping_state, v.shipping_zip_code as contact_shipping_zip_code, v.details_json as contact_details_json, v.handle as contact_handle, v.notes as contact_notes FROM orders o LEFT JOIN contacts v ON o.contact_id = v.id WHERE o.status != 'Deleted' ORDER BY o.order_date DESC, o.order_id DESC")
    orders_from_db = cursor.fetchall()
    orders_payload = [serialize_order(cursor, row, user_timezone, include_logs=False) for row in orders_from_db]
    conn.close()
    return jsonify(orders_payload)

@app.route('/api/orders/<string:order_id>', methods=['GET'])
def get_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    settings = read_json_file(SETTINGS_FILE)
    user_timezone_str = settings.get('timezone', 'UTC')
    user_timezone = pytz.timezone(user_timezone_str)

    cursor.execute("SELECT o.*, v.company_name as contact_company_name, v.contact_name as contact_contact_name, v.email as contact_email, v.phone as contact_phone, v.billing_address as contact_billing_address, v.billing_city as contact_billing_city, v.billing_state as contact_billing_state, v.billing_zip_code as contact_billing_zip_code, v.shipping_address as contact_shipping_address, v.shipping_city as contact_shipping_city, v.shipping_state as contact_shipping_state, v.shipping_zip_code as contact_shipping_zip_code, v.details_json as contact_details_json, v.handle as contact_handle, v.notes as contact_notes FROM orders o LEFT JOIN contacts v ON o.contact_id = v.id WHERE o.order_id = ?", (order_id,))
    order_row = cursor.fetchone()
    if not order_row:
        conn.close()
        return jsonify({"status": "error", "message": "Order not found"}), 404

    order_payload = serialize_order(cursor, order_row, user_timezone, include_logs=True)
    conn.close()
    return jsonify(order_payload)

@app.route('/api/orders/<string:order_id>/logs', methods=['GET', 'POST'])
def handle_order_logs(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    settings = read_json_file(SETTINGS_FILE)
    user_timezone_str = settings.get('timezone', 'UTC')
    user_timezone = pytz.timezone(user_timezone_str)

    if request.method == 'POST':
        # Handles multipart/form-data
        action_raw = request.form.get('action', 'Manual Entry')
        action = action_raw.strip() if isinstance(action_raw, str) else 'Manual Entry'
        if not action:
            action = 'Manual Entry'
        normalized_action = action.lower()
        details = request.form.get('details')
        note = request.form.get('note')
        log_body = (details if details is not None else note) or ''
        upload_candidates = request.files.getlist('attachments')
        if not upload_candidates:
            single_file = request.files.get('attachment')
            if single_file:
                upload_candidates = [single_file]

        saved_attachments: List[Dict[str, str]] = []
        for upload in upload_candidates:
            if not upload or not upload.filename:
                continue
            original_name = upload.filename
            sanitized_name = secure_filename(original_name) or f"attachment_{uuid.uuid4().hex[:8]}"
            unique_filename = f"{uuid.uuid4().hex[:8]}_{sanitized_name}"
            try:
                upload.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            except Exception as e:
                app.logger.error(f"Failed to save attachment for order {order_id}: {e}")
                _remove_saved_files([entry['path'] for entry in saved_attachments])
                return jsonify({"status": "error", "message": "Failed to save attachment"}), 500
            saved_attachments.append({"path": unique_filename, "name": original_name})

        primary_attachment_path = saved_attachments[0]['path'] if saved_attachments else None

        try:
            cursor.execute(
                "INSERT INTO order_logs (order_id, user, action, details, note, attachment_path) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, "system", action, log_body, log_body, primary_attachment_path)
            )
            log_id = cursor.lastrowid
            for saved in saved_attachments:
                cursor.execute(
                    "INSERT INTO order_log_attachments (log_id, file_path, original_filename) VALUES (?, ?, ?)",
                    (log_id, saved['path'], saved['name']),
                )
            handles = extract_mentions(log_body)
            sync_record_mentions(cursor.connection, handles, 'order_log', log_id, log_body)
            cursor.execute("SELECT contact_id FROM orders WHERE order_id = ?", (order_id,))
            primary_row = cursor.fetchone()
            primary_contact_for_order = primary_row['contact_id'] if primary_row else None
            refresh_order_contact_links(cursor, order_id, primary_contact_for_order)
            conn.commit()

            cursor.execute("SELECT * FROM order_logs WHERE log_id = ?", (log_id,))
            new_log_row = cursor.fetchone()

            if not new_log_row:
                conn.close()
                return jsonify({"status": "error", "message": "Failed to retrieve new log entry"}), 500

            new_log_dict = dict(new_log_row)
            if new_log_dict.get('timestamp'):
                naive_date = dateutil_parse(new_log_dict['timestamp'])
                utc_date = pytz.utc.localize(naive_date)
                new_log_dict['timestamp'] = utc_date.astimezone(user_timezone).isoformat()

            attachments_map = _fetch_attachments_for_logs(cursor, [log_id])
            attachments_payload = attachments_map.get(log_id, [])
            new_log_dict['attachments'] = attachments_payload
            new_log_dict['attachment_path'] = attachments_payload[0]['path'] if attachments_payload else None

            if normalized_action in {'status update', 'status'} and log_body:
                try:
                    cursor.execute("UPDATE orders SET status = ? WHERE order_id = ?", (log_body, order_id))
                    cursor.execute("INSERT INTO order_status_history (order_id, status, status_date) VALUES (?, ?, ?)",
                                   (order_id, log_body, datetime.now(timezone.utc).isoformat()))
                    conn.commit()
                except sqlite3.Error as e:
                    conn.rollback()
                    app.logger.error(f"Failed to update order status for order {order_id}: {e}")
                    # Decide if this should be a fatal error for the log entry
            
            conn.close()
            return jsonify(new_log_dict), 201

        except sqlite3.Error as e:
            conn.rollback()
            _remove_saved_files([entry['path'] for entry in saved_attachments])
            conn.close()
            app.logger.error(f"Database error adding log for order {order_id}: {e}")
            return jsonify({"status": "error", "message": "Database error"}), 500

    # GET request
    settings = read_json_file(SETTINGS_FILE)
    user_timezone_str = settings.get('timezone', 'UTC')
    user_timezone = pytz.timezone(user_timezone_str)

    cursor.execute("SELECT log_id, timestamp, user, action, details, note, attachment_path FROM order_logs WHERE order_id = ? ORDER BY timestamp DESC", (order_id,))
    logs_from_db = cursor.fetchall()
    log_ids = [row['log_id'] for row in logs_from_db]
    attachments_map = _fetch_attachments_for_logs(cursor, log_ids)
    logs = []
    for log_row in logs_from_db:
        log_dict = dict(log_row)
        if not log_dict.get('details') and log_dict.get('note'):
            log_dict['details'] = log_dict['note']
        if log_dict.get('timestamp'):
            # Timestamps from DB are naive, so we assume they are UTC
            naive_date = dateutil_parse(log_dict['timestamp'])
            utc_date = pytz.utc.localize(naive_date)
            log_dict['timestamp'] = utc_date.astimezone(user_timezone).isoformat()
        attachments_payload = attachments_map.get(log_dict['log_id'], [])
        log_dict['attachments'] = attachments_payload
        log_dict['attachment_path'] = attachments_payload[0]['path'] if attachments_payload else None
        logs.append(log_dict)

    conn.close()
    return jsonify(logs)

@app.route('/api/orders/<string:order_id>/logs/<int:log_id>', methods=['POST', 'DELETE'])
def handle_specific_order_log(order_id, log_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT log_id, action, details, note, attachment_path FROM order_logs WHERE log_id = ? AND order_id = ?",
        (log_id, order_id),
    )
    log = cursor.fetchone()

    if not log:
        conn.close()
        return jsonify({"status": "error", "message": "Log not found"}), 404

    attachments_map = _fetch_attachments_for_logs(cursor, [log_id])
    existing_attachments = attachments_map.get(log_id, [])

    if request.method == 'POST':  # Using POST for update to handle multipart/form-data
        note = request.form.get('note')
        details = request.form.get('details')
        action_override = request.form.get('action')
        log_body = (details if details is not None else note) or ''
        upload_candidates = request.files.getlist('attachments')
        if not upload_candidates:
            single_file = request.files.get('attachment')
            if single_file:
                upload_candidates = [single_file]

        saved_attachments: List[Dict[str, str]] = []
        for upload in upload_candidates:
            if not upload or not upload.filename:
                continue
            original_name = upload.filename
            sanitized_name = secure_filename(original_name) or f"attachment_{uuid.uuid4().hex[:8]}"
            unique_filename = f"{uuid.uuid4().hex[:8]}_{sanitized_name}"
            try:
                upload.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            except Exception as e:
                app.logger.error(f"Failed to save attachment for order {order_id}: {e}")
                _remove_saved_files([entry['path'] for entry in saved_attachments])
                conn.close()
                return jsonify({"status": "error", "message": "Failed to save attachment"}), 500
            saved_attachments.append({"path": unique_filename, "name": original_name})

        replace_attachments = bool(saved_attachments)
        attachment_path = saved_attachments[0]['path'] if replace_attachments else log['attachment_path']

        updated_action = action_override.strip() if action_override and action_override.strip() else log['action']
        cursor.execute(
            "UPDATE order_logs SET action = ?, details = ?, note = ?, attachment_path = ? WHERE log_id = ?",
            (updated_action, log_body, log_body, attachment_path, log_id)
        )
        if replace_attachments:
            cursor.execute("DELETE FROM order_log_attachments WHERE log_id = ?", (log_id,))
            for saved in saved_attachments:
                cursor.execute(
                    "INSERT INTO order_log_attachments (log_id, file_path, original_filename) VALUES (?, ?, ?)",
                    (log_id, saved['path'], saved['name']),
                )
        handles = extract_mentions(log_body)
        sync_record_mentions(cursor.connection, handles, 'order_log', log_id, log_body)
        cursor.execute("SELECT contact_id FROM orders WHERE order_id = ?", (order_id,))
        primary_row = cursor.fetchone()
        primary_contact_for_order = primary_row['contact_id'] if primary_row else None
        refresh_order_contact_links(cursor, order_id, primary_contact_for_order)
        conn.commit()
        if replace_attachments:
            old_paths = {attachment['path'] for attachment in existing_attachments}
            if log['attachment_path']:
                old_paths.add(log['attachment_path'])
            _remove_saved_files(list(old_paths))
        conn.close()
        return jsonify({"status": "success", "message": "Log updated."})

    elif request.method == 'DELETE':
        paths_to_remove = {attachment['path'] for attachment in existing_attachments}
        if log['attachment_path']:
            paths_to_remove.add(log['attachment_path'])
        _remove_saved_files(list(paths_to_remove))

        cursor.execute("DELETE FROM order_logs WHERE log_id = ?", (log_id,))
        cursor.execute(
            "DELETE FROM record_mentions WHERE context_entity_type = ? AND context_entity_id = ?",
            ('order_log', str(log_id))
        )
        cursor.execute("SELECT contact_id FROM orders WHERE order_id = ?", (order_id,))
        primary_row = cursor.fetchone()
        primary_contact_for_order = primary_row['contact_id'] if primary_row else None
        refresh_order_contact_links(cursor, order_id, primary_contact_for_order)
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Log deleted."})

@app.route('/api/search-orders', methods=['GET'])
def search_orders():
    query = request.args.get('query', '').strip()
    if not query:
        return get_orders()

    conn = get_db_connection()
    cursor = conn.cursor()
    settings = read_json_file(SETTINGS_FILE)
    user_timezone_str = settings.get('timezone', 'UTC')
    user_timezone = pytz.timezone(user_timezone_str)
    
    base_query = "SELECT DISTINCT o.order_id FROM orders o "
    joins = set()
    conditions = []
    params = []

    pattern = re.compile(r'(\b\w+\b):("([^"]+)"|(\S+))|(\btotal\s*(?:>=|<=|<>|!=|=|<|>)\s*\d+\.?\d*)')
    
    structured_queries = pattern.findall(query)
    text_search_parts = pattern.sub('', query).split()

    for key, _, quoted_val, unquoted_val, total_val in structured_queries:
        if total_val:
            match = re.match(r'total\s*(>=|<=|<>|!=|=|<|>)\s*(\d+\.?\d*)', total_val.strip())
            if match:
                op, value_str = match.groups()
                conditions.append(f"o.total_amount {op} ?")
                params.append(float(value_str))
            continue

        key = key.lower()
        value = quoted_val if quoted_val else unquoted_val

        if key in ['before', 'after', 'during']:
            try:
                # Use fuzzy parsing to handle a wide variety of date formats
                parsed_date = dateutil_parse(value, fuzzy=True)
                
                if key == 'before':
                    # strictly less than the beginning of the parsed day
                    conditions.append("o.order_date < ?")
                    params.append(parsed_date.strftime('%Y-%m-%d'))
                elif key == 'after':
                    # an entire day after the one provided
                    end_of_day = parsed_date + timedelta(days=1)
                    conditions.append("o.order_date >= ?")
                    params.append(end_of_day.strftime('%Y-%m-%d'))
                elif key == 'during':
                    # The entire day of the date provided
                    next_day = parsed_date + timedelta(days=1)
                    conditions.append("o.order_date >= ? AND o.order_date < ?")
                    params.append(parsed_date.strftime('%Y-%m-%d'))
                    params.append(next_day.strftime('%Y-%m-%d'))

            except (ValueError, TypeError) as e:
                # If parsing fails, skip this condition
                app.logger.warning(f"Could not parse date for '{key}:{value}'. Error: {e}")
                continue # Move to the next query part
        
        else:
          # Keep the existing logic for non-date fields
          field_map = {
              'from': {'join': "LEFT JOIN contacts v ON o.contact_id = v.id", 'condition': "(v.company_name LIKE ? OR v.contact_name LIKE ?)", 'params': [f'%{value}%', f'%{value}%']},
              'contact': {'join': "LEFT JOIN contacts v ON o.contact_id = v.id", 'condition': "(v.company_name LIKE ? OR v.contact_name LIKE ?)", 'params': [f'%{value}%', f'%{value}%']},
              'customer': {'join': "LEFT JOIN contacts v ON o.contact_id = v.id", 'condition': "(v.company_name LIKE ? OR v.contact_name LIKE ?)", 'params': [f'%{value}%', f'%{value}%']},
              'status': {'condition': "o.status LIKE ?", 'params': [f'%{value}%']},
              'title': {'condition': "o.title LIKE ?", 'params': [f'%{value}%']},
              'item': {'join': "LEFT JOIN order_line_items oli ON o.order_id = oli.order_id", 'condition': "(oli.name LIKE ? OR oli.description LIKE ?)", 'params': [f'%{value}%', f'%{value}%']},
              'note': {'condition': "o.notes LIKE ?", 'params': [f'%{value}%']},
              'log': {'join': "LEFT JOIN order_logs ol ON o.order_id = ol.order_id", 'condition': "(ol.details LIKE ? OR ol.note LIKE ?)", 'params': [f'%{value}%', f'%{value}%']},
          }

          if key in field_map:
              rule = field_map[key]
              if 'join' in rule:
                  joins.add(rule['join'])
              conditions.append(rule['condition'])
              params.extend(rule['params'])

    join_order = [
      "LEFT JOIN contacts v ON o.contact_id = v.id",
      "LEFT JOIN order_logs ol ON o.order_id = ol.order_id",
      "LEFT JOIN order_line_items oli ON o.order_id = oli.order_id"
    ]
    
    if text_search_parts:
        for join_sql in join_order:
            joins.add(join_sql)

        for term in text_search_parts:
            if term:
                term_param = f'%{term}%'
                text_conditions = [
                    "o.order_id LIKE ?", "o.display_id LIKE ?", "o.title LIKE ?", "o.status LIKE ?", "o.notes LIKE ?",
                    "v.company_name LIKE ?", "v.contact_name LIKE ?", "oli.name LIKE ?", "oli.description LIKE ?",
                    "ol.details LIKE ?", "ol.note LIKE ?"
                ]
                conditions.append(f"({' OR '.join(text_conditions)})")
                params.extend([term_param] * len(text_conditions))

    if not conditions:
        return jsonify([])

    # Ensure joins are added in a valid order
    final_joins = [j for j in join_order if j in joins]
    final_query = base_query + " ".join(final_joins) + " WHERE " + " AND ".join(conditions)
    
    try:
        cursor.execute(final_query, tuple(params))
        order_ids = [row[0] for row in cursor.fetchall()]

        if not order_ids:
            return jsonify([])

        placeholders = ','.join('?' for _ in order_ids)
        sql_fetch_orders = f"""
            SELECT o.*, v.company_name as contact_company_name, v.contact_name as contact_contact_name, v.email as contact_email, v.phone as contact_phone, v.billing_address as contact_billing_address, v.billing_city as contact_billing_city, v.billing_state as contact_billing_state, v.billing_zip_code as contact_billing_zip_code, v.shipping_address as contact_shipping_address, v.shipping_city as contact_shipping_city, v.shipping_state as contact_shipping_state, v.shipping_zip_code as contact_shipping_zip_code 
            FROM orders o 
            LEFT JOIN contacts v ON o.contact_id = v.id 
            WHERE o.order_id IN ({placeholders}) 
            ORDER BY o.order_date DESC, o.order_id DESC
        """
        
        cursor.execute(sql_fetch_orders, tuple(order_ids))
        orders_from_db = cursor.fetchall()
        orders_payload = [serialize_order(cursor, row, user_timezone, include_logs=False) for row in orders_from_db]
        conn.close()
        return jsonify(orders_payload)

    except sqlite3.Error as e:
        app.logger.error(f"Database error during search: {e}\nQuery: {final_query}\nParams: {params}")
        return jsonify({"status": "error", "message": "Database search error"}), 500
    finally:
        conn.close()

@app.route('/api/dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT SUM(total_amount) FROM orders WHERE status != 'Deleted'")
        tr = cursor.fetchone(); total_revenue = tr[0] if tr and tr[0] is not None else 0.0
        cursor.execute("SELECT COUNT(order_id) FROM orders WHERE status != 'Deleted'")
        to = cursor.fetchone(); total_orders = to[0] if to and to[0] is not None else 0
        avg_rev = total_revenue / total_orders if total_orders > 0 else 0.0
        return jsonify({"totalRevenue": round(total_revenue, 2), "averageOrderRevenue": round(avg_rev, 2), "totalOrders": total_orders})
    except sqlite3.Error as e: app.logger.error(f"DB error dashboard: {e}"); return jsonify({"status": "error"}), 500
    finally: conn.close()


@app.route('/api/analytics/reports', methods=['GET'])
def api_list_analytics_reports():
    conn = get_db_connection()
    try:
        engine = get_analytics_engine()
        definitions = engine.list_report_definitions(conn)
        return jsonify({'reports': definitions})
    except Exception as exc:  # pragma: no cover - defensive logging
        app.logger.exception("Failed to list analytics reports: %s", exc)
        return jsonify({'message': 'Failed to load analytics definitions.'}), 500
    finally:
        conn.close()


@app.route('/api/analytics/reports/run', methods=['POST'])
def api_run_analytics_report():
    payload = request.get_json(force=True, silent=True) or {}
    report_id = payload.get('reportId') or payload.get('report_id')
    if not report_id:
        return jsonify({'message': 'reportId is required.'}), 400
    params = payload.get('params') or {}
    engine = get_analytics_engine()
    conn = get_db_connection()
    try:
        settings = read_json_file(SETTINGS_FILE)
        timezone_name = settings.get('timezone', 'UTC')
        result = engine.run_report(conn, report_id, params, timezone_name=timezone_name)
        return jsonify({'report': result})
    except KeyError as exc:
        return jsonify({'message': str(exc)}), 404
    except ValueError as exc:
        return jsonify({'message': str(exc)}), 400
    except Exception as exc:  # pragma: no cover - defensive logging
        app.logger.exception("Failed to execute analytics report %s: %s", report_id, exc)
        return jsonify({'message': 'Failed to generate analytics report.'}), 500
    finally:
        conn.close()


@app.route('/api/orders', methods=['POST'])
def save_order():
    new_order_payload = request.json
    if not new_order_payload:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400
    conn_main = None
    processed_order_id = new_order_payload.get('id', 'NEW_ORDER_PENDING_ID') 

    try:
        conn_main = get_db_connection()
        cursor = conn_main.cursor()
        settings = read_json_file(SETTINGS_FILE)
        user_timezone_str = settings.get('timezone', 'UTC')
        user_timezone = pytz.timezone(user_timezone_str)
        order_id_from_payload = new_order_payload.get('id')
        
        existing_order_row = None
        if order_id_from_payload:
            cursor.execute("SELECT status, contact_id FROM orders WHERE order_id = ?", (order_id_from_payload,))
            existing_order_row = cursor.fetchone()

        current_order_id_for_db_ops = order_id_from_payload if existing_order_row else None
        
        is_attempting_delete = new_order_payload.get('status') == "Deleted"

        if order_id_from_payload and is_attempting_delete: 
            if existing_order_row:
                if existing_order_row['status'] != "Draft":
                    contact_id_for_confirm = existing_order_row['contact_id']
                    company_name_for_confirm = ""
                    if contact_id_for_confirm:
                        cursor.execute("SELECT company_name FROM contacts WHERE id = ?", (contact_id_for_confirm,))
                        contact_row = cursor.fetchone()
                        if contact_row: company_name_for_confirm = contact_row['company_name']
                    order_id_str = order_id_from_payload.replace("PO-", "")
                    order_id_last_4 = order_id_str[-4:] if len(order_id_str) >= 4 else order_id_str
                    if not company_name_for_confirm or not order_id_last_4: 
                        if conn_main: conn_main.rollback() 
                        return jsonify({"status": "error", "message": "Cannot perform deletion: Missing data."}), 400
                    expected_confirmation = f"delete {company_name_for_confirm} order {order_id_last_4}"
                    if new_order_payload.get('deleteConfirmation') != expected_confirmation:
                        if conn_main: conn_main.rollback()
                        return jsonify({"status": "error", "message": "Deletion confirmation failed."}), 403
                new_order_payload.pop('deleteConfirmation', None)
            else: 
                if conn_main: conn_main.rollback()
                return jsonify({"status": "error", "message": f"Order ID {order_id_from_payload} not found."}), 404
        
        contact_info_payload = new_order_payload.get('contactInfo') or {}
        primary_contact_id = contact_info_payload.get('id') or new_order_payload.get('primaryContactId')
        if not primary_contact_id:
            if conn_main and conn_main.in_transaction:
                conn_main.rollback()
            return jsonify({"status": "error", "message": "A primary contact is required for every order."}), 400

        cursor.execute("SELECT id FROM contacts WHERE id = ?", (primary_contact_id,))
        if not cursor.fetchone():
            if conn_main and conn_main.in_transaction:
                conn_main.rollback()
            return jsonify({"status": "error", "message": "Selected primary contact could not be found."}), 400

        db_processed_contact_id = primary_contact_id
        new_order_payload['contactInfo'] = {**contact_info_payload, 'id': primary_contact_id}

        additional_contact_ids = new_order_payload.get('additionalContactIds') or []
        normalized_additional = []
        for candidate in additional_contact_ids:
            if not candidate or candidate == db_processed_contact_id or candidate in normalized_additional:
                continue
            cursor.execute("SELECT 1 FROM contacts WHERE id = ?", (candidate,))
            if cursor.fetchone():
                normalized_additional.append(candidate)
        additional_contact_ids = normalized_additional
        new_order_payload['additionalContactIds'] = additional_contact_ids
        
        raw_line_items = new_order_payload.get('lineItems', [])
        sanitized_line_items = []
        subtotal_cents = 0
        for li in raw_line_items:
            if not isinstance(li, dict):
                continue
            name = (li.get('name') or '').strip()
            if not name:
                continue
            try:
                quantity = int(float(li.get('quantity', 0)))
            except (TypeError, ValueError):
                quantity = 0
            if quantity <= 0:
                continue
            try:
                price_cents = int(round(float(li.get('price', 0))))
            except (TypeError, ValueError):
                price_cents = 0
            if price_cents < 0:
                price_cents = 0
            subtotal_cents += quantity * price_cents
            sanitized_item = dict(li)
            sanitized_item['name'] = name
            sanitized_item['quantity'] = quantity
            sanitized_item['price'] = price_cents
            sanitized_line_items.append(sanitized_item)

        new_order_payload['lineItems'] = sanitized_line_items

        estimated_shipping_cost_dollars = max(0.0, _safe_parse_float(new_order_payload.get('estimatedShipping', 0.0)))
        tax_amount_dollars = max(0.0, _safe_parse_float(new_order_payload.get('taxAmount', 0.0)))

        normalized_discounts, discount_total_cents = _normalize_discount_entries(
            new_order_payload.get('discounts', []),
            sanitized_line_items,
        )
        discount_total_cents = min(discount_total_cents, subtotal_cents)
        discount_total_dollars = round(discount_total_cents / 100.0, 2)
        new_order_payload['discounts'] = normalized_discounts
        discounts_json_str = json.dumps(normalized_discounts or [])

        estimated_shipping_cents = int(round(estimated_shipping_cost_dollars * 100))
        tax_amount_cents = int(round(tax_amount_dollars * 100))
        subtotal_after_discounts = max(0, subtotal_cents - discount_total_cents)
        total_cents = subtotal_after_discounts + estimated_shipping_cents + tax_amount_cents
        final_total_dollars = round(total_cents / 100.0, 2)

        new_order_payload['estimatedShipping'] = f"{estimated_shipping_cost_dollars:.2f}"
        new_order_payload['taxAmount'] = f"{tax_amount_dollars:.2f}"
        new_order_payload['discountTotal'] = discount_total_cents
        new_order_payload['total'] = final_total_dollars

        title_value = new_order_payload.get('title', '')
        if isinstance(title_value, str):
            title_value = title_value.strip()
        else:
            title_value = ''
        new_order_payload['title'] = title_value

        display_id = new_order_payload.get('display_id')
        if isinstance(display_id, str):
            display_id = display_id.strip()
        display_id = display_id or None

        def normalize_optional_text(value):
            if isinstance(value, str):
                stripped = value.strip()
                return stripped if stripped else None
            return None

        priority_level_value = normalize_optional_text(new_order_payload.get('priorityLevel'))
        fulfillment_channel_value = normalize_optional_text(new_order_payload.get('fulfillmentChannel'))
        customer_reference_value = normalize_optional_text(new_order_payload.get('customerReference'))

        new_order_payload['priorityLevel'] = priority_level_value or ''
        new_order_payload['fulfillmentChannel'] = fulfillment_channel_value or ''
        new_order_payload['customerReference'] = customer_reference_value or ''

        if current_order_id_for_db_ops:
            cursor.execute(
                "UPDATE orders SET display_id=?, contact_id=?, order_date=?, status=?, notes=?, estimated_shipping_date=?, shipping_address=?, shipping_city=?, shipping_state=?, shipping_zip_code=?, billing_address=?, billing_city=?, billing_state=?, billing_zip_code=?, estimated_shipping_cost=?, tax_amount=?, discounts_json=?, discount_total=?, signature_data_url=?, total_amount=?, title=?, priority_level=?, fulfillment_channel=?, customer_reference=?, updated_at=CURRENT_TIMESTAMP WHERE order_id=?",
                (
                    display_id,
                    db_processed_contact_id,
                    new_order_payload.get('date', datetime.now(timezone.utc).isoformat()+"Z"),
                    new_order_payload.get('status','Draft'),
                    new_order_payload.get('notes'),
                    new_order_payload.get('estimatedShippingDate'),
                    new_order_payload.get('shippingAddress'),
                    new_order_payload.get('shippingCity'),
                    new_order_payload.get('shippingState'),
                    new_order_payload.get('shippingZipCode'),
                    new_order_payload.get('billingAddress'),
                    new_order_payload.get('billingCity'),
                    new_order_payload.get('billingState'),
                    new_order_payload.get('billingZipCode'),
                    estimated_shipping_cost_dollars,
                    tax_amount_dollars,
                    discounts_json_str,
                    discount_total_dollars,
                    new_order_payload.get('signatureDataUrl'),
                    final_total_dollars,
                    title_value,
                    priority_level_value,
                    fulfillment_channel_value,
                    customer_reference_value,
                    current_order_id_for_db_ops
                )
            )
            cursor.execute("DELETE FROM order_line_items WHERE order_id = ?", (current_order_id_for_db_ops,))
            cursor.execute("DELETE FROM order_status_history WHERE order_id = ?", (current_order_id_for_db_ops,))
        else:
            current_order_id_for_db_ops = f"ORD-{uuid.uuid4()}"
            new_order_payload['id'] = current_order_id_for_db_ops
            cursor.execute(
                "INSERT INTO orders (order_id, display_id, contact_id, order_date, status, notes, estimated_shipping_date, shipping_address, shipping_city, shipping_state, shipping_zip_code, billing_address, billing_city, billing_state, billing_zip_code, estimated_shipping_cost, tax_amount, discounts_json, discount_total, signature_data_url, total_amount, title, priority_level, fulfillment_channel, customer_reference) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    current_order_id_for_db_ops,
                    display_id,
                    db_processed_contact_id,
                    new_order_payload.get('date', datetime.now(timezone.utc).isoformat()+"Z"),
                    new_order_payload.get('status','Draft'),
                    new_order_payload.get('notes'),
                    new_order_payload.get('estimatedShippingDate'),
                    new_order_payload.get('shippingAddress'),
                    new_order_payload.get('shippingCity'),
                    new_order_payload.get('shippingState'),
                    new_order_payload.get('shippingZipCode'),
                    new_order_payload.get('billingAddress'),
                    new_order_payload.get('billingCity'),
                    new_order_payload.get('billingState'),
                    new_order_payload.get('billingZipCode'),
                    estimated_shipping_cost_dollars,
                    tax_amount_dollars,
                    discounts_json_str,
                    discount_total_dollars,
                    new_order_payload.get('signatureDataUrl'),
                    final_total_dollars,
                    title_value,
                    priority_level_value,
                    fulfillment_channel_value,
                    customer_reference_value
                )
            )
        
        processed_order_id = current_order_id_for_db_ops 
        app.logger.info(f"DB-OP: processed_order_id is now set to: '{processed_order_id}' before line item processing.")

        for li in new_order_payload.get('lineItems', []):
            name = (li.get('name') or '').strip()
            if not name:
                continue

            try:
                quantity = int(float(li.get('quantity', 0)))
            except (TypeError, ValueError):
                quantity = 0
            if quantity <= 0:
                continue

            price_raw = li.get('price', 0)
            try:
                price_cents = int(round(float(price_raw)))
            except (TypeError, ValueError):
                price_cents = 0

            description = (li.get('description') or '').strip()
            catalog_item_id = li.get('catalogItemId') or li.get('catalog_item_id')
            package_id = li.get('packageId') or li.get('package_id')

            cursor.execute(
                """
                INSERT INTO order_line_items
                (order_id, catalog_item_id, name, description, quantity, price_per_unit_cents, package_id, weight_oz, client_reference_id)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    processed_order_id,
                    catalog_item_id,
                    name,
                    description,
                    quantity,
                    price_cents,
                    package_id,
                    None,
                    str(li.get('id')) if li.get('id') not in (None, '') else None,
                )
            )
        for hist in new_order_payload.get('statusHistory',[]):
            cursor.execute("INSERT INTO order_status_history (order_id, status, status_date) VALUES (?,?,?)", (processed_order_id, hist.get('status'), hist.get('date')))
        if not any(h['status'] == new_order_payload.get('status') for h in new_order_payload.get('statusHistory',[])):
            cursor.execute("INSERT INTO order_status_history (order_id, status, status_date) VALUES (?,?,?)", (processed_order_id, new_order_payload.get('status'), datetime.now(timezone.utc).isoformat()+"Z"))

        notes_text = new_order_payload.get('notes')
        handles_from_notes = extract_mentions(notes_text)
        sync_record_mentions(cursor.connection, handles_from_notes, 'order_note', processed_order_id, notes_text)
        refresh_order_contact_links(cursor, processed_order_id, db_processed_contact_id)

        existing_display_id = None
        existing_title = ''
        if existing_order_row:
            try:
                existing_display_id = existing_order_row['display_id']
            except (KeyError, IndexError, TypeError):
                existing_display_id = None
            try:
                existing_title = existing_order_row['title']
            except (KeyError, IndexError, TypeError):
                existing_title = ''

        cleaned_display_id = display_id or (existing_display_id.strip() if isinstance(existing_display_id, str) else None)
        cleaned_title = title_value or (existing_title.strip() if isinstance(existing_title, str) else '')
        order_label = cleaned_title or cleaned_display_id or processed_order_id

        ensure_order_record_handle(
            cursor,
            str(processed_order_id),
            cleaned_display_id,
            cleaned_title,
        )

        if existing_order_row:
            cursor.execute(
                "INSERT INTO order_logs (order_id, user, action, details) VALUES (?, ?, ?, ?)",
                (current_order_id_for_db_ops, "system", "Order Updated", f"Order {order_label} was updated.")
            )
        else:
            cursor.execute(
                "INSERT INTO order_logs (order_id, user, action, details) VALUES (?, ?, ?, ?)",
                (processed_order_id, "system", "Order Created", f"Order {order_label} was created.")
            )

        conn_main.commit()
        app.logger.info(f"Order {processed_order_id} committed successfully.")

        cursor.execute(
            """
                SELECT o.*, v.company_name as contact_company_name, v.contact_name as contact_contact_name, v.email as contact_email,
                       v.phone as contact_phone, v.billing_address as contact_billing_address, v.billing_city as contact_billing_city,
                       v.billing_state as contact_billing_state, v.billing_zip_code as contact_billing_zip_code,
                       v.shipping_address as contact_shipping_address, v.shipping_city as contact_shipping_city,
                       v.shipping_state as contact_shipping_state, v.shipping_zip_code as contact_shipping_zip_code,
                       v.details_json as contact_details_json, v.handle as contact_handle, v.notes as contact_notes
                FROM orders o
                LEFT JOIN contacts v ON o.contact_id = v.id
                WHERE o.order_id = ?
            """,
            (processed_order_id,)
        )
        refreshed_row = cursor.fetchone()
        if refreshed_row:
            final_order_response = serialize_order(cursor, refreshed_row, user_timezone, include_logs=True)
        else:
            final_order_response = {
                "id": processed_order_id,
                **{k: v for k, v in new_order_payload.items() if k != 'id'}
            }

        app.logger.info(f"Order {processed_order_id} processed and response prepared successfully.")
        return jsonify({
            "status": "success",
            "message": "Order saved successfully.",
            "order": final_order_response
        }), 200

    except sqlite3.Error as e_tx:
        if conn_main:
            try:
                if conn_main.in_transaction: conn_main.rollback()
            except Exception as e_rb: app.logger.error(f"Error during rollback: {e_rb}")
        app.logger.error(f"DB error in main transaction or same-conn re-fetch for order '{processed_order_id}': {e_tx}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": f"DB error: {str(e_tx)}"}), 500
    except Exception as e_global_tx:
        if conn_main:
            try:
                if conn_main.in_transaction: conn_main.rollback()
            except Exception as e_rb_global: app.logger.error(f"Error during global exception rollback: {e_rb_global}")
        app.logger.error(f"Global error in main transaction or same-conn re-fetch for order '{processed_order_id}': {e_global_tx}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": f"Unexpected error: {str(e_global_tx)}"}), 500
    finally:
        if conn_main: 
            try: 
                conn_main.close()
                app.logger.info(f"Main conn (outer finally) closed for order ID '{processed_order_id}'.")
            except Exception as e_close_final:
                 app.logger.error(f"Error closing main conn in outer finally for order ID '{processed_order_id}': {e_close_final}")
                 
    app.logger.error(f"Reached unexpected end of save_order for order ID '{processed_order_id}'. This indicates a logic flow issue.")
    return jsonify({"status": "error", "message": "An unexpected server error occurred."}), 500

@app.route('/api/items', methods=['GET'])
def get_items():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, name, description, price_cents
        FROM items
        ORDER BY name COLLATE NOCASE ASC
        """
    )
    items_from_db = cursor.fetchall()
    items_list = []
    for item_row in items_from_db:
        item_dict = dict(item_row)
        items_list.append({
            'id': item_dict['id'],
            'name': item_dict['name'],
            'description': item_dict.get('description') or '',
            'price': item_dict['price_cents'],
        })
    conn.close()
    return jsonify(items_list)


def _parse_price_to_cents(price_value):
    if price_value is None:
        raise ValueError('Price is required')
    if isinstance(price_value, (int, float)):
        return int(round(float(price_value) * 100))
    value_str = str(price_value).strip().replace('$', '')
    if not value_str:
        raise ValueError('Price is required')
    return int(round(float(value_str) * 100))


@app.route('/api/items', methods=['POST'])
def add_item():
    payload = request.json or {}
    name = (payload.get('name') or '').strip()
    description = (payload.get('description') or '').strip()
    if not name:
        return jsonify({"message": "Item name is required."}), 400

    try:
        price_cents = _parse_price_to_cents(payload.get('price'))
    except (ValueError, TypeError):
        return jsonify({"message": "Invalid price."}), 400

    item_id = str(uuid.uuid4())

    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO items (id, name, description, price_cents, weight_oz) VALUES (?,?,?,?,?)",
            (item_id, name, description, price_cents, None)
        )
        conn.commit()
        created_item = {
            'id': item_id,
            'name': name,
            'description': description,
            'price': price_cents,
        }
        return jsonify({"message": "Item added.", "item": created_item}), 201
    except sqlite3.Error as e:
        conn.rollback()
        app.logger.error(f"DB err add item {item_id}:{e}")
        return jsonify({"message": "DB error."}), 500
    finally:
        conn.close()


@app.route('/api/items/<string:item_id>', methods=['PUT'])
def update_item(item_id):
    payload = request.json or {}
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id FROM items WHERE id=?", (item_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"message": "Item not found."}), 404

    updates = []
    values = []

    if 'name' in payload:
        name = (payload.get('name') or '').strip()
        if not name:
            conn.close()
            return jsonify({"message": "Item name cannot be empty."}), 400
        updates.append("name=?")
        values.append(name)

    if 'description' in payload:
        description = (payload.get('description') or '').strip()
        updates.append("description=?")
        values.append(description)

    if 'price' in payload:
        try:
            price_cents = _parse_price_to_cents(payload.get('price'))
        except (ValueError, TypeError):
            conn.close()
            return jsonify({"message": "Invalid price."}), 400
        updates.append("price_cents=?")
        values.append(price_cents)

    try:
        if updates:
            set_clause = ",".join(updates)
            cursor.execute(
                f"UPDATE items SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                tuple(values + [item_id])
            )
            conn.commit()

        cursor.execute("SELECT id, name, description, price_cents FROM items WHERE id=?", (item_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"message": "Item not found."}), 404
        updated_item = {
            'id': row['id'],
            'name': row['name'],
            'description': row['description'] or '',
            'price': row['price_cents'],
        }
        return jsonify({"message": "Item updated.", "item": updated_item}), 200
    except sqlite3.Error as e:
        conn.rollback()
        app.logger.error(f"DB err update item {item_id}:{e}")
        return jsonify({"message": "DB error."}), 500
    finally:
        conn.close()


def resolve_item_identifier(cursor, identifier):
    if identifier is None:
        return None
    trimmed = str(identifier).strip()
    if not trimmed:
        return None

    cursor.execute("SELECT id FROM items WHERE id = ?", (trimmed,))
    row = cursor.fetchone()
    if row:
        return row['id']

    cursor.execute("SELECT id FROM items WHERE LOWER(name) = LOWER(?)", (trimmed,))
    row = cursor.fetchone()
    if row:
        return row['id']

    return None


def parse_package_contents(cursor, payload):
    """Normalize package contents from a payload.

    Accepts either a list of objects under the ``contents`` key or a newline-delimited
    string under ``contents_raw_text``/``contentsRawText``. Each entry is resolved
    against the catalog to ensure we persist canonical item identifiers.
    """

    parsed_entries = []
    contents_list = payload.get('contents')
    if isinstance(contents_list, list):
        for entry in contents_list:
            if not isinstance(entry, dict):
                raise ValueError('Each package content must be an object with item and quantity fields.')
            identifier = (
                entry.get('itemId')
                or entry.get('item_id')
                or entry.get('catalogItemId')
                or entry.get('id')
                or entry.get('item')
                or entry.get('identifier')
                or entry.get('name')
            )
            if not identifier:
                raise ValueError('Package contents require an item identifier.')
            try:
                quantity = int(entry.get('quantity', 0))
            except (TypeError, ValueError):
                raise ValueError(f"Invalid quantity for item '{identifier}'.")
            if quantity <= 0:
                raise ValueError(f"Quantity for item '{identifier}' must be greater than zero.")
            resolved_item_id = resolve_item_identifier(cursor, identifier)
            if not resolved_item_id:
                raise ValueError(f"Item '{identifier}' not found in catalog.")
            parsed_entries.append({'itemId': resolved_item_id, 'quantity': quantity})
        return parsed_entries

    raw_text = (
        payload.get('contents_raw_text')
        if payload.get('contents_raw_text') is not None
        else payload.get('contentsRawText')
    )
    if not raw_text:
        return []

    for line in str(raw_text).strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split(':')
        if len(parts) != 2:
            raise ValueError(f"Malformed line: {line}.")
        identifier, qty_str = parts[0].strip(), parts[1].strip()
        if not identifier:
            raise ValueError("Package item identifier cannot be blank.")
        try:
            quantity = int(qty_str)
        except ValueError:
            raise ValueError(f"Invalid quantity for {identifier}.")
        if quantity <= 0:
            raise ValueError(f"Quantity for {identifier} must be greater than zero.")
        resolved_item_id = resolve_item_identifier(cursor, identifier)
        if not resolved_item_id:
            raise ValueError(f"Item '{identifier}' not found in catalog.")
        parsed_entries.append({'itemId': resolved_item_id, 'quantity': quantity})

    return parsed_entries


def serialize_package(cursor, package_id):
    cursor.execute(
        "SELECT package_id, name, created_at, updated_at FROM packages WHERE package_id=?",
        (package_id,)
    )
    pkg_row = cursor.fetchone()
    if not pkg_row:
        return None

    cursor.execute(
        """
        SELECT pi.item_id, pi.quantity, i.name, i.description, i.price_cents
        FROM package_items pi
        LEFT JOIN items i ON i.id = pi.item_id
        WHERE pi.package_id = ?
        ORDER BY COALESCE(i.name, pi.item_id) COLLATE NOCASE ASC
        """,
        (package_id,)
    )
    contents = [
        {
            'itemId': row['item_id'],
            'quantity': row['quantity'],
            'name': row['name'],
            'description': row['description'],
            'price': row['price_cents'],
        }
        for row in cursor.fetchall()
    ]

    return {
        'name': pkg_row['name'],
        'packageId': pkg_row['package_id'],
        'id_val': pkg_row['package_id'],
        'createdAt': pkg_row['created_at'],
        'updatedAt': pkg_row['updated_at'],
        'contents': contents,
    }


@app.route('/api/items/<string:item_id>', methods=['DELETE'])
def delete_item(item_id):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM items WHERE id=?", (item_id,))
        conn.commit()
        if cursor.rowcount > 0:
            return jsonify({"message": "Item deleted."}), 200
        else:
            return jsonify({"message": "Item not found."}), 404
    except sqlite3.Error as e:
        conn.rollback()
        app.logger.error(f"DB err delete item {item_id}:{e}")
        return jsonify({"message": "DB error."}), 500
    finally:
        conn.close()

@app.route('/api/contacts', methods=['GET'])
def get_contacts():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code,
               shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes
        FROM contacts
        ORDER BY
            CASE
                WHEN contact_name IS NULL OR TRIM(contact_name) = '' THEN company_name
                ELSE contact_name
            END COLLATE NOCASE ASC
        """
    )
    contacts_list = [serialize_contact_row(r) for r in cursor.fetchall()]
    conn.close(); return jsonify(contacts_list)

@app.route('/api/contacts/<string:contact_id>', methods=['GET'])
def api_get_contact(contact_id):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes, created_at, updated_at FROM contacts WHERE id=?", (contact_id,))
        contact_row = cursor.fetchone()
        if not contact_row:
            conn.close();
            return jsonify({"message": "Contact not found."}), 404
        ensure_contact_handle(cursor, contact_id, contact_row['contact_name'] or contact_row['company_name'])
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, details_json, handle, notes, created_at, updated_at FROM contacts WHERE id=?", (contact_id,))
        refreshed_row = cursor.fetchone()
        base_contact = serialize_contact_row(refreshed_row)

        cursor.execute(
            "SELECT order_id, display_id, status, updated_at FROM orders WHERE contact_id = ? ORDER BY updated_at DESC",
            (contact_id,)
        )
        primary_orders = [
            {
                "orderId": order_row["order_id"],
                "orderDisplayId": order_row["display_id"] or order_row["order_id"],
                "status": order_row["status"],
                "updatedAt": order_row["updated_at"],
            }
            for order_row in cursor.fetchall()
        ]
        base_contact["primaryOrders"] = primary_orders

        cursor.execute(
            """
            SELECT mention_id, mentioned_handle, context_entity_type, context_entity_id, snippet, created_at
            FROM record_mentions
            WHERE mentioned_entity_type = 'contact' AND mentioned_entity_id = ?
            ORDER BY created_at DESC
            """,
            (str(contact_id),)
        )
        mentions = []
        for mention in cursor.fetchall():
            context_type = mention['context_entity_type']
            context_id = mention['context_entity_id']
            mention_entry = {
                "id": mention['mention_id'],
                "contextType": context_type,
                "contextId": context_id,
                "handle": mention['mentioned_handle'],
                "snippet": mention['snippet'],
                "createdAt": mention['created_at'],
            }
            if context_type == 'order_log':
                try:
                    log_id = int(context_id)
                except (TypeError, ValueError):
                    log_id = None
                if log_id is not None:
                    cursor.execute("SELECT order_id, timestamp FROM order_logs WHERE log_id = ?", (log_id,))
                    log_row = cursor.fetchone()
                    if log_row:
                        mention_entry['orderId'] = log_row['order_id']
                        mention_entry['logTimestamp'] = log_row['timestamp']
                        cursor.execute("SELECT display_id, contact_id, status, updated_at FROM orders WHERE order_id = ?", (log_row['order_id'],))
                        order_meta = cursor.fetchone()
                        if order_meta:
                            mention_entry['orderDisplayId'] = order_meta['display_id'] or log_row['order_id']
                            mention_entry['orderStatus'] = order_meta['status']
                            mention_entry['orderUpdatedAt'] = order_meta['updated_at']
                            mention_entry['isPrimaryContact'] = order_meta['contact_id'] == contact_id
            elif context_type == 'order_note':
                cursor.execute("SELECT order_id, display_id, updated_at, contact_id, status FROM orders WHERE order_id = ?", (context_id,))
                order_row = cursor.fetchone()
                if order_row:
                    mention_entry['orderId'] = order_row['order_id']
                    mention_entry['orderDisplayId'] = order_row['display_id'] or order_row['order_id']
                    mention_entry['orderUpdatedAt'] = order_row['updated_at']
                    mention_entry['orderStatus'] = order_row['status']
                    mention_entry['isPrimaryContact'] = order_row['contact_id'] == contact_id
            mentions.append(mention_entry)
        conn.close()
        return jsonify({"contact": base_contact, "mentions": mentions})
    except sqlite3.Error as e:
        conn.close()
        app.logger.error(f"DB err fetch contact {contact_id}: {e}")
        return jsonify({"message": "DB error."}), 500


@app.route('/api/contacts/<string:contact_id>', methods=['PUT'])
def api_update_contact(contact_id):
    payload=request.json
    if not payload: return jsonify({"message":"Missing data."}),400
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        updated_contact=update_contact_by_id(cursor,contact_id,payload)
        if updated_contact is None: conn.close(); return jsonify({"message":f"Contact {contact_id} not found."}),404
        conn.commit(); conn.close()
        return jsonify({"message":"Contact updated.","contact":updated_contact}),200
    except sqlite3.Error as e: conn.rollback(); conn.close(); app.logger.error(f"DB err update contact {contact_id}:{e}"); return jsonify({"message":"DB error."}),500
    except Exception as e_g: conn.rollback(); conn.close(); app.logger.error(f"Global err update contact {contact_id}:{e_g}"); return jsonify({"message":"Unexpected error."}),500

@app.route('/api/contacts', methods=['POST'])
def api_create_contact():
    payload=request.json
    if not payload or not (payload.get("companyName") or payload.get("contactName")):
        return jsonify({"message":"Contact name or company is required."}),400
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        contact_id=update_or_create_contact(cursor,payload)
        if not contact_id: conn.rollback(); conn.close(); return jsonify({"message":"Failed to process contact."}),500
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes, created_at, updated_at FROM contacts WHERE id=?",(contact_id,))
        contact_db=cursor.fetchone()
        if not contact_db: conn.rollback(); conn.close(); app.logger.error(f"Contact {contact_id} processed but not retrieved."); return jsonify({"message":"Contact processed but not retrieved."}),500
        serialized_contact = serialize_contact_row(contact_db)
        conn.commit(); conn.close()
        return jsonify({"message":"Contact processed.","contact":serialized_contact}),201
    except sqlite3.Error as e: conn.rollback(); conn.close(); app.logger.error(f"DB err create contact:{e}"); return jsonify({"message":"DB error."}),500
    except Exception as e_g: conn.rollback(); conn.close(); app.logger.error(f"Global err create contact:{e_g}"); return jsonify({"message":"Unexpected error."}),500


def _sanitize_calendar_handle_text(raw: str) -> str:
    if not raw:
        return ""
    normalised = CALENDAR_HANDLE_SANITIZE_RE.sub('-', raw.lower()).strip('-')
    return normalised[:64]


def _suggest_calendar_handle(title: str, start_dt: Optional[datetime]) -> str:
    base = _sanitize_calendar_handle_text(title)
    date_fragment = start_dt.strftime('%Y%m%d') if start_dt else ''
    if base and date_fragment:
        candidate = f"{base[:40]}-{date_fragment}"
    elif base:
        candidate = base[:48]
    elif date_fragment:
        candidate = f"event-{date_fragment}"
    else:
        candidate = f"event-{uuid.uuid4().hex[:8]}"
    return candidate or f"event-{uuid.uuid4().hex[:8]}"


def _ensure_unique_calendar_handle(
    conn: sqlite3.Connection,
    preferred_handle: str,
    *,
    existing_id: Optional[str] = None,
) -> str:
    base = _sanitize_calendar_handle_text(preferred_handle)
    if not base:
        base = f"event-{uuid.uuid4().hex[:8]}"
    candidate = base
    suffix = 2
    while True:
        row = conn.execute(
            "SELECT entity_type, entity_id FROM record_handles WHERE handle = ?",
            (candidate,),
        ).fetchone()
        if not row:
            return candidate
        entity_type = row["entity_type"] if isinstance(row, sqlite3.Row) else row[0]
        entity_id = row["entity_id"] if isinstance(row, sqlite3.Row) else row[1]
        if entity_type == 'calendar_event' and existing_id is not None and str(entity_id) == str(existing_id):
            return candidate
        candidate = f"{base[:48]}-{suffix}"
        suffix += 1


def _coerce_calendar_datetime(value: Any, field_name: str) -> datetime:
    if value in (None, ""):
        raise ValueError(f"Field '{field_name}' is required")
    try:
        parsed = dateutil_parse(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid datetime for '{field_name}': {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_calendar_event_payload(
    conn: sqlite3.Connection,
    payload: Dict[str, Any],
    *,
    existing_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be an object")
    title = (payload.get('title') or '').strip()
    if not title:
        raise ValueError("Title is required")
    start_dt = _coerce_calendar_datetime(payload.get('start_at'), 'start_at')
    end_value = payload.get('end_at')
    end_dt = None
    if end_value not in (None, ""):
        end_dt = _coerce_calendar_datetime(end_value, 'end_at')
    if end_dt and end_dt < start_dt:
        raise ValueError("Event end time must be after the start time")
    all_day = bool(payload.get('all_day'))
    timezone_value = (payload.get('timezone') or 'UTC').strip() or 'UTC'
    location_value = (payload.get('location') or '').strip()
    notes_value = payload.get('notes')
    if notes_value is None:
        notes_value = ''
    else:
        notes_value = str(notes_value)
    incoming_handle = payload.get('handle') or ''
    candidate_handle = incoming_handle.strip()
    if not candidate_handle:
        candidate_handle = _suggest_calendar_handle(title, start_dt)
    normalized_handle = _ensure_unique_calendar_handle(
        conn,
        candidate_handle,
        existing_id=existing_id,
    )
    end_dt = end_dt or start_dt
    return {
        'title': title,
        'handle': normalized_handle,
        'start_at': start_dt.isoformat(),
        'end_at': end_dt.isoformat(),
        'all_day': all_day,
        'location': location_value,
        'notes': notes_value,
        'timezone': timezone_value,
    }


def _parse_calendar_range_boundary(value: Optional[str], *, end: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = dateutil_parse(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    has_time_component = 'T' in value or 't' in value
    if not has_time_component:
        if end:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
        else:
            parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed.astimezone(timezone.utc)


def _event_overlaps_range(
    event_payload: Dict[str, Any],
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
) -> bool:
    try:
        event_start = dateutil_parse(event_payload.get('start_at'))
        event_end_raw = event_payload.get('end_at') or event_payload.get('start_at')
        event_end = dateutil_parse(event_end_raw)
    except (TypeError, ValueError):
        return False
    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=timezone.utc)
    else:
        event_start = event_start.astimezone(timezone.utc)
    if event_end.tzinfo is None:
        event_end = event_end.replace(tzinfo=timezone.utc)
    else:
        event_end = event_end.astimezone(timezone.utc)
    if event_end < event_start:
        event_end = event_start
    if start_dt and event_end < start_dt:
        return False
    if end_dt and event_start > end_dt:
        return False
    return True


def _serialize_calendar_event(data: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(data)
    payload['all_day'] = bool(payload.get('all_day'))
    payload['location'] = (payload.get('location') or '').strip()
    payload['notes'] = payload.get('notes') or ''
    payload['timezone'] = (payload.get('timezone') or 'UTC').strip() or 'UTC'
    return payload


def _normalize_timezone_value(value: Any, default: str = 'UTC') -> str:
    text = (value or '').strip()
    if not text:
        return default
    try:
        pytz.timezone(text)
    except Exception:
        return default
    return text


def _sanitize_reminder_handle_text(raw: str) -> str:
    if not raw:
        return ""
    normalised = CALENDAR_HANDLE_SANITIZE_RE.sub('-', raw.lower()).strip('-')
    return normalised[:64]


def _suggest_reminder_handle(title: str, due_dt: Optional[datetime]) -> str:
    base = _sanitize_reminder_handle_text(title)
    date_fragment = due_dt.strftime('%Y%m%d') if due_dt else ''
    if base and date_fragment:
        candidate = f"{base[:40]}-{date_fragment}"
    elif base:
        candidate = base[:48]
    elif date_fragment:
        candidate = f"reminder-{date_fragment}"
    else:
        candidate = f"reminder-{uuid.uuid4().hex[:8]}"
    return candidate or f"reminder-{uuid.uuid4().hex[:8]}"


def _ensure_unique_reminder_handle(
    conn: sqlite3.Connection,
    preferred_handle: str,
    *,
    existing_id: Optional[str] = None,
) -> str:
    base = _sanitize_reminder_handle_text(preferred_handle)
    if not base:
        base = f"reminder-{uuid.uuid4().hex[:8]}"
    candidate = base
    suffix = 2
    while True:
        row = conn.execute(
            "SELECT entity_type, entity_id FROM record_handles WHERE handle = ?",
            (candidate,),
        ).fetchone()
        if not row:
            return candidate
        entity_type = row["entity_type"] if isinstance(row, sqlite3.Row) else row[0]
        entity_id = row["entity_id"] if isinstance(row, sqlite3.Row) else row[1]
        if entity_type == 'reminder' and existing_id is not None and str(entity_id) == str(existing_id):
            return candidate
        candidate = f"{base[:48]}-{suffix}"
        suffix += 1


def _coerce_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes', 'y', 'on'}:
            return True
        if lowered in {'false', '0', 'no', 'n', 'off', ''}:
            return False
    return bool(value)


def _parse_timer_expression(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    matches = TIMER_COMPONENT_RE.findall(str(text))
    if not matches:
        return None
    total_seconds = 0
    unit_map = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    for amount_text, unit_text in matches:
        try:
            amount = int(amount_text)
        except (TypeError, ValueError):
            return None
        if amount < 0:
            return None
        multiplier = unit_map.get(unit_text.lower())
        if not multiplier:
            return None
        total_seconds += amount * multiplier
    if total_seconds <= 0:
        return None
    return total_seconds


def _format_timer_label(seconds: int) -> str:
    remaining = max(int(seconds), 0)
    parts: List[str] = []
    for unit_seconds, suffix in ((86400, 'd'), (3600, 'h'), (60, 'm')):
        if remaining >= unit_seconds:
            value, remaining = divmod(remaining, unit_seconds)
            parts.append(f"{value}{suffix}")
    if remaining or not parts:
        parts.append(f"{remaining}s")
    return ''.join(parts)


def _split_timer_prefix(text: str) -> Tuple[Optional[int], str]:
    if not text:
        return None, ''
    match = TIMER_PREFIX_RE.match(text)
    if not match:
        return None, text.strip()
    timer_seconds = _parse_timer_expression(match.group('timer'))
    if not timer_seconds:
        return None, text.strip()
    remainder = text[match.end():].strip()
    return timer_seconds, remainder


def _coerce_optional_reminder_datetime(
    value: Any,
    timezone_name: str,
    field_name: str,
) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = dateutil_parse(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid datetime for '{field_name}': {value}") from exc
    if parsed.tzinfo is None:
        try:
            tz = pytz.timezone(timezone_name)
            parsed = tz.localize(parsed)
        except Exception:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_reminder_payload(
    conn: sqlite3.Connection,
    payload: Dict[str, Any],
    *,
    existing_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be an object")
    existing_data: Dict[str, Any] = {}
    if existing_id:
        existing_record: Optional[Dict[str, Any]] = None
        try:
            service = get_record_service()
            existing_record = service.get_record(conn, 'reminder', str(existing_id))
        except Exception:
            existing_record = None
        if existing_record:
            existing_data = dict(existing_record)
        else:
            row = conn.execute(
                """
                SELECT data
                FROM records
                WHERE entity_type = 'reminder' AND entity_id = ?
                """,
                (existing_id,),
            ).fetchone()
            if row:
                raw = row["data"] if isinstance(row, sqlite3.Row) else row[0]
                try:
                    existing_data = json.loads(raw)
                except Exception:
                    existing_data = {}

    MISSING = object()

    title_source = payload.get('title', MISSING)
    if title_source is MISSING:
        title = (existing_data.get('title') or '').strip()
    else:
        title = (title_source or '').strip()
    if not title and existing_data:
        title = (existing_data.get('title') or '').strip()
    if not title:
        raise ValueError("Title is required")

    timezone_source = payload.get('timezone', MISSING)
    timezone_value = _normalize_timezone_value(
        timezone_source if timezone_source is not MISSING else existing_data.get('timezone')
    )

    notes_source = payload.get('notes', MISSING)
    if notes_source is MISSING:
        notes_candidate = existing_data.get('notes', '')
    else:
        notes_candidate = notes_source
    notes_text = '' if notes_candidate is None else str(notes_candidate)

    kind_source = payload.get('kind', MISSING)
    if kind_source is MISSING:
        kind_value = (existing_data.get('kind') or 'reminder').strip().lower() or 'reminder'
    else:
        kind_value = (str(kind_source) or 'reminder').strip().lower() or 'reminder'
    if kind_value not in {'reminder', 'task'}:
        kind_value = 'reminder'

    timer_source = payload.get('timer_seconds', MISSING)
    timer_was_explicit = timer_source is not MISSING
    if timer_was_explicit:
        if timer_source in (None, '', 0, '0'):
            timer_seconds: Optional[int] = None
        else:
            try:
                timer_seconds = int(timer_source)
            except (TypeError, ValueError) as exc:
                raise ValueError("Timer must be a whole number of seconds.") from exc
            if timer_seconds <= 0:
                raise ValueError("Timer must be a whole number of seconds.")
    else:
        existing_timer = existing_data.get('timer_seconds')
        if existing_timer in (None, '', 0, '0'):
            timer_seconds = None
        else:
            try:
                timer_seconds = int(existing_timer)
            except (TypeError, ValueError):
                timer_seconds = None

    due_source_present = 'due_at' in payload
    due_source_value = payload.get('due_at') if due_source_present else existing_data.get('due_at')
    if timer_was_explicit and timer_seconds is not None:
        due_dt: Optional[datetime] = datetime.now(timezone.utc) + timedelta(seconds=timer_seconds)
    else:
        if due_source_value in (None, ''):
            due_dt = None
        else:
            due_dt = _coerce_optional_reminder_datetime(due_source_value, timezone_value, 'due_at')

    due_has_time_source = payload.get('due_has_time', MISSING)
    if timer_was_explicit and timer_seconds is not None:
        due_has_time = True
    else:
        if due_has_time_source is MISSING:
            due_has_time_flag = existing_data.get('due_has_time')
        else:
            due_has_time_flag = due_has_time_source
        due_has_time = bool(due_has_time_flag) if due_dt else False
        if due_dt and due_has_time_source is MISSING and due_source_present:
            try:
                tz = pytz.timezone(timezone_value)
                local_dt = due_dt.astimezone(tz)
                if any((local_dt.hour, local_dt.minute, local_dt.second, local_dt.microsecond)):
                    due_has_time = True
            except Exception:
                if isinstance(due_source_value, str) and 'T' in due_source_value:
                    time_fragment = due_source_value.split('T', 1)[1]
                    if not time_fragment.startswith('00:00'):
                        due_has_time = True

    remind_source_present = 'remind_at' in payload
    remind_source_value = payload.get('remind_at') if remind_source_present else existing_data.get('remind_at')
    if timer_was_explicit and timer_seconds is not None:
        remind_dt: Optional[datetime] = due_dt
    elif remind_source_value in (None, ''):
        remind_dt = due_dt
    else:
        remind_dt = _coerce_optional_reminder_datetime(remind_source_value, timezone_value, 'remind_at')

    completed_source = payload.get('completed', MISSING)
    if completed_source is MISSING:
        completed = bool(existing_data.get('completed'))
    else:
        completed = bool(completed_source)

    completed_at_source_present = 'completed_at' in payload
    completed_at_source_value = payload.get('completed_at') if completed_at_source_present else None
    completed_dt: Optional[datetime] = None
    if completed:
        if completed_at_source_present and completed_at_source_value not in (None, ''):
            completed_dt = _coerce_optional_reminder_datetime(
                completed_at_source_value, timezone_value, 'completed_at'
            )
        elif existing_data.get('completed') and existing_data.get('completed_at'):
            completed_dt = _coerce_optional_reminder_datetime(
                existing_data['completed_at'], timezone_value, 'completed_at'
            )
        else:
            completed_dt = datetime.now(timezone.utc)
    else:
        if completed_at_source_present and completed_at_source_value not in (None, ''):
            completed_dt = _coerce_optional_reminder_datetime(
                completed_at_source_value, timezone_value, 'completed_at'
            )
        else:
            completed_dt = None

    persistent_source = payload.get('persistent', MISSING)
    if persistent_source is MISSING:
        persistent_flag = _coerce_boolean(existing_data.get('persistent'))
    else:
        persistent_flag = _coerce_boolean(persistent_source)

    context_note_source = payload.get('context_note_id', MISSING)
    if context_note_source is MISSING:
        context_note_id = (existing_data.get('context_note_id') or '').strip()
    else:
        context_note_id = str(context_note_source or '').strip()
    if not context_note_id:
        context_note_id = None

    last_notified_present = 'last_notified_at' in payload
    last_notified_source_value = (
        payload.get('last_notified_at') if last_notified_present else existing_data.get('last_notified_at')
    )
    if last_notified_source_value in (None, ''):
        last_notified_dt: Optional[datetime] = None
    else:
        last_notified_dt = _coerce_optional_reminder_datetime(
            last_notified_source_value,
            timezone_value,
            'last_notified_at',
        )

    handle_source = payload.get('handle', MISSING)
    if handle_source is MISSING:
        incoming_handle = (existing_data.get('handle') or '').strip()
    else:
        incoming_handle = (handle_source or '').strip()
    candidate_handle = incoming_handle or _suggest_reminder_handle(title, due_dt)
    normalized_handle = _ensure_unique_reminder_handle(
        conn,
        candidate_handle,
        existing_id=existing_id,
    )

    return {
        'title': title,
        'handle': normalized_handle,
        'notes': notes_text,
        'kind': kind_value,
        'due_at': due_dt.isoformat() if due_dt else None,
        'due_has_time': bool(due_has_time),
        'remind_at': remind_dt.isoformat() if remind_dt else None,
        'timer_seconds': int(timer_seconds) if timer_seconds is not None else None,
        'timezone': timezone_value,
        'completed': completed,
        'completed_at': completed_dt.isoformat() if completed_dt else None,
        'persistent': bool(persistent_flag),
        'context_note_id': context_note_id,
        'last_notified_at': last_notified_dt.isoformat() if last_notified_dt else None,
    }


def _reminder_overlaps_range(
    reminder_payload: Dict[str, Any],
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    *,
    include_without_due: bool = True,
) -> bool:
    due_value = reminder_payload.get('due_at')
    if not due_value:
        return include_without_due
    try:
        due_dt = dateutil_parse(due_value)
    except (TypeError, ValueError):
        return False
    if due_dt.tzinfo is None:
        due_dt = due_dt.replace(tzinfo=timezone.utc)
    else:
        due_dt = due_dt.astimezone(timezone.utc)
    if start_dt and due_dt < start_dt:
        return False
    if end_dt and due_dt > end_dt:
        return False
    return True


def _serialize_reminder(data: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(data)
    payload['notes'] = payload.get('notes') or ''
    payload['due_at'] = payload.get('due_at') or None
    payload['due_has_time'] = bool(payload.get('due_has_time')) and bool(payload['due_at'])
    kind_value = (payload.get('kind') or 'reminder').strip().lower() or 'reminder'
    payload['kind'] = kind_value
    remind_at_value = payload.get('remind_at') or payload['due_at'] or None
    payload['remind_at'] = remind_at_value
    timer_value = payload.get('timer_seconds')
    try:
        payload['timer_seconds'] = int(timer_value) if timer_value not in (None, '') else None
    except (TypeError, ValueError):
        payload['timer_seconds'] = None
    payload['timezone'] = (payload.get('timezone') or 'UTC').strip() or 'UTC'
    payload['completed'] = bool(payload.get('completed'))
    payload['completed_at'] = payload.get('completed_at') or None
    payload['handle'] = (payload.get('handle') or '').strip()
    payload['persistent'] = bool(payload.get('persistent'))
    payload['context_note_id'] = (payload.get('context_note_id') or '').strip() or None
    payload['last_notified_at'] = payload.get('last_notified_at') or None
    return payload


def _reminder_sort_key(reminder: Dict[str, Any]):
    due_value = reminder.get('due_at') or ''
    has_no_due = 1 if not reminder.get('due_at') else 0
    title_value = (reminder.get('title') or '').lower()
    return (has_no_due, due_value, title_value)


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ''):
        return None
    try:
        parsed = dateutil_parse(str(value))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _reminder_should_fire(reminder: Dict[str, Any], now: datetime) -> bool:
    kind_value = (reminder.get('kind') or 'reminder').strip().lower() or 'reminder'
    if kind_value != 'reminder':
        return False
    if reminder.get('completed'):
        return False
    remind_value = reminder.get('remind_at') or reminder.get('due_at')
    remind_dt = _parse_utc_datetime(remind_value)
    if not remind_dt:
        return False
    if remind_dt > now:
        return False
    last_notified_dt = _parse_utc_datetime(reminder.get('last_notified_at'))
    if last_notified_dt and last_notified_dt >= remind_dt:
        return False
    return True


def _build_reminder_fire_message(reminder: Dict[str, Any]) -> str:
    title = reminder.get('title') or 'Reminder'
    timer_value = reminder.get('timer_seconds')
    try:
        timer_seconds = int(timer_value) if timer_value not in (None, '') else None
    except (TypeError, ValueError):
        timer_seconds = None
    if timer_seconds:
        timer_label = _format_timer_label(timer_seconds)
        if timer_label:
            return f"⏰ Reminder \"{title}\" timer {timer_label} is done."
    due_label = _format_reminder_due(reminder)
    if due_label and due_label != 'no due date':
        return f"⏰ Reminder \"{title}\" {due_label}."
    return f"⏰ Reminder \"{title}\" is due now."


def _deliver_reminder_notification(
    conn: sqlite3.Connection,
    reminder: Dict[str, Any],
    now: datetime,
) -> Dict[str, Any]:
    reminder_id = reminder.get('id')
    if not reminder_id:
        return reminder
    service = get_record_service()
    update_payload: Dict[str, Any] = {
        'last_notified_at': now.isoformat(),
    }
    if not reminder.get('persistent'):
        update_payload['completed'] = True
        update_payload['completed_at'] = now.isoformat()
    normalized = _normalize_reminder_payload(conn, update_payload, existing_id=str(reminder_id))
    updated = service.update_record(
        conn,
        'reminder',
        str(reminder_id),
        normalized,
        actor='reminder-dispatcher',
    )
    serialized = _serialize_reminder(updated['data'])
    note_id = serialized.get('context_note_id')
    if note_id:
        try:
            message_text = _build_reminder_fire_message(serialized)
            metadata = {'action': 'reminder_fired', 'reminder': serialized}
            _store_chat_message(conn, str(note_id), 'assistant', message_text, metadata=metadata)
        except Exception as exc:  # pragma: no cover - defensive logging
            app.logger.exception('Failed to post reminder notification for %s: %s', reminder_id, exc)
    return serialized


def _dispatch_due_reminders(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    effective_now = now or datetime.now(timezone.utc)
    conn = get_db_connection()
    fired: List[Dict[str, Any]] = []
    try:
        service = get_record_service()
        records = service.list_records(conn, 'reminder')
        for record in records:
            payload = dict(record)
            serialized = _serialize_reminder(payload)
            serialized['id'] = payload.get('id') or serialized.get('id')
            if not serialized.get('id'):
                continue
            if not _reminder_should_fire(serialized, effective_now):
                continue
            try:
                updated = _deliver_reminder_notification(conn, serialized, effective_now)
                fired.append(updated)
            except Exception as exc:  # pragma: no cover - defensive logging
                app.logger.exception('Reminder delivery failed for %s: %s', serialized.get('id'), exc)
        conn.commit()
        return fired
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_reminder_dispatch_cycle(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    return _dispatch_due_reminders(now=now)


def _reminder_dispatcher_loop() -> None:
    while not _reminder_dispatcher_stop_event.is_set():
        try:
            _dispatch_due_reminders()
        except Exception as exc:  # pragma: no cover - defensive logging
            app.logger.exception('Reminder dispatch cycle failed: %s', exc)
        _reminder_dispatcher_stop_event.wait(_REMINDER_DISPATCH_INTERVAL_SECONDS)


def _ensure_reminder_dispatcher_started() -> None:
    global _reminder_dispatcher_started, _reminder_dispatcher_thread
    with _reminder_dispatcher_lock:
        if _reminder_dispatcher_started or app.config.get('TESTING'):
            return
        if _reminder_dispatcher_stop_event.is_set():
            _reminder_dispatcher_stop_event.clear()
        thread = Thread(target=_reminder_dispatcher_loop, name='ReminderDispatcher', daemon=True)
        thread.start()
        _reminder_dispatcher_thread = thread
        _reminder_dispatcher_started = True

@app.route('/api/records/handles', methods=['GET'])
def api_record_handles():
    service = get_record_service()
    conn = get_db_connection()
    try:
        entity_types_param = request.args.get('entity_types') or request.args.get('entityTypes')
        entity_types = [value.strip() for value in entity_types_param.split(',')] if entity_types_param else None
        if entity_types:
            entity_types = [value for value in entity_types if value]
        search = request.args.get('q') or request.args.get('search') or None
        handles = service.list_handles(conn, entity_types, search)
        return jsonify({'handles': handles})
    finally:
        conn.close()


@app.route('/api/records/schemas', methods=['GET', 'POST'])
def api_record_schemas():
    service = get_record_service()
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            schemas = [schema.to_dict() for schema in service.registry.all()]
            return jsonify({'schemas': schemas})
        payload = request.get_json(force=True, silent=True) or {}
        schema = service.register_schema(conn, payload)
        conn.commit()
        return jsonify({'schema': schema.to_dict()}), 201
    except Exception as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    finally:
        conn.close()

@app.route('/api/records/<string:entity_type>', methods=['GET', 'POST'])
def api_records(entity_type):
    service = get_record_service()
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            records = service.list_records(conn, entity_type)
            return jsonify({'records': records})
        payload = request.get_json(force=True, silent=True) or {}
        actor = payload.pop('actor', None)
        record_payload = payload.get('data') if isinstance(payload.get('data'), dict) else payload
        created = service.create_record(conn, entity_type, record_payload, actor=actor)
        conn.commit()
        return jsonify({'record': created}), 201
    except RecordValidationError as err:
        conn.rollback()
        return jsonify({'message': 'Validation failed', 'errors': err.errors}), 400
    except KeyError:
        conn.rollback()
        return jsonify({'message': f'Unknown record type {entity_type}'}), 404
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    finally:
        conn.close()

@app.route('/api/records/<string:entity_type>/<string:entity_id>', methods=['GET', 'PUT'])
def api_record_detail(entity_type, entity_id):
    service = get_record_service()
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            record = service.get_record(conn, entity_type, entity_id)
            if not record:
                return jsonify({'message': 'Record not found'}), 404
            return jsonify({'record': record})
        payload = request.get_json(force=True, silent=True) or {}
        actor = payload.pop('actor', None)
        record_payload = payload.get('data') if isinstance(payload.get('data'), dict) else payload
        updated = service.update_record(conn, entity_type, entity_id, record_payload, actor=actor)
        conn.commit()
        return jsonify({'record': updated})
    except RecordValidationError as err:
        conn.rollback()
        return jsonify({'message': 'Validation failed', 'errors': err.errors}), 400
    except KeyError:
        conn.rollback()
        return jsonify({'message': f'Unknown record type {entity_type}'}), 404
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    finally:
        conn.close()

@app.route('/api/records/<string:entity_type>/<string:entity_id>/activity', methods=['GET'])
def api_record_activity(entity_type, entity_id):
    service = get_record_service()
    conn = get_db_connection()
    try:
        try:
            limit = int(request.args.get('limit', 50))
        except (TypeError, ValueError):
            limit = 50
        activity = service.fetch_activity(conn, entity_type, entity_id, limit=limit)
        return jsonify({'activity': activity})
    finally:
        conn.close()


@app.route('/api/calendar/events', methods=['GET', 'POST'])
def api_calendar_events():
    service = get_record_service()
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            start_param = request.args.get('start') or request.args.get('range_start')
            end_param = request.args.get('end') or request.args.get('range_end')
            range_start = _parse_calendar_range_boundary(start_param) if start_param else None
            range_end = _parse_calendar_range_boundary(end_param, end=True) if end_param else None
            records = service.list_records(conn, 'calendar_event')
            events: List[Dict[str, Any]] = []
            for record in records:
                normalized = _serialize_calendar_event(record)
                if _event_overlaps_range(normalized, range_start, range_end):
                    events.append(normalized)
            events.sort(key=lambda payload: payload.get('start_at') or '')
            return jsonify({'events': events})
        payload = request.get_json(force=True, silent=True) or {}
        actor = payload.pop('actor', None)
        event_payload = _normalize_calendar_event_payload(conn, payload)
        created = service.create_record(conn, 'calendar_event', event_payload, actor=actor)
        conn.commit()
        return jsonify({'event': _serialize_calendar_event(created['data'])}), 201
    except RecordValidationError as err:
        conn.rollback()
        return jsonify({'message': 'Validation failed', 'errors': err.errors}), 400
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    finally:
        conn.close()


@app.route('/api/calendar/events/<string:event_id>', methods=['GET', 'PUT', 'DELETE'])
def api_calendar_event_detail(event_id):
    service = get_record_service()
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            record = service.get_record(conn, 'calendar_event', event_id)
            if not record:
                return jsonify({'message': 'Event not found'}), 404
            return jsonify({'event': _serialize_calendar_event(record)})
        if request.method == 'DELETE':
            try:
                service.delete_record(conn, 'calendar_event', event_id)
            except KeyError:
                conn.rollback()
                return jsonify({'message': 'Event not found'}), 404
            conn.commit()
            return jsonify({'message': 'Event deleted'})
        payload = request.get_json(force=True, silent=True) or {}
        actor = payload.pop('actor', None)
        event_payload = _normalize_calendar_event_payload(conn, payload, existing_id=event_id)
        updated = service.update_record(conn, 'calendar_event', event_id, event_payload, actor=actor)
        conn.commit()
        return jsonify({'event': _serialize_calendar_event(updated['data'])})
    except RecordValidationError as err:
        conn.rollback()
        return jsonify({'message': 'Validation failed', 'errors': err.errors}), 400
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    finally:
        conn.close()


def _parse_truthy_param(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


@app.route('/api/reminders', methods=['GET', 'POST'])
def api_reminders():
    service = get_record_service()
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            status_param = (request.args.get('status') or 'active').strip().lower()
            if status_param not in {'active', 'completed', 'all'}:
                status_param = 'active'
            kind_param = request.args.get('kind') or request.args.get('type')
            kind_filter = None
            if kind_param:
                normalized_kind = kind_param.strip().lower()
                if normalized_kind in {'task', 'tasks'}:
                    kind_filter = 'task'
                elif normalized_kind in {'reminder', 'reminders'}:
                    kind_filter = 'reminder'
            start_param = request.args.get('start') or request.args.get('range_start')
            end_param = request.args.get('end') or request.args.get('range_end')
            range_start = _parse_calendar_range_boundary(start_param) if start_param else None
            range_end = _parse_calendar_range_boundary(end_param, end=True) if end_param else None
            scheduled_only = _parse_truthy_param(request.args.get('scheduled_only') or request.args.get('scheduledOnly'))
            records = service.list_records(conn, 'reminder')
            reminders: List[Dict[str, Any]] = []
            for record in records:
                serialized = _serialize_reminder(record)
                if kind_filter and serialized.get('kind') != kind_filter:
                    continue
                if status_param == 'active' and serialized['completed']:
                    continue
                if status_param == 'completed' and not serialized['completed']:
                    continue
                if not _reminder_overlaps_range(
                    serialized,
                    range_start,
                    range_end,
                    include_without_due=not scheduled_only,
                ):
                    continue
                reminders.append(serialized)
            reminders.sort(key=_reminder_sort_key)
            return jsonify({'reminders': reminders})
        payload = request.get_json(force=True, silent=True) or {}
        actor = payload.pop('actor', None)
        reminder_payload = _normalize_reminder_payload(conn, payload)
        created = service.create_record(conn, 'reminder', reminder_payload, actor=actor)
        conn.commit()
        return jsonify({'reminder': _serialize_reminder(created['data'])}), 201
    except RecordValidationError as err:
        conn.rollback()
        return jsonify({'message': 'Validation failed', 'errors': err.errors}), 400
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    finally:
        conn.close()


@app.route('/api/reminders/<string:reminder_id>', methods=['GET', 'PUT', 'DELETE'])
def api_reminder_detail(reminder_id):
    service = get_record_service()
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            record = service.get_record(conn, 'reminder', reminder_id)
            if not record:
                return jsonify({'message': 'Reminder not found'}), 404
            return jsonify({'reminder': _serialize_reminder(record)})
        if request.method == 'DELETE':
            try:
                service.delete_record(conn, 'reminder', reminder_id)
            except KeyError:
                conn.rollback()
                return jsonify({'message': 'Reminder not found'}), 404
            conn.commit()
            return jsonify({'message': 'Reminder deleted'})
        payload = request.get_json(force=True, silent=True) or {}
        actor = payload.pop('actor', None)
        reminder_payload = _normalize_reminder_payload(conn, payload, existing_id=reminder_id)
        updated = service.update_record(conn, 'reminder', reminder_id, reminder_payload, actor=actor)
        conn.commit()
        return jsonify({'reminder': _serialize_reminder(updated['data'])})
    except RecordValidationError as err:
        conn.rollback()
        return jsonify({'message': 'Validation failed', 'errors': err.errors}), 400
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    finally:
        conn.close()


@app.route('/api/contacts/<string:contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("DELETE FROM contacts WHERE id=?",(contact_id,))
        conn.commit()
        if cursor.rowcount>0: conn.close(); return jsonify({"message":"Contact deleted."}),200
        else: conn.close(); return jsonify({"message":"Contact not found."}),404
    except sqlite3.Error as e: conn.rollback(); conn.close(); app.logger.error(f"DB err delete contact {contact_id}:{e}"); return jsonify({"message":"DB error."}),500

@app.route('/api/packages', methods=['GET'])
def get_packages():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT package_id, name, created_at, updated_at FROM packages ORDER BY name COLLATE NOCASE ASC")
    packages = {}
    for pkg_row in cursor.fetchall():
        pkg_dict = dict(pkg_row)
        cursor.execute(
            """
            SELECT pi.item_id, pi.quantity, i.name, i.description, i.price_cents
            FROM package_items pi
            LEFT JOIN items i ON i.id = pi.item_id
            WHERE pi.package_id = ?
            ORDER BY COALESCE(i.name, pi.item_id) COLLATE NOCASE ASC
            """,
            (pkg_dict['package_id'],)
        )
        contents = []
        for content_row in cursor.fetchall():
            contents.append({
                'itemId': content_row['item_id'],
                'quantity': content_row['quantity'],
                'name': content_row['name'],
                'description': content_row['description'],
                'price': content_row['price_cents'],
            })
        packages[str(pkg_dict['package_id'])] = {
            'name': pkg_dict['name'],
            'packageId': pkg_dict['package_id'],
            'id_val': pkg_dict['package_id'],
            'contents': contents,
            'createdAt': pkg_dict.get('created_at'),
            'updatedAt': pkg_dict.get('updated_at'),
        }
    conn.close()
    return jsonify(packages)

@app.route('/api/packages', methods=['POST'])
def add_package():
    payload = request.json or {}
    name = (payload.get('name') or '').strip()
    if not name:
        return jsonify({"message": "Package name is required."}), 400

    raw_id = payload.get('packageId', payload.get('id_val', payload.get('id')))
    if raw_id is None:
        return jsonify({"message": "Package ID is required."}), 400
    try:
        pkg_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"message": "Package ID must be a number."}), 400

    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT package_id FROM packages WHERE name=? OR package_id=?", (name, pkg_id))
        existing = cursor.fetchone()
        if existing:
            return jsonify({"message": f"Package '{name}' or ID {pkg_id} already exists."}), 409

        cursor.execute("INSERT INTO packages (package_id, name) VALUES (?,?)", (pkg_id, name))

        try:
            parsed_contents = parse_package_contents(cursor, payload)
        except ValueError as exc:
            conn.rollback()
            return jsonify({"message": str(exc)}), 400

        aggregated = {}
        for entry in parsed_contents:
            item_id = entry['itemId']
            quantity = entry['quantity']
            if item_id in aggregated:
                aggregated[item_id]['quantity'] += quantity
            else:
                aggregated[item_id] = {'itemId': item_id, 'quantity': quantity}

        for entry in aggregated.values():
            cursor.execute(
                "INSERT OR REPLACE INTO package_items (package_id, item_id, quantity) VALUES (?,?,?)",
                (pkg_id, entry['itemId'], entry['quantity'])
            )

        conn.commit()
        serialized = serialize_package(cursor, pkg_id) or {
            'name': name,
            'packageId': pkg_id,
            'id_val': pkg_id,
            'createdAt': None,
            'updatedAt': None,
            'contents': [],
        }
        return jsonify({"message": "Package added.", "package": {str(pkg_id): serialized}}), 201
    except sqlite3.Error as e:
        conn.rollback(); app.logger.error(f"DB err add pkg:{e}")
        return jsonify({"message": "DB error."}), 500
    finally:
        conn.close()

@app.route('/api/packages/<string:package_id_str>', methods=['PUT'])
def update_package(package_id_str):
    payload = request.json or {}
    try:
        target_pkg_id = int(package_id_str)
    except ValueError:
        return jsonify({"message": "Invalid pkg ID in URL."}), 400

    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT package_id, name FROM packages WHERE package_id=?", (target_pkg_id,))
        curr_pkg = cursor.fetchone()
        if not curr_pkg:
            return jsonify({"message": f"Package ID {target_pkg_id} not found."}), 404

        new_name = (payload.get('name', curr_pkg['name']) or '').strip()
        if not new_name:
            return jsonify({"message": "Package name cannot be empty."}), 400

        new_id_raw = payload.get('packageId', payload.get('id_val', payload.get('id')))
        new_id = target_pkg_id
        if new_id_raw is not None:
            try:
                new_id = int(new_id_raw)
            except (TypeError, ValueError):
                return jsonify({"message": "New package ID must be a number."}), 400

        if new_name != curr_pkg['name']:
            cursor.execute("SELECT package_id FROM packages WHERE name=? AND package_id!=?", (new_name, target_pkg_id))
            if cursor.fetchone():
                return jsonify({"message": f"Package name '{new_name}' already exists."}), 409

        if new_id != target_pkg_id:
            cursor.execute("SELECT package_id FROM packages WHERE package_id=?", (new_id,))
            if cursor.fetchone():
                return jsonify({"message": f"Package ID '{new_id}' already exists."}), 409
            cursor.execute(
                "UPDATE packages SET package_id=?, name=?, updated_at=CURRENT_TIMESTAMP WHERE package_id=?",
                (new_id, new_name, target_pkg_id)
            )
            cursor.execute("UPDATE package_items SET package_id=? WHERE package_id=?", (new_id, target_pkg_id))
            cursor.execute("UPDATE order_line_items SET package_id=? WHERE package_id=?", (new_id, target_pkg_id))
        else:
            cursor.execute(
                "UPDATE packages SET name=?, updated_at=CURRENT_TIMESTAMP WHERE package_id=?",
                (new_name, target_pkg_id)
            )

        final_id_for_contents = new_id

        if any(key in payload for key in ('contents', 'contents_raw_text', 'contentsRawText')):
            try:
                parsed_contents = parse_package_contents(cursor, payload)
            except ValueError as exc:
                conn.rollback()
                return jsonify({"message": str(exc)}), 400

            cursor.execute("DELETE FROM package_items WHERE package_id=?", (final_id_for_contents,))
            aggregated = {}
            for entry in parsed_contents:
                item_id = entry['itemId']
                quantity = entry['quantity']
                if item_id in aggregated:
                    aggregated[item_id]['quantity'] += quantity
                else:
                    aggregated[item_id] = {'itemId': item_id, 'quantity': quantity}

            for entry in aggregated.values():
                cursor.execute(
                    "INSERT OR REPLACE INTO package_items (package_id, item_id, quantity) VALUES (?,?,?)",
                    (final_id_for_contents, entry['itemId'], entry['quantity'])
                )

        conn.commit()
        serialized = serialize_package(cursor, final_id_for_contents) or {
            'name': new_name,
            'packageId': final_id_for_contents,
            'id_val': final_id_for_contents,
            'createdAt': None,
            'updatedAt': None,
            'contents': [],
        }
        return jsonify({"message": "Package updated.", "package": {str(final_id_for_contents): serialized}}), 200
    except sqlite3.Error as e:
        conn.rollback(); app.logger.error(f"DB err update pkg {package_id_str}:{e}")
        return jsonify({"message": "DB error."}), 500
    finally:
        conn.close()

@app.route('/api/packages/<string:package_id_str>', methods=['DELETE'])
def delete_package(package_id_str):
    try:
        target_pkg_id = int(package_id_str)
    except ValueError:
        return jsonify({"message": "Invalid pkg ID."}), 400
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM packages WHERE package_id=?", (target_pkg_id,))
        conn.commit()
        if cursor.rowcount > 0:
            return jsonify({"message": "Package deleted."}), 200
        else:
            return jsonify({"message": "Package not found."}), 404
    except sqlite3.Error as e:
        conn.rollback(); app.logger.error(f"DB err delete pkg {target_pkg_id}:{e}")
        return jsonify({"message": "DB error."}), 500
    finally:
        conn.close()

@app.route('/api/upload-attachment', methods=['POST'])
def upload_attachment():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({"status": "error", "message": "No selected file"}), 400
    
    original_filename = secure_filename(file.filename)
    unique_id = uuid.uuid4().hex[:6]
    filename, file_extension = os.path.splitext(original_filename)
    new_filename = f"{filename}_{unique_id}{file_extension}"
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
    try:
        file.save(filepath)
        return jsonify({
            "status": "success",
            "message": "File uploaded successfully",
            "originalFilename": original_filename,
            "uniqueFilename": new_filename 
        }), 200
    except Exception as e:
        app.logger.error(f"Error saving uploaded file: {e}")
        return jsonify({"status": "error", "message": f"Could not save file: {str(e)}"}), 500

@app.route('/api/import-customers-csv', methods=['POST'])
def import_customers_csv():
    if 'csv_file' not in request.files:
        return "No file part", 400
    file = request.files['csv_file']
    if file.filename == '':
        return "No selected file", 400
    if file and file.filename and file.filename.endswith('.csv'):
        try:
            csv_file = file.stream.read().decode("utf-8")
            csv_reader = csv.reader(csv_file.splitlines())
            header = [h.lower().strip() for h in next(csv_reader)]
            
            header_map = {
                'company name': 'company_name',
                'contact name': 'contact_name',
                'email': 'email',
                'phone': 'phone',
                'billing address': 'billing_address',
                'billing city': 'billing_city',
                'billing state': 'billing_state',
                'billing zip code': 'billing_zip_code',
                'shipping address': 'shipping_address',
                'shipping city': 'shipping_city',
                'shipping state': 'shipping_state',
                'shipping zip code': 'shipping_zip_code'
            }
            
            column_indices = {db_col: header.index(csv_col) for csv_col, db_col in header_map.items() if csv_col in header}

            if not column_indices:
                flash("Could not find any matching headers in the CSV file. Please make sure the file contains at least one of the following headers: Company Name, Contact Name, Email, Phone, Billing Address, Shipping Address.", "warning")
                return redirect('/manage/customers')

            if 'company_name' not in column_indices:
                flash("CSV must have a 'Company Name' column.", "danger")
                return redirect('/manage/customers')

            conn = get_db_connection()
            cursor = conn.cursor()
            
            for row in csv_reader:
                company_name_idx = column_indices.get('company_name')
                if company_name_idx is None:
                    continue
                company_name = row[company_name_idx]

                contact_name_idx = column_indices.get('contact_name')
                contact_name = row[contact_name_idx] if contact_name_idx is not None else ''

                email_idx = column_indices.get('email')
                email = row[email_idx] if email_idx is not None else ''

                phone_idx = column_indices.get('phone')
                phone = row[phone_idx] if phone_idx is not None else ''

                billing_address_idx = column_indices.get('billing_address')
                billing_address = row[billing_address_idx] if billing_address_idx is not None else ''
                billing_city_idx = column_indices.get('billing_city')
                billing_city = row[billing_city_idx] if billing_city_idx is not None else ''
                billing_state_idx = column_indices.get('billing_state')
                billing_state = row[billing_state_idx] if billing_state_idx is not None else ''
                billing_zip_code_idx = column_indices.get('billing_zip_code')
                billing_zip_code = row[billing_zip_code_idx] if billing_zip_code_idx is not None else ''

                shipping_address_idx = column_indices.get('shipping_address')
                shipping_address = row[shipping_address_idx] if shipping_address_idx is not None else ''
                shipping_city_idx = column_indices.get('shipping_city')
                shipping_city = row[shipping_city_idx] if shipping_city_idx is not None else ''
                shipping_state_idx = column_indices.get('shipping_state')
                shipping_state = row[shipping_state_idx] if shipping_state_idx is not None else ''
                shipping_zip_code_idx = column_indices.get('shipping_zip_code')
                shipping_zip_code = row[shipping_zip_code_idx] if shipping_zip_code_idx is not None else ''

                cursor.execute("SELECT id FROM contacts WHERE company_name = ?", (company_name,))
                existing_contact = cursor.fetchone()
                
                if existing_contact:
                    cursor.execute("""
                        UPDATE contacts 
                        SET contact_name = ?, email = ?, phone = ?, billing_address = ?, billing_city = ?, billing_state = ?, billing_zip_code = ?, shipping_address = ?, shipping_city = ?, shipping_state = ?, shipping_zip_code = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE company_name = ?
                    """, (contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, company_name))
                else:
                    contact_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO contacts (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (contact_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code))
            
            conn.commit()
            conn.close()
            
            return redirect('/manage/customers')
        except Exception as e:
            app.logger.error(f"Error processing CSV file: {e}")
            return "Error processing file", 500
    return "Invalid file type", 400

@app.route('/api/import-items-csv', methods=['POST'])
def import_items_csv():
    if 'csv_file' not in request.files:
        flash("No file part", "danger")
        return redirect('/manage/items')
    file = request.files['csv_file']
    if file.filename == '':
        flash("No selected file", "danger")
        return redirect('/manage/items')
    if file and file.filename and file.filename.endswith('.csv'):
        try:
            csv_file = file.stream.read().decode("utf-8")
            csv_reader = csv.reader(csv_file.splitlines())
            header = [h.lower().strip() for h in next(csv_reader)]

            column_indices = {}
            for idx, col in enumerate(header):
                if col in ('item id', 'item code', 'id') and 'id' not in column_indices:
                    column_indices['id'] = idx
                elif col == 'name':
                    column_indices['name'] = idx
                elif col == 'description':
                    column_indices['description'] = idx
                elif col in ('price', 'price dollars', 'price$'):
                    column_indices['price'] = idx

            if 'name' not in column_indices:
                flash("CSV must have at least a 'Name' column.", "danger")
                return redirect('/manage/items')

            conn = get_db_connection()
            cursor = conn.cursor()

            items_added = 0
            items_updated = 0

            for row in csv_reader:
                try:
                    name = row[column_indices['name']].strip()
                    if not name:
                        continue

                    item_id = None
                    if 'id' in column_indices and column_indices['id'] < len(row):
                        item_id = row[column_indices['id']].strip() or None
                    if not item_id:
                        item_id = str(uuid.uuid4())

                    description = ''
                    if 'description' in column_indices and column_indices['description'] < len(row):
                        description = row[column_indices['description']].strip()

                    price_cents = 0
                    if 'price' in column_indices and column_indices['price'] < len(row):
                        try:
                            price_cents = _parse_price_to_cents(row[column_indices['price']])
                        except (ValueError, TypeError):
                            price_cents = 0

                    cursor.execute("SELECT id FROM items WHERE id = ?", (item_id,))
                    existing_item = cursor.fetchone()

                    if existing_item:
                        cursor.execute(
                            """
                            UPDATE items
                            SET name = ?, description = ?, price_cents = ?, weight_oz = NULL, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (name, description, price_cents, item_id)
                        )
                        items_updated += 1
                    else:
                        cursor.execute(
                            """
                            INSERT INTO items (id, name, description, price_cents, weight_oz)
                            VALUES (?, ?, ?, ?, NULL)
                            """,
                            (item_id, name, description, price_cents)
                        )
                        items_added += 1
                except IndexError:
                    app.logger.warning(f"Skipping malformed row: {row}")
                    continue

            conn.commit()
            conn.close()

            flash(f"Successfully added {items_added} and updated {items_updated} items.", "success")
            return redirect('/manage/items')
        except Exception as e:
            app.logger.error(f"Error processing items CSV file: {e}")
            flash(f"Error processing file: {e}", "danger")
            return redirect('/manage/items')
    
    flash("Invalid file type. Please upload a .csv file.", "warning")
    return redirect('/manage/items')

@app.route('/api/send-order-email', methods=['POST'])
def send_order_email_route():
    data = request.json
    if not data:
        return jsonify({"message": "Request must be JSON"}), 400

    order_data = data.get('order')
    to_email = data.get('recipientEmail')
    subject = data.get('subject')
    body = data.get('body')
    custom_attachment_filenames = data.get('attachments', [])

    if not all([order_data, to_email, subject, body]):
        return jsonify({"message": "Missing required email data."}), 400

    settings = read_json_file(SETTINGS_FILE)
    from_email = settings.get('email_address')
    from_pass = settings.get('app_password')
    email_cc = settings.get('email_cc')
    email_bcc = settings.get('email_bcc')

    if not from_email or not from_pass:
        app.logger.error("Email credentials are not configured on the server.")
        return jsonify({"message": "Email service is not configured."}), 500

    attachment_paths_to_delete = []
    try:
        order_id_log = order_data.get('order_id', 'N/A')
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        if email_cc:
            msg['Cc'] = email_cc
        if email_bcc:
            msg['Bcc'] = email_bcc
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        if isinstance(custom_attachment_filenames, list):
            for attachment_info in custom_attachment_filenames:
                unique_fn = attachment_info.get('unique')
                original_fn = attachment_info.get('original')
                if not unique_fn or not original_fn:
                    continue

                attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(unique_fn))
                if os.path.exists(attachment_path):
                    with open(attachment_path, "rb") as attachment_file:
                        part = MIMEApplication(attachment_file.read(), Name=original_fn)
                    part['Content-Disposition'] = f'attachment; filename="{original_fn}"'
                    msg.attach(part)
                    attachment_paths_to_delete.append(attachment_path)
                else:
                    app.logger.warning(f"Attachment file not found on server: {unique_fn}")
        
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(from_email, from_pass)
        
        all_recipients = [to_email]
        if email_cc:
            all_recipients.extend([e.strip() for e in email_cc.split(',')])
        if email_bcc:
            all_recipients.extend([e.strip() for e in email_bcc.split(',')])
            
        server.sendmail(from_email, all_recipients, msg.as_string())
        server.close()
        
        app.logger.info(f"Email with {len(attachment_paths_to_delete)} attachment(s) sent for order {order_id_log}")
        
        return jsonify({"message": "Email sent."}), 200
    except Exception as e:
        app.logger.error(f"Failed to send email for order {order_data.get('id', 'N/A')}: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"message": f"Failed to send email: {str(e)}"}), 500

@app.route('/api/navigation', methods=['GET'])
def get_navigation_settings():
    settings = _load_settings_dict()
    selected = get_selected_nav_shortcut_ids(settings=settings)
    return jsonify({
        'available': get_available_nav_shortcuts(),
        'selected': selected,
    })


@app.route('/api/navigation', methods=['POST'])
def update_navigation_settings():
    payload = request.get_json(silent=True) or {}
    requested_shortcuts = payload.get('selected')
    selected_shortcuts = _coerce_nav_shortcut_ids(requested_shortcuts)
    if not selected_shortcuts:
        selected_shortcuts = list(DEFAULT_NAV_SHORTCUT_IDS)

    settings = _load_settings_dict()
    settings['nav_shortcuts'] = selected_shortcuts
    write_json_file(SETTINGS_FILE, settings)

    return jsonify({
        'available': get_available_nav_shortcuts(),
        'selected': selected_shortcuts,
    }), 200


@app.route('/api/settings', methods=['GET'])
def get_settings():
    settings = _load_settings_dict()

    defaults = {
        "company_name": "FireNotes OMS",
        "default_shipping_zip_code": "",
        "default_email_body": "Dear [contactCompany],\n\nPlease find attached the purchase order [orderID] for your records.\n\nWe appreciate your business!\n\nThank you,\n[yourCompany]",
        "timezone": 'UTC',
        "email_address": "",
        "app_password": "",
        "email_cc": "",
        "email_bcc": "",
        "invoice_business_name": "FireNotes OMS",
        "invoice_business_details": "123 Harbor Way\nPortland, OR 97203\nhello@firenotes.com",
        "invoice_brand_color": "#f97316",
        "invoice_logo_data_url": "",
        "invoice_footer": "",
        "nav_shortcuts": DEFAULT_NAV_SHORTCUT_IDS,
    }

    updated = False
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
            updated = True

    if updated:
        write_json_file(SETTINGS_FILE, settings)

    return jsonify(settings)


def render_with_navigation(template_name: str, active_nav: Optional[str] = None, **context):
    settings = _load_settings_dict()
    context.setdefault('nav_links', get_navigation_shortcuts(settings=settings))
    context.setdefault('active_nav', active_nav)
    return render_template(template_name, **context)

@app.route('/api/settings', methods=['POST'])
def update_settings():
    new_settings_payload = request.json
    if not new_settings_payload:
        return jsonify({"message": "Request must be JSON"}), 400
    
    existing_settings = _load_settings_dict()

    existing_settings['company_name'] = new_settings_payload.get('company_name', existing_settings.get('company_name'))
    existing_settings['default_shipping_zip_code'] = new_settings_payload.get('default_shipping_zip_code', existing_settings.get('default_shipping_zip_code'))
    existing_settings['default_email_body'] = new_settings_payload.get('default_email_body', existing_settings.get('default_email_body'))

    for key in ('invoice_business_name', 'invoice_business_details', 'invoice_brand_color', 'invoice_footer'):
        if key in new_settings_payload:
            existing_settings[key] = new_settings_payload.get(key, existing_settings.get(key))

    write_json_file(SETTINGS_FILE, existing_settings)
    return jsonify({"message": "Settings updated."}), 200

@app.route('/api/settings/timezone', methods=['POST'])
def update_timezone_settings():
    payload = request.json
    if not payload or 'timezone' not in payload:
        return jsonify({"message": "Invalid request"}), 400

    settings = _load_settings_dict()
    settings['timezone'] = payload['timezone']
    write_json_file(SETTINGS_FILE, settings)

    return jsonify({"message": "Timezone updated successfully"}), 200

@app.route('/api/settings/email', methods=['POST'])
def update_email_settings():
    email_settings_payload = request.json
    if not email_settings_payload:
        return jsonify({"message": "Request must be JSON"}), 400

    email_address = email_settings_payload.get('email_address')
    app_password = email_settings_payload.get('app_password')
    email_cc = email_settings_payload.get('email_cc', '')
    email_bcc = email_settings_payload.get('email_bcc', '')

    if not email_address or not app_password:
        return jsonify({"message": "Email address and App Password are required."}), 400

    existing_settings = _load_settings_dict()

    existing_settings['email_address'] = email_address
    existing_settings['app_password'] = app_password
    existing_settings['email_cc'] = email_cc
    existing_settings['email_bcc'] = email_bcc

    write_json_file(SETTINGS_FILE, existing_settings)

    return jsonify({"message": "Email settings updated successfully."}), 200


@app.route('/api/settings/invoice', methods=['POST'])
def update_invoice_settings():
    invoice_payload = request.json
    if invoice_payload is None:
        return jsonify({"message": "Request must be JSON"}), 400

    existing_settings = _load_settings_dict()

    for key in ('invoice_business_name', 'invoice_business_details'):
        if key in invoice_payload:
            existing_settings[key] = invoice_payload.get(key) or ""

    if 'invoice_brand_color' in invoice_payload:
        incoming_color = (invoice_payload.get('invoice_brand_color') or '').strip()
        if not re.fullmatch(r'#([0-9a-fA-F]{6})', incoming_color):
            incoming_color = existing_settings.get('invoice_brand_color', '#f97316') or '#f97316'
        existing_settings['invoice_brand_color'] = incoming_color or '#f97316'

    if 'invoice_logo_data_url' in invoice_payload:
        existing_settings['invoice_logo_data_url'] = invoice_payload.get('invoice_logo_data_url') or ""

    if 'invoice_footer' in invoice_payload:
        footer_value = invoice_payload.get('invoice_footer')
        if isinstance(footer_value, str):
            existing_settings['invoice_footer'] = footer_value.strip()
        else:
            existing_settings['invoice_footer'] = ""

    write_json_file(SETTINGS_FILE, existing_settings)
    return jsonify({"message": "Invoice appearance updated.", "settings": existing_settings}), 200


@app.route('/api/system/upgrade', methods=['POST'])
def trigger_system_upgrade():
    """Upgrade the application to the latest master commit and return details."""

    if not app.config.get('TESTING'):
        app.logger.info('Upgrade requested via settings UI')

    payload = request.get_json(silent=True) or {}
    remote = (payload.get('remote') or 'origin').strip() or 'origin'
    branch = (payload.get('branch') or 'master').strip() or 'master'
    skip_dependencies = bool(payload.get('skipDependencies'))
    repository_url = (payload.get('repositoryUrl') or '').strip() or None

    try:
        result = perform_upgrade(
            remote=remote,
            branch=branch,
            install_dependencies=not skip_dependencies,
            repository_url=repository_url,
        )
    except UpgradeError as exc:
        app.logger.warning('Upgrade failed: %s', exc)
        return (
            jsonify({'status': 'error', 'message': str(exc)}),
            400,
        )
    except Exception:  # pragma: no cover - defensive
        app.logger.exception('Unexpected error during upgrade')
        return (
            jsonify(
                {
                    'status': 'error',
                    'message': 'An unexpected error occurred while upgrading.',
                }
            ),
            500,
        )

    app.logger.info('Upgrade succeeded; scheduling application restart')
    _schedule_post_upgrade_restart()

    return (
        jsonify(
            {
                'status': 'ok',
                'backupPath': str(result.backup_path),
                'previousRevision': result.previous_revision,
                'currentRevision': result.current_revision,
                'dependenciesInstalled': not skip_dependencies,
                'restartScheduled': True,
            }
        ),
        200,
    )


@app.route('/api/passwords', methods=['GET', 'POST'])
def password_entries_collection():
    if request.method == 'GET':
        return jsonify(read_password_entries())

    payload = request.json
    if payload is None:
        return jsonify({"message": "Request must be JSON"}), 400

    service = (payload.get('service') or '').strip()
    username = (payload.get('username') or '').strip()
    password_value = payload.get('password', '')
    notes = payload.get('notes', '')

    if not service:
        return jsonify({"message": "Service name is required."}), 400

    entries = read_password_entries()
    entry_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat() + 'Z'
    new_entry = {
        "id": entry_id,
        "service": service,
        "username": username,
        "password": password_value,
        "notes": notes,
        "updatedAt": created_at,
    }
    entries.append(new_entry)
    write_password_entries(entries)
    return jsonify(new_entry), 201


@app.route('/api/passwords/<entry_id>', methods=['PUT', 'DELETE'])
def password_entry_detail(entry_id):
    entries = read_password_entries()
    index = next((i for i, entry in enumerate(entries) if entry.get('id') == entry_id), None)
    if index is None:
        return jsonify({"message": "Password entry not found."}), 404

    if request.method == 'DELETE':
        removed = entries.pop(index)
        write_password_entries(entries)
        return jsonify({"message": "Deleted.", "entry": removed})

    payload = request.json
    if payload is None:
        return jsonify({"message": "Request must be JSON"}), 400

    entry = entries[index]
    if 'service' in payload:
        entry['service'] = (payload.get('service') or '').strip()
    if 'username' in payload:
        entry['username'] = (payload.get('username') or '').strip()
    if 'password' in payload:
        entry['password'] = payload.get('password', '')
    if 'notes' in payload:
        entry['notes'] = payload.get('notes', '')
    entry['updatedAt'] = datetime.utcnow().isoformat() + 'Z'

    entries[index] = entry
    write_password_entries(entries)
    return jsonify(entry)


@app.route('/api/firenotes/notes', methods=['GET', 'POST', 'PATCH', 'DELETE'])
@app.route('/api/firecoast/notes', methods=['GET', 'POST', 'PATCH', 'DELETE'])
def api_firenotes_notes():
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            query = request.args.get('q') or request.args.get('query')
            notes = _list_notes(conn, query, limit=200)
            return jsonify({'notes': notes})
        payload = request.get_json(force=True, silent=True) or {}
        if request.method == 'POST':
            title = payload.get('title') or payload.get('name')
            note = _create_note(conn, title or '')
            conn.commit()
            return jsonify({'note': note}), 201
        if request.method == 'DELETE':
            note_id = (
                payload.get('id')
                or payload.get('note_id')
                or payload.get('noteId')
                or request.args.get('id')
                or request.args.get('note_id')
                or request.args.get('noteId')
                or ''
            ).strip()
            if not note_id:
                return jsonify({'message': 'note_id is required.'}), 400
            note = _get_note(conn, note_id)
            if not note:
                return jsonify({'message': 'Note not found.'}), 404
            _delete_note(conn, note_id)
            conn.commit()
            return jsonify({'message': 'Note deleted.'})
        note_id = (payload.get('id') or payload.get('note_id') or payload.get('noteId') or '').strip()
        if not note_id:
            return jsonify({'message': 'note_id is required.'}), 400
        note = _get_note(conn, note_id)
        if not note:
            return jsonify({'message': 'Note not found.'}), 404
        title = payload.get('title')
        if title is not None:
            normalized_title = _normalize_note_title(title)
            conn.execute(
                "UPDATE firecoast_notes SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (normalized_title, note_id),
            )
            _upsert_note_handle(conn, note_id, normalized_title)
            note = _get_note(conn, note_id)
        conn.commit()
        return jsonify({'note': note})
    except Exception as exc:
        conn.rollback()
        app.logger.exception("Failed to process FireNotes notes request: %s", exc)
        return jsonify({'message': 'Unable to process notes request.'}), 500
    finally:
        conn.close()


@app.route('/api/firenotes/chat', methods=['GET', 'POST'])
@app.route('/api/firecoast/chat', methods=['GET', 'POST'])
def api_firenotes_chat():
    conn = get_db_connection()
    try:
        if request.method == 'GET':
            note_id = (request.args.get('noteId') or request.args.get('note_id') or '').strip()
            if not note_id:
                return jsonify({'message': 'noteId is required.'}), 400
            limit_param = request.args.get('limit') or request.args.get('pageSize')
            try:
                limit_value = int(limit_param) if limit_param is not None else 100
            except ValueError:
                limit_value = 100
            messages = _list_chat_messages(conn, note_id, limit_value)
            note = _get_note(conn, note_id)
            return jsonify({'messages': messages, 'note': note})

        attachments: List[Dict[str, Any]] = []
        author = 'user'
        note_id = ''
        content = ''
        reply_to_payload: Optional[Dict[str, Any]] = None
        if request.content_type and 'multipart/form-data' in request.content_type:
            note_id = (request.form.get('note_id') or request.form.get('noteId') or '').strip()
            content = (request.form.get('content') or '').strip()
            author = (request.form.get('author') or 'user').strip().lower() or 'user'
            attachments = _save_note_attachments(request.files.getlist('attachments'))
            raw_reply_to = (request.form.get('reply_to') or request.form.get('replyTo') or '').strip()
            if raw_reply_to:
                try:
                    reply_to_payload = json.loads(raw_reply_to)
                except json.JSONDecodeError:
                    reply_to_payload = None
        else:
            payload = request.get_json(force=True, silent=True) or {}
            note_id = (payload.get('note_id') or payload.get('noteId') or '').strip()
            content = (payload.get('content') or '').strip()
            author = (payload.get('author') or 'user').strip().lower() or 'user'
            candidate_reply = payload.get('reply_to') or payload.get('replyTo')
            if isinstance(candidate_reply, dict):
                reply_to_payload = candidate_reply
        if not note_id:
            return jsonify({'message': 'note_id is required.'}), 400
        note = _get_note(conn, note_id)
        if not note:
            return jsonify({'message': 'Note not found.'}), 404
        if not content and not attachments:
            return jsonify({'message': 'Add a note or attachment before sending.'}), 400
        reply_reference = None
        if reply_to_payload:
            reply_id = (
                reply_to_payload.get('id')
                or reply_to_payload.get('message_id')
                or reply_to_payload.get('messageId')
                or ''
            )
            reply_reference = _build_message_reference(conn, str(reply_id))
        metadata = {'reply_to': reply_reference} if reply_reference else None
        stored = _store_chat_message(
            conn,
            note_id,
            author,
            content,
            metadata=metadata,
            attachments=attachments or None,
        )
        responses: List[Dict[str, Any]] = []
        clear_result: Optional[Dict[str, Any]] = None
        if author == 'user':
            try:
                lowered = content.lower()
                if lowered.startswith('.clear'):
                    clear_result = _handle_clear_command(conn, stored)
                else:
                    responses = _handle_chat_message(conn, stored)
            except (ValueError, RecordValidationError) as exc:
                error_message = _store_chat_message(
                    conn,
                    note_id,
                    'assistant',
                    f"Something went wrong: {exc}",
                    metadata={'action': 'error', 'reason': str(exc)},
                )
                responses = [error_message]
        deleted_ids: Set[str] = set()
        if clear_result and clear_result.get('deleted_message_ids'):
            deleted_ids = {str(value) for value in clear_result['deleted_message_ids'] if value}
        conn.commit()
        refreshed_note = _get_note(conn, note_id)
        messages = [stored] + responses
        if deleted_ids:
            messages = [msg for msg in messages if str(msg.get('id')) not in deleted_ids]
        payload: Dict[str, Any] = {'messages': messages, 'note': refreshed_note}
        if clear_result:
            payload['clear'] = clear_result
        return jsonify(payload)
    except Exception as exc:
        conn.rollback()
        app.logger.exception("Failed to process FireNotes chat request: %s", exc)
        return jsonify({'message': 'Unable to process chat request.'}), 500
    finally:
        conn.close()


@app.route('/api/firenotes/chat/reactions', methods=['POST'])
@app.route('/api/firecoast/chat/reactions', methods=['POST'])
def api_firenotes_chat_reactions():
    payload = request.get_json(force=True, silent=True) or {}
    message_id = (payload.get('message_id') or payload.get('messageId') or '').strip()
    emoji = (payload.get('emoji') or '').strip()
    if not message_id or not emoji:
        return jsonify({'message': 'message_id and emoji are required.'}), 400
    conn = get_db_connection()
    try:
        updated, action = _toggle_chat_reaction(conn, message_id, emoji, DEFAULT_CHAT_REACTOR)
        conn.commit()
        return jsonify({'message': updated, 'action': action})
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    except Exception as exc:
        conn.rollback()
        app.logger.exception("Failed to update FireNotes chat reaction: %s", exc)
        return jsonify({'message': 'Unable to update reaction.'}), 500
    finally:
        conn.close()


@app.route('/api/firenotes/chat/messages/<message_id>', methods=['PATCH', 'DELETE'])
@app.route('/api/firecoast/chat/messages/<message_id>', methods=['PATCH', 'DELETE'])
def api_firenotes_chat_message(message_id: str):
    normalized_id = (message_id or '').strip()
    if not normalized_id:
        return jsonify({'message': 'message_id is required.'}), 400
    conn = get_db_connection()
    try:
        if request.method == 'DELETE':
            deleted = _delete_chat_message(conn, normalized_id)
            note = _get_note(conn, deleted.get('note_id')) if deleted.get('note_id') else None
            conn.commit()
            response: Dict[str, Any] = {'message': deleted}
            if note:
                response['note'] = note
            return jsonify(response)
        payload = request.get_json(force=True, silent=True) or {}
        content = payload.get('content')
        updated = _edit_chat_message(conn, normalized_id, content)
        note_id = updated.get('note_id')
        note = _get_note(conn, note_id) if note_id else None
        conn.commit()
        response: Dict[str, Any] = {'message': updated}
        if note:
            response['note'] = note
        return jsonify(response)
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 404
    except Exception as exc:
        conn.rollback()
        app.logger.exception("Failed to update FireNotes chat message: %s", exc)
        return jsonify({'message': 'Unable to update message.'}), 500
    finally:
        conn.close()


@app.route('/api/firenotes/chat/forward', methods=['POST'])
@app.route('/api/firecoast/chat/forward', methods=['POST'])
def api_firenotes_chat_forward():
    payload = request.get_json(force=True, silent=True) or {}
    message_id = (payload.get('message_id') or payload.get('messageId') or '').strip()
    target_note_id = (payload.get('target_note_id') or payload.get('targetNoteId') or '').strip()
    conn = get_db_connection()
    try:
        forwarded = _forward_chat_message(conn, message_id, target_note_id)
        note = _get_note(conn, forwarded.get('note_id')) if forwarded.get('note_id') else None
        conn.commit()
        response: Dict[str, Any] = {'message': forwarded}
        if note:
            response['note'] = note
        return jsonify(response), 201
    except ValueError as exc:
        conn.rollback()
        return jsonify({'message': str(exc)}), 400
    except Exception as exc:
        conn.rollback()
        app.logger.exception("Failed to forward FireNotes chat message: %s", exc)
        return jsonify({'message': 'Unable to forward message.'}), 500
    finally:
        conn.close()


@app.route('/manage/customers')
def manage_customers_page():
    return render_with_navigation('manage_customers.html', active_nav='settings')


@app.route('/manage/items')
def manage_items_page():
    return render_with_navigation('manage_items.html', active_nav='settings')


@app.route('/manage/packages')
def manage_packages_page():
    return render_with_navigation('manage_packages.html', active_nav='settings')

@app.route('/firenotes')
@app.route('/firecoast')
def firenotes_chat_page():
    return render_with_navigation('firenotes_chat.html', active_nav='firenotes')

@app.route('/settings')
def settings_page():
    timezones = pytz.all_timezones
    settings = _load_settings_dict()
    selected_timezone = settings.get('timezone', 'UTC')
    return render_with_navigation('settings.html', timezones=timezones, selected_timezone=selected_timezone, active_nav='settings')

@app.route('/dashboard')
def dashboard_page():
    return render_with_navigation('admin.html')


@app.route('/admin')
def legacy_admin_redirect():
    return redirect(url_for('dashboard_page'))

@app.route('/analytics')
def analytics_page():
    return render_with_navigation('analytics.html', active_nav='analytics')

@app.route('/contacts')
def contacts_page():
    return render_with_navigation('contacts.html', active_nav='contacts')

@app.route('/orders')
def orders_page():
    return render_with_navigation('orders.html', active_nav='orders')


@app.route('/passwords')
def passwords_page():
    return render_with_navigation('passwords.html', active_nav='passwords')


@app.route('/reminders')
def reminders_page():
    return render_with_navigation('reminders.html', active_nav='reminders')


@app.route('/tasks')
def tasks_page():
    return render_with_navigation('tasks.html', active_nav='tasks')


@app.route('/calendar')
def calendar_page():
    return render_with_navigation('calendar.html', active_nav='calendar')

@app.route('/api/export-data', methods=['GET'])
def export_data():
    """Create a zip archive of the application's data directory."""
    try:
        archive_path = create_backup_archive()
    except BackupError as exc:
        app.logger.error("Backup failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - defensive logging
        app.logger.error(f"Error creating data backup: {exc}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Failed to create backup."}), 500

    response = send_file(
        archive_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=archive_path.name,
    )

    @response.call_on_close
    def cleanup():
        try:
            archive_dir = archive_path.parent
            if archive_path.exists():
                archive_path.unlink()
            if archive_dir.is_dir() and not any(archive_dir.iterdir()):
                archive_dir.rmdir()
        except Exception as cleanup_exc:  # pragma: no cover - defensive logging
            app.logger.error("Error cleaning up backup file: %s", cleanup_exc)

    return response


@app.route('/api/import-data', methods=['POST'])
def import_data():
    """Restore the data directory from a zip archive."""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400

    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.zip'):
        return jsonify({"status": "error", "message": "Invalid file. Please upload a .zip backup file."}), 400

    try:
        file.stream.seek(0)
        restore_backup_from_stream(file.stream)
        reset_record_service()

        global _db_bootstrapped
        _db_bootstrapped = False

        init_db()

        return jsonify({"status": "success", "message": "Data restored successfully. Your data is ready to use."}), 200

    except BackupError as exc:
        app.logger.error("Import rejected: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        app.logger.error(f"Error restoring data: {exc}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "An error occurred during the restore process. The original data has been restored."}), 500

@app.route('/order-logs/<string:order_id>')
def order_logs_page(order_id):
    return render_with_navigation('order_logs.html', order_id=order_id, active_nav='orders')

@app.route('/order/<string:order_id>')
def view_order_page(order_id):
    return render_with_navigation('view_order.html', order_id=order_id, active_nav='orders')

@app.route('/favicon.ico')
def favicon(): return send_from_directory(os.path.join(app.root_path, ''),'favicon.ico',mimetype='image/vnd.microsoft.icon')
@app.route('/data/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/assets/<path:filename>')
def serve_assets(filename): return send_from_directory(os.path.join(app.root_path,'assets'),filename)
@app.route('/')
def home():
    return redirect(url_for('dashboard_page'))

@app.route('/shutdown', methods=['POST'])
def shutdown(): Timer(0.1,lambda:os._exit(0)).start(); return "Shutdown initiated.",200

def open_browser():
    webbrowser.open_new("http://127.0.0.1:5002/")

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def main():
    PORT = 5002
    if is_port_in_use(PORT):
        print(f"Port {PORT} is already in use. Opening browser to existing instance.")
        open_browser()
        sys.exit(0)
    else:
        print(f"Port {PORT} is free. Starting new server.")
        Timer(1, open_browser).start()
        app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == '__main__':
    init_db()
    main()
