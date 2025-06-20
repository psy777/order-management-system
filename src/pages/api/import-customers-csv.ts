import type { NextApiRequest, NextApiResponse } from 'next';
import formidable from 'formidable';
import fs from 'fs';
import { parse } from 'csv-parse';
import db from '../../lib/db';

export const config = {
    api: {
        bodyParser: false,
    },
};

export default async function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method !== 'POST') {
        res.setHeader('Allow', ['POST']);
        return res.status(405).end(`Method ${req.method} Not Allowed`);
    }

    const form = formidable({});

    form.parse(req, (err, fields, files) => {
        if (err) {
            return res.status(500).json({ message: 'Error parsing form data' });
        }

        const file = files.csv_file;
        if (!file) {
            return res.status(400).json({ message: 'No file uploaded' });
        }

        const uploadedFile = Array.isArray(file) ? file[0] : file;
        const filePath = uploadedFile.filepath;

        const fileContent = fs.readFileSync(filePath, 'utf-8');

        parse(fileContent, {
            columns: header => header.map((h: string) => h.toLowerCase().trim()),
            skip_empty_lines: true,
        }, (err, records) => {
            if (err) {
                return res.status(500).json({ message: 'Error parsing CSV file' });
            }

            const header_map: { [key: string]: string } = {
                'company name': 'company_name',
                'contact name': 'contact_name',
                'email': 'email',
                'phone': 'phone',
                'billing address': 'billing_address',
                'billing city': 'billing_city',
                'billing state': 'billing_state',
                'billing zip code': 'billing_zip_code',
                'shipping address': 'shipping_address',
                'shipping city': 'shipping_city',
                'shipping state': 'shipping_state',
                'shipping zip code': 'shipping_zip_code'
            };

            const transaction = db.transaction((records) => {
                const insertStmt = db.prepare(`
                    INSERT INTO vendors (id, company_name, contact_name, email, phone, billing_address, billing_city, billing_state, billing_zip_code, shipping_address, shipping_city, shipping_state, shipping_zip_code)
                    VALUES (@id, @company_name, @contact_name, @email, @phone, @billing_address, @billing_city, @billing_state, @billing_zip_code, @shipping_address, @shipping_city, @shipping_state, @shipping_zip_code)
                `);

                const updateStmt = db.prepare(`
                    UPDATE vendors 
                    SET contact_name = @contact_name, email = @email, phone = @phone, billing_address = @billing_address, billing_city = @billing_city, billing_state = @billing_state, billing_zip_code = @billing_zip_code, shipping_address = @shipping_address, shipping_city = @shipping_city, shipping_state = @shipping_state, shipping_zip_code = @shipping_zip_code, updated_at = CURRENT_TIMESTAMP
                    WHERE company_name = @company_name
                `);

                const selectStmt = db.prepare("SELECT id FROM vendors WHERE company_name = ?");

                for (const record of records) {
                    const company_name = record['company name'];
                    if (!company_name) continue;

                    const existing_vendor = selectStmt.get(company_name);

                    const vendorData = {
                        company_name: company_name,
                        contact_name: record['contact name'] || '',
                        email: record['email'] || '',
                        phone: record['phone'] || '',
                        billing_address: record['billing address'] || '',
                        billing_city: record['billing city'] || '',
                        billing_state: record['billing state'] || '',
                        billing_zip_code: record['billing zip code'] || '',
                        shipping_address: record['shipping address'] || '',
                        shipping_city: record['shipping city'] || '',
                        shipping_state: record['shipping state'] || '',
                        shipping_zip_code: record['shipping zip code'] || '',
                    };

                    if (existing_vendor) {
                        updateStmt.run(vendorData);
                    } else {
                        insertStmt.run({ ...vendorData, id: Math.random().toString(36).substr(2, 9) });
                    }
                }
            });

            try {
                transaction(records);
                res.status(200).json({ message: 'Customers imported successfully' });
            } catch (error) {
                res.status(500).json({ message: 'Error importing customers' });
            } finally {
                fs.unlinkSync(filePath); // Clean up uploaded file
            }
        });
    });
}
