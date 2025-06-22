import { google } from 'googleapis';
import { NextApiRequest, NextApiResponse } from 'next';
import db, { getSettings } from '@/lib/db';
import { Settings } from '@/lib/types';
import { unstable_getServerSession } from 'next-auth/next';
import { authOptions } from '@/pages/api/auth/[...nextauth]';

const handler = async (req: NextApiRequest, res: NextApiResponse) => {
  const session = await unstable_getServerSession(req, res, authOptions);

  if (!session || !session.user) {
    res.status(401).json({ message: 'You must be logged in.' });
    return;
  }

  const code = req.query.code as string;
  const settings = getSettings() as Settings;

  const oauth2Client = new google.auth.OAuth2(
    settings.GMAIL_CLIENT_ID,
    settings.GMAIL_CLIENT_SECRET,
    `${process.env.NEXT_PUBLIC_APP_URL}/api/auth/google/callback`
  );

  const { tokens } = await oauth2Client.getToken(code);
  const refreshToken = tokens.refresh_token;

  if (refreshToken) {
    const stmt = db.prepare('UPDATE users SET GMAIL_REFRESH_TOKEN = ? WHERE email = ?');
    stmt.run(refreshToken, session.user.email);
  }

  res.redirect('/settings');
};

export default handler;
