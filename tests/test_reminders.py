import json
import pathlib
import sqlite3
import sys
import unittest
from datetime import timedelta

from dateutil.parser import isoparse

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import _normalize_reminder_payload
from services.records import RecordRegistry, RecordService


class ReminderNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE record_handles (
                handle TEXT PRIMARY KEY,
                entity_type TEXT,
                entity_id TEXT,
                display_name TEXT,
                search_blob TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_generates_handle_and_normalizes_due_date(self):
        payload = {
            'title': 'Send invoice',
            'due_at': '2024-05-01T09:30:00-04:00',
            'due_has_time': True,
            'timezone': 'America/New_York',
            'notes': 'Ping @clientalpha about payment.',
        }
        normalized = _normalize_reminder_payload(self.conn, payload)
        self.assertEqual(normalized['title'], 'Send invoice')
        due_dt = isoparse(normalized['due_at'])
        self.assertEqual(due_dt.tzinfo.utcoffset(due_dt), timedelta(0))
        self.assertTrue(normalized['due_has_time'])
        self.assertTrue(normalized['handle'].startswith('send-invoice-'))
        self.assertFalse(normalized['completed'])
        self.assertIsNone(normalized['completed_at'])

    def test_completed_sets_timestamp_when_missing(self):
        payload = {
            'title': 'Archive order',
            'completed': True,
        }
        normalized = _normalize_reminder_payload(self.conn, payload)
        self.assertTrue(normalized['completed'])
        self.assertIsNotNone(normalized['completed_at'])

    def test_ensures_unique_handle(self):
        self.conn.execute(
            "INSERT INTO record_handles (handle, entity_type, entity_id, display_name, search_blob) VALUES (?, ?, ?, ?, ?)",
            ('send-invoice-20240501', 'reminder', 'existing', 'Send invoice', 'send invoice'),
        )
        payload = {
            'title': 'Send invoice',
            'due_at': '2024-05-01T15:00:00Z',
            'due_has_time': True,
            'timezone': 'UTC',
        }
        normalized = _normalize_reminder_payload(self.conn, payload)
        self.assertNotEqual(normalized['handle'], 'send-invoice-20240501')
        self.assertTrue(normalized['handle'].startswith('send-invoice-20240501'))

    def test_infers_due_has_time_when_flag_missing(self):
        payload = {
            'title': 'Schedule briefing',
            'due_at': '2024-07-04T18:45:00-04:00',
            'timezone': 'America/New_York',
        }
        normalized = _normalize_reminder_payload(self.conn, payload)
        self.assertTrue(normalized['due_has_time'])

    def test_infers_all_day_when_flag_missing(self):
        payload = {
            'title': 'Production kickoff',
            'due_at': '2024-07-05',
            'timezone': 'America/Los_Angeles',
        }
        normalized = _normalize_reminder_payload(self.conn, payload)
        self.assertFalse(normalized['due_has_time'])


class ReminderRecordServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE record_schemas (
                entity_type TEXT PRIMARY KEY,
                schema_json TEXT NOT NULL,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE records (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (entity_type, entity_id)
            );

            CREATE TABLE record_handles (
                handle TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                display_name TEXT,
                search_blob TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE record_mentions (
                mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentioned_handle TEXT NOT NULL,
                mentioned_entity_type TEXT NOT NULL,
                mentioned_entity_id TEXT NOT NULL,
                context_entity_type TEXT NOT NULL,
                context_entity_id TEXT NOT NULL,
                snippet TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE record_activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT,
                payload TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.registry = RecordRegistry()
        self.service = RecordService(self.registry)
        self.service.bootstrap(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_reminder_schema_registered(self):
        self.assertTrue(self.service.registry.has('reminder'))

    def test_create_reminder_registers_handle_and_mentions(self):
        self.service.register_handle(
            self.conn,
            'contact',
            'contact-1',
            'clientalpha',
            display_name='Client Alpha',
            search_blob='client alpha',
        )
        payload = {
            'title': 'Confirm delivery window',
            'due_at': '2024-06-01T12:00:00Z',
            'due_has_time': True,
            'timezone': 'UTC',
            'notes': 'Coordinate final timing with @clientalpha',
        }
        normalized = _normalize_reminder_payload(self.conn, payload)
        created = self.service.create_record(self.conn, 'reminder', normalized, actor='ops')
        self.assertEqual(created['data']['handle'], normalized['handle'])
        handle_rows = list(self.conn.execute("SELECT handle, entity_type FROM record_handles"))
        handles = {(row['handle'], row['entity_type']) for row in handle_rows}
        self.assertIn((normalized['handle'], 'reminder'), handles)
        metadata_row = self.conn.execute(
            "SELECT metadata_json FROM record_handles WHERE handle = ?",
            (normalized['handle'],),
        ).fetchone()
        self.assertIsNotNone(metadata_row)
        metadata = json.loads(metadata_row['metadata_json'])
        self.assertEqual(metadata['dueAt'], normalized['due_at'])
        self.assertTrue(metadata['dueHasTime'])
        self.assertEqual(metadata['timezone'], 'UTC')
        self.assertFalse(metadata['completed'])
        self.assertIn('notesPreview', metadata)
        mention_rows = list(
            self.conn.execute(
                "SELECT mentioned_handle, context_entity_type FROM record_mentions WHERE context_entity_type = 'reminder'"
            )
        )
        self.assertEqual(len(mention_rows), 1)
        self.assertEqual(mention_rows[0]['mentioned_handle'], 'clientalpha')
        self.assertEqual(mention_rows[0]['context_entity_type'], 'reminder')


if __name__ == '__main__':
    unittest.main()
