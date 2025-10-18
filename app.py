import os
import uuid
import webbrowser
from threading import Timer
import socket
import sqlite3
import sys
import smtplib
import re
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
import shutil
import zipfile
import pytz

MENTION_PATTERN = re.compile(r'@([A-Za-z0-9_.-]+)')

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, flash, url_for
from database import get_db_connection, init_db

# Load environment variables from .env file
load_dotenv()

# --- App Initialization ---
app = Flask(__name__, template_folder='templates')
app.config['JSON_SORT_KEYS'] = False
app.secret_key = os.urandom(24)

_db_bootstrapped = False


@app.before_request
def _ensure_database_initialized():
    """Guarantee the SQLite schema exists before serving any request."""
    global _db_bootstrapped
    if _db_bootstrapped:
        return
    try:
        init_db()
        _db_bootstrapped = True
    except Exception as exc:  # pragma: no cover - defensive logging
        app.logger.exception("Failed to initialize database before request: %s", exc)

DATA_DIR = 'data'
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
PASSWORDS_FILE = os.path.join(DATA_DIR, 'passwords.json')

UPLOAD_FOLDER = os.path.join(os.path.dirname(app.root_path), 'data')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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


def _slugify_handle(source_text: str) -> str:
    base = re.sub(r'[^a-z0-9]+', '-', (source_text or '').lower()).strip('-')
    if not base:
        return 'contact'
    return base.replace('-', '')[:32]


def _generate_unique_handle(cursor, preferred_text: str) -> str:
    base = _slugify_handle(preferred_text)
    candidate = base
    suffix = 1
    while True:
        cursor.execute("SELECT 1 FROM contacts WHERE handle = ?", (candidate,))
        if not cursor.fetchone():
            return candidate
        candidate = f"{base}{suffix}"
        suffix += 1


def ensure_contact_handle(cursor, contact_id, fallback_text=""):
    cursor.execute("SELECT handle, company_name, contact_name FROM contacts WHERE id = ?", (contact_id,))
    existing = cursor.fetchone()
    if not existing:
        return None
    handle, company_name, contact_name = existing
    if handle:
        return handle
    new_handle = _generate_unique_handle(cursor, contact_name or company_name or fallback_text or 'contact')
    cursor.execute("UPDATE contacts SET handle = ? WHERE id = ?", (new_handle, contact_id))
    return new_handle


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
    if "created_at" in keys:
        contact["createdAt"] = row["created_at"]
    if "updated_at" in keys:
        contact["updatedAt"] = row["updated_at"]
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
        "companyName": order_dict.pop('contact_company_name', None) or "[Contact Not Found]",
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

    if not contact_snapshot['id']:
        contact_snapshot = {
            "id": None,
            "companyName": "[Contact Not Found]",
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
    order_dict['signatureDataUrl'] = order_dict.pop('signature_data_url')

    if include_logs:
        cursor.execute(
            "SELECT log_id, timestamp, user, action, details, note, attachment_path FROM order_logs WHERE order_id = ? ORDER BY timestamp DESC",
            (order_dict['id'],)
        )
        logs = []
        for log_row in cursor.fetchall():
            log_dict = dict(log_row)
            if log_dict.get('timestamp'):
                naive_date = dateutil_parse(log_dict['timestamp'])
                utc_date = pytz.utc.localize(naive_date)
                log_dict['timestamp'] = utc_date.astimezone(user_timezone).isoformat()
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

    email = contact_info_payload.get("email", "")
    phone = contact_info_payload.get("phone", "")
    billing_address = contact_info_payload.get("billingAddress", "")
    billing_city = contact_info_payload.get("billingCity", "")
    billing_state = contact_info_payload.get("billingState", "")
    billing_zip_code = contact_info_payload.get("billingZipCode", "")
    shipping_address = contact_info_payload.get("shippingAddress", "")
    shipping_city = contact_info_payload.get("shippingCity", "")
    shipping_state = contact_info_payload.get("shippingState", "")
    shipping_zip_code = contact_info_payload.get("shippingZipCode", "")
    notes = contact_info_payload.get("notes")
    provided_handle = contact_info_payload.get("handle")
    if provided_handle:
        provided_handle = provided_handle.lower().lstrip('@')

    final_contact_id = provided_id
    if provided_id:
        field_values = [company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code,
                        shipping_address, shipping_city, shipping_state, shipping_zip_code, provided_id]
        cursor.execute(
            "UPDATE contacts SET company_name = ?, contact_name = ?, email = ?, phone = ?, billing_address = ?, billing_city = ?, "
            "billing_state = ?, billing_zip_code = ?, shipping_address = ?, shipping_city = ?, shipping_state = ?, shipping_zip_code = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(field_values)
        )
        if cursor.rowcount == 0:
            final_contact_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO contacts (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, "
                "billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (final_contact_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state,
                 billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code,
                 provided_handle or _generate_unique_handle(cursor, contact_name or company_name), notes)
            )
        else:
            if provided_handle:
                cursor.execute("UPDATE contacts SET handle = ? WHERE id = ?", (provided_handle, provided_id))
            if notes is not None:
                cursor.execute("UPDATE contacts SET notes = ? WHERE id = ?", (notes, provided_id))
            ensure_contact_handle(cursor, provided_id, contact_name or company_name)
    else:
        final_contact_id = str(uuid.uuid4())
        handle_to_use = provided_handle or _generate_unique_handle(cursor, contact_name or company_name)
        cursor.execute(
            "INSERT INTO contacts (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (final_contact_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state,
             billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle_to_use, notes)
        )
    final_notes = notes
    if final_contact_id and notes is None:
        cursor.execute("SELECT notes FROM contacts WHERE id = ?", (final_contact_id,))
        existing_notes_row = cursor.fetchone()
        if existing_notes_row:
            final_notes = existing_notes_row['notes'] if isinstance(existing_notes_row, sqlite3.Row) else existing_notes_row[0]
    sync_contact_mentions(cursor, extract_contact_handles(final_notes), 'contact_profile_note', f'note:{final_contact_id}', final_notes)
    return final_contact_id


