import sqlite3
import json
import logging
import re
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from data_paths import DATA_ROOT, ensure_data_root

ensure_data_root()

DATA_DIR = DATA_ROOT
DATABASE_FILE = DATA_DIR / 'orders_manager.db'


HANDLE_SANITIZE_PATTERN = re.compile(r'[^a-z0-9]+')


def _slugify_handle(source_text: str) -> str:
    base = HANDLE_SANITIZE_PATTERN.sub('-', (source_text or '').lower()).strip('-')
    if not base:
        return 'contact'
    return base.replace('-', '')[:32]


def _generate_unique_handle(cursor: sqlite3.Cursor, preferred_text: str) -> str:
    base = _slugify_handle(preferred_text)
    candidate = base
    suffix = 1
    while True:
        cursor.execute("SELECT 1 FROM contacts WHERE handle = ?", (candidate,))
        if not cursor.fetchone():
            return candidate
        candidate = f"{base}{suffix}"
        suffix += 1


def ensure_contact_handle(cursor: sqlite3.Cursor, contact_id: str, fallback_text: str = "") -> Optional[str]:
    cursor.execute(
        "SELECT handle, company_name, contact_name FROM contacts WHERE id = ?",
        (contact_id,),
    )
    existing = cursor.fetchone()
    if not existing:
        return None
    handle, company_name, contact_name = existing
    if handle:
        return handle
    new_handle = _generate_unique_handle(cursor, contact_name or company_name or fallback_text or 'contact')
    cursor.execute("UPDATE contacts SET handle = ? WHERE id = ?", (new_handle, contact_id))
    return new_handle


