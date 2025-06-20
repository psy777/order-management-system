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

            const transaction = db.transaction((records) => {
                const insertStmt = db.prepare(`
                    INSERT INTO items (item_code, name, type, price_cents, weight_oz)
                    VALUES (@item_code, @name, @type, @price_cents, @weight_oz)
                `);

                const updateStmt = db.prepare(`
                    UPDATE items 
                    SET name = @name, type = @type, price_cents = @price_cents, weight_oz = @weight_oz, updated_at = CURRENT_TIMESTAMP
                    WHERE item_code = @item_code
                `);

                const selectStmt = db.prepare("SELECT item_code FROM items WHERE item_code = ?");

                let items_added = 0;
                let items_updated = 0;

                for (const record of records) {
                    const item_code = record['item code'];
                    const name = record['name'];
                    if (!item_code || !name) continue;

                    const item_type = record['type'] || 'other';
                    const price_cents = record['price'] ? Math.round(parseFloat(record['price']) * 100) : 0;
                    const weight_oz = record['weight oz'] ? parseFloat(record['weight oz']) : null;

                    const existing_item = selectStmt.get(item_code);

                    const itemData = {
                        item_code,
                        name,
                        type: item_type,
                        price_cents,
                        weight_oz,
                    };

                    if (existing_item) {
                        updateStmt.run(itemData);
                        items_updated++;
                    } else {
                        insertStmt.run(itemData);
                        items_added++;
                    }
                }
                return { items_added, items_updated };
            });

            try {
                const { items_added, items_updated } = transaction(records);
                res.status(200).json({ message: `Successfully added ${items_added} and updated ${items_updated} items.` });
            } catch (error) {
                res.status(500).json({ message: 'Error importing items' });
            } finally {
                fs.unlinkSync(filePath); // Clean up uploaded file
            }
        });
    });
}
