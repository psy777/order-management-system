import type { NextApiRequest, NextApiResponse } from 'next';
import nodemailer from 'nodemailer';
import formidable from 'formidable';
import fs from 'fs';
import path from 'path';

export const config = {
    api: {
        bodyParser: false,
    },
};

const SETTINGS_FILE = path.resolve(process.cwd(), 'data/settings.json');

const readSettings = () => {
    try {
        const data = fs.readFileSync(SETTINGS_FILE, 'utf-8');
        return JSON.parse(data);
    } catch (error) {
        console.error("Failed to read settings file:", error);
        return {};
    }
};

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== 'POST') {
        res.setHeader('Allow', ['POST']);
        return res.status(405).end(`Method ${req.method} Not Allowed`);
    }

    const settings = readSettings();
    const { email_address: from_email, app_password: from_pass, email_cc, email_bcc } = settings;

    if (!from_email || !from_pass) {
        return res.status(500).json({ message: "Email service is not configured." });
    }

    const form = formidable({ multiples: true });

    form.parse(req, async (err, fields, files) => {
        if (err) {
            console.error('Error parsing form data:', err);
            return res.status(500).json({ message: 'Error processing request' });
        }

        try {
            const { order: orderStr, recipientEmail, subject, body } = fields;
            
            if (!orderStr || !recipientEmail || !subject || !body) {
                return res.status(400).json({ message: "Missing required email data." });
            }
            
            const order = JSON.parse(Array.isArray(orderStr) ? orderStr[0] : orderStr);

            const transporter = nodemailer.createTransport({
                service: 'gmail',
                auth: { user: from_email, pass: from_pass },
            });

            const mailOptions: nodemailer.SendMailOptions = {
                from: from_email,
                to: Array.isArray(recipientEmail) ? recipientEmail[0] : recipientEmail,
                subject: Array.isArray(subject) ? subject[0] : subject,
                text: Array.isArray(body) ? body[0] : body,
                attachments: [],
            };

            if (email_cc) mailOptions.cc = email_cc;
            if (email_bcc) mailOptions.bcc = email_bcc;

            const attachments = files.attachments;
            if (attachments) {
                const attachmentArray = Array.isArray(attachments) ? attachments : [attachments];
                for (const file of attachmentArray) {
                    (mailOptions.attachments as any[]).push({
                        filename: file.originalFilename || 'attachment',
                        path: file.filepath,
                    });
                }
            }

            await transporter.sendMail(mailOptions);
            
            // Clean up temporary files
            if (attachments) {
                const attachmentArray = Array.isArray(attachments) ? attachments : [attachments];
                for (const file of attachmentArray) {
                    fs.unlink(file.filepath, (unlinkErr) => {
                        if (unlinkErr) console.error(`Failed to delete temp file: ${file.filepath}`, unlinkErr);
                    });
                }
            }

            res.status(200).json({ message: "Email sent." });

        } catch (error) {
            console.error(`Failed to send email:`, error);
            res.status(500).json({ message: `Failed to send email: ${error}` });
        }
    });
}