def generate_unique_contact_handle(cursor: sqlite3.Cursor, preferred_text: str) -> str:
    """Public helper to generate a unique contact handle."""
    return _generate_unique_handle(cursor, preferred_text)

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    ensure_data_root()
    conn = sqlite3.connect(str(DATABASE_FILE), timeout=30.0, isolation_level='DEFERRED')
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=10000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
    except sqlite3.Error as e:
        logger.warning(f"Could not set PRAGMA settings: {e}")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Ensure the legacy vendors table is renamed to contacts before creating triggers or columns
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in cursor.fetchall()}

    if 'contacts' not in existing_tables and 'vendors' in existing_tables:
        # Drop the legacy trigger so the rename succeeds on older SQLite versions
        cursor.execute("DROP TRIGGER IF EXISTS update_vendors_updated_at")
        cursor.execute("ALTER TABLE vendors RENAME TO contacts")
        existing_tables.add('contacts')

    # Create contacts table if it still does not exist (fresh installs)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id TEXT PRIMARY KEY NOT NULL,
            company_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            billing_address TEXT,
            billing_city TEXT,
            billing_state TEXT,
            billing_zip_code TEXT,
            shipping_address TEXT,
            shipping_city TEXT,
            shipping_state TEXT,
            shipping_zip_code TEXT,
            handle TEXT UNIQUE,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Ensure the contacts table includes the new columns for handle and notes
    cursor.execute("PRAGMA table_info(contacts)")
    contact_columns = {row[1] for row in cursor.fetchall()}
    if 'handle' not in contact_columns:
        cursor.execute("ALTER TABLE contacts ADD COLUMN handle TEXT")
    if 'notes' not in contact_columns:
        cursor.execute("ALTER TABLE contacts ADD COLUMN notes TEXT")

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_handle ON contacts(handle)")
    cursor.execute("CREATE TRIGGER IF NOT EXISTS update_contacts_updated_at AFTER UPDATE ON contacts FOR EACH ROW BEGIN UPDATE contacts SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;")
    # Drop legacy style tables that are no longer used
    cursor.execute("DROP TABLE IF EXISTS item_styles")
    cursor.execute("DROP TABLE IF EXISTS styles")

    # Ensure the items table uses the simplified schema (id, name, description, price, weight)
    cursor.execute("PRAGMA table_info(items)")
    item_columns = [row[1] for row in cursor.fetchall()]
    needs_item_migration = False
    if not item_columns:
        needs_item_migration = True
    else:
        if 'id' not in item_columns:
            needs_item_migration = True
        if 'description' not in item_columns:
            needs_item_migration = True
        if 'type' in item_columns or 'item_code' in item_columns:
            needs_item_migration = True

    if needs_item_migration:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS items_migrated (
                id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                price_cents INTEGER NOT NULL,
                weight_oz REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        if item_columns:
            selectable_columns = set(item_columns)
            if 'item_code' in selectable_columns:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO items_migrated (id, name, description, price_cents, weight_oz, created_at, updated_at)
                    SELECT item_code, name, '' AS description, price_cents, weight_oz,
                           COALESCE(created_at, CURRENT_TIMESTAMP),
                           COALESCE(updated_at, CURRENT_TIMESTAMP)
                    FROM items
                    """
                )
            else:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO items_migrated (id, name, description, price_cents, weight_oz, created_at, updated_at)
                    SELECT id, name, description, price_cents, weight_oz,
                           COALESCE(created_at, CURRENT_TIMESTAMP),
                           COALESCE(updated_at, CURRENT_TIMESTAMP)
                    FROM items
                    """
                )

        cursor.execute("DROP TABLE IF EXISTS items")
        cursor.execute("ALTER TABLE items_migrated RENAME TO items")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL,
            weight_oz REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS update_items_updated_at
        AFTER UPDATE ON items
        FOR EACH ROW
        BEGIN
            UPDATE items SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
        END;
        """
    )

    cursor.execute("PRAGMA table_info(packages)")
    package_columns = [row[1] for row in cursor.fetchall()]
    needs_package_migration = False
    if package_columns and ('type' in package_columns or 'style' in package_columns):
        needs_package_migration = True

    if needs_package_migration:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS packages_migrated (
                package_id INTEGER PRIMARY KEY NOT NULL,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO packages_migrated (package_id, name, created_at, updated_at)
            SELECT
                package_id,
                name,
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM packages
            """
        )
        cursor.execute("DROP TABLE IF EXISTS packages")
        cursor.execute("ALTER TABLE packages_migrated RENAME TO packages")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS packages (
            package_id INTEGER PRIMARY KEY NOT NULL,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS update_packages_updated_at
        AFTER UPDATE ON packages
        FOR EACH ROW
        BEGIN
            UPDATE packages SET updated_at = CURRENT_TIMESTAMP WHERE package_id = OLD.package_id;
        END;
        """
    )

    cursor.execute("PRAGMA table_info(package_items)")
    package_item_columns = [row[1] for row in cursor.fetchall()]
    needs_package_items_migration = False
    if 'item_id' not in package_item_columns:
        needs_package_items_migration = True

    if needs_package_items_migration:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS package_items_migrated (
                package_id INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (package_id, item_id),
                FOREIGN KEY (package_id) REFERENCES packages (package_id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
            );
            """
        )
        if package_item_columns:
            if 'item_code' in package_item_columns:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO package_items_migrated (package_id, item_id, quantity)
                    SELECT package_id, item_code, quantity FROM package_items
                    """
                )
        cursor.execute("DROP TABLE IF EXISTS package_items")
        cursor.execute("ALTER TABLE package_items_migrated RENAME TO package_items")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS package_items (
            package_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (package_id, item_id),
            FOREIGN KEY (package_id) REFERENCES packages (package_id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
        );
        """
    )
    # Ensure orders table references contacts instead of vendors
    cursor.execute("PRAGMA table_info(orders)")
    order_columns = {row[1] for row in cursor.fetchall()}
    if 'contact_id' not in order_columns and 'vendor_id' in order_columns:
        cursor.execute("ALTER TABLE orders RENAME COLUMN vendor_id TO contact_id")

    if 'title' not in order_columns:
        if 'orders' in existing_tables:
            cursor.execute("ALTER TABLE orders ADD COLUMN title TEXT")

    if 'priority_level' not in order_columns and 'orders' in existing_tables:
        cursor.execute("ALTER TABLE orders ADD COLUMN priority_level TEXT")

    if 'fulfillment_channel' not in order_columns and 'orders' in existing_tables:
        cursor.execute("ALTER TABLE orders ADD COLUMN fulfillment_channel TEXT")

    if 'customer_reference' not in order_columns and 'orders' in existing_tables:
        cursor.execute("ALTER TABLE orders ADD COLUMN customer_reference TEXT")

    if 'tax_amount' not in order_columns and 'orders' in existing_tables:
        cursor.execute("ALTER TABLE orders ADD COLUMN tax_amount REAL DEFAULT 0")

    if 'discounts_json' not in order_columns and 'orders' in existing_tables:
        cursor.execute("ALTER TABLE orders ADD COLUMN discounts_json TEXT")

    if 'discount_total' not in order_columns and 'orders' in existing_tables:
        cursor.execute("ALTER TABLE orders ADD COLUMN discount_total REAL DEFAULT 0")

    cursor.execute("CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY NOT NULL, display_id TEXT UNIQUE, contact_id TEXT, order_date TEXT, status TEXT, notes TEXT, estimated_shipping_date TEXT, shipping_address TEXT, shipping_city TEXT, shipping_state TEXT, shipping_zip_code TEXT, estimated_shipping_cost REAL, tax_amount REAL, discounts_json TEXT, discount_total REAL, signature_data_url TEXT, total_amount REAL, title TEXT, priority_level TEXT, fulfillment_channel TEXT, customer_reference TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE SET NULL);")
    cursor.execute("CREATE TRIGGER IF NOT EXISTS update_orders_updated_at AFTER UPDATE ON orders FOR EACH ROW BEGIN UPDATE orders SET updated_at = CURRENT_TIMESTAMP WHERE order_id = OLD.order_id; END;")
    cursor.execute("PRAGMA table_info(order_line_items)")
    order_line_item_columns = [row[1] for row in cursor.fetchall()]
    needs_order_line_item_migration = False
    if 'name' not in order_line_item_columns or 'catalog_item_id' not in order_line_item_columns:
        needs_order_line_item_migration = True

    if needs_order_line_item_migration:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS order_line_items_migrated (
                line_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                catalog_item_id TEXT,
                name TEXT NOT NULL,
                description TEXT,
                quantity INTEGER NOT NULL,
                price_per_unit_cents INTEGER NOT NULL,
                package_id TEXT,
                weight_oz REAL,
                client_reference_id TEXT,
                FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE,
                FOREIGN KEY (catalog_item_id) REFERENCES items (id) ON DELETE SET NULL
            );
            """
        )

        if order_line_item_columns:
            selectable_cols = set(order_line_item_columns)
            if 'item_code' in selectable_cols:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO order_line_items_migrated (line_item_id, order_id, catalog_item_id, name, description, quantity, price_per_unit_cents, package_id, weight_oz, client_reference_id)
                    SELECT
                        line_item_id,
                        order_id,
                        item_code,
                        COALESCE(i.name, item_code, 'Line Item'),
                        '' AS description,
                        quantity,
                        price_per_unit_cents,
                        package_code,
                        NULL,
                        CAST(line_item_id AS TEXT)
                    FROM order_line_items
                    LEFT JOIN items i ON i.id = order_line_items.item_code
                    """
                )

        cursor.execute("DROP TABLE IF EXISTS order_line_items")
        cursor.execute("ALTER TABLE order_line_items_migrated RENAME TO order_line_items")

    cursor.execute("PRAGMA table_info(order_line_items)")
    order_line_item_columns = [row[1] for row in cursor.fetchall()]

    if 'client_reference_id' not in order_line_item_columns and 'order_line_items' in existing_tables:
        cursor.execute("ALTER TABLE order_line_items ADD COLUMN client_reference_id TEXT")
        cursor.execute("UPDATE order_line_items SET client_reference_id = CAST(line_item_id AS TEXT) WHERE client_reference_id IS NULL")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS order_line_items (
            line_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            catalog_item_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            quantity INTEGER NOT NULL,
            price_per_unit_cents INTEGER NOT NULL,
            package_id TEXT,
            weight_oz REAL,
            client_reference_id TEXT,
            FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE,
            FOREIGN KEY (catalog_item_id) REFERENCES items (id) ON DELETE SET NULL
        );
        """
    )
    cursor.execute("CREATE TABLE IF NOT EXISTS order_status_history (history_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, status TEXT NOT NULL, status_date TEXT NOT NULL, FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE);")
    cursor.execute("CREATE TABLE IF NOT EXISTS order_logs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, timestamp TEXT DEFAULT CURRENT_TIMESTAMP, user TEXT, action TEXT NOT NULL, details TEXT, note TEXT, attachment_path TEXT, FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE);")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_contact_links (
            order_id TEXT NOT NULL,
            contact_id TEXT NOT NULL,
            relationship TEXT NOT NULL DEFAULT 'secondary',
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (order_id, contact_id),
            FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE,
            FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE CASCADE
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_contact_links_contact ON order_contact_links(contact_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS record_schemas (
            entity_type TEXT PRIMARY KEY,
            schema_json TEXT NOT NULL,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS records (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_type, entity_id)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS record_handles (
            handle TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            display_name TEXT,
            search_blob TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_record_handles_entity ON record_handles(entity_type, entity_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS record_mentions (
            mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mentioned_handle TEXT NOT NULL,
            mentioned_entity_type TEXT NOT NULL,
            mentioned_entity_id TEXT NOT NULL,
            context_entity_type TEXT NOT NULL,
            context_entity_id TEXT NOT NULL,
            snippet TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_record_mentions_target ON record_mentions(mentioned_entity_type, mentioned_entity_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_record_mentions_context ON record_mentions(context_entity_type, context_entity_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS record_activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_record_activity_target ON record_activity_logs(entity_type, entity_id)")

    cursor.execute("SELECT id, handle, contact_name, company_name, email FROM contacts")
    for row in cursor.fetchall():
        contact_id = row['id'] if isinstance(row, sqlite3.Row) else row[0]
        handle = row['handle'] if isinstance(row, sqlite3.Row) else row[1]
        contact_name = row['contact_name'] if isinstance(row, sqlite3.Row) else row[2]
        company_name = row['company_name'] if isinstance(row, sqlite3.Row) else row[3]
        email = row['email'] if isinstance(row, sqlite3.Row) else row[4]
        fallback = contact_name or company_name or email or contact_id
        if not handle:
            handle = ensure_contact_handle(cursor, contact_id, fallback)
        if not handle:
            continue
        display_name = (contact_name or company_name or email or handle).strip()
        search_values = [contact_name, company_name, email, handle]
        search_blob = ' '.join([value.strip() for value in search_values if value]).lower()
        cursor.execute(
            """
            INSERT OR REPLACE INTO record_handles (handle, entity_type, entity_id, display_name, search_blob, updated_at)
            VALUES (?, 'contact', ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (handle.lower(), contact_id, display_name, search_blob),
        )

    if 'contact_mentions' in existing_tables:
        cursor.execute(
            "SELECT mention_id, contact_id, context_type, context_id, snippet, created_at FROM contact_mentions"
        )
        legacy_mentions = cursor.fetchall()
        for mention in legacy_mentions:
            contact_id = mention['contact_id'] if isinstance(mention, sqlite3.Row) else mention[1]
            handle = ensure_contact_handle(cursor, contact_id)
            if not handle:
                continue
            snippet = mention['snippet'] if isinstance(mention, sqlite3.Row) else mention[4]
            context_type = mention['context_type'] if isinstance(mention, sqlite3.Row) else mention[2]
            context_id = mention['context_id'] if isinstance(mention, sqlite3.Row) else mention[3]
            created_at = mention['created_at'] if isinstance(mention, sqlite3.Row) else mention[5]
            cursor.execute(
                """
                INSERT INTO record_mentions (
                    mentioned_handle,
                    mentioned_entity_type,
                    mentioned_entity_id,
                    context_entity_type,
                    context_entity_id,
                    snippet,
                    created_at
                ) VALUES (?, 'contact', ?, ?, ?, ?, ?)
                """,
                (handle.lower(), contact_id, context_type, str(context_id), snippet, created_at),
            )
        cursor.execute("DROP TABLE contact_mentions")
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

if __name__ == '__main__':
    init_db()
