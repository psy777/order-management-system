CREATE TABLE IF NOT EXISTS vendors (
    id TEXT PRIMARY KEY,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    vendor_id TEXT,
    order_date TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT,
    estimated_shipping_date TEXT,
    shipping_address TEXT,
    shipping_city TEXT,
    shipping_state TEXT,
    shipping_zip_code TEXT,
    estimated_shipping_cost REAL,
    scent_option TEXT,
    name_drop INTEGER,
    signature_data_url TEXT,
    total_amount REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors (id)
);

CREATE TABLE IF NOT EXISTS items (
    item_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    price REAL,
    weight_oz REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS styles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS item_styles (
    item_code TEXT NOT NULL,
    style_id INTEGER NOT NULL,
    PRIMARY KEY (item_code, style_id),
    FOREIGN KEY (item_code) REFERENCES items (item_code) ON DELETE CASCADE,
    FOREIGN KEY (style_id) REFERENCES styles (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS order_line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    item_code TEXT NOT NULL,
    package_code INTEGER,
    quantity INTEGER NOT NULL,
    price_per_unit REAL,
    style_chosen TEXT,
    item_type TEXT,
    FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE,
    FOREIGN KEY (item_code) REFERENCES items (item_code)
);

CREATE TABLE IF NOT EXISTS order_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    status_date TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS packages (
    package_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS package_items (
    package_id INTEGER NOT NULL,
    item_code TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    PRIMARY KEY (package_id, item_code),
    FOREIGN KEY (package_id) REFERENCES packages (package_id) ON DELETE CASCADE,
    FOREIGN KEY (item_code) REFERENCES items (item_code) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT,
    default_shipping_zip_code TEXT,
    default_email_body TEXT,
    email_address TEXT,
    app_password TEXT,
    email_cc TEXT,
    email_bcc TEXT
);
