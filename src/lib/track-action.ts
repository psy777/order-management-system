import db from "./db";

export function trackAction(action: string, sessionId: string | null) {
  if (!sessionId) {
    return;
  }
  const stmt = db.prepare(
    "INSERT INTO user_actions(session_id, action, happened_at) VALUES(?, ?, ?)"
  );
  stmt.run(sessionId, action, new Date().toISOString());
}
