"""Date-idempotent daily-goal coordination state and receipts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo
from agent.redact import redact_sensitive_text


CLAIM_LEASE_SECONDS = 15 * 60
MAX_DELIVERY_ATTEMPTS = 2
_DELIVERY_FIELDS = {
    "telegram_delivery",
    "delivery_attempts",
    "delivery_last_attempt_at",
    "delivery_last_error",
}


def sanitize_delivery_error(error: object) -> str:
    text = redact_sensitive_text(str(error or ""))
    text = re.sub(
        r"https?://[^\s]+", "[REDACTED_URL]", text, flags=re.IGNORECASE
    )
    text = re.sub(
        r"\b(?:authorization|proxy-authorization)\s*:\s*[^\s]+(?:\s+[^\s]+)?",
        "Authorization: [REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL)|"
        r"token|secret|password|api[_-]?key|credential)\s*[=:]\s*[^\s&]+",
        "[REDACTED_SECRET]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bbot\d+:[A-Za-z0-9_-]+", "bot[REDACTED]", text)
    return text[:500]


class WorkStatus(str, Enum):
    PENDING_WORK = "PENDING_WORK"
    NO_PENDING_WORK = "NO_PENDING_WORK"
    UNKNOWN = "UNKNOWN"


class CycleState(str, Enum):
    AWAITING_ERNIE = "awaiting_ernie"
    AWAITING_BERT = "awaiting_bert"
    NORMAL_WORK = "normal_work"
    IMPROVEMENT_SELECTING = "improvement_selecting"
    IMPROVEMENT_RUNNING = "improvement_running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ActionKind(str, Enum):
    READ_ONLY_AUDIT = "read_only_audit"
    FOCUSED_TEST = "focused_test"
    DOCUMENTATION_DRAFT = "documentation_draft"
    PATCH_PROPOSAL = "patch_proposal"


@dataclass(frozen=True)
class ImprovementCandidate:
    candidate_id: str
    title: str
    category: str
    evidence: tuple[str, ...]
    impact: int
    recurrence: int
    confidence: int
    effort: int
    risk: int
    action_kind: ActionKind
    recommended_owner: str
    executor_id: str


@dataclass(frozen=True)
class AgentStatus:
    agent: str
    status: WorkStatus
    summary: str
    evidence: tuple[str, ...]
    freshness_at: str
    candidates: tuple[ImprovementCandidate, ...]
    history_complete: bool = False
    source_receipts: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailyCycle:
    cycle_id: str
    local_date: str
    state: CycleState
    payload: dict


@dataclass(frozen=True)
class DailyReceipt:
    cycle_id: str
    ernie_status: str
    bert_status: str
    trigger: str
    ranked_candidates: tuple[str, ...]
    selected_goal: str | None
    selection_reason: str | None
    owner: str | None
    collaborator: str | None
    actions: tuple[str, ...]
    verification: tuple[str, ...]
    blockers: tuple[str, ...]
    telegram_delivery: str
    outcome: str = ""
    review_statement: str | None = None
    review_hash: str | None = None
    review_source: str | None = None
    review_metrics_hash: str | None = None
    delivery_attempts: int = 0
    delivery_last_attempt_at: str | None = None
    delivery_last_error: str | None = None
    local_date: str = ""
    ernie_evidence: tuple[str, ...] = ()
    bert_evidence: tuple[str, ...] = ()
    ernie_freshness_at: str = ""
    bert_freshness_at: str = ""
    ernie_source_receipts: tuple[str, ...] = ()
    bert_source_receipts: tuple[str, ...] = ()
    review_observations: tuple[tuple[str, str], ...] = ()
    decision_integrity_hash: str = ""


def receipt_integrity_hash(receipt: DailyReceipt | dict) -> str:
    data = asdict(receipt) if isinstance(receipt, DailyReceipt) else dict(receipt)
    data.pop("decision_integrity_hash", None)
    for key in _DELIVERY_FIELDS:
        data.pop(key, None)
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def resolve_trigger(
    ernie: AgentStatus,
    bert: AgentStatus,
    *,
    now: datetime | None = None,
) -> CycleState:
    if (
        ernie.agent == "ernie"
        and ernie.status is WorkStatus.PENDING_WORK
        and bool(ernie.evidence)
    ) or (
        bert.agent == "bert"
        and bert.status is WorkStatus.PENDING_WORK
        and bool(bert.evidence)
    ):
        return CycleState.NORMAL_WORK
    current = (now or datetime.now(UTC)).astimezone(UTC)
    local_zone = ZoneInfo("America/New_York")
    for expected, status in (("ernie", ernie), ("bert", bert)):
        try:
            fresh = datetime.fromisoformat(
                status.freshness_at.replace("Z", "+00:00")
            )
            if fresh.tzinfo is None:
                raise ValueError
            fresh = fresh.astimezone(UTC)
            valid = (
                status.agent == expected
                and bool(status.evidence)
                and bool(status.source_receipts)
                and status.history_complete
                and timedelta(0) <= current - fresh <= timedelta(hours=24)
                and fresh.astimezone(local_zone).date()
                == current.astimezone(local_zone).date()
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            return CycleState.UNKNOWN
    statuses = {ernie.status, bert.status}
    if WorkStatus.PENDING_WORK in statuses:
        return CycleState.NORMAL_WORK
    if WorkStatus.UNKNOWN in statuses:
        return CycleState.UNKNOWN
    if statuses == {WorkStatus.NO_PENDING_WORK}:
        return CycleState.IMPROVEMENT_SELECTING
    return CycleState.UNKNOWN


class DailyGoalStore:
    """SQLite persistence for exactly one daily cycle and receipt per local date."""

    def __init__(self, database_path: str | Path):
        self._conn = sqlite3.connect(str(database_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_goal_cycles (
                cycle_id TEXT PRIMARY KEY,
                local_date TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_goal_receipts (
                cycle_id TEXT PRIMARY KEY,
                receipt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_goal_claims (
                cycle_id TEXT NOT NULL,
                claim_kind TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                PRIMARY KEY (cycle_id, claim_kind),
                FOREIGN KEY (cycle_id) REFERENCES daily_goal_cycles(cycle_id)
            );
            CREATE TABLE IF NOT EXISTS daily_goal_outbox (
                cycle_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (cycle_id) REFERENCES daily_goal_cycles(cycle_id)
            );
            CREATE TABLE IF NOT EXISTS daily_goal_alert_outbox (
                cycle_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                last_error TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_goal_retry_claims (
                cycle_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._conn.commit()

    def get_or_create_cycle(self, local_date: date) -> DailyCycle:
        cycle_id = f"daily-goal:{local_date.isoformat()}"
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO daily_goal_cycles
            (cycle_id, local_date, state, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                local_date.isoformat(),
                CycleState.AWAITING_ERNIE.value,
                "{}",
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_cycle(cycle_id)

    def get_cycle(self, cycle_id: str) -> DailyCycle:
        row = self._conn.execute(
            "SELECT cycle_id, local_date, state, payload "
            "FROM daily_goal_cycles WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone()
        if row is None:
            raise KeyError(cycle_id)
        return DailyCycle(
            row["cycle_id"],
            row["local_date"],
            CycleState(row["state"]),
            json.loads(row["payload"]),
        )

    def update_cycle(
        self, cycle_id: str, state: CycleState, payload: dict
    ) -> DailyCycle:
        self._conn.execute(
            "UPDATE daily_goal_cycles SET state = ?, payload = ?, updated_at = ? "
            "WHERE cycle_id = ?",
            (
                state.value,
                json.dumps(payload, sort_keys=True),
                datetime.now(UTC).isoformat(),
                cycle_id,
            ),
        )
        self._conn.commit()
        return self.get_cycle(cycle_id)

    def try_claim(
        self,
        cycle_id: str,
        claim_kind: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Atomically claim one check-in or UNKNOWN retry across connections."""
        if claim_kind not in {"checkin", "unknown_retry"}:
            raise ValueError("unsupported daily goal claim kind")
        claimed_at = now or datetime.now(UTC)
        if claimed_at.tzinfo is None:
            raise ValueError("claim time must be timezone-aware")
        claimed_at = claimed_at.astimezone(UTC)
        stale_before = claimed_at - timedelta(seconds=CLAIM_LEASE_SECONDS)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if claim_kind == "unknown_retry":
                cursor = self._conn.execute(
                    """
                    INSERT INTO daily_goal_retry_claims
                    (cycle_id, state, claimed_at, attempt_count)
                    VALUES (?, 'in_progress', ?, 1)
                    ON CONFLICT(cycle_id) DO UPDATE SET
                      state='in_progress', claimed_at=excluded.claimed_at,
                      attempt_count=daily_goal_retry_claims.attempt_count+1
                    WHERE daily_goal_retry_claims.state='in_progress'
                      AND daily_goal_retry_claims.claimed_at <= ?
                    """,
                    (
                        cycle_id,
                        claimed_at.isoformat(),
                        stale_before.isoformat(),
                    ),
                )
            else:
                cursor = self._conn.execute(
                """
                INSERT INTO daily_goal_claims
                (cycle_id, claim_kind, claimed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cycle_id, claim_kind) DO UPDATE
                SET claimed_at = excluded.claimed_at
                WHERE daily_goal_claims.claimed_at <= ?
                """,
                (
                    cycle_id,
                    claim_kind,
                    claimed_at.isoformat(),
                    stale_before.isoformat(),
                ),
                )
            self._conn.commit()
            return cursor.rowcount == 1
        except Exception:
            self._conn.rollback()
            raise

    def complete_unknown_retry(self, cycle_id: str) -> None:
        cursor = self._conn.execute(
            "UPDATE daily_goal_retry_claims SET state='completed' "
            "WHERE cycle_id=? AND state='in_progress'",
            (cycle_id,),
        )
        self._conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("unknown retry is not in progress")

    def save_receipt(self, receipt: DailyReceipt) -> DailyReceipt:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT receipt FROM daily_goal_receipts WHERE cycle_id = ?",
                (receipt.cycle_id,),
            ).fetchone()
            data = asdict(receipt)
            if not data.get("decision_integrity_hash"):
                data["decision_integrity_hash"] = receipt_integrity_hash(data)
            now = datetime.now(UTC).isoformat()
            if row is not None:
                current = json.loads(row["receipt"])
                normalized_data = json.loads(json.dumps(data))
                semantic_current = {
                    key: value
                    for key, value in current.items()
                    if key not in _DELIVERY_FIELDS
                }
                semantic_new = {
                    key: value
                    for key, value in normalized_data.items()
                    if key not in _DELIVERY_FIELDS
                }
                if semantic_current != semantic_new:
                    self._conn.execute(
                        """
                        INSERT INTO daily_goal_outbox
                        (cycle_id, state, attempt_count, last_attempt_at,
                         last_error, updated_at)
                        VALUES (?, 'pending', 0, NULL, NULL, ?)
                        ON CONFLICT(cycle_id) DO UPDATE SET
                            state = 'pending',
                            attempt_count = 0,
                            last_attempt_at = NULL,
                            last_error = NULL,
                            updated_at = excluded.updated_at
                        """,
                        (receipt.cycle_id, now),
                    )
            self._conn.execute(
                """
                INSERT OR IGNORE INTO daily_goal_outbox
                (cycle_id, state, attempt_count, last_attempt_at,
                 last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.cycle_id,
                    receipt.telegram_delivery,
                    receipt.delivery_attempts,
                    receipt.delivery_last_attempt_at,
                    receipt.delivery_last_error,
                    now,
                ),
            )
            outbox = self._conn.execute(
                """
                SELECT state, attempt_count, last_attempt_at, last_error
                FROM daily_goal_outbox WHERE cycle_id = ?
                """,
                (receipt.cycle_id,),
            ).fetchone()
            if outbox is None:
                raise RuntimeError("daily goal outbox was not created")
            data.update(
                {
                    "telegram_delivery": outbox["state"],
                    "delivery_attempts": outbox["attempt_count"],
                    "delivery_last_attempt_at": outbox["last_attempt_at"],
                    "delivery_last_error": outbox["last_error"],
                }
            )
            self._conn.execute(
                """
                INSERT INTO daily_goal_receipts (cycle_id, receipt, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET receipt = excluded.receipt
                """,
                (
                    receipt.cycle_id,
                    json.dumps(data, sort_keys=True),
                    now,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        saved = self.get_receipt(receipt.cycle_id)
        assert saved is not None
        return saved

    def get_receipt(self, cycle_id: str) -> DailyReceipt | None:
        row = self._conn.execute(
            """
            SELECT r.receipt, o.state, o.attempt_count, o.last_attempt_at,
                   o.last_error
            FROM daily_goal_receipts AS r
            LEFT JOIN daily_goal_outbox AS o ON o.cycle_id = r.cycle_id
            WHERE r.cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["receipt"])
        supplied_integrity = data.get("decision_integrity_hash")
        if supplied_integrity:
            if supplied_integrity != receipt_integrity_hash(data):
                raise ValueError("daily goal decision integrity hash mismatch")
        elif data.get("outcome") == "completed":
            raise ValueError("completed daily goal receipt lacks decision integrity")
        review_values = (
            data.get("review_statement"),
            data.get("review_hash"),
            data.get("review_source"),
            data.get("review_metrics_hash"),
        )
        if data.get("outcome") == "completed" and not all(
            isinstance(value, str) and value for value in review_values
        ):
            raise ValueError("completed daily goal review receipt is incomplete")
        if data.get("outcome") == "completed" and not data.get(
            "review_observations"
        ):
            raise ValueError(
                "completed daily goal review observations are incomplete"
            )
        if any(value is not None for value in review_values):
            if not all(isinstance(value, str) and value for value in review_values):
                raise ValueError("daily goal review receipt is incomplete")
            expected_review_hash = hashlib.sha256(
                json.dumps(
                    {
                        "metrics_hash": data["review_metrics_hash"],
                        "source": data["review_source"],
                        "statement": data["review_statement"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if data["review_hash"] != expected_review_hash:
                raise ValueError("daily goal review receipt hash mismatch")
            if (
                len(data["review_metrics_hash"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in data["review_metrics_hash"]
                )
            ):
                raise ValueError("daily goal review metrics hash is invalid")
        delivery_state = row["state"] or data.get("telegram_delivery") or "pending"
        delivery_attempts = (
            row["attempt_count"]
            if row["attempt_count"] is not None
            else int(data.get("delivery_attempts") or 0)
        )
        return DailyReceipt(
            cycle_id=data["cycle_id"],
            ernie_status=data["ernie_status"],
            bert_status=data["bert_status"],
            trigger=data["trigger"],
            ranked_candidates=tuple(data["ranked_candidates"]),
            selected_goal=data["selected_goal"],
            selection_reason=data["selection_reason"],
            owner=data["owner"],
            collaborator=data["collaborator"],
            actions=tuple(data["actions"]),
            verification=tuple(data["verification"]),
            blockers=tuple(data["blockers"]),
            telegram_delivery=delivery_state,
            outcome=str(data.get("outcome") or ""),
            review_statement=data.get("review_statement"),
            review_hash=data.get("review_hash"),
            review_source=data.get("review_source"),
            review_metrics_hash=data.get("review_metrics_hash"),
            delivery_attempts=delivery_attempts,
            delivery_last_attempt_at=(
                row["last_attempt_at"]
                if row["last_attempt_at"] is not None
                else data.get("delivery_last_attempt_at")
            ),
            delivery_last_error=(
                row["last_error"]
                if row["last_error"] is not None
                else data.get("delivery_last_error")
            ),
            local_date=str(data.get("local_date") or ""),
            ernie_evidence=tuple(data.get("ernie_evidence") or ()),
            bert_evidence=tuple(data.get("bert_evidence") or ()),
            ernie_freshness_at=str(data.get("ernie_freshness_at") or ""),
            bert_freshness_at=str(data.get("bert_freshness_at") or ""),
            ernie_source_receipts=tuple(data.get("ernie_source_receipts") or ()),
            bert_source_receipts=tuple(data.get("bert_source_receipts") or ()),
            review_observations=tuple(
                tuple(value) for value in data.get("review_observations") or ()
            ),
            decision_integrity_hash=str(supplied_integrity or ""),
        )

    def begin_delivery(
        self,
        cycle_id: str,
        *,
        now: datetime | None = None,
    ) -> DailyReceipt | None:
        """Atomically claim one safe Telegram delivery attempt."""
        attempted_at = now or datetime.now(UTC)
        if attempted_at.tzinfo is None:
            raise ValueError("delivery attempt time must be timezone-aware")
        attempted_at = attempted_at.astimezone(UTC).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                """
                SELECT state, attempt_count FROM daily_goal_outbox
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()
            if row is None:
                raise KeyError(cycle_id)
            eligible = (
                (row["state"] == "pending" and row["attempt_count"] == 0)
                or (
                    row["state"] == "failed"
                    and row["attempt_count"] < MAX_DELIVERY_ATTEMPTS
                )
            )
            if not eligible:
                self._conn.rollback()
                return None
            self._conn.execute(
                """
                UPDATE daily_goal_outbox
                SET state = 'attempting',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE cycle_id = ?
                """,
                (attempted_at, attempted_at, cycle_id),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return self.get_receipt(cycle_id)

    def update_delivery(
        self,
        cycle_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> DailyReceipt:
        if status not in {"delivered", "failed", "unknown"}:
            raise ValueError("unsupported Telegram delivery status")
        bounded_error = None if error is None else sanitize_delivery_error(error)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT state FROM daily_goal_outbox WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()
            if row is None:
                raise KeyError(cycle_id)
            current = row["state"]
            if current == "delivered" or (
                current == "unknown" and status != "delivered"
            ):
                status = current
            self._conn.execute(
                """
                UPDATE daily_goal_outbox
                SET state = ?, last_error = ?, updated_at = ?
                WHERE cycle_id = ?
                """,
                (
                    status,
                    bounded_error,
                    datetime.now(UTC).isoformat(),
                    cycle_id,
                ),
            )
            attempt_count = self._conn.execute(
                "SELECT attempt_count FROM daily_goal_outbox WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()["attempt_count"]
            if status == "unknown" or (
                status == "failed" and attempt_count >= MAX_DELIVERY_ATTEMPTS
            ):
                reason = "ambiguous" if status == "unknown" else "exhausted"
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO daily_goal_alert_outbox
                    (cycle_id, state, attempt_count, last_attempt_at,
                     last_error, updated_at)
                    VALUES (?, 'pending', 0, NULL, ?, ?)
                    """,
                    (
                        cycle_id,
                        f"{reason}: {bounded_error or 'delivery outcome unavailable'}"[:500],
                        datetime.now(UTC).isoformat(),
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        updated = self.get_receipt(cycle_id)
        assert updated is not None
        return updated

    def get_alert(self, cycle_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM daily_goal_alert_outbox WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone()

    def get_next_alert(self) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM daily_goal_alert_outbox "
            "WHERE state IN ('pending','failed') AND attempt_count < ? "
            "ORDER BY updated_at, cycle_id LIMIT 1",
            (MAX_DELIVERY_ATTEMPTS,),
        ).fetchone()

    def begin_alert_delivery(self, cycle_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = self._conn.execute(
            """
            UPDATE daily_goal_alert_outbox SET state='attempting',
            attempt_count=attempt_count+1,last_attempt_at=?,updated_at=?
            WHERE cycle_id=? AND state IN ('pending','failed') AND attempt_count < ?
            """,
            (now, now, cycle_id, MAX_DELIVERY_ATTEMPTS),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def update_alert_delivery(
        self, cycle_id: str, status: str, *, error: str | None = None
    ) -> None:
        if status not in {"delivered", "failed", "unknown"}:
            raise ValueError("unsupported alert delivery status")
        if error:
            self._conn.execute(
                "UPDATE daily_goal_alert_outbox SET state=?, last_error=?, updated_at=? "
                "WHERE cycle_id=? AND state!='delivered'",
                (status, sanitize_delivery_error(error), datetime.now(UTC).isoformat(), cycle_id),
            )
        else:
            self._conn.execute(
                "UPDATE daily_goal_alert_outbox SET state=?, updated_at=? "
                "WHERE cycle_id=? AND state!='delivered'",
                (status, datetime.now(UTC).isoformat(), cycle_id),
            )
        self._conn.commit()

    def list_cycles(self) -> list[DailyCycle]:
        rows = self._conn.execute(
            "SELECT cycle_id FROM daily_goal_cycles ORDER BY local_date"
        ).fetchall()
        return [self.get_cycle(row["cycle_id"]) for row in rows]
