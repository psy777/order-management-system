import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';

const updateItem = (req: NextApiRequest, res: NextApiResponse) => {
    const { item_code: item_code_url } = req.query;
    const payload = req.body;

    if (!payload) {
        return res.status(400).json({ message: "Request must be JSON" });
    }

    const checkItemExistsStmt = db.prepare("SELECT item_code FROM items WHERE item_code = ?");
    if (!checkItemExistsStmt.get(item_code_url)) {
        return res.status(404).json({ message: "Item not found." });
    }

    const { item_code: new_code, name, type, price, weight_oz, styles: styles_payload = [] } = payload;
    const new_code_trimmed = new_code ? new_code.trim() : item_code_url;

    if (new_code_trimmed !== item_code_url) {
        if (checkItemExistsStmt.get(new_code_trimmed)) {
            return res.status(409).json({ message: `Item code ${new_code_trimmed} exists.` });
        }
    }

    const price_cents = price;

    const transaction = db.transaction(() => {
        let current_code_for_styles = item_code_url as string;

        if (new_code_trimmed !== item_code_url) {
            const orig_item_stmt = db.prepare("SELECT * FROM items WHERE item_code = ?");
            const orig_item = orig_item_stmt.get(item_code_url);

            const insertStmt = db.prepare("INSERT INTO items (item_code, name, type, price_cents, weight_oz) VALUES (?, ?, ?, ?, ?)");
            insertStmt.run(
                new_code_trimmed,
                name || (orig_item as any).name,
                type || (orig_item as any).type,
                price_cents !== null ? price_cents : (orig_item as any).price_cents,
                'weight_oz' in payload ? weight_oz : (orig_item as any).weight_oz
            );

            const itemStylesStmt = db.prepare("SELECT style_id FROM item_styles WHERE item_code = ?");
            const stylesToMove = itemStylesStmt.all(item_code_url);
            const insertItemStyleStmt = db.prepare("INSERT OR IGNORE INTO item_styles (item_code, style_id) VALUES (?, ?)");
            for (const style of stylesToMove) {
                insertItemStyleStmt.run(new_code_trimmed, (style as any).style_id);
            }

            db.prepare("DELETE FROM items WHERE item_code = ?").run(item_code_url);
            current_code_for_styles = new_code_trimmed;
        } else {
            const updates: string[] = [];
            const vals: any[] = [];
            if (name !== undefined) { updates.push("name = ?"); vals.push(name); }
            if (type !== undefined) { updates.push("type = ?"); vals.push(type); }
            if (price_cents !== undefined) { updates.push("price_cents = ?"); vals.push(price_cents); }
            if ('weight_oz' in payload) { updates.push("weight_oz = ?"); vals.push(weight_oz === "" ? null : weight_oz); }

            if (updates.length > 0) {
                const query = `UPDATE items SET ${updates.join(', ')}, updated_at = CURRENT_TIMESTAMP WHERE item_code = ?`;
                vals.push(item_code_url);
                db.prepare(query).run(...vals);
            }
        }

        db.prepare("DELETE FROM item_styles WHERE item_code = ?").run(current_code_for_styles);
        if (Array.isArray(styles_payload)) {
            const insertStyleStmt = db.prepare("INSERT OR IGNORE INTO styles (style_name) VALUES (?)");
            const selectStyleStmt = db.prepare("SELECT id FROM styles WHERE style_name = ?");
            const insertItemStyleStmt = db.prepare("INSERT OR IGNORE INTO item_styles (item_code, style_id) VALUES (?, ?)");
            for (const sn of styles_payload) {
                if (!sn) continue;
                insertStyleStmt.run(sn);
                const sr = selectStyleStmt.get(sn) as { id: number } | undefined;
                if (sr) {
                    insertItemStyleStmt.run(current_code_for_styles, sr.id);
                }
            }
        }

        const selectItemStmt = db.prepare("SELECT item_code, name, type, price_cents, weight_oz FROM items WHERE item_code = ?");
        const updated_item = selectItemStmt.get(current_code_for_styles);
        const selectStylesStmt = db.prepare("SELECT s.style_name FROM styles s JOIN item_styles ist ON s.id = ist.style_id WHERE ist.item_code = ? ORDER BY s.style_name");
        const styles = selectStylesStmt.all(current_code_for_styles).map((s: any) => s.style_name);

        return {
            ...(updated_item as any),
            styles,
            id: (updated_item as any).item_code,
            price: (updated_item as any).price_cents,
        };
    });

    try {
        const updatedItem = transaction();
        res.status(200).json({ message: "Item updated.", item: updatedItem });
    } catch (error) {
        console.error(`DB err update item ${item_code_url}:`, error);
        res.status(500).json({ message: "DB error." });
    }
};

const deleteItem = (req: NextApiRequest, res: NextApiResponse) => {
    const { item_code: item_code_url } = req.query;

    try {
        const result = db.prepare("DELETE FROM items WHERE item_code = ?").run(item_code_url);
        if (result.changes > 0) {
            res.status(200).json({ message: "Item deleted." });
        } else {
            res.status(404).json({ message: "Item not found." });
        }
    } catch (error) {
        console.error(`DB err delete item ${item_code_url}:`, error);
        res.status(500).json({ message: "DB error." });
    }
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'PUT') {
        return updateItem(req, res);
    } else if (req.method === 'DELETE') {
        return deleteItem(req, res);
    } else {
        res.setHeader('Allow', ['PUT', 'DELETE']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
