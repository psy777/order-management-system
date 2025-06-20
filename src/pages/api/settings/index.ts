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

const getSettings = (req: NextApiRequest, res: NextApiResponse) => {
    let settings = readSettings();
    
    let updated = false;
    if (!settings.company_name) {
        settings.company_name = "Your Company Name";
        updated = true;
    }
    if (!settings.default_shipping_zip_code) {
        settings.default_shipping_zip_code = "00000";
        updated = true;
    }
    if (!settings.default_email_body) {
        settings.default_email_body = "Dear [vendorCompany],\n\nPlease find attached the purchase order [orderID] for your records.\n\nWe appreciate your business!\n\nThank you,\n[yourCompany]";
        updated = true;
    }
    if (!settings.email_address) {
        settings.email_address = "";
        updated = true;
    }
    if (!settings.app_password) {
        settings.app_password = "";
        updated = true;
    }
    if (!settings.email_cc) {
        settings.email_cc = "";
        updated = true;
    }
    if (!settings.email_bcc) {
        settings.email_bcc = "";
        updated = true;
    }

    if (updated) {
        writeSettings(settings);
    }

    res.status(200).json(settings);
};

const updateSettings = (req: NextApiRequest, res: NextApiResponse) => {
    const new_settings_payload = req.body;
    if (!new_settings_payload) {
        return res.status(400).json({ message: "Request must be JSON" });
    }

    let existing_settings = readSettings();

    existing_settings.company_name = new_settings_payload.company_name ?? existing_settings.company_name;
    existing_settings.default_shipping_zip_code = new_settings_payload.default_shipping_zip_code ?? existing_settings.default_shipping_zip_code;
    existing_settings.default_email_body = new_settings_payload.default_email_body ?? existing_settings.default_email_body;

    writeSettings(existing_settings);
    res.status(200).json({ message: "Settings updated." });
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
