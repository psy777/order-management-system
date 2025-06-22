import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';
import { Statement } from 'better-sqlite3';

type Order = {
  order_id: string;
  vendor_id: string | null;
  vendor_company_name: string | null;
  vendor_contact_name: string | null;
  vendor_email: string | null;
  vendor_phone: string | null;
  vendor_billing_address: string | null;
  vendor_billing_city: string | null;
  vendor_billing_state: string | null;
  vendor_billing_zip_code: string | null;
  vendor_shipping_address: string | null;
  vendor_shipping_city: string | null;
  vendor_shipping_state: string | null;
  vendor_shipping_zip_code: string | null;
  order_date: string;
  total_amount: number;
  estimated_shipping_cost: number;
  name_drop: number;
  status: string;
  notes: string;
  estimatedShippingDate: string;
  shippingAddress: string;
  shippingCity: string;
  shippingState: string;
  shippingZipCode: string;
  scentOption: string;
  signatureDataUrl: string;
};

type LineItem = {
  item_code: string;
  package_code: string;
  price_per_unit_cents: number;
  quantity: number;
  style_chosen: string;
  item_type: string;
};

type StatusHistory = {
  status: string;
  status_date: string;
};

import { getAuth } from '@clerk/nextjs/server';

const getOrders = (req: NextApiRequest, res: NextApiResponse) => {
  const { userId } = getAuth(req);
  if (!userId) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  try {
    const ordersQuery = db.prepare(`
      SELECT 
        o.*, 
        v.company_name as vendor_company_name, 
        v.contact_name as vendor_contact_name, 
        v.email as vendor_email, 
        v.phone as vendor_phone, 
        v.billing_address as vendor_billing_address, 
        v.billing_city as vendor_billing_city, 
        v.billing_state as vendor_billing_state, 
        v.billing_zip_code as vendor_billing_zip_code, 
        v.shipping_address as vendor_shipping_address, 
        v.shipping_city as vendor_shipping_city, 
        v.shipping_state as vendor_shipping_state, 
        v.shipping_zip_code as vendor_shipping_zip_code 
      FROM orders o 
      LEFT JOIN vendors v ON o.vendor_id = v.id 
      WHERE o.status != 'Deleted' AND o.user_id = ?
      ORDER BY o.order_date DESC, o.order_id DESC
    `);

    const lineItemsQuery: Statement = db.prepare("SELECT item_code, package_code, quantity, price_per_unit_cents, style_chosen, item_type FROM order_line_items WHERE order_id = ?");
    const statusHistoryQuery: Statement = db.prepare("SELECT status, status_date FROM order_status_history WHERE order_id = ? ORDER BY status_date ASC");

    const ordersFromDb: Order[] = ordersQuery.all(userId) as Order[];
    
    const active_orders_response = ordersFromDb.map(order_row => {
      const lineItemsFromDb = lineItemsQuery.all(order_row.order_id) as LineItem[];
      const statusHistoryFromDb = statusHistoryQuery.all(order_row.order_id) as StatusHistory[];

      const order_dict = {
        ...order_row,
        vendorInfo: {
          id: order_row.vendor_id,
          companyName: order_row.vendor_company_name || "[Vendor Not Found]",
          contactName: order_row.vendor_contact_name,
          email: order_row.vendor_email,
          phone: order_row.vendor_phone,
          billingAddress: order_row.vendor_billing_address,
          billingCity: order_row.vendor_billing_city,
          billingState: order_row.vendor_billing_state,
          billingZipCode: order_row.vendor_billing_zip_code,
          shippingAddress: order_row.vendor_shipping_address,
          shippingCity: order_row.vendor_shipping_city,
          shippingState: order_row.vendor_shipping_state,
          shippingZipCode: order_row.vendor_shipping_zip_code
        },
        lineItems: lineItemsFromDb.map(li => ({
          item: li.item_code,
          packageCode: li.package_code,
          price: li.price_per_unit_cents,
          quantity: li.quantity,
          style: li.style_chosen,
          type: li.item_type
        })),
        statusHistory: statusHistoryFromDb.map(h => ({
          status: h.status,
          date: h.status_date
        })),
        id: order_row.order_id,
        date: order_row.order_date,
        total: order_row.total_amount,
        estimatedShipping: order_row.estimated_shipping_cost,
        nameDrop: order_row.name_drop === 1
      };

      // remove redundant fields
      delete (order_dict as any).order_id;
      delete (order_dict as any).order_date;
      delete (order_dict as any).total_amount;
      delete (order_dict as any).estimated_shipping_cost;
      delete (order_dict as any).name_drop;
      delete (order_dict as any).vendor_id;
      delete (order_dict as any).vendor_company_name;
      delete (order_dict as any).vendor_contact_name;
      delete (order_dict as any).vendor_email;
      delete (order_dict as any).vendor_phone;
      delete (order_dict as any).vendor_billing_address;
      delete (order_dict as any).vendor_billing_city;
      delete (order_dict as any).vendor_billing_state;
      delete (order_dict as any).vendor_billing_zip_code;
      delete (order_dict as any).vendor_shipping_address;
      delete (order_dict as any).vendor_shipping_city;
      delete (order_dict as any).vendor_shipping_state;
      delete (order_dict as any).vendor_shipping_zip_code;

      return order_dict;
    });

    res.status(200).json(active_orders_response);
  } catch (error) {
    console.error("DB error getting orders:", error);
    res.status(500).json({ status: "error", message: "Failed to retrieve orders" });
  }
};

