import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';
import { getAuth } from '@clerk/nextjs/server';

const getVendors = (req: NextApiRequest, res: NextApiResponse) => {
    const { userId } = getAuth(req);
    if (!userId) {
        return res.status(401).json({ error: "Unauthorized" });
    }

    try {
        const vendorsQuery = db.prepare("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code FROM vendors WHERE user_id = ? ORDER BY company_name COLLATE NOCASE ASC");
        const vendors_list = vendorsQuery.all(userId).map((v: any) => ({
            id: v.id,
            companyName: v.company_name,
            contactName: v.contact_name,
            email: v.email,
            phone: v.phone,
            billingAddress: v.billing_address,
            billingCity: v.billing_city,
            billingState: v.billing_state,
            billingZipCode: v.billing_zip_code,
            shippingAddress: v.shipping_address,
            shippingCity: v.shipping_city,
            shippingState: v.shipping_state,
            shippingZipCode: v.shipping_zip_code,
        }));
        res.status(200).json(vendors_list);
    } catch (error) {
        console.error("DB error getting vendors:", error);
        res.status(500).json({ status: "error", message: "Failed to retrieve vendors" });
    }
};

const postVendor = (req: NextApiRequest, res: NextApiResponse) => {
    const { userId } = getAuth(req);
    if (!userId) {
        return res.status(401).json({ error: "Unauthorized" });
    }

    const payload = req.body;
    if (!payload || !payload.companyName) {
        return res.status(400).json({ message: "Missing companyName." });
    }

    try {
        const vendor_id = update_or_create_vendor(payload, userId);
        if (!vendor_id) {
            return res.status(500).json({ message: "Failed to process vendor." });
        }
        const selectVendorStmt = db.prepare("SELECT id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code FROM vendors WHERE id = ?");
        const vendor_db = selectVendorStmt.get(vendor_id);
        if (!vendor_db) {
            return res.status(500).json({ message: "Vendor processed but not retrieved." });
        }
        res.status(201).json({ message: "Vendor processed.", vendor: vendor_db });
    } catch (error) {
        console.error("DB err create vendor:", error);
        res.status(500).json({ message: "DB error." });
    }
};

const update_or_create_vendor = (vendor_info_payload: any, userId: string) => {
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
            final_vendor_id = Math.random().toString(36).substr(2, 9);
            const insertStmt = db.prepare("INSERT INTO vendors (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
            insertStmt.run(final_vendor_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, userId);
        }
    } else {
        final_vendor_id = Math.random().toString(36).substr(2, 9);
        const insertStmt = db.prepare("INSERT INTO vendors (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
        insertStmt.run(final_vendor_id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code, userId);
    }
    return final_vendor_id;
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'GET') {
        return getVendors(req, res);
    } else if (req.method === 'POST') {
        return postVendor(req, res);
    } else {
        res.setHeader('Allow', ['GET', 'POST']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
