import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';
import { Statement } from 'better-sqlite3';

type Item = {
    item_code: string;
    name: string;
    type: string;
    price_cents: number;
    weight_oz: number;
};

const getItems = (req: NextApiRequest, res: NextApiResponse) => {
    try {
        const itemsQuery = db.prepare("SELECT item_code, name, type, price_cents, weight_oz FROM items ORDER BY name COLLATE NOCASE ASC");
        const stylesQuery: Statement = db.prepare("SELECT s.style_name FROM styles s JOIN item_styles ist ON s.id = ist.style_id WHERE ist.item_code = ? ORDER BY s.style_name COLLATE NOCASE ASC");

        const itemsFromDb: Item[] = itemsQuery.all() as Item[];

        const items_list = itemsFromDb.map(item_row => {
            const styles = stylesQuery.all(item_row.item_code).map((s: any) => s.style_name);
            return {
                ...item_row,
                styles,
                id: item_row.item_code,
                price: item_row.price_cents,
            };
        });

        res.status(200).json(items_list);
    } catch (error) {
        console.error("DB error getting items:", error);
        res.status(500).json({ status: "error", message: "Failed to retrieve items" });
    }
};

const postItem = (req: NextApiRequest, res: NextApiResponse) => {
    const payload = req.body;
    if (!payload) {
        return res.status(400).json({ message: "Request must be JSON" });
    }

    const { item_code, name, price, type = "other", weight_oz, styles = [] } = payload;

    if (!item_code || !name) {
        return res.status(400).json({ message: "Missing item_code or name" });
    }

    const checkItemExistsStmt = db.prepare("SELECT item_code FROM items WHERE item_code = ?");
    if (checkItemExistsStmt.get(item_code)) {
        return res.status(409).json({ message: `Item ${item_code} exists.` });
    }

    const price_cents = price;

    const transaction = db.transaction(() => {
        const insertItemStmt = db.prepare("INSERT INTO items (item_code, name, type, price_cents, weight_oz) VALUES (?, ?, ?, ?, ?)");
        insertItemStmt.run(item_code, name, type, price_cents, weight_oz);

        const insertStyleStmt = db.prepare("INSERT OR IGNORE INTO styles (style_name) VALUES (?)");
        const selectStyleStmt = db.prepare("SELECT id FROM styles WHERE style_name = ?");
        const insertItemStyleStmt = db.prepare("INSERT OR IGNORE INTO item_styles (item_code, style_id) VALUES (?, ?)");

        for (const style_name of styles) {
            if (!style_name) continue;
            insertStyleStmt.run(style_name);
            const style_row = selectStyleStmt.get(style_name) as { id: number } | undefined;
            if (style_row) {
                insertItemStyleStmt.run(item_code, style_row.id);
            }
        }

        const selectItemStmt = db.prepare("SELECT item_code, name, type, price_cents, weight_oz FROM items WHERE item_code = ?");
        const created_item = selectItemStmt.get(item_code) as Item;

        if (!created_item) {
            throw new Error("Failed to create or find the item after insertion.");
        }

        const selectStylesStmt = db.prepare("SELECT s.style_name FROM styles s JOIN item_styles ist ON s.id = ist.style_id WHERE ist.item_code = ? ORDER BY s.style_name");
        const item_styles = selectStylesStmt.all(item_code).map((s: any) => s.style_name);

        return {
            ...created_item,
            styles: item_styles,
            id: created_item.item_code,
            price: created_item.price_cents,
        };
    });

    try {
        const newItem = transaction();
        res.status(201).json({ message: "Item added.", item: newItem });
    } catch (error) {
        console.error(`DB err add item ${item_code}:`, error);
        res.status(500).json({ message: "DB error." });
    }
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'GET') {
        return getItems(req, res);
    } else if (req.method === 'POST') {
        return postItem(req, res);
    } else {
        res.setHeader('Allow', ['GET', 'POST']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
