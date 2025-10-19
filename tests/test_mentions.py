import os
import sqlite3
import sys
import unittest
from textwrap import dedent

CURRENT_DIR = os.path.dirname(__file__)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from services.records import (
    FieldDefinition,
    RecordRegistry,
    RecordSchema,
    RecordService,
    RecordValidationError,
    extract_mentions,
    sync_record_mentions,
)


class MentionExtractionTests(unittest.TestCase):
    def test_ignores_email_addresses(self):
        text = "Please email support@example.com for assistance."
        self.assertEqual(extract_mentions(text), [])

    def test_allows_mentions_with_whitespace_prefix(self):
        text = "Ping @Account.Manager then follow up via billing@example.com."
        self.assertEqual(extract_mentions(text), ['account.manager'])


class RecordServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.registry = RecordRegistry()
        self.service = RecordService(self.registry)
        self._create_tables()
        self._register_note_schema()

    def tearDown(self):
        self.conn.close()

    def _create_tables(self):
        schema_sql = dedent(
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX idx_record_handles_entity ON record_handles(entity_type, entity_id);

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

            CREATE INDEX idx_record_mentions_target ON record_mentions(mentioned_entity_type, mentioned_entity_id);
            CREATE INDEX idx_record_mentions_context ON record_mentions(context_entity_type, context_entity_id);

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
        self.conn.executescript(schema_sql)

    def _register_note_schema(self):
        note_schema = RecordSchema(
            entity_type='note',
            fields={
                'title': FieldDefinition('title', field_type='string', required=True),
                'body': FieldDefinition('body', field_type='text', required=True, mention=True),
                'handle': FieldDefinition('handle', field_type='string', required=True),
                'author': FieldDefinition('author', field_type='string'),
            },
            handle_field='handle',
            display_field='title',
            description='Test note schema',
        )
        self.service.register_schema(self.conn, note_schema)

    def test_registers_mentions_and_activity_for_created_record(self):
        self.service.register_handle(
            self.conn,
            'contact',
            'contact-1',
            'clientalpha',
            display_name='Client Alpha',
            search_blob='client alpha',
        )
        payload = {
            'title': 'Daily summary',
            'body': 'Meeting notes for @clientalpha',
            'handle': 'note-alpha',
            'author': 'ops',
        }
        created = self.service.create_record(self.conn, 'note', payload, actor='tester')
        self.assertEqual(created['data']['handle'], 'note-alpha')

        handle_rows = list(self.conn.execute("SELECT handle, entity_type FROM record_handles"))
        handles = {(row['handle'], row['entity_type']) for row in handle_rows}
        self.assertIn(('clientalpha', 'contact'), handles)
        self.assertIn(('note-alpha', 'note'), handles)

        mention_rows = list(self.conn.execute("SELECT mentioned_entity_type, context_entity_type FROM record_mentions"))
        self.assertEqual(len(mention_rows), 1)
        self.assertEqual(mention_rows[0]['mentioned_entity_type'], 'contact')
        self.assertEqual(mention_rows[0]['context_entity_type'], 'note')

        activity = self.service.fetch_activity(self.conn, 'note', created['id'], limit=5)
        self.assertGreaterEqual(len(activity), 1)
        self.assertEqual(activity[0]['action'], 'created')

    def test_sync_record_mentions_supports_cross_entity_context(self):
        self.service.register_handle(
            self.conn,
            'contact',
            'primary-contact',
            'primary',
            display_name='Primary Contact',
            search_blob='primary contact',
        )
        self.service.register_handle(
            self.conn,
            'note',
            'note-1',
            'noteone',
            display_name='Note One',
            search_blob='note one',
        )
        sync_record_mentions(
            self.conn,
            ['primary', 'noteone'],
            'order_log',
            'log-42',
            'Updated order for @primary and referenced @noteone',
        )
        rows = list(self.conn.execute(
            "SELECT mentioned_handle, context_entity_type FROM record_mentions ORDER BY mentioned_handle"
        ))
        handles = [row['mentioned_handle'] for row in rows]
        self.assertEqual(handles, ['noteone', 'primary'])
        self.assertTrue(all(row['context_entity_type'] == 'order_log' for row in rows))

    def test_validation_errors_are_raised_for_missing_fields(self):
        with self.assertRaises(RecordValidationError):
            self.service.create_record(self.conn, 'note', {'body': 'Missing handle'}, actor='tester')


if __name__ == '__main__':
    unittest.main()
