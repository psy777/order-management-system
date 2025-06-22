import { Webhook } from 'svix';
import { buffer } from 'micro';
import { NextApiRequest, NextApiResponse } from 'next';
import db from '../../../lib/db';

export const config = {
  api: {
    bodyParser: false,
  },
};

const webhookSecret = process.env.CLERK_WEBHOOK_SECRET || '';

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') {
    return res.status(405).end();
  }

  const payload = (await buffer(req)).toString();
  const headers = req.headers;

  const wh = new Webhook(webhookSecret);
  let msg: any;
  try {
    msg = wh.verify(payload, headers as any);
  } catch (err) {
    console.error('Error verifying webhook:', err);
    return res.status(400).json({});
  }

  const { id, first_name, last_name, email_addresses, profile_image_url } = msg.data;
  const eventType = msg.type;

  if (eventType === 'user.created' || eventType === 'user.updated') {
    const email = email_addresses[0]?.email_address;
    if (!email) {
      return res.status(400).json({ error: 'Email address is missing' });
    }

    try {
      const existingUser = db.prepare('SELECT id FROM users WHERE id = ?').get(id);

      if (existingUser) {
        // Update existing user
        const stmt = db.prepare(
          'UPDATE users SET first_name = ?, last_name = ?, email = ?, profile_image_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?'
        );
        stmt.run(first_name, last_name, email, profile_image_url, id);
      } else {
        // Create new user
        const stmt = db.prepare(
          'INSERT INTO users (id, first_name, last_name, email, profile_image_url) VALUES (?, ?, ?, ?, ?)'
        );
        stmt.run(id, first_name, last_name, email, profile_image_url);
      }

      res.status(200).json({ success: true });
    } catch (error) {
      console.error('Error saving user to database:', error);
      res.status(500).json({ error: 'Error saving user to database' });
    }
  } else {
    res.status(200).json({ success: true });
  }
}
