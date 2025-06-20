import type { NextApiRequest, NextApiResponse } from 'next';
import fs from 'fs';
import path from 'path';

const SETTINGS_FILE = path.resolve(process.cwd(), 'data/settings.json');

const readSettings = () => {
    if (!fs.existsSync(SETTINGS_FILE) || fs.statSync(SETTINGS_FILE).size === 0) {
        return {};
    }
    try {
        const data = fs.readFileSync(SETTINGS_FILE, 'utf-8');
        return JSON.parse(data);
    } catch (error) {
        console.error("Error reading settings file:", error);
        return {};
    }
};

const writeSettings = (data: any) => {
    try {
        fs.writeFileSync(SETTINGS_FILE, JSON.stringify(data, null, 4));
    } catch (error) {
        console.error("Error writing settings file:", error);
    }
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'POST') {
        const email_settings_payload = req.body;
        if (!email_settings_payload) {
            return res.status(400).json({ message: "Request must be JSON" });
        }

        const { email_address, app_password, email_cc = '', email_bcc = '' } = email_settings_payload;

        if (!email_address || !app_password) {
            return res.status(400).json({ message: "Email address and App Password are required." });
        }

        let existing_settings = readSettings();

        existing_settings.email_address = email_address;
        existing_settings.app_password = app_password;
        existing_settings.email_cc = email_cc;
        existing_settings.email_bcc = email_bcc;

        writeSettings(existing_settings);

        res.status(200).json({ message: "Email settings updated successfully." });
    } else {
        res.setHeader('Allow', ['POST']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
