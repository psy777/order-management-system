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
from datetime import datetime, timezone
import traceback
import time
import json

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory
from shipcostestimate import calculate_shipping_cost_for_order
from database import get_db_connection, init_db

# Load environment variables from .env file
load_dotenv()

# --- App Initialization ---
app = Flask(__name__, template_folder='templates')
app.config['JSON_SORT_KEYS'] = False

DATA_DIR = 'data'
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')

UPLOAD_FOLDER = 'uploads'
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

def update_or_create_vendor(cursor, vendor_info_payload):
    if not vendor_info_payload or not vendor_info_payload.get("companyName"): return vendor_info_payload.get("id") if vendor_info_payload else None
    provided_id, company_name, contact_name, email, phone, billing_address, shipping_address = (
        vendor_info_payload.get("id"), vendor_info_payload.get("companyName"), vendor_info_payload.get("contactName", ""),
        vendor_info_payload.get("email", ""), vendor_info_payload.get("phone", ""),
        vendor_info_payload.get("billingAddress", ""), vendor_info_payload.get("shippingAddress", ""))
    final_vendor_id = provided_id
    if provided_id:
        cursor.execute("UPDATE vendors SET company_name = ?, contact_name = ?, email = ?, phone = ?, billing_address = ?, shipping_address = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                       (company_name, contact_name, email, phone, billing_address, shipping_address, provided_id))
        if cursor.rowcount == 0:
            final_vendor_id = str(uuid.uuid4())
            cursor.execute("INSERT INTO vendors (id, company_name, contact_name, email, phone, billing_address, shipping_address) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (final_vendor_id, company_name, contact_name, email, phone, billing_address, shipping_address))
    else:
        final_vendor_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO vendors (id, company_name, contact_name, email, phone, billing_address, shipping_address) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (final_vendor_id, company_name, contact_name, email, phone, billing_address, shipping_address))
    return final_vendor_id

def update_vendor_by_id(cursor, vendor_id, vendor_data_payload):
    field_mappings = {"companyName": "company_name", "contactName": "contact_name", "email": "email", "phone": "phone", "billingAddress": "billing_address", "shippingAddress": "shipping_address"}
    fields_to_update, values_to_update = [], []
    for pk, dn in field_mappings.items():
        if pk in vendor_data_payload: fields_to_update.append(f"{dn} = ?"); values_to_update.append(vendor_data_payload[pk])
    if not fields_to_update:
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, shipping_address FROM vendors WHERE id = ?", (vendor_id,))
        cv = cursor.fetchone()
        return dict(cv) if cv else None
    sql_query = f"UPDATE vendors SET {', '.join(fields_to_update)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    values_to_update.append(vendor_id)
    try:
        cursor.execute(sql_query, tuple(values_to_update))
        if cursor.rowcount == 0: return None
        cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, shipping_address FROM vendors WHERE id = ?", (vendor_id,))
        uvd = cursor.fetchone()
        return dict(uvd) if uvd else None
    except sqlite3.Error as e: app.logger.error(f"DB error updating vendor {vendor_id}: {e}"); raise

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
if not os.path.exists(SETTINGS_FILE):
    write_json_file(SETTINGS_FILE, {"company_name": "Your Company Name", "default_shipping_zip_code": "00000"})

with app.app_context():
    init_db()

@app.route('/api/orders', methods=['GET'])
def get_orders():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT o.*, v.company_name as vendor_company_name, v.contact_name as vendor_contact_name, v.email as vendor_email, v.phone as vendor_phone, v.billing_address as vendor_billing_address, v.shipping_address as vendor_shipping_address FROM orders o LEFT JOIN vendors v ON o.vendor_id = v.id WHERE o.status != 'Deleted' ORDER BY o.order_date DESC, o.order_id DESC")
    orders_from_db = cursor.fetchall()
    active_orders_response = []
    for order_row in orders_from_db:
        order_dict = dict(order_row)
        order_dict['vendorInfo'] = {"id": order_dict.pop('vendor_id'), "companyName": order_dict.pop('vendor_company_name') or "[Vendor Not Found]", "contactName": order_dict.pop('vendor_contact_name'), "email": order_dict.pop('vendor_email'), "phone": order_dict.pop('vendor_phone'), "billingAddress": order_dict.pop('vendor_billing_address'), "shippingAddress": order_dict.pop('vendor_shipping_address')}
        if not order_dict['vendorInfo']['id']: order_dict['vendorInfo'] = {"id": None, "companyName": "[Vendor Not Found]", "contactName": "", "email": "", "phone": "", "billingAddress": "", "shippingAddress": ""}
        cursor.execute("SELECT item_code, package_code, quantity, price_per_unit_cents, style_chosen, item_type FROM order_line_items WHERE order_id = ?", (order_dict['order_id'],))
        order_dict['lineItems'] = [{'item': li['item_code'], 'packageCode': li['package_code'], 'price': li['price_per_unit_cents'], 'quantity': li['quantity'], 'style': li['style_chosen'], 'type': li['item_type']} for li in cursor.fetchall()]
        cursor.execute("SELECT status, status_date FROM order_status_history WHERE order_id = ? ORDER BY status_date ASC", (order_dict['order_id'],))
        order_dict['statusHistory'] = [{'status': h['status'], 'date': h['status_date']} for h in cursor.fetchall()]
        order_dict['id'] = order_dict.pop('order_id'); order_dict['date'] = order_dict.pop('order_date'); order_dict['total'] = order_dict.pop('total_amount'); order_dict['estimatedShipping'] = order_dict.pop('estimated_shipping_cost')
        order_dict['nameDrop'] = True if order_dict.pop('name_drop', 0) == 1 else False
        active_orders_response.append(order_dict)
    conn.close()
    return jsonify(active_orders_response)

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
        order_id_from_payload = new_order_payload.get('id')
        
        existing_order_row = None
        if order_id_from_payload:
            cursor.execute("SELECT status, vendor_id FROM orders WHERE order_id = ?", (order_id_from_payload,))
            existing_order_row = cursor.fetchone()

        current_order_id_for_db_ops = order_id_from_payload if existing_order_row else None
        
        is_attempting_delete = new_order_payload.get('status') == "Deleted"

        if order_id_from_payload and is_attempting_delete: 
            if existing_order_row:
                if existing_order_row['status'] != "Draft":
                    vendor_id_for_confirm = existing_order_row['vendor_id']
                    company_name_for_confirm = ""
                    if vendor_id_for_confirm:
                        cursor.execute("SELECT company_name FROM vendors WHERE id = ?", (vendor_id_for_confirm,))
                        vendor_row = cursor.fetchone()
                        if vendor_row: company_name_for_confirm = vendor_row['company_name']
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
        
        db_processed_vendor_id = None
        if 'vendorInfo' in new_order_payload and new_order_payload['vendorInfo']:
            db_processed_vendor_id = update_or_create_vendor(cursor, new_order_payload['vendorInfo'])
            if db_processed_vendor_id: new_order_payload['vendorInfo']['id'] = db_processed_vendor_id
            else: app.logger.error(f"Vendor processing failed. Payload: {new_order_payload.get('vendorInfo')}")
        
        if 'nameDrop' not in new_order_payload: new_order_payload['nameDrop'] = False
        
        subtotal_cents = sum(item.get('quantity',0) * item.get('price',0) for item in new_order_payload.get('lineItems',[]))
        name_drop_surcharge_cents = sum(item.get('quantity',0) * 100 for item in new_order_payload.get('lineItems',[]) if new_order_payload.get('nameDrop',False) and item.get('type')=='cross')
        
        estimated_shipping_cost_dollars = 0.0
        origin_zip = "63366"; destination_zip = None
        shipping_zip_code_raw = new_order_payload.get('shippingZipCode')
        if isinstance(shipping_zip_code_raw, str) and shipping_zip_code_raw.strip():
            if re.fullmatch(r'\d{5}', shipping_zip_code_raw.strip()): destination_zip = shipping_zip_code_raw.strip()
        
        if destination_zip:
            total_weight_oz = sum( (item.get('quantity',0)*5 if item.get('type')=='cross' else (item.get('quantity',0)*80 if item.get('type')=='display' else 0)) for item in new_order_payload.get('lineItems',[]))
            if total_weight_oz > 0:
                shipping_cost_calculated = calculate_shipping_cost_for_order(origin_zip, destination_zip, total_weight_oz / 16.0)
                if shipping_cost_calculated is not None: estimated_shipping_cost_dollars = round(float(shipping_cost_calculated), 2)
        
        new_order_payload['estimatedShipping'] = estimated_shipping_cost_dollars
        estimated_shipping_cents = int(round(estimated_shipping_cost_dollars * 100))
        final_total_dollars = round((subtotal_cents + name_drop_surcharge_cents + estimated_shipping_cents) / 100.0, 2)
        new_order_payload['total'] = final_total_dollars

        if current_order_id_for_db_ops: 
            cursor.execute("UPDATE orders SET vendor_id=?, order_date=?, status=?, notes=?, estimated_shipping_date=?, shipping_zip_code=?, estimated_shipping_cost=?, scent_option=?, name_drop=?, signature_data_url=?, total_amount=?, updated_at=CURRENT_TIMESTAMP WHERE order_id=?",
                           (db_processed_vendor_id, new_order_payload.get('date', datetime.now(timezone.utc).isoformat()+"Z"), new_order_payload.get('status','Draft'), new_order_payload.get('notes'), new_order_payload.get('estimatedShippingDate'), new_order_payload.get('shippingZipCode'), estimated_shipping_cost_dollars, new_order_payload.get('scentOption'), 1 if new_order_payload.get('nameDrop') else 0, new_order_payload.get('signatureDataUrl'), final_total_dollars, current_order_id_for_db_ops))
            cursor.execute("DELETE FROM order_line_items WHERE order_id = ?", (current_order_id_for_db_ops,))
            cursor.execute("DELETE FROM order_status_history WHERE order_id = ?", (current_order_id_for_db_ops,))
        else: 
            # Generate timestamp-based ID to match existing pattern
            timestamp_ms = int(time.time() * 1000)
            current_order_id_for_db_ops = f"PO-{timestamp_ms}"
            new_order_payload['id'] = current_order_id_for_db_ops 
            cursor.execute("INSERT INTO orders (order_id, vendor_id, order_date, status, notes, estimated_shipping_date, shipping_zip_code, estimated_shipping_cost, scent_option, name_drop, signature_data_url, total_amount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (current_order_id_for_db_ops, db_processed_vendor_id, new_order_payload.get('date', datetime.now(timezone.utc).isoformat()+"Z"), new_order_payload.get('status','Draft'), new_order_payload.get('notes'), new_order_payload.get('estimatedShippingDate'), new_order_payload.get('shippingZipCode'), estimated_shipping_cost_dollars, new_order_payload.get('scentOption'), 1 if new_order_payload.get('nameDrop') else 0, new_order_payload.get('signatureDataUrl'), final_total_dollars))
        
        processed_order_id = current_order_id_for_db_ops 
        app.logger.info(f"DB-OP: processed_order_id is now set to: '{processed_order_id}' before line item processing.")

        for li in new_order_payload.get('lineItems',[]):
            cursor.execute("SELECT item_code FROM items WHERE item_code = ?", (li.get('item'),))
            if not cursor.fetchone(): continue
            cursor.execute("INSERT INTO order_line_items (order_id, item_code, package_code, quantity, price_per_unit_cents, style_chosen, item_type) VALUES (?,?,?,?,?,?,?)",
                           (processed_order_id, li.get('item'), li.get('packageCode'), li.get('quantity'), li.get('price'), li.get('style'), li.get('type')))
        for hist in new_order_payload.get('statusHistory',[]):
            cursor.execute("INSERT INTO order_status_history (order_id, status, status_date) VALUES (?,?,?)", (processed_order_id, hist.get('status'), hist.get('date')))
        if not any(h['status'] == new_order_payload.get('status') for h in new_order_payload.get('statusHistory',[])):
            cursor.execute("INSERT INTO order_status_history (order_id, status, status_date) VALUES (?,?,?)", (processed_order_id, new_order_payload.get('status'), datetime.now(timezone.utc).isoformat()+"Z"))

        conn_main.commit()
        app.logger.info(f"Order {processed_order_id} committed successfully.")

        # The re-fetch was causing issues with SQLite's WAL mode.
        # Instead, we'll construct the response from the processed payload,
        # which has already been updated with the new ID and calculated fields.
        if 'id' in new_order_payload:
            new_order_payload['id'] = new_order_payload.pop('id')
        
        # Ensure the status history in the payload is up-to-date for the response
        current_status = new_order_payload.get('status', 'Draft')
        status_history = new_order_payload.get('statusHistory', [])
        if not any(h['status'] == current_status for h in status_history):
            status_history.append({
                'status': current_status,
                'date': datetime.now(timezone.utc).isoformat() + "Z"
            })
        new_order_payload['statusHistory'] = status_history

        # The payload is now the source of truth for the response.
        final_order_response = new_order_payload
        
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

# ... (rest of the API endpoints: /api/items, /api/vendors, etc. as they were in the 1:28:04 AM version) ...
@app.route('/api/items', methods=['GET'])
def get_items():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT item_code, name, type, price_cents, weight_oz FROM items ORDER BY name COLLATE NOCASE ASC")
    items_from_db = cursor.fetchall()
    items_list = []
    for item_row in items_from_db:
        item_dict = dict(item_row)
        cursor.execute("SELECT s.style_name FROM styles s JOIN item_styles ist ON s.id = ist.style_id WHERE ist.item_code = ? ORDER BY s.style_name COLLATE NOCASE ASC", (item_dict['item_code'],))
        item_dict['styles'] = [sr['style_name'] for sr in cursor.fetchall()]
        item_dict['id'] = item_dict['item_code']
        item_dict['price'] = item_dict['price_cents']
        items_list.append(item_dict)
    conn.close()
    return jsonify(items_list)

@app.route('/api/items', methods=['POST'])
def add_item():
    payload = request.json
    if not payload: return jsonify({"message":"Request must be JSON"}),400
    item_code, name = payload.get('item_code'), payload.get('name')
    if not item_code or not name: return jsonify({"message":"Missing item_code or name"}),400
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT item_code FROM items WHERE item_code = ?",(item_code,))
    if cursor.fetchone(): conn.close(); return jsonify({"message":f"Item {item_code} exists."}),409
    try: price_cents = int(round(float(payload.get('price',0.0))*100))
    except ValueError: conn.close(); return jsonify({"message":"Invalid price."}),400
    item_type, weight_oz = payload.get("type","other"), payload.get("weight_oz")
    try:
        cursor.execute("INSERT INTO items (item_code,name,type,price_cents,weight_oz) VALUES (?,?,?,?,?)",(item_code,name,item_type,price_cents,weight_oz))
        for style_name in payload.get("styles",[]):
            if not style_name: continue
            cursor.execute("INSERT OR IGNORE INTO styles (style_name) VALUES (?)",(style_name,))
            cursor.execute("SELECT id FROM styles WHERE style_name=?",(style_name,))
            style_row = cursor.fetchone()
            if style_row: cursor.execute("INSERT OR IGNORE INTO item_styles (item_code,style_id) VALUES (?,?)",(item_code,style_row['id']))
        conn.commit()
        cursor.execute("SELECT item_code,name,type,price_cents,weight_oz FROM items WHERE item_code=?",(item_code,))
        created_item=dict(cursor.fetchone())
        cursor.execute("SELECT s.style_name FROM styles s JOIN item_styles ist ON s.id=ist.style_id WHERE ist.item_code=? ORDER BY s.style_name",(item_code,))
        created_item['styles']=[sr['style_name'] for sr in cursor.fetchall()]; created_item['id']=created_item['item_code']; created_item['price']=created_item['price_cents']
        return jsonify({"message":"Item added.","item":created_item}),201
    except sqlite3.Error as e: conn.rollback(); app.logger.error(f"DB err add item {item_code}:{e}"); return jsonify({"message":"DB error."}),500
    finally: conn.close()

@app.route('/api/items/<string:item_code_url>', methods=['PUT'])
def update_item(item_code_url):
    payload=request.json
    if not payload: return jsonify({"message":"Request must be JSON"}),400
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT item_code FROM items WHERE item_code=?",(item_code_url,))
    if not cursor.fetchone(): conn.close(); return jsonify({"message":"Item not found."}),404
    new_code=payload.get('item_code',item_code_url).strip(); name,item_type=payload.get("name"),payload.get("type")
    price_str,weight_oz,styles_payload=payload.get('price'),payload.get("weight_oz"),payload.get("styles",[])
    if new_code!=item_code_url:
        cursor.execute("SELECT item_code FROM items WHERE item_code=?",(new_code,))
        if cursor.fetchone(): conn.close(); return jsonify({"message":f"Item code {new_code} exists."}),409
    price_cents=None
    if price_str is not None:
        try: price_cents=int(round(float(price_str)*100))
        except ValueError: conn.close(); return jsonify({"message":"Invalid price."}),400
    try:
        updates,vals=[],[]
        if name is not None: updates.append("name=?"); vals.append(name)
        if item_type is not None: updates.append("type=?"); vals.append(item_type)
        if price_cents is not None: updates.append("price_cents=?"); vals.append(price_cents)
        if 'weight_oz' in payload: updates.append("weight_oz=?"); vals.append(weight_oz if weight_oz!="" else None)
        
        current_code_for_styles = item_code_url
        if new_code != item_code_url:
            orig_item = dict(cursor.execute("SELECT * FROM items WHERE item_code=?",(item_code_url,)).fetchone())
            cursor.execute("INSERT INTO items (item_code,name,type,price_cents,weight_oz) VALUES (?,?,?,?,?)",
                           (new_code, name or orig_item['name'], item_type or orig_item['type'], price_cents if price_cents is not None else orig_item['price_cents'], weight_oz if 'weight_oz' in payload else orig_item['weight_oz']))
            for sr in cursor.execute("SELECT style_id FROM item_styles WHERE item_code=?",(item_code_url,)).fetchall():
                cursor.execute("INSERT OR IGNORE INTO item_styles (item_code,style_id) VALUES (?,?)",(new_code,sr['style_id']))
            cursor.execute("DELETE FROM items WHERE item_code=?",(item_code_url,))
            current_code_for_styles = new_code
        elif updates:
            cursor.execute(f"UPDATE items SET {','.join(updates)},updated_at=CURRENT_TIMESTAMP WHERE item_code=?",tuple(vals+[item_code_url]))

        cursor.execute("DELETE FROM item_styles WHERE item_code=?",(current_code_for_styles,))
        if isinstance(styles_payload,list):
            for sn in styles_payload:
                if not sn: continue
                cursor.execute("INSERT OR IGNORE INTO styles (style_name) VALUES (?)",(sn,))
                sr=cursor.execute("SELECT id FROM styles WHERE style_name=?",(sn,)).fetchone()
                if sr:
                    cursor.execute("INSERT OR IGNORE INTO item_styles (item_code,style_id) VALUES (?,?)",(current_code_for_styles,sr['id']))
        conn.commit()
        cursor.execute("SELECT item_code,name,type,price_cents,weight_oz FROM items WHERE item_code=?",(current_code_for_styles,))
        updated_item=dict(cursor.fetchone())
        cursor.execute("SELECT s.style_name FROM styles s JOIN item_styles ist ON s.id=ist.style_id WHERE ist.item_code=? ORDER BY s.style_name",(current_code_for_styles,))
        updated_item['styles']=[sr['style_name'] for sr in cursor.fetchall()]; updated_item['id']=updated_item['item_code']; updated_item['price']=updated_item['price_cents']
        return jsonify({"message":"Item updated.","item":updated_item}),200
    except sqlite3.Error as e: conn.rollback(); app.logger.error(f"DB err update item {item_code_url}:{e}"); return jsonify({"message":"DB error."}),500
    finally: conn.close()

@app.route('/api/items/<string:item_code_url>', methods=['DELETE'])
def delete_item(item_code_url):
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("DELETE FROM items WHERE item_code=?",(item_code_url,))
        conn.commit()
        if cursor.rowcount>0: return jsonify({"message":"Item deleted."}),200
        else: return jsonify({"message":"Item not found."}),404
    except sqlite3.Error as e: conn.rollback(); app.logger.error(f"DB err delete item {item_code_url}:{e}"); return jsonify({"message":"DB error."}),500
    finally: conn.close()

@app.route('/api/vendors', methods=['GET'])
def get_vendors():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, company_name, contact_name, email, phone, billing_address, shipping_address FROM vendors ORDER BY company_name COLLATE NOCASE ASC")
    vendors_list = [{"id":r["id"],"companyName":r["company_name"],"contactName":r["contact_name"],"email":r["email"],"phone":r["phone"],"billingAddress":r["billing_address"],"shippingAddress":r["shipping_address"]} for r in cursor.fetchall()]
    conn.close(); return jsonify(vendors_list)

@app.route('/api/vendors/<string:vendor_id>', methods=['PUT'])
def api_update_vendor(vendor_id):
    payload=request.json
    if not payload: return jsonify({"message":"Missing data."}),400
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        updated_vendor=update_vendor_by_id(cursor,vendor_id,payload)
        if updated_vendor is None: conn.close(); return jsonify({"message":f"Vendor {vendor_id} not found."}),404
        conn.commit(); conn.close()
        return jsonify({"message":"Vendor updated.","vendor":updated_vendor}),200
    except sqlite3.Error as e: conn.rollback(); conn.close(); app.logger.error(f"DB err update vendor {vendor_id}:{e}"); return jsonify({"message":"DB error."}),500
    except Exception as e_g: conn.rollback(); conn.close(); app.logger.error(f"Global err update vendor {vendor_id}:{e_g}"); return jsonify({"message":"Unexpected error."}),500

@app.route('/api/vendors', methods=['POST'])
def api_create_vendor():
    payload=request.json
    if not payload or not payload.get("companyName"): return jsonify({"message":"Missing companyName."}),400
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        vendor_id=update_or_create_vendor(cursor,payload)
        if not vendor_id: conn.rollback(); conn.close(); return jsonify({"message":"Failed to process vendor."}),500
        cursor.execute("SELECT id,company_name,contact_name,email,phone,billing_address,shipping_address FROM vendors WHERE id=?",(vendor_id,))
        vendor_db=cursor.fetchone()
        if not vendor_db: conn.rollback(); conn.close(); app.logger.error(f"Vendor {vendor_id} processed but not retrieved."); return jsonify({"message":"Vendor processed but not retrieved."}),500
        conn.commit(); conn.close()
        return jsonify({"message":"Vendor processed.","vendor":dict(vendor_db)}),201
    except sqlite3.Error as e: conn.rollback(); conn.close(); app.logger.error(f"DB err create vendor:{e}"); return jsonify({"message":"DB error."}),500
    except Exception as e_g: conn.rollback(); conn.close(); app.logger.error(f"Global err create vendor:{e_g}"); return jsonify({"message":"Unexpected error."}),500

@app.route('/api/vendors/<string:vendor_id>', methods=['DELETE'])
def delete_vendor(vendor_id):
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("DELETE FROM vendors WHERE id=?",(vendor_id,))
        conn.commit()
        if cursor.rowcount>0: conn.close(); return jsonify({"message":"Vendor deleted."}),200
        else: conn.close(); return jsonify({"message":"Vendor not found."}),404
    except sqlite3.Error as e: conn.rollback(); conn.close(); app.logger.error(f"DB err delete vendor {vendor_id}:{e}"); return jsonify({"message":"DB error."}),500

@app.route('/api/calculate-shipping-estimate', methods=['POST'])
def calculate_shipping_estimate_endpoint():
    payload=request.json
    if not payload: return jsonify({"message":"Request must be JSON"}),400
    origin_zip="63366"; dest_zip_str=payload.get('shippingZipCode'); line_items=payload.get('lineItems',[])
    if not dest_zip_str or not isinstance(dest_zip_str,str) or not re.fullmatch(r'\d{5}',dest_zip_str.strip()): return jsonify({"message":"Valid 5-digit ZIP required."}),400
    dest_zip=dest_zip_str.strip(); total_weight_oz=0
    if not line_items: return jsonify({"estimatedShipping":0.0}),200
    for item in line_items:
        qty,item_type=item.get('quantity',0),item.get('type')
        if item_type=='cross': total_weight_oz+=qty*5
        elif item_type=='display': total_weight_oz+=qty*80
    if total_weight_oz<=0: return jsonify({"estimatedShipping":0.0}),200
    cost=calculate_shipping_cost_for_order(origin_zip,dest_zip,total_weight_oz/16.0)
    if cost is not None: return jsonify({"estimatedShipping":round(cost,2)}),200
    else: return jsonify({"estimatedShipping":0.0,"message":"Could not calculate shipping."}),200

@app.route('/api/packages', methods=['GET'])
def get_packages():
    conn=get_db_connection(); cursor=conn.cursor()
    cursor.execute("SELECT package_id,name,type FROM packages ORDER BY name COLLATE NOCASE ASC")
    pkgs_db=cursor.fetchall(); transformed_pkgs={}
    for pkg_row in pkgs_db:
        pkg_dict=dict(pkg_row)
        cursor.execute("SELECT item_code,quantity FROM package_items WHERE package_id=?",(pkg_dict['package_id'],))
        contents_db=cursor.fetchall()
        transformed_pkgs[str(pkg_dict['package_id'])]={'name':pkg_dict['name'],'id_val':pkg_dict['package_id'],'type':(pkg_dict['type'] or 'package').lower(),'contents':[{'itemCode':str(cr['item_code']),'quantity':cr['quantity']} for cr in contents_db]}
    conn.close(); return jsonify(transformed_pkgs)

@app.route('/api/packages', methods=['POST'])
def add_package():
    payload=request.json
    if not payload: return jsonify({"message":"Request must be JSON"}),400
    pkg_name,pkg_id_val=payload.get('name'),payload.get('id_val')
    pkg_type,contents_raw=payload.get('type','package'),payload.get('contents_raw_text',"")
    if not pkg_name or pkg_id_val is None: return jsonify({"message":"Name and ID required."}),400
    try: pkg_id=int(pkg_id_val)
    except ValueError: return jsonify({"message":"ID must be number."}),400
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("SELECT package_id FROM packages WHERE name=? OR package_id=?",(pkg_name,pkg_id))
        if cursor.fetchone(): conn.close(); return jsonify({"message":f"Package {pkg_name} or ID {pkg_id} exists."}),409
        cursor.execute("INSERT INTO packages (package_id,name,type) VALUES (?,?,?)",(pkg_id,pkg_name,pkg_type))
        parsed_contents_resp=[]
        if contents_raw:
            for line in contents_raw.strip().split('\n'):
                parts=line.split(':')
                if len(parts)==2:
                    item_code,qty_str=parts[0].strip(),parts[1].strip()
                    try:
                        qty=int(qty_str)
                        if qty>0:
                            cursor.execute("SELECT item_code FROM items WHERE item_code=?",(item_code,))
                            if cursor.fetchone():
                                cursor.execute("INSERT INTO package_items (package_id,item_code,quantity) VALUES (?,?,?)",(pkg_id,item_code,qty))
                                parsed_contents_resp.append({'itemCode':item_code,'quantity':qty})
                            else: app.logger.warning(f"Item {item_code} not found for pkg {pkg_id}.")
                    except ValueError: conn.rollback();conn.close();return jsonify({"message":f"Invalid qty for {item_code}."}),400
                elif line.strip(): conn.rollback();conn.close();return jsonify({"message":f"Malformed line: {line}."}),400
        conn.commit()
        return_data={str(pkg_id):{'name':pkg_name,'id_val':pkg_id,'type':pkg_type.lower(),'contents':parsed_contents_resp}}
        return jsonify({"message":"Package added.","package":return_data}),201
    except sqlite3.Error as e: conn.rollback();conn.close();app.logger.error(f"DB err add pkg:{e}"); return jsonify({"message":"DB error."}),500
    finally: conn.close()

@app.route('/api/packages/<string:package_id_str>', methods=['PUT'])
def update_package(package_id_str):
    payload=request.json
    if not payload: return jsonify({"message":"Request must be JSON"}),400
    try: target_pkg_id=int(package_id_str)
    except ValueError: return jsonify({"message":"Invalid pkg ID in URL."}),400
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("SELECT name,type FROM packages WHERE package_id=?",(target_pkg_id,))
        curr_pkg=cursor.fetchone()
        if not curr_pkg: conn.close(); return jsonify({"message":f"Pkg ID {target_pkg_id} not found."}),404
        new_name,new_id_val_str=payload.get('name',curr_pkg['name']),payload.get('id_val')
        new_type,contents_raw=payload.get('type',curr_pkg['type']),payload.get('contents_raw_text')
        new_id=target_pkg_id
        if new_id_val_str is not None:
            try: new_id=int(new_id_val_str)
            except ValueError: conn.close(); return jsonify({"message":"New Pkg ID must be number."}),400
        if new_name!=curr_pkg['name']:
            cursor.execute("SELECT package_id FROM packages WHERE name=? AND package_id!=?",(new_name,target_pkg_id))
            if cursor.fetchone(): conn.close(); return jsonify({"message":f"Pkg name '{new_name}' exists."}),409
        if new_id!=target_pkg_id:
            cursor.execute("SELECT package_id FROM packages WHERE package_id=?",(new_id,))
            if cursor.fetchone(): conn.close(); return jsonify({"message":f"Pkg ID '{new_id}' exists."}),409
            cursor.execute("UPDATE packages SET package_id=?,name=?,type=?,updated_at=CURRENT_TIMESTAMP WHERE package_id=?",(new_id,new_name,new_type,target_pkg_id))
        else:
            cursor.execute("UPDATE packages SET name=?,type=?,updated_at=CURRENT_TIMESTAMP WHERE package_id=?",(new_name,new_type,target_pkg_id))
        
        final_id_for_contents=new_id; parsed_contents_resp=[]
        if contents_raw is not None:
            cursor.execute("DELETE FROM package_items WHERE package_id=?",(final_id_for_contents,))
            if contents_raw:
                for line in contents_raw.strip().split('\n'):
                    parts=line.split(':')
                    if len(parts)==2:
                        item_code,qty_str=parts[0].strip(),parts[1].strip()
                        try:
                            qty=int(qty_str)
                            if qty>0:
                                cursor.execute("SELECT item_code FROM items WHERE item_code=?",(item_code,))
                                if cursor.fetchone():
                                    cursor.execute("INSERT INTO package_items (package_id,item_code,quantity) VALUES (?,?,?)",(final_id_for_contents,item_code,qty))
                                    parsed_contents_resp.append({'itemCode':item_code,'quantity':qty})
                                else: app.logger.warning(f"Item {item_code} not found for pkg {final_id_for_contents}.")
                        except ValueError: conn.rollback();conn.close();return jsonify({"message":f"Invalid qty for {item_code}."}),400
                    elif line.strip(): conn.rollback();conn.close();return jsonify({"message":f"Malformed line: {line}."}),400
        else:
            cursor.execute("SELECT item_code,quantity FROM package_items WHERE package_id=?",(final_id_for_contents,))
            parsed_contents_resp=[{'itemCode':str(r['item_code']),'quantity':r['quantity']} for r in cursor.fetchall()]
        conn.commit()
        return_data={str(final_id_for_contents):{'name':new_name,'id_val':final_id_for_contents,'type':new_type.lower(),'contents':parsed_contents_resp}}
        return jsonify({"message":"Package updated.","package":return_data}),200
    except sqlite3.Error as e: conn.rollback();conn.close();app.logger.error(f"DB err update pkg {package_id_str}:{e}"); return jsonify({"message":"DB error."}),500
    finally: conn.close()

@app.route('/api/packages/<string:package_id_str>', methods=['DELETE'])
def delete_package(package_id_str):
    try: target_pkg_id=int(package_id_str)
    except ValueError: return jsonify({"message":"Invalid pkg ID."}),400
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("DELETE FROM packages WHERE package_id=?",(target_pkg_id,))
        conn.commit()
        if cursor.rowcount>0: return jsonify({"message":"Package deleted."}),200
        else: return jsonify({"message":"Package not found."}),404
    except sqlite3.Error as e: conn.rollback();app.logger.error(f"DB err delete pkg {target_pkg_id}:{e}"); return jsonify({"message":"DB error."}),500
    finally: conn.close()

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

    from_email = os.environ.get('EMAIL_USER')
    from_pass = os.environ.get('EMAIL_PASS')
    if not from_email or not from_pass:
        app.logger.error("Email credentials are not configured on the server.")
        return jsonify({"message": "Email service is not configured."}), 500

    attachment_paths_to_delete = []
    try:
        order_id_log = order_data.get('order_id', 'N/A')
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
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
        server.sendmail(from_email, to_email, msg.as_string())
        server.close()
        
        app.logger.info(f"Email with {len(attachment_paths_to_delete)} attachment(s) sent for order {order_id_log}")
        
        order_id_update, updated_order_resp = order_data.get('order_id'), None
        if order_id_update:
            conn_upd = get_db_connection()
            cur_upd = conn_upd.cursor()
            try:
                utc_now_iso = datetime.now(timezone.utc).isoformat() + "Z"
                cur_upd.execute("UPDATE orders SET status='Sent', updated_at=CURRENT_TIMESTAMP WHERE order_id=?", (order_id_update,))
                cur_upd.execute("INSERT INTO order_status_history (order_id, status, status_date) VALUES (?, ?, ?)", (order_id_update, 'Sent', utc_now_iso))
                conn_upd.commit()
                app.logger.info(f"Order {order_id_update} status updated to Sent.")
                
                cur_upd.execute("SELECT o.*, v.company_name as vendor_company_name, v.contact_name as vendor_contact_name, v.email as vendor_email, v.phone as vendor_phone, v.billing_address as vendor_billing_address, v.shipping_address as vendor_shipping_address FROM orders o LEFT JOIN vendors v ON o.vendor_id=v.id WHERE o.order_id=?", (order_id_update,))
                updated_row = cur_upd.fetchone()
                if updated_row:
                    updated_order_resp = dict(updated_row)
                    updated_order_resp['vendorInfo'] = {
                        "id": updated_order_resp.pop('vendor_id'), 
                        "companyName": updated_order_resp.pop('vendor_company_name') or "[Vendor Not Found]",
                        "contactName": updated_order_resp.pop('vendor_contact_name'),
                        "email": updated_order_resp.pop('vendor_email'),
                        "phone": updated_order_resp.pop('vendor_phone'),
                        "billingAddress": updated_order_resp.pop('vendor_billing_address'),
                        "shippingAddress": updated_order_resp.pop('vendor_shipping_address')
                    }
                    if not updated_order_resp['vendorInfo']['id']: 
                        updated_order_resp['vendorInfo'] = {"id": None, "companyName": "[Vendor Not Found]", "contactName": "", "email": "", "phone": "", "billingAddress": "", "shippingAddress": ""}
                    
                    cur_upd.execute("SELECT item_code, package_code, quantity, price_per_unit_cents, style_chosen, item_type FROM order_line_items WHERE order_id=?", (order_id_update,))
                    updated_order_resp['lineItems'] = [{'item': li['item_code'], 'packageCode': li['package_code'], 'price': li['price_per_unit_cents'], 'quantity': li['quantity'], 'style': li['style_chosen'], 'type': li['item_type']} for li in cur_upd.fetchall()]
                    cur_upd.execute("SELECT status, status_date FROM order_status_history WHERE order_id=? ORDER BY status_date ASC", (order_id_update,))
                    updated_order_resp['statusHistory'] = [{'status': h['status'], 'date': h['status_date']} for h in cur_upd.fetchall()]
                    updated_order_resp['date'] = updated_order_resp.pop('order_date')
                    updated_order_resp['total'] = updated_order_resp.pop('total_amount')
                    updated_order_resp['estimatedShipping'] = updated_order_resp.pop('estimated_shipping_cost')
                    updated_order_resp['nameDrop'] = True if updated_order_resp.pop('name_drop', 0) == 1 else False
                else:
                    app.logger.warning(f"Order {order_id_update} status updated, but failed to re-fetch.")
            except sqlite3.Error as e_upd:
                conn_upd.rollback()
                app.logger.error(f"DB error updating status for {order_id_update}: {e_upd}")
            finally:
                conn_upd.close()
        else:
            app.logger.warning("Order ID missing, cannot update status.")
        
        return jsonify({"message": "Email sent.", "order": updated_order_resp} if updated_order_resp else {"message": "Email sent."}), 200
    except Exception as e:
        app.logger.error(f"Failed to send email for order {order_data.get('id', 'N/A')}: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"message": f"Failed to send email: {str(e)}"}), 500
    finally:
        for path in attachment_paths_to_delete:
            if os.path.exists(path):
                try:
                    os.remove(path)
                    app.logger.info(f"Successfully deleted attachment: {os.path.basename(path)}")
                except Exception as e_del:
                    app.logger.error(f"Error deleting attachment {os.path.basename(path)}: {e_del}")

@app.route('/api/settings', methods=['GET'])
def get_settings():
    settings = read_json_file(SETTINGS_FILE)
    if not isinstance(settings, dict): 
        settings = {} 
    if not settings: 
        default_settings = {"company_name": "Your Company Name", "default_shipping_zip_code": "00000", "default_email_body": "Dear [vendorCompany],\n\nPlease find attached the purchase order [orderID] for your records.\n\nWe appreciate your business!\n\nThank you,\n[yourCompany]"}
        write_json_file(SETTINGS_FILE, default_settings)
        return jsonify(default_settings)
    if 'default_email_body' not in settings:
        settings['default_email_body'] = "Dear [vendorCompany],\n\nPlease find attached the purchase order [orderID] for your records.\n\nWe appreciate your business!\n\nThank you,\n[yourCompany]"
        write_json_file(SETTINGS_FILE, settings)
    return jsonify(settings)

@app.route('/api/settings', methods=['POST'])
def update_settings():
    new_settings = request.json
    if not new_settings: return jsonify({"message":"Request must be JSON"}),400
    write_json_file(SETTINGS_FILE, new_settings)
    return jsonify({"message":"Settings updated."}),200

@app.route('/manage/customers')
def manage_customers_page(): return render_template('manage_customers.html')
@app.route('/manage/items')
def manage_items_page(): return render_template('manage_items.html')
@app.route('/manage/packages')
def manage_packages_page(): return render_template('manage_packages.html')
@app.route('/settings')
def settings_page(): return render_template('settings.html')

@app.route('/favicon.ico')
def favicon(): return send_from_directory(os.path.join(app.root_path,'assets'),'favicon.ico',mimetype='image/vnd.microsoft.icon')
@app.route('/assets/<path:filename>')
def serve_assets(filename): return send_from_directory(os.path.join(app.root_path,'assets'),filename)
@app.route('/')
def home(): return render_template('index.html')

@app.route('/shutdown', methods=['POST'])
def shutdown(): Timer(0.1,lambda:os._exit(0)).start(); return "Shutdown initiated.",200

def open_browser(): webbrowser.open_new("http://127.0.0.1:5000/")
def is_port_in_use(port):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        try: s.bind(("127.0.0.1",port))
        except socket.error as e: return e.errno==10048 
        return False

if __name__=='__main__':
    PORT=5000
    if is_port_in_use(PORT): print(f"Port {PORT} in use."); open_browser(); sys.exit(0)
    else: print(f"Port {PORT} free."); Timer(1,open_browser).start(); app.run(port=PORT,debug=False)
