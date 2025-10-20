import sqlite3

from database import _ensure_record_mentions_schema


def test_ensure_record_mentions_schema_backfills_missing_columns():
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE record_mentions (
            mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mentioned_handle TEXT NOT NULL,
            mentioned_entity_id TEXT NOT NULL,
            context_entity_type TEXT NOT NULL,
            context_entity_id TEXT NOT NULL,
            snippet TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    cursor.execute(
        """
        INSERT INTO record_mentions (
            mentioned_handle,
            mentioned_entity_id,
            context_entity_type,
            context_entity_id,
            snippet
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        ("@acustomer", "1", "contact_profile_note", "note:1", "hello"),
    )

    _ensure_record_mentions_schema(cursor)

    cursor.execute("PRAGMA table_info(record_mentions)")
    column_names = {row[1] for row in cursor.fetchall()}
    assert "mentioned_entity_type" in column_names

    cursor.execute("SELECT mentioned_entity_type FROM record_mentions")
    assert [row[0] for row in cursor.fetchall()] == ["contact"]

    cursor.execute("PRAGMA index_list(record_mentions)")
    index_names = {row[1] for row in cursor.fetchall()}
    assert "idx_record_mentions_target" in index_names
    assert "idx_record_mentions_context" in index_names

    conn.close()
