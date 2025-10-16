import sqlite3
import os
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = 'data'
DATABASE_FILE = os.path.join(DATA_DIR, 'orders_manager.db')

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    conn = sqlite3.connect(DATABASE_FILE, timeout=30.0, isolation_level='DEFERRED')
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
    cursor.execute("CREATE TABLE IF NOT EXISTS items (item_code TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, type TEXT, price_cents INTEGER NOT NULL, weight_oz INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);")
    cursor.execute("CREATE TRIGGER IF NOT EXISTS update_items_updated_at AFTER UPDATE ON items FOR EACH ROW BEGIN UPDATE items SET updated_at = CURRENT_TIMESTAMP WHERE item_code = OLD.item_code; END;")
    cursor.execute("CREATE TABLE IF NOT EXISTS styles (id INTEGER PRIMARY KEY AUTOINCREMENT, style_name TEXT UNIQUE NOT NULL);")
    cursor.execute("CREATE TABLE IF NOT EXISTS item_styles (item_code TEXT NOT NULL, style_id INTEGER NOT NULL, PRIMARY KEY (item_code, style_id), FOREIGN KEY (item_code) REFERENCES items (item_code) ON DELETE CASCADE, FOREIGN KEY (style_id) REFERENCES styles (id) ON DELETE CASCADE);")
    cursor.execute("CREATE TABLE IF NOT EXISTS packages (package_id INTEGER PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE, type TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);")
    cursor.execute("CREATE TRIGGER IF NOT EXISTS update_packages_updated_at AFTER UPDATE ON packages FOR EACH ROW BEGIN UPDATE packages SET updated_at = CURRENT_TIMESTAMP WHERE package_id = OLD.package_id; END;")
    cursor.execute("CREATE TABLE IF NOT EXISTS package_items (package_id INTEGER NOT NULL, item_code TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (package_id, item_code), FOREIGN KEY (package_id) REFERENCES packages (package_id) ON DELETE CASCADE, FOREIGN KEY (item_code) REFERENCES items (item_code) ON DELETE CASCADE);")
    # Ensure orders table references contacts instead of vendors
    cursor.execute("PRAGMA table_info(orders)")
    order_columns = {row[1] for row in cursor.fetchall()}
    if 'contact_id' not in order_columns and 'vendor_id' in order_columns:
        cursor.execute("ALTER TABLE orders RENAME COLUMN vendor_id TO contact_id")

    if 'title' not in order_columns:
        if 'orders' in existing_tables:
            cursor.execute("ALTER TABLE orders ADD COLUMN title TEXT")

    cursor.execute("CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY NOT NULL, display_id TEXT UNIQUE, contact_id TEXT, order_date TEXT, status TEXT, notes TEXT, estimated_shipping_date TEXT, shipping_address TEXT, shipping_city TEXT, shipping_state TEXT, shipping_zip_code TEXT, estimated_shipping_cost REAL, scent_option TEXT, name_drop INTEGER, signature_data_url TEXT, total_amount REAL, title TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE SET NULL);")
    cursor.execute("CREATE TRIGGER IF NOT EXISTS update_orders_updated_at AFTER UPDATE ON orders FOR EACH ROW BEGIN UPDATE orders SET updated_at = CURRENT_TIMESTAMP WHERE order_id = OLD.order_id; END;")
    cursor.execute("CREATE TABLE IF NOT EXISTS order_line_items (line_item_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, item_code TEXT NOT NULL, package_code TEXT, quantity INTEGER NOT NULL, price_per_unit_cents INTEGER NOT NULL, style_chosen TEXT, item_type TEXT, FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE, FOREIGN KEY (item_code) REFERENCES items (item_code) ON DELETE RESTRICT);")
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
        CREATE TABLE IF NOT EXISTS contact_mentions (
            mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL,
            context_type TEXT NOT NULL,
            context_id TEXT NOT NULL,
            snippet TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contact_mentions_contact ON contact_mentions(contact_id)")
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

if __name__ == '__main__':
    init_db()
