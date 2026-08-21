PRAGMA foreign_keys=ON;
CREATE TABLE handoff(task_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, envelope_digest TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE handoff_event(sequence INTEGER PRIMARY KEY, task_id TEXT NOT NULL, event_code TEXT NOT NULL, observed_at TEXT NOT NULL);
