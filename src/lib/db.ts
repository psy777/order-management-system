import path from 'path';
import fs from 'fs';
import Database from 'better-sqlite3';

let dbPath;

// This logic is crucial for deploying on a server like Render
if (process.env.NODE_ENV === 'production') {
  // In production, we use the absolute path to the persistent disk.
  // This path MUST match the Mount Path you set in Render's disk settings.
  dbPath = path.join('/app/data', 'database.db');

  // Ensure the directory exists on the persistent disk
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

} else {
  // In local development, we use a relative path in your project folder.
  dbPath = path.join(process.cwd(), 'data', 'database.db');
}

// This log will appear in your Render logs and show you the exact path being used.
console.log(`[DB] Connecting to database at: ${dbPath}`);

const db = new Database(dbPath);

// Enable WAL mode for better concurrency
db.pragma('journal_mode = WAL');

export function getSettings() {
    try {
        let settings = db.prepare('SELECT * FROM settings WHERE id = 1').get();
        if (!settings) {
            settings = {
                id: 1,
                company_name: '',
                default_shipping_zip_code: '',
                default_email_body: '',
                email_address: '',
                app_password: '',
                email_cc: '',
                email_bcc: '',
                GMAIL_CLIENT_ID: '',
                GMAIL_CLIENT_SECRET: '',
                GMAIL_REFRESH_TOKEN: ''
            };
        }
        return settings;
    } catch (error) {
        console.error("Failed to get settings:", error);
        throw new Error("Failed to get settings.");
    }
}

export default db;
