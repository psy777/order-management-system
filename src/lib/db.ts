import Database from 'better-sqlite3';
import path from 'path';

import { Settings } from './types';

const dbPath = path.resolve(process.cwd(), 'data/database.db');
const db = new Database(dbPath);

// Enable WAL mode for better concurrency
db.pragma('journal_mode = WAL');

export const getSettings = (): Settings => {
  const stmt = db.prepare('SELECT * FROM settings LIMIT 1');
  return stmt.get() as Settings;
};

export default db;
