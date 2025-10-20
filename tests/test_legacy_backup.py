import json
import sqlite3
from zipfile import ZipFile

from services.legacy_backup import build_legacy_backup


def test_build_legacy_backup_from_json_payload(tmp_path):
    payload = {
        "settings": {"timezone": "America/New_York"},
        "orders": [
            {
                "id": "A1",
                "status": "pending",
                "total": 123.45,
                "contact_id": "C1",
                "created_at": "2024-01-01T10:00:00Z",
                "updated_at": "2024-01-01T12:00:00Z",
            }
        ],
        "line_items": [
            {
                "id": "L1",
                "order_id": "A1",
                "item_id": "I1",
                "quantity": 2,
                "price": 12.34,
            }
        ],
        "contacts": [
            {
                "id": "C1",
                "company_name": "Acme Corp",
                "contact_name": "Pat Customer",
                "email": "pat@example.com",
                "phone": "123",
            }
        ],
        "items": [
            {
                "id": "I1",
                "name": "Widget",
                "description": "Legacy widget",
                "price": 12.34,
                "weight_oz": 4.5,
            }
        ],
        "records": {
            "note": [
                {
                    "id": "R1",
                    "title": "Sample",
                    "created_at": "2024-01-02T01:02:03Z",
                    "updated_at": "2024-01-02T04:05:06Z",
                }
            ]
        },
    }

    legacy_file = tmp_path / "legacy.json"
    legacy_file.write_text(json.dumps(payload))

    archive = build_legacy_backup(legacy_file, destination_dir=tmp_path)
    assert archive.exists()

    with ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "settings.json" in names
        assert "orders_manager.db" in names
        assert "records/note.json" in names

        settings_payload = json.loads(zf.read("settings.json").decode("utf-8"))
        assert settings_payload["timezone"] == "America/New_York"

        db_copy = tmp_path / "converted.db"
        db_copy.write_bytes(zf.read("orders_manager.db"))

    connection = sqlite3.connect(db_copy)
    try:
        order_row = connection.execute(
            "SELECT order_id, status, total_cents FROM orders"
        ).fetchone()
        assert order_row == ("A1", "pending", 12345)

        line_item_row = connection.execute(
            "SELECT order_id, item_id, quantity, price_cents FROM order_line_items"
        ).fetchone()
        assert line_item_row == ("A1", "I1", 2, 1234)

        contact_row = connection.execute(
            "SELECT id, company_name FROM contacts"
        ).fetchone()
        assert contact_row == ("C1", "Acme Corp")
    finally:
        connection.close()


def test_build_legacy_backup_prefers_existing_database(tmp_path):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()

    (legacy_dir / "config.json").write_text(json.dumps({"timezone": "UTC"}))

    db_path = legacy_dir / "orders_manager.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO orders (order_id, status) VALUES (?, ?)", ("X1", "done"))
        conn.commit()
    finally:
        conn.close()

    (legacy_dir / "note.txt").write_text("remember me")

    archive = build_legacy_backup(legacy_dir, destination_dir=tmp_path)

    with ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "orders_manager.db" in names
        assert "legacy_assets/note.txt" in names
        report = json.loads(zf.read("legacy_import_report.json"))
        assert report["summary"]["attachments"] == 1
        assert report["summary"]["has_database"] is True

        db_copy = tmp_path / "copied.db"
        db_copy.write_bytes(zf.read("orders_manager.db"))

    conn = sqlite3.connect(db_copy)
    try:
        row = conn.execute("SELECT order_id, status FROM orders").fetchone()
        assert row == ("X1", "done")
    finally:
        conn.close()
