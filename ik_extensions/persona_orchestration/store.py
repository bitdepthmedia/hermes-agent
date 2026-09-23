"""Cell-local SQLite handoff state; never suitable for a shared database path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .envelope import DelegationEnvelope, canonical_digest, validate_envelope
from .envelope import Owner
from .transport import TransportAck


@dataclass(frozen=True)
class StoredHandoff:
    row_id: int
    task_id: str
    version: int
    status: str
    envelope: DelegationEnvelope
    next_attempt_at: datetime


class HandoffStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS handoff(
                  id INTEGER PRIMARY KEY, task_id TEXT UNIQUE NOT NULL,
                  idempotency_key TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
                  envelope TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                  version INTEGER NOT NULL DEFAULT 1, attempt INTEGER NOT NULL DEFAULT 0,
                  next_attempt_at TEXT NOT NULL, ack_sequence INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS handoff_event(
                  id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, event TEXT NOT NULL, at TEXT NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def enqueue_once(self, envelope: DelegationEnvelope, *, now: datetime | None = None) -> StoredHandoff:
        digest = canonical_digest(envelope)
        encoded = json.dumps(envelope.to_dict(), sort_keys=True, separators=(",", ":"))
        enqueued_at = (now or datetime.now(timezone.utc)).isoformat()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM handoff WHERE idempotency_key=?", (envelope.idempotency_key,)).fetchone()
            if existing:
                if existing["digest"] != digest:
                    raise ValueError("idempotency conflict")
                return self._stored(existing)
            cursor = db.execute("INSERT INTO handoff(task_id,idempotency_key,digest,envelope,next_attempt_at) VALUES(?,?,?,?,?)", (envelope.task_id, envelope.idempotency_key, digest, encoded, enqueued_at))
            db.execute("INSERT INTO handoff_event(task_id,event,at) VALUES(?,?,?)", (envelope.task_id, "enqueued", enqueued_at))
            row = db.execute("SELECT * FROM handoff WHERE id=?", (cursor.lastrowid,)).fetchone()
            return self._stored(row)

    def _stored(self, row: sqlite3.Row) -> StoredHandoff:
        return StoredHandoff(row["id"], row["task_id"], row["version"], row["status"], validate_envelope(json.loads(row["envelope"])), datetime.fromisoformat(row["next_attempt_at"]))

    def due(self, now: datetime, limit: int = 10) -> tuple[StoredHandoff, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM handoff WHERE status='pending' AND next_attempt_at<=? ORDER BY id LIMIT ?", (now.isoformat(), limit)).fetchall()
        return tuple(self._stored(row) for row in rows)

    def claim(self, task_id: str, expected_version: int, claimant: Owner) -> StoredHandoff:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            row = db.execute("SELECT * FROM handoff WHERE task_id=?", (task_id,)).fetchone()
            if not row or self._stored(row).envelope.owner != claimant:
                raise ValueError("claimant does not own handoff")
            cursor = db.execute(
                "UPDATE handoff SET status='claimed',version=version+1 WHERE task_id=? AND version=? AND status='pending'",
                (task_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise ValueError("handoff CAS claim lost")
            db.execute("INSERT INTO handoff_event(task_id,event,at) VALUES(?,?,?)", (task_id, f"claimed:{claimant.value}", now))
            claimed = db.execute("SELECT * FROM handoff WHERE task_id=?", (task_id,)).fetchone()
            return self._stored(claimed)

    def schedule_retry(self, task_id: str, when: datetime) -> None:
        with self._connect() as db:
            db.execute("UPDATE handoff SET attempt=attempt+1,next_attempt_at=?,version=version+1 WHERE task_id=? AND status='pending'", (when.isoformat(), task_id))

    def acknowledge(self, ack: TransportAck) -> None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM handoff WHERE task_id=?", (ack.task_id,)).fetchone()
            if not row or ack.envelope_digest != row["digest"] or ack.sequence <= row["ack_sequence"]:
                raise ValueError("acknowledgement binding or sequence invalid")
            db.execute("UPDATE handoff SET status='acknowledged',ack_sequence=?,version=version+1 WHERE task_id=?", (ack.sequence, ack.task_id))

    def count_pending(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT count(*) FROM handoff WHERE status='pending'").fetchone()[0])

    def by_idempotency_key(self, idempotency_key: str) -> StoredHandoff | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM handoff WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return self._stored(row) if row is not None else None

    def next_attempt_at(self, task_id: str) -> datetime:
        with self._connect() as db:
            value = db.execute("SELECT next_attempt_at FROM handoff WHERE task_id=?", (task_id,)).fetchone()[0]
        return datetime.fromisoformat(value)
