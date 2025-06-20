import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';

const updateVendor = (req: NextApiRequest, res: NextApiResponse) => {
    const { vendor_id } = req.query;
    const payload = req.body;

    if (!payload) {
        return res.status(400).json({ message: "Missing data." });
    }

    try {
        const updated_vendor = update_vendor_by_id(vendor_id as string, payload);
        if (updated_vendor === null) {
            return res.status(404).json({ message: `Vendor ${vendor_id} not found.` });
        }
        res.status(200).json({ message: "Vendor updated.", vendor: updated_vendor });
    } catch (error) {
        console.error(`DB err update vendor ${vendor_id}:`, error);
        res.status(500).json({ message: "DB error." });
    }
};

const update_vendor_by_id = (vendor_id: string, vendor_data_payload: any) => {
    const field_mappings: { [key: string]: string } = {
        "companyName": "company_name", "contactName": "contact_name", "email": "email", "phone": "phone",
        "billingAddress": "billing_address", "billingCity": "billing_city", "billingState": "billing_state", "billingZipCode": "billing_zip_code",
        "shippingAddress": "shipping_address", "shippingCity": "shipping_city", "shippingState": "shipping_state", "shippingZipCode": "shipping_zip_code"
    };

    const fields_to_update: string[] = [];
    const values_to_update: any[] = [];

    for (const pk in field_mappings) {
        if (pk in vendor_data_payload) {
            fields_to_update.push(`${field_mappings[pk]} = ?`);
            values_to_update.push(vendor_data_payload[pk]);
        }
    }

    if (fields_to_update.length === 0) {
        const stmt = db.prepare("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code FROM vendors WHERE id = ?");
        const cv = stmt.get(vendor_id);
        return cv || null;
    }

    const sql_query = `UPDATE vendors SET ${fields_to_update.join(', ')}, updated_at = CURRENT_TIMESTAMP WHERE id = ?`;
    values_to_update.push(vendor_id);

    const stmt = db.prepare(sql_query);
    const result = stmt.run(...values_to_update);

    if (result.changes === 0) {
        return null;
    }

    const selectStmt = db.prepare("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code FROM vendors WHERE id = ?");
    const uvd = selectStmt.get(vendor_id);
    return uvd || null;
};

const deleteVendor = (req: NextApiRequest, res: NextApiResponse) => {
    const { vendor_id } = req.query;

    try {
        const result = db.prepare("DELETE FROM vendors WHERE id = ?").run(vendor_id);
        if (result.changes > 0) {
            res.status(200).json({ message: "Vendor deleted." });
        } else {
            res.status(404).json({ message: "Vendor not found." });
        }
    } catch (error) {
        console.error(`DB err delete vendor ${vendor_id}:`, error);
        res.status(500).json({ message: "DB error." });
    }
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'PUT') {
        return updateVendor(req, res);
    } else if (req.method === 'DELETE') {
        return deleteVendor(req, res);
    } else {
        res.setHeader('Allow', ['PUT', 'DELETE']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