const postOrder = (req: NextApiRequest, res: NextApiResponse) => {
  const { userId } = getAuth(req);
  if (!userId) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  const new_order_payload = req.body;
  if (!new_order_payload) {
    return res.status(400).json({ status: "error", message: "Request must be JSON" });
  }

  const processed_order_id = new_order_payload.id || 'NEW_ORDER_PENDING_ID';

  const transaction = db.transaction(() => {
    const order_id_from_payload = new_order_payload.id;
    
    let existing_order_row: { status: string; vendor_id: string | null } | undefined;
    if (order_id_from_payload) {
      const stmt = db.prepare("SELECT status, vendor_id FROM orders WHERE order_id = ?");
      existing_order_row = stmt.get(order_id_from_payload) as { status: string; vendor_id: string | null } | undefined;
    }

    let current_order_id_for_db_ops = order_id_from_payload;
    
    let db_processed_vendor_id: string | null | undefined = null;
    if (new_order_payload.vendorInfo) {
      db_processed_vendor_id = update_or_create_vendor(new_order_payload.vendorInfo);
      if (db_processed_vendor_id) new_order_payload.vendorInfo.id = db_processed_vendor_id;
    }

    if (!('nameDrop' in new_order_payload)) new_order_payload.nameDrop = false;

    const subtotal_cents = (new_order_payload.lineItems || []).reduce((acc: number, item: any) => acc + (item.quantity || 0) * (item.price || 0), 0);
    const name_drop_surcharge_cents = new_order_payload.nameDrop ? (new_order_payload.lineItems || []).filter((item: any) => item.type === 'cross').reduce((acc: number, item: any) => acc + (item.quantity || 0) * 100, 0) : 0;
    
    let estimated_shipping_cost_dollars = new_order_payload.estimatedShipping || 0.0;
    if (typeof estimated_shipping_cost_dollars !== 'number') {
      estimated_shipping_cost_dollars = 0.0;
    }
    const estimated_shipping_cents = Math.round(estimated_shipping_cost_dollars * 100);
    const final_total_dollars = parseFloat(((subtotal_cents + name_drop_surcharge_cents + estimated_shipping_cents) / 100.0).toFixed(2));
    new_order_payload.total = final_total_dollars;

    if (existing_order_row) {
      // This is an UPDATE
      const updateStmt = db.prepare("UPDATE orders SET vendor_id=?, order_date=?, status=?, notes=?, estimated_shipping_date=?, shipping_address=?, shipping_city=?, shipping_state=?, shipping_zip_code=?, estimated_shipping_cost=?, scent_option=?, name_drop=?, signature_data_url=?, total_amount=?, updated_at=CURRENT_TIMESTAMP WHERE order_id=?");
      updateStmt.run(db_processed_vendor_id, new_order_payload.date || new Date().toISOString(), new_order_payload.status || 'Draft', new_order_payload.notes, new_order_payload.estimatedShippingDate, new_order_payload.shippingAddress, new_order_payload.shippingCity, new_order_payload.shippingState, new_order_payload.shippingZipCode, estimated_shipping_cost_dollars, new_order_payload.scentOption, new_order_payload.nameDrop ? 1 : 0, new_order_payload.signatureDataUrl, final_total_dollars, current_order_id_for_db_ops);
      db.prepare("DELETE FROM order_line_items WHERE order_id = ?").run(current_order_id_for_db_ops);
      db.prepare("DELETE FROM order_status_history WHERE order_id = ?").run(current_order_id_for_db_ops);
    } else {
      // This is an INSERT
      if (!current_order_id_for_db_ops) {
        current_order_id_for_db_ops = `PO-${Date.now()}`;
        new_order_payload.id = current_order_id_for_db_ops;
      }
      const insertStmt = db.prepare("INSERT INTO orders (order_id, vendor_id, order_date, status, notes, estimated_shipping_date, shipping_address, shipping_city, shipping_state, shipping_zip_code, estimated_shipping_cost, scent_option, name_drop, signature_data_url, total_amount, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
      insertStmt.run(current_order_id_for_db_ops, db_processed_vendor_id, new_order_payload.date || new Date().toISOString(), new_order_payload.status || 'Draft', new_order_payload.notes, new_order_payload.estimatedShippingDate, new_order_payload.shippingAddress, new_order_payload.shippingCity, new_order_payload.shippingState, new_order_payload.shippingZipCode, estimated_shipping_cost_dollars, new_order_payload.scentOption, new_order_payload.nameDrop ? 1 : 0, new_order_payload.signatureDataUrl, final_total_dollars, userId);
    }

    const insertLineItemStmt = db.prepare("INSERT INTO order_line_items (order_id, item_code, package_code, quantity, price_per_unit_cents, style_chosen, item_type) VALUES (?,?,?,?,?,?,?)");
    const checkItemExistsStmt = db.prepare("SELECT item_code FROM items WHERE item_code = ?");
    for (const li of new_order_payload.lineItems || []) {
      if (checkItemExistsStmt.get(li.item)) {
        insertLineItemStmt.run(current_order_id_for_db_ops, li.item, li.packageCode, li.quantity, li.price, li.style, li.type);
      }
    }

    const insertStatusHistoryStmt = db.prepare("INSERT INTO order_status_history (order_id, status, status_date) VALUES (?,?,?)");
    for (const hist of new_order_payload.statusHistory || []) {
      insertStatusHistoryStmt.run(current_order_id_for_db_ops, hist.status, hist.date);
    }
    if (!(new_order_payload.statusHistory || []).some((h: any) => h.status === new_order_payload.status)) {
      insertStatusHistoryStmt.run(current_order_id_for_db_ops, new_order_payload.status, new Date().toISOString());
    }
    
    return new_order_payload;
  });

  try {
    const final_order_response = transaction();
    return res.status(200).json({
      status: "success",
      message: "Order saved successfully.",
      order: final_order_response
    });
  } catch (error: any) {
    console.error(`DB error in transaction for order '${processed_order_id}':`, error);
    return res.status(500).json({ status: "error", message: `DB error: ${error.message}` });
  }
};

const update_or_create_vendor = (vendor_info_payload: any) => {
  if (!vendor_info_payload || !vendor_info_payload.companyName) {
    return vendor_info_payload ? vendor_info_payload.id : null;
  }

  const {
    id: provided_id,
    companyName: company_name,
    contactName: contact_name = "",
    email = "",
    phone = "",
    billingAddress: billing_address = "",
    billingCity: billing_city = "",
    billingState: billing_state = "",
    billingZipCode: billing_zip_code = "",
    shippingAddress: shipping_address = "",
    shippingCity: shipping_city = "",
    shippingState: shipping_state = "",
    shippingZipCode: shipping_zip_code = "",
  } = vendor_info_payload;

  let final_vendor_id = provided_id;
  if (provided_id) {
    const updateStmt = db.prepare("UPDATE vendors SET company_name = ?, contact_name = ?, email = ?, phone = ?, billing_address = ?, billing_city = ?, billing_state = ?, billing_zip_code = ?, shipping_address = ?, shipping_city = ?, shipping_state = ?, shipping_zip_code = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?");
    const result = updateStmt.run(company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, provided_id);
    if (result.changes === 0) {
      final_vendor_id = Math.random().toString(36).substr(2, 9); // simple uuid
      const insertStmt = db.prepare("INSERT INTO vendors (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
      insertStmt.run(final_vendor_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code);
    }
  } else {
    final_vendor_id = Math.random().toString(36).substr(2, 9); // simple uuid
    const insertStmt = db.prepare("INSERT INTO vendors (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
    insertStmt.run(final_vendor_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code);
  }
  return final_vendor_id;
};

export default function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method === 'GET') {
    return getOrders(req, res);
  } else if (req.method === 'POST') {
    return postOrder(req, res);
  } else {
    res.setHeader('Allow', ['GET', 'POST']);
    res.status(405).end(`Method ${req.method} Not Allowed`);
  }
}
