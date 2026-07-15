"""SQLite-backed task, worker, policy, handoff, and workflow control plane."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

from .policy import DataPolicy


class TaskOwner(str, Enum):
    BERT = "bert"
    ERNIE = "ernie"


class ActionClass(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class TaskState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    READY_TO_SYNTHESIZE = "ready_to_synthesize"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerState(str, Enum):
    RUNNING = "running"
    CLEANED_UP = "cleaned_up"


@dataclass(frozen=True)
class Task:
    id: str
    owner: TaskOwner
    session_id: str
    request: str
    action_class: ActionClass
    state: TaskState


@dataclass(frozen=True)
class WorkerRun:
    id: str
    task_id: str
    capability: str
    scope: str
    state: WorkerState


@dataclass(frozen=True)
class Handoff:
    id: str
    task_id: str
    recipient: TaskOwner
    sanitized_content: str
    finding_kinds: set[str]


@dataclass(frozen=True)
class Workflow:
    id: str
    pattern: str
    reversible: bool
    active: bool
    review_due_at: str | None


@dataclass(frozen=True)
class PolicyRule:
    id: str
    kind: str
    pattern: str
    active: bool


@dataclass(frozen=True)
class AuditEvent:
    task_id: str
    kind: str
    data: dict


class SharedCore:
    """The local source of truth. It never stores unsanitized handoff content."""

    def __init__(self, database_path: str | Path):
        self._conn = sqlite3.connect(str(database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL,
                request TEXT NOT NULL, action_class TEXT NOT NULL, state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, capability TEXT NOT NULL,
                scope TEXT NOT NULL, state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS handoffs (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, recipient TEXT NOT NULL,
                sanitized_content TEXT NOT NULL, finding_kinds TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_rules (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, pattern TEXT NOT NULL, active INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, pattern TEXT NOT NULL, reversible INTEGER NOT NULL,
                active INTEGER NOT NULL, review_due_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def create_task(self, *, owner: TaskOwner, session_id: str, request: str, action_class: ActionClass) -> Task:
        task = Task(uuid.uuid4().hex, owner, session_id, request, action_class, TaskState.PLANNED)
        self._conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
            (task.id, task.owner.value, task.session_id, task.request, task.action_class.value, task.state.value),
        )
        self._audit(task.id, "task.created", {"owner": owner.value, "action_class": action_class.value})
        self._conn.commit()
        return task

    def get_task(self, task_id: str) -> Task:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return Task(row["id"], TaskOwner(row["owner"]), row["session_id"], row["request"], ActionClass(row["action_class"]), TaskState(row["state"]))

    def start_worker(self, task_id: str, *, capability: str, scope: str) -> WorkerRun:
        worker = WorkerRun(uuid.uuid4().hex, task_id, capability, scope, WorkerState.RUNNING)
        self._conn.execute("UPDATE tasks SET state = ? WHERE id = ?", (TaskState.RUNNING.value, task_id))
        self._conn.execute("INSERT INTO workers VALUES (?, ?, ?, ?, ?)", (worker.id, task_id, capability, scope, worker.state.value))
        self._audit(task_id, "worker.started", {"capability": capability, "scope": scope})
        self._conn.commit()
        return worker

    def complete_worker(self, worker_id: str, *, evidence: dict, result_valid: bool) -> None:
        row = self._conn.execute("SELECT task_id FROM workers WHERE id = ?", (worker_id,)).fetchone()
        if row is None:
            raise ValueError("unknown worker")
        state = TaskState.READY_TO_SYNTHESIZE if result_valid else TaskState.FAILED
        self._conn.execute("UPDATE workers SET state = ? WHERE id = ?", (WorkerState.CLEANED_UP.value, worker_id))
        self._conn.execute("UPDATE tasks SET state = ? WHERE id = ?", (state.value, row["task_id"]))
        self._audit(row["task_id"], "worker.completed", {"result_valid": result_valid, "evidence": evidence})
        self._conn.commit()

    def get_worker(self, worker_id: str) -> WorkerRun:
        row = self._conn.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)).fetchone()
        return WorkerRun(row["id"], row["task_id"], row["capability"], row["scope"], WorkerState(row["state"]))

    def create_handoff(self, task_id: str, *, recipient: TaskOwner, content: str) -> Handoff:
        sanitized = self.policy().sanitize(content)
        handoff = Handoff(uuid.uuid4().hex, task_id, recipient, sanitized.content, sanitized.finding_kinds)
        self._conn.execute(
            "INSERT INTO handoffs VALUES (?, ?, ?, ?, ?)",
            (handoff.id, task_id, recipient.value, handoff.sanitized_content, json.dumps(sorted(handoff.finding_kinds))),
        )
        self._audit(task_id, "handoff.created", {"recipient": recipient.value, "finding_kinds": sorted(handoff.finding_kinds)})
        self._conn.commit()
        return handoff

    def complete_task(self, task_id: str, *, success: bool) -> None:
        self._conn.execute("UPDATE tasks SET state = ? WHERE id = ?", ((TaskState.COMPLETED if success else TaskState.FAILED).value, task_id))
        self._audit(task_id, "task.completed", {"success": success})
        self._conn.commit()

    def evaluate_pattern(self, pattern: str) -> Workflow | None:
        rows = self._conn.execute("SELECT session_id, action_class FROM tasks WHERE lower(request) = lower(?) AND state = ?", (pattern, TaskState.COMPLETED.value)).fetchall()
        if len(rows) < 3 or len({row["session_id"] for row in rows}) < 2:
            return None
        action_classes = {row["action_class"] for row in rows}
        if not action_classes <= {ActionClass.READ_ONLY.value, ActionClass.REVERSIBLE.value}:
            return None
        existing = self._conn.execute("SELECT * FROM workflows WHERE lower(pattern) = lower(?)", (pattern,)).fetchone()
        if existing:
            return self._workflow(existing)
        review_due = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        workflow = Workflow(uuid.uuid4().hex, pattern, True, True, review_due)
        self._conn.execute("INSERT INTO workflows VALUES (?, ?, ?, ?, ?)", (workflow.id, workflow.pattern, int(workflow.reversible), int(workflow.active), workflow.review_due_at))
        self._audit("workflow:" + workflow.id, "workflow.auto_activated", {"pattern": pattern})
        self._conn.commit()
        return workflow

    def propose_policy_rule(self, kind: str, pattern: str) -> PolicyRule:
        rule = PolicyRule(uuid.uuid4().hex, kind, pattern, False)
        self._conn.execute("INSERT INTO policy_rules VALUES (?, ?, ?, ?)", (rule.id, kind, pattern, 0))
        self._audit("policy:" + rule.id, "policy.proposed", {"kind": kind})
        self._conn.commit()
        return rule

    def approve_policy_rule(self, rule_id: str, *, reviewer: str) -> None:
        self._conn.execute("UPDATE policy_rules SET active = 1 WHERE id = ?", (rule_id,))
        self._audit("policy:" + rule_id, "policy.approved", {"reviewer": reviewer})
        self._conn.commit()

    def policy(self) -> DataPolicy:
        rows = self._conn.execute("SELECT kind, pattern FROM policy_rules WHERE active = 1").fetchall()
        return DataPolicy((row["kind"], row["pattern"]) for row in rows)

    def audit_events(self, task_id: str) -> list[AuditEvent]:
        rows = self._conn.execute("SELECT task_id, kind, data FROM audit_events WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        return [AuditEvent(row["task_id"], row["kind"], json.loads(row["data"])) for row in rows]

    def _audit(self, task_id: str, kind: str, data: dict) -> None:
        self._conn.execute("INSERT INTO audit_events (task_id, kind, data, created_at) VALUES (?, ?, ?, ?)", (task_id, kind, json.dumps(data, sort_keys=True), datetime.now(UTC).isoformat()))

    @staticmethod
    def _workflow(row: sqlite3.Row) -> Workflow:
        return Workflow(row["id"], row["pattern"], bool(row["reversible"]), bool(row["active"]), row["review_due_at"])
