import { google } from 'googleapis';
import { NextApiRequest, NextApiResponse } from 'next';
import { getSettings } from '@/lib/db';
import { Settings } from '@/lib/types';

const handler = async (req: NextApiRequest, res: NextApiResponse) => {
  const settings = getSettings() as Settings;

  const oauth2Client = new google.auth.OAuth2(
    settings.GMAIL_CLIENT_ID,
    settings.GMAIL_CLIENT_SECRET,
    // This should be the full URL to your callback route
    `${process.env.NEXT_PUBLIC_APP_URL}/api/auth/google/callback`
  );

  const scopes = [
    'https://www.googleapis.com/auth/gmail.send'
  ];

  const url = oauth2Client.generateAuthUrl({
    access_type: 'offline',
    prompt: 'consent',
    scope: scopes
  });

  res.redirect(url);
};

export default handler;
