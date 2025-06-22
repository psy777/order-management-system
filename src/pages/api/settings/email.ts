import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'POST') {
        const email_settings_payload = req.body;
        if (!email_settings_payload) {
            return res.status(400).json({ message: "Request must be JSON" });
        }

        const { email_address, app_password, email_cc = '', email_bcc = '', GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN } = email_settings_payload;

        if (!email_address || !app_password || !GMAIL_CLIENT_ID || !GMAIL_CLIENT_SECRET || !GMAIL_REFRESH_TOKEN) {
            return res.status(400).json({ message: "Email address, App Password, and all Gmail API credentials are required." });
        }

        const validateEmails = (emails: string) => {
            if (!emails) return true;
            const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            return emails.split(',').every(email => emailRegex.test(email.trim()));
        };

        if (!validateEmails(email_cc) || !validateEmails(email_bcc)) {
            return res.status(400).json({ message: "Invalid email format in CC or BCC fields." });
        }

        try {
            const stmt = db.prepare(`
                INSERT INTO settings (id, email_address, app_password, email_cc, email_bcc, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                email_address = excluded.email_address,
                app_password = excluded.app_password,
                email_cc = excluded.email_cc,
                email_bcc = excluded.email_bcc,
                GMAIL_CLIENT_ID = excluded.GMAIL_CLIENT_ID,
                GMAIL_CLIENT_SECRET = excluded.GMAIL_CLIENT_SECRET,
                GMAIL_REFRESH_TOKEN = excluded.GMAIL_REFRESH_TOKEN;
            `);
            stmt.run(email_address, app_password, email_cc, email_bcc, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN);
            res.status(200).json({ message: "Email settings updated successfully." });
        } catch (error) {
            console.error("Failed to update settings:", error);
            res.status(500).json({ message: "Failed to update settings." });
        }
    } else {
        res.setHeader('Allow', ['POST']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