def update_contact_by_id(cursor, contact_id, contact_data_payload):
    field_mappings = {
        "companyName": "company_name",
        "contactName": "contact_name",
        "email": "email",
        "phone": "phone",
        "billingAddress": "billing_address",
        "billingCity": "billing_city",
        "billingState": "billing_state",
        "billingZipCode": "billing_zip_code",
        "shippingAddress": "shipping_address",
        "shippingCity": "shipping_city",
        "shippingState": "shipping_state",
        "shippingZipCode": "shipping_zip_code",
        "notes": "notes",
        "handle": "handle",
    }
    fields_to_update, values_to_update = [], []
    for pk, dn in field_mappings.items():
        if pk in contact_data_payload:
            value = contact_data_payload[pk]
            if pk == 'handle' and value:
                value = value.lower().lstrip('@')
            fields_to_update.append(f"{dn} = ?")
            values_to_update.append(value)
    if not fields_to_update:
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes, created_at, updated_at FROM contacts WHERE id = ?", (contact_id,))
        cv = cursor.fetchone()
        return serialize_contact_row(cv) if cv else None
    sql_query = f"UPDATE contacts SET {', '.join(fields_to_update)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    values_to_update.append(contact_id)
    try:
        cursor.execute(sql_query, tuple(values_to_update))
        if cursor.rowcount == 0:
            return None
        ensure_contact_handle(cursor, contact_id)
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes, created_at, updated_at FROM contacts WHERE id = ?", (contact_id,))
        uvd = cursor.fetchone()
        if not uvd:
            return None
        updated_contact = serialize_contact_row(uvd)
        if 'notes' in contact_data_payload:
            sync_contact_mentions(cursor, extract_contact_handles(updated_contact.get('notes')), 'contact_profile_note', f'note:{contact_id}', updated_contact.get('notes'))
        return updated_contact
    except sqlite3.Error as e:
        app.logger.error(f"DB error updating contact {contact_id}: {e}")
        raise


def extract_contact_handles(text):
    if not text:
        return []
    handles = []
    for match in MENTION_PATTERN.finditer(text):
        handle = match.group(1).lower()
        if handle not in handles:
            handles.append(handle)
    return handles


