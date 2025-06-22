import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';

const deleteOrder = (req: NextApiRequest, res: NextApiResponse) => {
  const { id } = req.query;

  if (!id) {
    return res.status(400).json({ status: "error", message: "Order ID is required" });
  }

  try {
    const stmt = db.prepare("UPDATE orders SET status = 'Deleted' WHERE order_id = ?");
    const result = stmt.run(id);

    if (result.changes === 0) {
      return res.status(404).json({ status: "error", message: "Order not found" });
    }

    res.status(200).json({ status: "success", message: "Order deleted successfully" });
  } catch (error) {
    console.error(`DB error deleting order ${id}:`, error);
    res.status(500).json({ status: "error", message: "Failed to delete order" });
  }
};

export default function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method === 'DELETE') {
    return deleteOrder(req, res);
  } else {
    res.setHeader('Allow', ['DELETE']);
    res.status(405).end(`Method ${req.method} Not Allowed`);
  }
}
