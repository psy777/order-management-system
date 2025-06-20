import type { NextApiRequest, NextApiResponse } from 'next';
import nodemailer from 'nodemailer';
import fs from 'fs';
import path from 'path';

const SETTINGS_FILE = path.resolve(process.cwd(), 'data/settings.json');
const UPLOAD_FOLDER = path.resolve(process.cwd(), 'uploads');

const readSettings = () => {
    try {
        const data = fs.readFileSync(SETTINGS_FILE, 'utf-8');
        return JSON.parse(data);
    } catch (error) {
        return {};
    }
};

export default async function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method !== 'POST') {
        res.setHeader('Allow', ['POST']);
        return res.status(405).end(`Method ${req.method} Not Allowed`);
    }

    const { order, recipientEmail, subject, body, attachments = [] } = req.body;

    if (!order || !recipientEmail || !subject || !body) {
        return res.status(400).json({ message: "Missing required email data." });
    }

    const settings = readSettings();
    const { email_address: from_email, app_password: from_pass, email_cc, email_bcc } = settings;

    if (!from_email || !from_pass) {
        return res.status(500).json({ message: "Email service is not configured." });
    }

    const transporter = nodemailer.createTransport({
        service: 'gmail',
        auth: {
            user: from_email,
            pass: from_pass,
        },
    });

    const mailOptions: nodemailer.SendMailOptions = {
        from: from_email,
        to: recipientEmail,
        subject: subject,
        text: body,
        attachments: [],
    };

    if (email_cc) mailOptions.cc = email_cc;
    if (email_bcc) mailOptions.bcc = email_bcc;

    const attachment_paths_to_delete: string[] = [];

    if (Array.isArray(attachments)) {
        for (const attachment_info of attachments) {
            const { unique: unique_fn, original: original_fn } = attachment_info;
            if (!unique_fn || !original_fn) continue;

            const attachment_path = path.join(UPLOAD_FOLDER, unique_fn);
            if (fs.existsSync(attachment_path)) {
                (mailOptions.attachments as any[]).push({
                    filename: original_fn,
                    path: attachment_path,
                });
                attachment_paths_to_delete.push(attachment_path);
            } else {
                console.warn(`Attachment file not found on server: ${unique_fn}`);
            }
        }
    }

    try {
        await transporter.sendMail(mailOptions);
        res.status(200).json({ message: "Email sent." });
    } catch (error) {
        console.error(`Failed to send email for order ${order.id}:`, error);
        res.status(500).json({ message: `Failed to send email: ${error}` });
    } finally {
        for (const path of attachment_paths_to_delete) {
            if (fs.existsSync(path)) {
                try {
                    fs.unlinkSync(path);
                    console.log(`Successfully deleted attachment: ${path}`);
                } catch (e_del) {
                    console.error(`Error deleting attachment ${path}:`, e_del);
                }
            }
        }
    }
}
