import type { NextApiRequest, NextApiResponse } from 'next';
import nodemailer from 'nodemailer';
import formidable from 'formidable';
import { google } from 'googleapis';
import { getSettings } from '../../lib/db';
import { Settings, User } from '../../lib/types';
import fs from 'fs';
import path from 'path';
import { unstable_getServerSession } from 'next-auth/next';
import { authOptions } from './auth/[...nextauth]';
import db from '../../lib/db';

export const config = {
    api: {
        bodyParser: false,
    },
};

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== 'POST') {
        res.setHeader('Allow', ['POST']);
        return res.status(405).end(`Method ${req.method} Not Allowed`);
    }

    const session = await unstable_getServerSession(req, res, authOptions);

    if (!session || !session.user) {
        return res.status(401).json({ message: 'You must be logged in.' });
    }

    const userStmt = db.prepare('SELECT * FROM users WHERE email = ?');
    const user = userStmt.get(session.user.email) as User;

    if (!user || !user.GMAIL_REFRESH_TOKEN) {
        return res.status(500).json({ message: 'Email service is not configured for this user.' });
    }

    const settings = getSettings() as Settings;
    const { email_address: from_email, email_cc, email_bcc, GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET } = settings;

    if (!from_email || !GMAIL_CLIENT_ID || !GMAIL_CLIENT_SECRET) {
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

            const OAuth2 = google.auth.OAuth2;
            const oauth2Client = new OAuth2(
                GMAIL_CLIENT_ID,
                GMAIL_CLIENT_SECRET,
                "https" // Redirect URL
            );

            oauth2Client.setCredentials({
                refresh_token: user.GMAIL_REFRESH_TOKEN
            });

            const accessToken = await oauth2Client.getAccessToken();

            const transporter = nodemailer.createTransport({
                host: 'smtp.gmail.com',
                port: 465,
                secure: true,
                auth: {
                    type: 'OAuth2',
                    user: from_email,
                    clientId: GMAIL_CLIENT_ID,
                    clientSecret: GMAIL_CLIENT_SECRET,
                    refreshToken: user.GMAIL_REFRESH_TOKEN,
                    accessToken: accessToken.token,
                },
            } as any);

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
