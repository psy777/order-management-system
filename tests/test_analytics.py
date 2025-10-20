import json
import pathlib
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.analytics import get_analytics_engine


class AnalyticsEngineTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                order_date TEXT,
                status TEXT,
                total_amount REAL,
                contact_id TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE order_line_items (
                order_id TEXT,
                catalog_item_id TEXT,
                name TEXT,
                quantity INTEGER,
                price_per_unit_cents INTEGER,
                package_id TEXT
            );

            CREATE TABLE contacts (
                id TEXT PRIMARY KEY,
                company_name TEXT,
                contact_name TEXT,
                email TEXT
            );

            CREATE TABLE records (
                entity_type TEXT,
                entity_id TEXT,
                data TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE record_mentions (
                mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mentioned_entity_type TEXT,
                mentioned_entity_id TEXT,
                context_entity_type TEXT,
                context_entity_id TEXT
            );

            CREATE TABLE record_activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT,
                entity_id TEXT,
                action TEXT,
                actor TEXT,
                payload TEXT,
                created_at TEXT
            );
            """
        )

        self.conn.execute(
            "INSERT INTO contacts (id, company_name, contact_name, email) VALUES (?, ?, ?, ?)",
            ("CUST-001", "Acme Co", "Avery Ops", "ops@acme.test"),
        )
        self.conn.execute(
            "INSERT INTO contacts (id, company_name, contact_name, email) VALUES (?, ?, ?, ?)",
            ("CUST-002", "Globex", "Jordan Supply", "supply@globex.test"),
        )

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        self.conn.execute(
            "INSERT INTO orders (order_id, order_date, status, total_amount, contact_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("PO-1001", (today - timedelta(days=10)).isoformat(), "Completed", 1250.0, "CUST-001", (today - timedelta(days=12)).isoformat(), (today - timedelta(days=9)).isoformat()),
        )
        self.conn.execute(
            "INSERT INTO orders (order_id, order_date, status, total_amount, contact_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("PO-1002", (today - timedelta(days=2)).isoformat(), "In Production", 750.0, "CUST-002", (today - timedelta(days=3)).isoformat(), (today - timedelta(days=1)).isoformat()),
        )

        self.conn.executemany(
            "INSERT INTO order_line_items (order_id, catalog_item_id, name, quantity, price_per_unit_cents, package_id) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("PO-1001", "SKU-001", "Widget", 5, 15000, None),
                ("PO-1001", "SKU-002", "Gadget", 10, 5000, "PKG-01"),
                ("PO-1002", "SKU-001", "Widget", 3, 15000, None),
            ],
        )

        reminder_payloads = [
            {
                "title": "Follow up invoice",
                "handle": "invoice-followup",
                "notes": "Send final invoice email",
                "due_at": (today - timedelta(days=5)).isoformat() + "Z",
                "due_has_time": False,
                "timezone": "UTC",
                "completed": False,
            },
            {
                "title": "Confirm shipment",
                "handle": "confirm-shipment",
                "notes": "Confirm carrier pickup",
                "due_at": (today + timedelta(days=3)).isoformat() + "Z",
                "due_has_time": False,
                "timezone": "UTC",
                "completed": False,
            },
            {
                "title": "Archive drawings",
                "handle": "archive-drawings",
                "notes": "Move CAD files to archive",
                "due_at": (today - timedelta(days=1)).isoformat() + "Z",
                "due_has_time": False,
                "timezone": "UTC",
                "completed": True,
                "completed_at": today.isoformat() + "Z",
            },
        ]
        for index, payload in enumerate(reminder_payloads, start=1):
            self.conn.execute(
                "INSERT INTO records (entity_type, entity_id, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "reminder",
                    f"rem-{index}",
                    json.dumps({**payload, "id": f"rem-{index}"}),
                    today.isoformat() + "Z",
                    today.isoformat() + "Z",
                ),
            )

        self.conn.execute(
            "INSERT INTO record_mentions (mentioned_entity_type, mentioned_entity_id, context_entity_type, context_entity_id) VALUES (?, ?, ?, ?)",
            ("reminder", "rem-1", "reminder", "rem-1"),
        )
        self.conn.execute(
            "INSERT INTO record_activity_logs (entity_type, entity_id, action, actor, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("reminder", "rem-1", "created", "ops", json.dumps({}), today.isoformat() + "Z"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_orders_overview_report(self):
        engine = get_analytics_engine()
        result = engine.run_report(
            self.conn,
            'orders_overview',
            {'start_date': '2024-04-01'},
            timezone_name='UTC',
        )
        self.assertEqual(result['id'], 'orders_overview')
        summary = {entry['id']: entry for entry in result['summary']}
        self.assertIn('total_revenue', summary)
        self.assertAlmostEqual(summary['total_revenue']['value'], 2000.0, places=2)
        self.assertIn('charts', result)
        top_customers = result['tables'][0]['rows']
        self.assertGreaterEqual(len(top_customers), 2)
        labels = [row['customer'] for row in top_customers]
        self.assertIn('Acme Co', labels)

    def test_reminder_health_counts(self):
        engine = get_analytics_engine()
        result = engine.run_report(
            self.conn,
            'reminder_health',
            {'days_ahead': 7},
            timezone_name='UTC',
        )
        summary = {entry['id']: entry for entry in result['summary']}
        self.assertEqual(summary['total']['value'], 3)
        self.assertEqual(summary['completed']['value'], 1)
        self.assertEqual(summary['overdue']['value'], 1)
        self.assertEqual(summary['upcoming']['value'], 1)

    def test_dataset_overview_orders(self):
        engine = get_analytics_engine()
        result = engine.run_report(
            self.conn,
            'dataset_overview',
            {'dataset': 'orders', 'sample_size': 1},
            timezone_name='UTC',
        )
        self.assertEqual(result['meta']['dataset'], 'orders')
        self.assertTrue(result['tables'])
        self.assertTrue(result['tables'][0]['rows'])


if __name__ == '__main__':
    unittest.main()