def sync_contact_mentions(cursor, handles, context_type, context_id, snippet):
    cursor.execute("DELETE FROM contact_mentions WHERE context_type = ? AND context_id = ?", (context_type, str(context_id)))
    if not handles:
        return
    snippet_text = (snippet or '').strip()
    if len(snippet_text) > 500:
        snippet_text = snippet_text[:497] + '...'
    for handle in handles:
        cursor.execute("SELECT id FROM contacts WHERE lower(handle) = ?", (handle,))
        row = cursor.fetchone()
        if not row:
            continue
        contact_id = row['id'] if isinstance(row, sqlite3.Row) else row[0]
        cursor.execute(
            "INSERT INTO contact_mentions (contact_id, context_type, context_id, snippet) VALUES (?, ?, ?, ?)",
            (contact_id, context_type, str(context_id), snippet_text)
        )


def refresh_order_contact_links(cursor, order_id, primary_contact_id=None):
    cursor.execute("DELETE FROM order_contact_links WHERE order_id = ?", (order_id,))
    cursor.execute(
        """
            SELECT DISTINCT contact_id
            FROM contact_mentions
            WHERE (context_type = 'order_note' AND context_id = ?)
               OR (context_type = 'order_log' AND context_id IN (
                    SELECT CAST(log_id AS TEXT) FROM order_logs WHERE order_id = ?
               ))
        """,
        (order_id, order_id)
    )
    rows = cursor.fetchall()
    for row in rows:
        contact_id = row['contact_id'] if isinstance(row, sqlite3.Row) else row[0]
        if not contact_id:
            continue
        if primary_contact_id and str(contact_id) == str(primary_contact_id):
            continue
        cursor.execute(
            "INSERT OR IGNORE INTO order_contact_links (order_id, contact_id, relationship) VALUES (?, ?, 'secondary')",
            (order_id, contact_id)
        )

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
if not os.path.exists(SETTINGS_FILE):
    write_json_file(SETTINGS_FILE, {"company_name": "Your Company Name", "default_shipping_zip_code": "00000"})

@app.route('/api/orders', methods=['GET'])
def get_orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    settings = read_json_file(SETTINGS_FILE)
    user_timezone_str = settings.get('timezone', 'UTC')
    user_timezone = pytz.timezone(user_timezone_str)

    cursor.execute("SELECT o.*, v.company_name as contact_company_name, v.contact_name as contact_contact_name, v.email as contact_email, v.phone as contact_phone, v.billing_address as contact_billing_address, v.billing_city as contact_billing_city, v.billing_state as contact_billing_state, v.billing_zip_code as contact_billing_zip_code, v.shipping_address as contact_shipping_address, v.shipping_city as contact_shipping_city, v.shipping_state as contact_shipping_state, v.shipping_zip_code as contact_shipping_zip_code, v.handle as contact_handle, v.notes as contact_notes FROM orders o LEFT JOIN contacts v ON o.contact_id = v.id WHERE o.status != 'Deleted' ORDER BY o.order_date DESC, o.order_id DESC")
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

    cursor.execute("SELECT o.*, v.company_name as contact_company_name, v.contact_name as contact_contact_name, v.email as contact_email, v.phone as contact_phone, v.billing_address as contact_billing_address, v.billing_city as contact_billing_city, v.billing_state as contact_billing_state, v.billing_zip_code as contact_billing_zip_code, v.shipping_address as contact_shipping_address, v.shipping_city as contact_shipping_city, v.shipping_state as contact_shipping_state, v.shipping_zip_code as contact_shipping_zip_code, v.handle as contact_handle, v.notes as contact_notes FROM orders o LEFT JOIN contacts v ON o.contact_id = v.id WHERE o.order_id = ?", (order_id,))
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
        file = request.files.get('attachment')
        attachment_path = None

        if file and file.filename:
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
            attachment_path = unique_filename
            try:
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            except Exception as e:
                app.logger.error(f"Failed to save attachment for order {order_id}: {e}")
                return jsonify({"status": "error", "message": "Failed to save attachment"}), 500

        try:
            cursor.execute(
                "INSERT INTO order_logs (order_id, user, action, details, note, attachment_path) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, "system", action, log_body, log_body, attachment_path)
            )
            log_id = cursor.lastrowid
            handles = extract_contact_handles(log_body)
            sync_contact_mentions(cursor, handles, 'order_log', log_id, log_body)
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
            conn.close()
            app.logger.error(f"Database error adding log for order {order_id}: {e}")
            return jsonify({"status": "error", "message": "Database error"}), 500

    # GET request
    settings = read_json_file(SETTINGS_FILE)
    user_timezone_str = settings.get('timezone', 'UTC')
    user_timezone = pytz.timezone(user_timezone_str)

    cursor.execute("SELECT log_id, timestamp, user, action, details, note, attachment_path FROM order_logs WHERE order_id = ? ORDER BY timestamp DESC", (order_id,))
    logs_from_db = cursor.fetchall()
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
        logs.append(log_dict)
    
    conn.close()
    return jsonify(logs)

