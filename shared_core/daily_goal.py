"""Date-idempotent daily-goal coordination state and receipts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path


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


def resolve_trigger(ernie: AgentStatus, bert: AgentStatus) -> CycleState:
    statuses = {ernie.status, bert.status}
    if WorkStatus.UNKNOWN in statuses:
        return CycleState.UNKNOWN
    if WorkStatus.PENDING_WORK in statuses:
        return CycleState.NORMAL_WORK
    if statuses == {WorkStatus.NO_PENDING_WORK}:
        return CycleState.IMPROVEMENT_SELECTING
    return CycleState.UNKNOWN


class DailyGoalStore:
    """SQLite persistence for exactly one daily cycle and receipt per local date."""

    def __init__(self, database_path: str | Path):
        self._conn = sqlite3.connect(str(database_path))
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

    def update_cycle(self, cycle_id: str, state: CycleState, payload: dict) -> DailyCycle:
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

    def save_receipt(self, receipt: DailyReceipt) -> DailyReceipt:
        self._conn.execute(
            """
            INSERT INTO daily_goal_receipts (cycle_id, receipt, created_at) VALUES (?, ?, ?)
            ON CONFLICT(cycle_id) DO UPDATE SET receipt = excluded.receipt
            """,
            (
                receipt.cycle_id,
                json.dumps(asdict(receipt), sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        saved = self.get_receipt(receipt.cycle_id)
        assert saved is not None
        return saved

    def get_receipt(self, cycle_id: str) -> DailyReceipt | None:
        row = self._conn.execute(
            "SELECT receipt FROM daily_goal_receipts WHERE cycle_id = ?", (cycle_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["receipt"])
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
            telegram_delivery=data["telegram_delivery"],
        )

    def update_delivery(self, cycle_id: str, status: str) -> DailyReceipt:
        receipt = self.get_receipt(cycle_id)
        if receipt is None:
            raise KeyError(cycle_id)
        data = asdict(receipt)
        data["telegram_delivery"] = status
        self._conn.execute(
            "UPDATE daily_goal_receipts SET receipt = ? WHERE cycle_id = ?",
            (json.dumps(data, sort_keys=True), cycle_id),
        )
        self._conn.commit()
        updated = self.get_receipt(cycle_id)
        assert updated is not None
        return updated

    def list_cycles(self) -> list[DailyCycle]:
        rows = self._conn.execute(
            "SELECT cycle_id FROM daily_goal_cycles ORDER BY local_date"
        ).fetchall()
        return [self.get_cycle(row["cycle_id"]) for row in rows]
