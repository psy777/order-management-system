import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';

const dbPath = path.resolve(process.cwd(), 'data/database.db');
const schemaPath = path.resolve(process.cwd(), 'schema.sql');

// Create the data directory if it doesn't exist
const dataDir = path.dirname(dbPath);
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}

const db = new Database(dbPath);
const schema = fs.readFileSync(schemaPath, 'utf-8');
db.exec(schema);

console.log('Database initialized successfully.');
