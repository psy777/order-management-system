import type { NextApiRequest, NextApiResponse } from 'next';
import db from '../../lib/db';

export default function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method === 'GET') {
    try {
      const totalRevenueStmt = db.prepare("SELECT SUM(total_amount) as total FROM orders WHERE status != 'Deleted'");
      const totalRevenueResult = totalRevenueStmt.get() as { total: number | null };
      const totalRevenue = totalRevenueResult?.total || 0;

      const totalOrdersStmt = db.prepare("SELECT COUNT(order_id) as total FROM orders WHERE status != 'Deleted'");
      const totalOrdersResult = totalOrdersStmt.get() as { total: number | null };
      const totalOrders = totalOrdersResult?.total || 0;

      const averageOrderRevenue = totalOrders > 0 ? totalRevenue / totalOrders : 0;

      res.status(200).json({
        totalRevenue: parseFloat(totalRevenue.toFixed(2)),
        averageOrderRevenue: parseFloat(averageOrderRevenue.toFixed(2)),
        totalOrders,
      });
    } catch (error) {
      console.error('DB error getting dashboard stats:', error);
      res.status(500).json({ status: 'error', message: 'Failed to retrieve dashboard stats' });
    }
  } else {
    res.setHeader('Allow', ['GET']);
    res.status(405).end(`Method ${req.method} Not Allowed`);
  }
}