@app.route('/api/orders/<string:order_id>/logs/<int:log_id>', methods=['POST', 'DELETE'])
def handle_specific_order_log(order_id, log_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT attachment_path FROM order_logs WHERE log_id = ? AND order_id = ?", (log_id, order_id))
    log = cursor.fetchone()

    if not log:
        conn.close()
        return jsonify({"status": "error", "message": "Log not found"}), 404

    if request.method == 'POST':  # Using POST for update to handle multipart/form-data
        note = request.form.get('note')
        details = request.form.get('details')
        action_override = request.form.get('action')
        log_body = (details if details is not None else note) or ''
        file = request.files.get('attachment')

        attachment_path = log['attachment_path']

        if file and file.filename:
            if attachment_path:
                old_file_path = os.path.join(app.config['UPLOAD_FOLDER'], attachment_path)
                if os.path.exists(old_file_path):
                    os.remove(old_file_path)

            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
            attachment_path = unique_filename
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))

        updated_action = action_override.strip() if action_override and action_override.strip() else log['action']
        cursor.execute(
            "UPDATE order_logs SET action = ?, details = ?, note = ?, attachment_path = ? WHERE log_id = ?",
            (updated_action, log_body, log_body, attachment_path, log_id)
        )
        handles = extract_contact_handles(log_body)
        sync_contact_mentions(cursor, handles, 'order_log', log_id, log_body)
        cursor.execute("SELECT contact_id FROM orders WHERE order_id = ?", (order_id,))
        primary_row = cursor.fetchone()
        primary_contact_for_order = primary_row['contact_id'] if primary_row else None
        refresh_order_contact_links(cursor, order_id, primary_contact_for_order)
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Log updated."})

    elif request.method == 'DELETE':
        attachment_path = log['attachment_path']
        if attachment_path:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], attachment_path)
            if os.path.exists(file_path):
                os.remove(file_path)

        cursor.execute("DELETE FROM order_logs WHERE log_id = ?", (log_id,))
        cursor.execute("DELETE FROM contact_mentions WHERE context_type = ? AND context_id = ?", ('order_log', str(log_id)))
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
                "UPDATE orders SET display_id=?, contact_id=?, order_date=?, status=?, notes=?, estimated_shipping_date=?, shipping_address=?, shipping_city=?, shipping_state=?, shipping_zip_code=?, estimated_shipping_cost=?, tax_amount=?, discounts_json=?, discount_total=?, signature_data_url=?, total_amount=?, title=?, priority_level=?, fulfillment_channel=?, customer_reference=?, updated_at=CURRENT_TIMESTAMP WHERE order_id=?",
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
                "INSERT INTO orders (order_id, display_id, contact_id, order_date, status, notes, estimated_shipping_date, shipping_address, shipping_city, shipping_state, shipping_zip_code, estimated_shipping_cost, tax_amount, discounts_json, discount_total, signature_data_url, total_amount, title, priority_level, fulfillment_channel, customer_reference) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
        handles_from_notes = extract_contact_handles(notes_text)
        sync_contact_mentions(cursor, handles_from_notes, 'order_note', processed_order_id, notes_text)
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
                       v.handle as contact_handle, v.notes as contact_notes
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
               shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes
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
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes, created_at, updated_at FROM contacts WHERE id=?", (contact_id,))
        contact_row = cursor.fetchone()
        if not contact_row:
            conn.close();
            return jsonify({"message": "Contact not found."}), 404
        ensure_contact_handle(cursor, contact_id, contact_row['contact_name'] or contact_row['company_name'])
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, handle, notes, created_at, updated_at FROM contacts WHERE id=?", (contact_id,))
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

        cursor.execute("SELECT mention_id, context_type, context_id, snippet, created_at FROM contact_mentions WHERE contact_id = ? ORDER BY created_at DESC", (contact_id,))
        mentions = []
        for mention in cursor.fetchall():
            context_type = mention['context_type']
            context_id = mention['context_id']
            mention_entry = {
                "id": mention['mention_id'],
                "contextType": context_type,
                "contextId": context_id,
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

@app.route('/api/settings', methods=['GET'])
def get_settings():
    settings = read_json_file(SETTINGS_FILE)
    if not isinstance(settings, dict):
        settings = {}

    defaults = {
        "company_name": "FireCoast OMS",
        "default_shipping_zip_code": "",
        "default_email_body": "Dear [contactCompany],\n\nPlease find attached the purchase order [orderID] for your records.\n\nWe appreciate your business!\n\nThank you,\n[yourCompany]",
        "timezone": 'UTC',
        "email_address": "",
        "app_password": "",
        "email_cc": "",
        "email_bcc": "",
        "invoice_business_name": "FireCoast OMS",
        "invoice_business_details": "123 Harbor Way\nPortland, OR 97203\nhello@firecoast.com",
        "invoice_brand_color": "#f97316",
        "invoice_logo_data_url": "",
    }

    updated = False
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
            updated = True

    if updated:
        write_json_file(SETTINGS_FILE, settings)

    return jsonify(settings)

@app.route('/api/settings', methods=['POST'])
def update_settings():
    new_settings_payload = request.json
    if not new_settings_payload:
        return jsonify({"message": "Request must be JSON"}), 400
    
    existing_settings = read_json_file(SETTINGS_FILE)
    if not isinstance(existing_settings, dict):
        existing_settings = {}

    existing_settings['company_name'] = new_settings_payload.get('company_name', existing_settings.get('company_name'))
    existing_settings['default_shipping_zip_code'] = new_settings_payload.get('default_shipping_zip_code', existing_settings.get('default_shipping_zip_code'))
    existing_settings['default_email_body'] = new_settings_payload.get('default_email_body', existing_settings.get('default_email_body'))

    for key in ('invoice_business_name', 'invoice_business_details', 'invoice_brand_color'):
        if key in new_settings_payload:
            existing_settings[key] = new_settings_payload.get(key, existing_settings.get(key))

    write_json_file(SETTINGS_FILE, existing_settings)
    return jsonify({"message": "Settings updated."}), 200

@app.route('/api/settings/timezone', methods=['POST'])
def update_timezone_settings():
    payload = request.json
    if not payload or 'timezone' not in payload:
        return jsonify({"message": "Invalid request"}), 400

    settings = read_json_file(SETTINGS_FILE)
    if not isinstance(settings, dict):
        settings = {}
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

    existing_settings = read_json_file(SETTINGS_FILE)
    if not isinstance(existing_settings, dict):
        existing_settings = {}

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

    existing_settings = read_json_file(SETTINGS_FILE)
    if not isinstance(existing_settings, dict):
        existing_settings = {}

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

    write_json_file(SETTINGS_FILE, existing_settings)
    return jsonify({"message": "Invoice appearance updated.", "settings": existing_settings}), 200


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

@app.route('/manage/customers')
def manage_customers_page(): return render_template('manage_customers.html')
@app.route('/manage/items')
def manage_items_page(): return render_template('manage_items.html')
@app.route('/manage/packages')
def manage_packages_page(): return render_template('manage_packages.html')

@app.route('/settings')
def settings_page():
    timezones = pytz.all_timezones
    settings = read_json_file(SETTINGS_FILE)
    selected_timezone = settings.get('timezone', 'UTC')
    return render_template('settings.html', timezones=timezones, selected_timezone=selected_timezone)

@app.route('/dashboard')
def dashboard_page():
    return render_template('admin.html')


@app.route('/admin')
def legacy_admin_redirect():
    return redirect(url_for('dashboard_page'))

@app.route('/analytics')
def analytics_page():
    return render_template('analytics.html')

@app.route('/contacts')
def contacts_page():
    return render_template('contacts.html')

@app.route('/orders')
def orders_page():
    return render_template('orders.html')


@app.route('/passwords')
def passwords_page():
    return render_template('passwords.html')

@app.route('/api/export-data', methods=['GET'])
def export_data():
    """Creates a zip archive of the entire /data directory."""
    try:
        # It's good practice to ensure the app is not writing to the DB during backup.
        # For this app's scale, a direct copy is likely fine, but for larger systems,
        # you might implement a read-only mode or a brief service pause.
        
        data_dir = os.path.join(os.path.dirname(app.root_path), 'data')
        if not os.path.isdir(data_dir):
            return jsonify({"status": "error", "message": "Data directory not found."}), 404

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # Create the archive in a temporary location to avoid including the archive in itself
        temp_dir = os.path.join(os.path.dirname(app.root_path), 'temp_backups')
        os.makedirs(temp_dir, exist_ok=True)
        
        archive_name = f'backup_{timestamp}'
        archive_path_base = os.path.join(temp_dir, archive_name)
        
        # Create the zip file from the 'data' directory
        shutil.make_archive(archive_path_base, 'zip', data_dir)

        archive_path_zip = f"{archive_path_base}.zip"

        # Send the file and clean up afterwards
        response = send_from_directory(temp_dir, f"{archive_name}.zip", as_attachment=True)

        @response.call_on_close
        def cleanup():
            try:
                os.remove(archive_path_zip)
                # If the temp dir is empty, remove it too
                if not os.listdir(temp_dir):
                    os.rmdir(temp_dir)
            except Exception as e:
                app.logger.error(f"Error cleaning up backup file: {e}")

        return response

    except Exception as e:
        app.logger.error(f"Error creating data backup: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Failed to create backup."}), 500

