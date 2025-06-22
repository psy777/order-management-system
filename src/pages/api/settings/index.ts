import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';

const getSettings = (req: NextApiRequest, res: NextApiResponse) => {
    try {
        let settings = db.prepare('SELECT * FROM settings WHERE id = 1').get();
        if (!settings) {
            settings = {
                id: 1,
                company_name: '',
                default_shipping_zip_code: '',
                default_email_body: '',
                email_address: '',
                app_password: '',
                email_cc: '',
                email_bcc: '',
                GMAIL_CLIENT_ID: '',
                GMAIL_CLIENT_SECRET: '',
                GMAIL_REFRESH_TOKEN: ''
            };
        }
        res.status(200).json(settings);
    } catch (error) {
        console.error("Failed to get settings:", error);
        res.status(500).json({ message: "Failed to get settings." });
    }
};

const updateSettings = (req: NextApiRequest, res: NextApiResponse) => {
    const { company_name, default_shipping_zip_code, default_email_body } = req.body;
    if (company_name === undefined || default_shipping_zip_code === undefined || default_email_body === undefined) {
        return res.status(400).json({ message: "Request body must contain company_name, default_shipping_zip_code, and default_email_body" });
    }

    try {
        const settingsExist = db.prepare('SELECT id FROM settings WHERE id = 1').get();

        if (settingsExist) {
            const stmt = db.prepare(`
                UPDATE settings
                SET company_name = ?, default_shipping_zip_code = ?, default_email_body = ?
                WHERE id = 1
            `);
            stmt.run(company_name, default_shipping_zip_code, default_email_body);
        } else {
            const stmt = db.prepare(`
                INSERT INTO settings (id, company_name, default_shipping_zip_code, default_email_body)
                VALUES (1, ?, ?, ?)
            `);
            stmt.run(company_name, default_shipping_zip_code, default_email_body);
        }
        
        res.status(200).json({ message: "Settings updated." });
    } catch (error) {
        console.error("Failed to update settings:", error);
        res.status(500).json({ message: "Failed to update settings." });
    }
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'GET') {
        return getSettings(req, res);
    } else if (req.method === 'POST') {
        return updateSettings(req, res);
    } else {
        res.setHeader('Allow', ['GET', 'POST']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
