import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';

const updatePackage = (req: NextApiRequest, res: NextApiResponse) => {
    const { package_id: package_id_str } = req.query;
    const payload = req.body;

    if (!payload) {
        return res.status(400).json({ message: "Request must be JSON" });
    }

    let target_pkg_id;
    try {
        target_pkg_id = parseInt(package_id_str as string, 10);
    } catch (e) {
        return res.status(400).json({ message: "Invalid pkg ID in URL." });
    }

    const transaction = db.transaction(() => {
        const currPkgStmt = db.prepare("SELECT name, type FROM packages WHERE package_id = ?");
        const curr_pkg = currPkgStmt.get(target_pkg_id) as { name: string, type: string } | undefined;

        if (!curr_pkg) {
            throw new Error(`Pkg ID ${target_pkg_id} not found.`);
        }

        const { name: new_name = curr_pkg.name, id_val: new_id_val_str, type: new_type = curr_pkg.type, contents_raw_text: contents_raw } = payload;

        let new_id = target_pkg_id;
        if (new_id_val_str !== undefined) {
            try {
                new_id = parseInt(new_id_val_str, 10);
            } catch (e) {
                throw new Error("New Pkg ID must be a number.");
            }
        }

        if (new_name !== curr_pkg.name) {
            const checkNameStmt = db.prepare("SELECT package_id FROM packages WHERE name = ? AND package_id != ?");
            if (checkNameStmt.get(new_name, target_pkg_id)) {
                throw new Error(`Pkg name '${new_name}' exists.`);
            }
        }

        if (new_id !== target_pkg_id) {
            const checkIdStmt = db.prepare("SELECT package_id FROM packages WHERE package_id = ?");
            if (checkIdStmt.get(new_id)) {
                throw new Error(`Pkg ID '${new_id}' exists.`);
            }
            const updateIdStmt = db.prepare("UPDATE packages SET package_id = ?, name = ?, type = ?, updated_at = CURRENT_TIMESTAMP WHERE package_id = ?");
            updateIdStmt.run(new_id, new_name, new_type, target_pkg_id);
        } else {
            const updateStmt = db.prepare("UPDATE packages SET name = ?, type = ?, updated_at = CURRENT_TIMESTAMP WHERE package_id = ?");
            updateStmt.run(new_name, new_type, target_pkg_id);
        }

        const final_id_for_contents = new_id;
        let parsed_contents_resp: any[] = [];

        if (contents_raw !== undefined) {
            db.prepare("DELETE FROM package_items WHERE package_id = ?").run(final_id_for_contents);
            if (contents_raw) {
                const checkItemExistsStmt = db.prepare("SELECT item_code FROM items WHERE item_code = ?");
                const insertPkgItemStmt = db.prepare("INSERT INTO package_items (package_id, item_code, quantity) VALUES (?, ?, ?)");
                for (const line of contents_raw.trim().split('\n')) {
                    const parts = line.split(':');
                    if (parts.length === 2) {
                        const item_code = parts[0].trim();
                        const qty_str = parts[1].trim();
                        try {
                            const qty = parseInt(qty_str, 10);
                            if (qty > 0) {
                                if (checkItemExistsStmt.get(item_code)) {
                                    insertPkgItemStmt.run(final_id_for_contents, item_code, qty);
                                    parsed_contents_resp.push({ itemCode: item_code, quantity: qty });
                                } else {
                                    console.warn(`Item ${item_code} not found for pkg ${final_id_for_contents}.`);
                                }
                            }
                        } catch (e) {
                            throw new Error(`Invalid qty for ${item_code}.`);
                        }
                    } else if (line.trim()) {
                        throw new Error(`Malformed line: ${line}.`);
                    }
                }
            }
        } else {
            const contents = db.prepare("SELECT item_code, quantity FROM package_items WHERE package_id = ?").all(final_id_for_contents);
            parsed_contents_resp = contents.map((r: any) => ({ itemCode: String(r.item_code), quantity: r.quantity }));
        }

        return {
            [String(final_id_for_contents)]: {
                name: new_name,
                id_val: final_id_for_contents,
                type: new_type.toLowerCase(),
                contents: parsed_contents_resp
            }
        };
    });

    try {
        const updatedPackage = transaction();
        res.status(200).json({ message: "Package updated.", package: updatedPackage });
    } catch (error: any) {
        res.status(error.message.includes("not found") ? 404 : error.message.includes("exists") ? 409 : 500).json({ message: error.message || "DB error." });
    }
};

const deletePackage = (req: NextApiRequest, res: NextApiResponse) => {
    const { package_id: package_id_str } = req.query;
    let target_pkg_id;
    try {
        target_pkg_id = parseInt(package_id_str as string, 10);
    } catch (e) {
        return res.status(400).json({ message: "Invalid pkg ID." });
    }

    try {
        const result = db.prepare("DELETE FROM packages WHERE package_id = ?").run(target_pkg_id);
        if (result.changes > 0) {
            res.status(200).json({ message: "Package deleted." });
        } else {
            res.status(404).json({ message: "Package not found." });
        }
    } catch (error) {
        console.error(`DB err delete pkg ${target_pkg_id}:`, error);
        res.status(500).json({ message: "DB error." });
    }
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'PUT') {
        return updatePackage(req, res);
    } else if (req.method === 'DELETE') {
        return deletePackage(req, res);
    } else {
        res.setHeader('Allow', ['PUT', 'DELETE']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