@app.route('/api/import-data', methods=['POST'])
def import_data():
    """Restores the /data directory from a zip archive."""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.zip'):
        return jsonify({"status": "error", "message": "Invalid file. Please upload a .zip backup file."}), 400

    data_dir = os.path.join(os.path.dirname(app.root_path), 'data')
    
    try:
        # Before replacing, create a temporary backup of the current data directory
        temp_backup_dir = os.path.join(os.path.dirname(app.root_path), 'data_temp_backup')
        if os.path.exists(temp_backup_dir):
            shutil.rmtree(temp_backup_dir) # remove old temp backup if it exists
        if os.path.exists(data_dir):
            shutil.copytree(data_dir, temp_backup_dir)

        # Clear the existing data directory
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)
        os.makedirs(data_dir)

        # Extract the new data from the uploaded zip file
        with zipfile.ZipFile(file.stream, 'r') as zip_ref:
            zip_ref.extractall(data_dir)
        
        # Restore completed, can now remove the temporary backup
        if os.path.exists(temp_backup_dir):
            shutil.rmtree(temp_backup_dir)

        # Re-initialize DB connection to ensure schema and pragmas are set if db was replaced
        # A full app restart might be safer, but re-running init_db can cover schema changes.
        init_db()

        # After a successful import, trigger a shutdown which will lead to a restart by the user or a process manager
        Timer(1.0, lambda: os.kill(os.getpid(), 9)).start()

        return jsonify({"status": "success", "message": "Data restored successfully. The application will restart in a few moments."}), 200

    except Exception as e:
        app.logger.error(f"Error restoring data: {e}")
        app.logger.error(traceback.format_exc())
        
        # Attempt to restore from the temporary backup
        try:
            if os.path.exists(temp_backup_dir):
                if os.path.exists(data_dir):
                    shutil.rmtree(data_dir)
                shutil.move(temp_backup_dir, data_dir)
                app.logger.info("Successfully restored data from temporary backup after import failure.")
        except Exception as e_restore:
            app.logger.error(f"CRITICAL: Failed to restore data from temporary backup: {e_restore}")

        return jsonify({"status": "error", "message": "An error occurred during the restore process. The original data has been restored."}), 500

@app.route('/order-logs/<string:order_id>')
def order_logs_page(order_id):
    return render_template('order_logs.html', order_id=order_id)

@app.route('/order/<string:order_id>')
def view_order_page(order_id):
    return render_template('view_order.html', order_id=order_id)

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
