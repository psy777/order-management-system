import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';
import { getAuth } from '@clerk/nextjs/server';

const getPackages = (req: NextApiRequest, res: NextApiResponse) => {
    const { userId } = getAuth(req);
    if (!userId) {
        return res.status(401).json({ error: "Unauthorized" });
    }

    try {
        const pkgs_db = db.prepare("SELECT package_id, name, type FROM packages WHERE user_id = ? ORDER BY name COLLATE NOCASE ASC").all(userId);
        const transformed_pkgs: { [key: string]: any } = {};

        const contents_stmt = db.prepare("SELECT item_code, quantity FROM package_items WHERE package_id = ?");

        for (const pkg_row of pkgs_db as any[]) {
            const contents_db = contents_stmt.all(pkg_row.package_id);
            transformed_pkgs[String(pkg_row.package_id)] = {
                name: pkg_row.name,
                id_val: pkg_row.package_id,
                type: (pkg_row.type || 'package').toLowerCase(),
                contents: contents_db.map((cr: any) => ({
                    itemCode: String(cr.item_code),
                    quantity: cr.quantity
                }))
            };
        }
        res.status(200).json(transformed_pkgs);
    } catch (error) {
        console.error("DB error getting packages:", error);
        res.status(500).json({ status: "error", message: "Failed to retrieve packages" });
    }
};

const addPackage = (req: NextApiRequest, res: NextApiResponse) => {
    const { userId } = getAuth(req);
    if (!userId) {
        return res.status(401).json({ error: "Unauthorized" });
    }

    const payload = req.body;
    if (!payload) {
        return res.status(400).json({ message: "Request must be JSON" });
    }

    const { name: pkg_name, id_val: pkg_id_val, type: pkg_type = 'package', contents_raw_text: contents_raw = "" } = payload;

    if (!pkg_name || pkg_id_val === undefined) {
        return res.status(400).json({ message: "Name and ID required." });
    }

    let pkg_id;
    try {
        pkg_id = parseInt(pkg_id_val, 10);
    } catch (e) {
        return res.status(400).json({ message: "ID must be a number." });
    }

    const transaction = db.transaction(() => {
        const checkPkgExistsStmt = db.prepare("SELECT package_id FROM packages WHERE name = ? OR package_id = ?");
        if (checkPkgExistsStmt.get(pkg_name, pkg_id)) {
            throw new Error(`Package ${pkg_name} or ID ${pkg_id} exists.`);
        }

        const insertPkgStmt = db.prepare("INSERT INTO packages (package_id, name, type, user_id) VALUES (?, ?, ?, ?)");
        insertPkgStmt.run(pkg_id, pkg_name, pkg_type, userId);

        const parsed_contents_resp: any[] = [];
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
                                insertPkgItemStmt.run(pkg_id, item_code, qty);
                                parsed_contents_resp.push({ itemCode: item_code, quantity: qty });
                            } else {
                                console.warn(`Item ${item_code} not found for pkg ${pkg_id}.`);
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
        return {
            [String(pkg_id)]: {
                name: pkg_name,
                id_val: pkg_id,
                type: pkg_type.toLowerCase(),
                contents: parsed_contents_resp
            }
        };
    });

    try {
        const newPackage = transaction();
        res.status(201).json({ message: "Package added.", package: newPackage });
    } catch (error: any) {
        res.status(error.message.includes("exists") ? 409 : 500).json({ message: error.message || "DB error." });
    }
};

export default function handler(
    req: NextApiRequest,
    res: NextApiResponse
) {
    if (req.method === 'GET') {
        return getPackages(req, res);
    } else if (req.method === 'POST') {
        return addPackage(req, res);
    } else {
        res.setHeader('Allow', ['GET', 'POST']);
        res.status(405).end(`Method ${req.method} Not Allowed`);
    }
}
