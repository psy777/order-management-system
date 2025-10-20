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

from app import _normalize_calendar_event_payload
from services.records import RecordRegistry, RecordService


class CalendarEventNormalizationTests(unittest.TestCase):
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

    def test_generates_handle_and_normalizes_dates(self):
        payload = {
            'title': 'Ops Sync',
            'start_at': '2024-05-01T09:30:00-04:00',
            'notes': 'Follow up with @clientalpha',
            'timezone': 'America/New_York',
        }
        normalized = _normalize_calendar_event_payload(self.conn, payload)
        self.assertEqual(normalized['title'], 'Ops Sync')
        start_dt = isoparse(normalized['start_at'])
        end_dt = isoparse(normalized['end_at'])
        self.assertEqual(start_dt.tzinfo.utcoffset(start_dt), timedelta(0))
        self.assertEqual(end_dt.tzinfo.utcoffset(end_dt), timedelta(0))
        self.assertEqual(normalized['end_at'], normalized['start_at'])
        self.assertTrue(normalized['handle'].startswith('ops-sync-'))
        self.assertFalse(normalized['all_day'])
        self.assertEqual(normalized['timezone'], 'America/New_York')

    def test_ensures_unique_handle(self):
        self.conn.execute(
            "INSERT INTO record_handles (handle, entity_type, entity_id, display_name, search_blob) VALUES (?, ?, ?, ?, ?)",
            ('ops-sync-20240501', 'calendar_event', 'existing', 'Ops Sync', 'ops sync'),
        )
        payload = {
            'title': 'Ops Sync',
            'start_at': '2024-05-01T13:00:00Z',
            'timezone': 'UTC',
        }
        normalized = _normalize_calendar_event_payload(self.conn, payload)
        self.assertNotEqual(normalized['handle'], 'ops-sync-20240501')
        self.assertTrue(normalized['handle'].startswith('ops-sync-20240501'))


class CalendarEventRecordServiceTests(unittest.TestCase):
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

    def test_calendar_schema_registered(self):
        self.assertTrue(self.service.registry.has('calendar_event'))

    def test_create_event_registers_handle_and_mentions(self):
        self.service.register_handle(
            self.conn,
            'contact',
            'contact-1',
            'clientalpha',
            display_name='Client Alpha',
            search_blob='client alpha',
        )
        payload = {
            'title': 'Carrier Review',
            'start_at': '2024-05-01T15:00:00Z',
            'end_at': '2024-05-01T16:00:00Z',
            'timezone': 'UTC',
            'notes': 'Coordinate with @clientalpha on throughput.',
        }
        normalized = _normalize_calendar_event_payload(self.conn, payload)
        created = self.service.create_record(self.conn, 'calendar_event', normalized, actor='ops')
        self.assertEqual(created['data']['handle'], normalized['handle'])

        handle_rows = list(self.conn.execute("SELECT handle, entity_type FROM record_handles"))
        handles = {(row['handle'], row['entity_type']) for row in handle_rows}
        self.assertIn((normalized['handle'], 'calendar_event'), handles)

        metadata_row = self.conn.execute(
            "SELECT metadata_json FROM record_handles WHERE handle = ?",
            (normalized['handle'],),
        ).fetchone()
        self.assertIsNotNone(metadata_row)
        metadata = json.loads(metadata_row['metadata_json'])
        self.assertEqual(metadata['startAt'], normalized['start_at'])
        self.assertEqual(metadata['endAt'], normalized['end_at'])
        self.assertTrue(metadata['allDay'] is False)
        self.assertEqual(metadata['timezone'], 'UTC')
        self.assertIn('notesPreview', metadata)

        mention_rows = list(
            self.conn.execute(
                "SELECT mentioned_handle, context_entity_type FROM record_mentions WHERE context_entity_type = 'calendar_event'"
            )
        )
        self.assertEqual(len(mention_rows), 1)
        self.assertEqual(mention_rows[0]['mentioned_handle'], 'clientalpha')
        self.assertEqual(mention_rows[0]['context_entity_type'], 'calendar_event')

