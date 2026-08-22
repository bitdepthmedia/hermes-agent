"""Privacy-safe semantic continuity rehearsal for an approved opaque snapshot.

Private content is inspected only inside this module.  Serialized evidence is
strictly aggregate and never contains paths, filenames, schema identifiers, row
values, or private identifiers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import tarfile
from typing import Any, Callable, Iterable

from .opaque_backup import (
    MacOSStorageAttestor,
    OpaqueBackupReceipt,
    _backup_sqlite_opaquely,
    _clone_permissions_clear,
    _copy_regular_opaquely,
    _hmac_file,
    _receipt_payload,
    _secure_mkdir,
    _sha256_file,
    _source_entries,
    _tree_digest,
)


_SCHEMA = "ik.semantic-continuity-rehearsal.v6"
_OPENSSL = Path("/usr/bin/openssl")
_HEX64 = frozenset("0123456789abcdef")
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "snapshot_id",
        "rehearsal_id",
        "created_at",
        "source_tree_sha256",
        "restored_tree_sha256",
        "migrated_tree_sha256",
        "architecture_contract_sha256",
        "candidate_release_manifest_sha256",
        "candidate_python_sha256",
        "structural_hmac_sha256",
        "aggregate_counts",
        "validation_counts",
        "discrepancy_classes",
        "permission_state",
        "rollback_state",
        "status",
    }
)
_COUNT_KEYS = (
    "artifacts",
    "bytes",
    "sensitive_artifacts_excluded",
    "sqlite_databases",
    "sqlite_tables",
    "sqlite_rows",
    "session_records",
    "message_records",
    "tool_call_records",
    "tool_result_records",
    "custom_role_records",
    "task_records",
    "schedule_records",
    "ledger_artifacts",
    "ledger_records",
    "daily_goal_records",
    "ownership_records",
    "provenance_records",
    "approval_records",
    "status_records",
    "timestamp_values",
    "persona_artifacts",
    "memory_artifacts",
)
_SENSITIVE_EXACT = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "auth.json",
        "credentials.json",
        "secrets.json",
        "tokens.json",
    }
)
_SENSITIVE_COMPONENTS = frozenset({"credentials", "secrets", "keychain", "private-keys"})
_SENSITIVE_MARKERS = (
    "credential",
    "secret",
    "password",
    "token",
    "oauth",
    "api-key",
    "apikey",
    "private-key",
)
_SENSITIVE_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".cert")
_TIMESTAMP_MARKERS = ("timestamp", "_at", "_time", "started", "ended", "expires", "heartbeat")
_ALLOWED_MESSAGE_ROLES = frozenset({"system", "developer", "user", "assistant", "tool", "function"})


class SemanticContinuityError(RuntimeError):
    """A redacted fail-closed semantic rehearsal error."""


@dataclass(frozen=True)
class SemanticRehearsalRequest:
    storage_root: Path
    snapshot_id: str
    rollback_handle: str
    rehearsal_id: str
    expected_snapshot_tree_sha256: str
    expected_archive_sha256: str
    expected_archive_hmac_sha256: str
    architecture_contract_sha256: str
    candidate_source_root: Path
    candidate_manifest_path: Path
    expected_candidate_manifest_sha256: str
    candidate_python: Path
    expected_candidate_python_sha256: str
    denied_roots: tuple[Path, ...]


@dataclass(frozen=True)
class SemanticRehearsalReceipt:
    schema_version: str
    snapshot_id: str
    rehearsal_id: str
    created_at: str
    source_tree_sha256: str
    restored_tree_sha256: str
    migrated_tree_sha256: str
    architecture_contract_sha256: str
    candidate_release_manifest_sha256: str
    candidate_python_sha256: str
    structural_hmac_sha256: str
    aggregate_counts: dict[str, int]
    validation_counts: dict[str, int]
    discrepancy_classes: tuple[str, ...]
    permission_state: str
    rollback_state: str
    status: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["discrepancy_classes"] = list(self.discrepancy_classes)
        return payload


@dataclass(frozen=True)
class SemanticRehearsalResult:
    receipt: SemanticRehearsalReceipt
    restored_root: Path
    migrated_root: Path
    receipt_path: Path


@dataclass
class _ScanResult:
    counts: Counter[str]
    checks: int
    passed: int
    failed: int
    retrieval_cases: int
    discrepancies: set[str]
    structural_hmac: str


class _Evidence:
    def __init__(self, key: bytes) -> None:
        self.counts: Counter[str] = Counter({name: 0 for name in _COUNT_KEYS})
        self.checks = 0
        self.passed = 0
        self.failed = 0
        self.retrieval_cases = 0
        self.discrepancies: set[str] = set()
        self.digest = hmac.new(key, digestmod=hashlib.sha256)

    def check(self, condition: bool, discrepancy: str) -> None:
        self.checks += 1
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            self.discrepancies.add(discrepancy)

    def bind(self, category: str, value: object) -> None:
        self.digest.update(category.encode("ascii") + b"\0")
        if isinstance(value, bytes):
            self.digest.update(value)
        else:
            self.digest.update(str(value).encode("utf-8", "surrogateescape"))
        self.digest.update(b"\0")


def _safe_token(value: str, label: str) -> str:
    if not value or len(value) > 96 or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value):
        raise SemanticContinuityError(f"{label}_invalid")
    return value


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and set(value) <= _HEX64


def _sensitive_path(relative: PurePosixPath) -> bool:
    components = tuple(part.lower() for part in relative.parts)
    name = components[-1] if components else ""
    return bool(
        name in _SENSITIVE_EXACT
        or any(component in _SENSITIVE_COMPONENTS for component in components)
        or any(marker in name for marker in _SENSITIVE_MARKERS)
        or name.endswith(_SENSITIVE_SUFFIXES)
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _canonical_cell(value: object) -> bytes:
    if value is None:
        return b"null"
    if isinstance(value, bytes):
        return b"blob:" + hashlib.sha256(value).hexdigest().encode("ascii")
    if isinstance(value, float):
        return ("float:" + repr(value)).encode("ascii")
    return (type(value).__name__ + ":" + str(value)).encode("utf-8", "surrogateescape")


def _timestamp_valid(value: object) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, (int, float)):
        return value >= 0
    if isinstance(value, str):
        try:
            numeric = float(value)
            return numeric >= 0
        except ValueError:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                return True
            except ValueError:
                return False
    return False


def _json_tool_call_ids(value: object) -> set[str]:
    if not value:
        return set()
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return set()
    if isinstance(payload, dict):
        payload = payload.get("tool_calls", [payload])
    if not isinstance(payload, list):
        return set()
    return {str(item.get("id")) for item in payload if isinstance(item, dict) and item.get("id")}


def _table_columns(database: sqlite3.Connection, table: str) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in database.execute(f"PRAGMA table_info({_quote_identifier(table)})"))


def _scan_session_surface(database: sqlite3.Connection, tables: set[str], evidence: _Evidence) -> None:
    if not {"sessions", "messages"} <= tables:
        return
    session_columns = {str(row[1]) for row in _table_columns(database, "sessions")}
    message_columns = {str(row[1]) for row in _table_columns(database, "messages")}
    session_count = int(database.execute("SELECT count(*) FROM sessions").fetchone()[0])
    message_count = int(database.execute("SELECT count(*) FROM messages").fetchone()[0])
    evidence.counts["session_records"] += session_count
    evidence.counts["message_records"] += message_count
    evidence.check("id" in session_columns and "session_id" in message_columns, "session_schema_incompatible")
    if "id" in session_columns and "session_id" in message_columns:
        orphans = int(
            database.execute(
                "SELECT count(*) FROM messages m LEFT JOIN sessions s ON s.id=m.session_id WHERE s.id IS NULL"
            ).fetchone()[0]
        )
        evidence.check(orphans == 0, "session_message_relationship")
    if "role" in message_columns:
        roles = [row[0] for row in database.execute("SELECT role FROM messages")]
        custom_roles = [role for role in roles if role not in _ALLOWED_MESSAGE_ROLES]
        evidence.counts["custom_role_records"] += len(custom_roles)
        evidence.check(
            all(
                isinstance(role, str)
                and bool(role.strip())
                and len(role) <= 64
                and not any(ord(character) < 32 for character in role)
                for role in roles
            ),
            "message_role_invalid",
        )
    call_ids: set[str] = set()
    result_ids: set[str] = set()
    if "tool_calls" in message_columns:
        for (value,) in database.execute("SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL"):
            call_ids.update(_json_tool_call_ids(value))
    if {"role", "tool_call_id"} <= message_columns:
        result_ids = {
            str(row[0])
            for row in database.execute(
                "SELECT tool_call_id FROM messages WHERE role IN ('tool','function') AND tool_call_id IS NOT NULL"
            )
        }
    evidence.counts["tool_call_records"] += len(call_ids)
    evidence.counts["tool_result_records"] += len(result_ids)
    evidence.check(call_ids == result_ids, "tool_relationship_unpaired")


def _scan_task_surface(database: sqlite3.Connection, tables: set[str], evidence: _Evidence) -> None:
    if "tasks" not in tables:
        return
    columns = {str(row[1]) for row in _table_columns(database, "tasks")}
    task_count = int(database.execute("SELECT count(*) FROM tasks").fetchone()[0])
    evidence.counts["task_records"] += task_count
    evidence.check("id" in columns and "status" in columns, "task_schema_incompatible")
    if task_count and "id" in columns:
        missing_ids = int(database.execute("SELECT count(*) FROM tasks WHERE id IS NULL OR trim(CAST(id AS TEXT))='' ").fetchone()[0])
        evidence.check(missing_ids == 0, "task_id_invalid")
    if task_count and "status" in columns:
        missing_status = int(database.execute("SELECT count(*) FROM tasks WHERE status IS NULL OR trim(CAST(status AS TEXT))='' ").fetchone()[0])
        evidence.check(missing_status == 0, "task_status_invalid")
    ownership_columns = columns & {"owner", "assignee", "created_by", "requester_persona"}
    evidence.check(task_count == 0 or bool(ownership_columns), "task_ownership_unverifiable")
    if "task_events" in tables:
        event_columns = {str(row[1]) for row in _table_columns(database, "task_events")}
        if "task_id" in event_columns and "id" in columns:
            orphans = int(
                database.execute(
                    "SELECT count(*) FROM task_events e LEFT JOIN tasks t ON t.id=e.task_id WHERE t.id IS NULL"
                ).fetchone()[0]
            )
            evidence.check(orphans == 0, "task_ledger_relationship")


def _scan_database(path: Path, evidence: _Evidence) -> None:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri, uri=True, timeout=30) as database:
            database.execute("PRAGMA query_only=ON")
            integrity_rows = tuple(str(row[0]) for row in database.execute("PRAGMA integrity_check"))
            foreign_rows = tuple(database.execute("PRAGMA foreign_key_check"))
            evidence.check(integrity_rows == ("ok",), "sqlite_integrity")
            evidence.check(not foreign_rows, "sqlite_foreign_keys")
            user_version = int(database.execute("PRAGMA user_version").fetchone()[0])
            evidence.bind("sqlite-user-version", user_version)
            table_rows = tuple(
                database.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
            tables = {str(row[0]) for row in table_rows}
            evidence.counts["sqlite_databases"] += 1
            evidence.counts["sqlite_tables"] += len(tables)
            for table_value, schema_value in table_rows:
                table = str(table_value)
                quoted = _quote_identifier(table)
                evidence.bind("schema", table)
                evidence.bind("schema", schema_value or "")
                columns = _table_columns(database, table)
                column_names = [str(row[1]) for row in columns]
                row_count = int(database.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0])
                evidence.counts["sqlite_rows"] += row_count
                if "goal" in table.lower():
                    evidence.counts["daily_goal_records"] += row_count
                if any(marker in table.lower() for marker in ("event", "run", "history", "ledger", "output")):
                    evidence.counts["ledger_records"] += row_count
                evidence.bind("row-count", row_count)
                order_clause = ""
                primary = [str(row[1]) for row in columns if int(row[5] or 0) > 0]
                if primary:
                    order_clause = " ORDER BY " + ",".join(_quote_identifier(name) for name in primary)
                try:
                    for row in database.execute(f"SELECT * FROM {quoted}{order_clause}"):
                        evidence.bind("row", b"\x1f".join(_canonical_cell(value) for value in row))
                except sqlite3.Error:
                    evidence.check(False, "sqlite_row_read")
                for column in column_names:
                    lowered_column = column.lower()
                    if lowered_column in {"owner", "assignee", "created_by", "requester_persona", "profile"}:
                        evidence.counts["ownership_records"] += int(
                            database.execute(
                                f"SELECT count(*) FROM {quoted} WHERE {_quote_identifier(column)} IS NOT NULL"
                            ).fetchone()[0]
                        )
                    if lowered_column in {"provenance", "source", "origin", "origin_source"}:
                        evidence.counts["provenance_records"] += int(
                            database.execute(
                                f"SELECT count(*) FROM {quoted} WHERE {_quote_identifier(column)} IS NOT NULL"
                            ).fetchone()[0]
                        )
                    if "approval" in lowered_column:
                        evidence.counts["approval_records"] += int(
                            database.execute(
                                f"SELECT count(*) FROM {quoted} WHERE {_quote_identifier(column)} IS NOT NULL"
                            ).fetchone()[0]
                        )
                    if lowered_column in {"status", "state", "completion", "end_reason"}:
                        evidence.counts["status_records"] += int(
                            database.execute(
                                f"SELECT count(*) FROM {quoted} WHERE {_quote_identifier(column)} IS NOT NULL"
                            ).fetchone()[0]
                        )
                    if any(marker in column.lower() for marker in _TIMESTAMP_MARKERS):
                        invalid = 0
                        populated = 0
                        for (value,) in database.execute(f"SELECT {_quote_identifier(column)} FROM {quoted}"):
                            populated += int(value is not None and value != "")
                            if not _timestamp_valid(value):
                                invalid += 1
                        evidence.counts["timestamp_values"] += populated
                        evidence.check(invalid == 0, "timestamp_invalid")
                if primary and row_count:
                    sample_columns = ",".join(_quote_identifier(name) for name in primary)
                    samples = tuple(database.execute(f"SELECT {sample_columns} FROM {quoted} LIMIT 5"))
                    predicate = " AND ".join(f"{_quote_identifier(name)} IS ?" for name in primary)
                    for sample in samples:
                        matched = int(database.execute(f"SELECT count(*) FROM {quoted} WHERE {predicate}", tuple(sample)).fetchone()[0])
                        evidence.retrieval_cases += 1
                        evidence.check(matched == 1, "retrieval_round_trip")
            _scan_session_surface(database, tables, evidence)
            _scan_task_surface(database, tables, evidence)
    except sqlite3.Error as error:
        raise SemanticContinuityError("sqlite_validation_failed") from error


def _jobs_from_payload(payload: object) -> list[dict[str, object]] | None:
    if not isinstance(payload, dict) or "jobs" not in payload:
        return None
    jobs = payload.get("jobs")
    if isinstance(jobs, list):
        return [item for item in jobs if isinstance(item, dict)]
    if isinstance(jobs, dict):
        normalized: list[dict[str, object]] = []
        for identifier, value in jobs.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", identifier)
                normalized.append(item)
        return normalized
    return []


def _scan_json_document(path: Path, relative: PurePosixPath, evidence: _Evidence) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        evidence.check(False, "structured_document_invalid")
        return
    jobs = _jobs_from_payload(payload)
    if jobs is None:
        return
    evidence.counts["schedule_records"] += len(jobs)
    identifiers: list[str] = []
    for job in jobs:
        identifier = job.get("id")
        if identifier is not None:
            identifiers.append(str(identifier))
        evidence.check(isinstance(job.get("schedule"), dict), "schedule_definition_invalid")
        if "next_run_at" in job:
            evidence.check(_timestamp_valid(job.get("next_run_at")), "schedule_timestamp_invalid")
        enabled = job.get("enabled", True)
        state = job.get("state")
        evidence.check(not (enabled is False and state not in {None, "paused", "disabled"}), "schedule_state_inconsistent")
        evidence.bind("schedule", json.dumps(job, sort_keys=True, separators=(",", ":"), default=str))
    evidence.check(len(identifiers) == len(set(identifiers)) == len(jobs), "schedule_id_invalid")


def _scan_profile(root: Path, key: bytes) -> _ScanResult:
    evidence = _Evidence(key)
    root = root.absolute()
    for path, relative_text, is_directory in _source_entries(root):
        if is_directory:
            continue
        relative = PurePosixPath(relative_text)
        size = path.stat().st_size
        evidence.counts["artifacts"] += 1
        evidence.counts["bytes"] += size
        if _sensitive_path(relative):
            evidence.counts["sensitive_artifacts_excluded"] += 1
            evidence.bind("sensitive-metadata", size)
            continue
        lowered = relative.as_posix().lower()
        is_persona = any(marker in lowered for marker in ("persona", "identity", "context.md", "soul.md", "user.md"))
        is_memory = any(marker in lowered for marker in ("memory", "memories"))
        is_ledger = any(marker in lowered for marker in ("ledger", "history", "outputs", "runs")) and any(
            marker in lowered for marker in ("cron", "kanban", "task", "daily-goal")
        )
        evidence.counts["persona_artifacts"] += int(is_persona)
        evidence.counts["memory_artifacts"] += int(is_memory)
        evidence.counts["ledger_artifacts"] += int(is_ledger)
        try:
            with path.open("rb") as stream:
                header = stream.read(16)
        except OSError as error:
            raise SemanticContinuityError("artifact_read_failed") from error
        if header == b"SQLite format 3\x00":
            _scan_database(path, evidence)
            continue
        if path.suffix.lower() == ".json":
            _scan_json_document(path, relative, evidence)
        if is_persona or is_memory:
            try:
                content = path.read_bytes()
                content.decode("utf-8")
            except (OSError, UnicodeError):
                evidence.check(False, "continuity_document_invalid")
                continue
            evidence.bind("persona" if is_persona else "memory", hashlib.sha256(content).digest())
    return _ScanResult(
        counts=evidence.counts,
        checks=evidence.checks,
        passed=evidence.passed,
        failed=evidence.failed,
        retrieval_cases=evidence.retrieval_cases,
        discrepancies=evidence.discrepancies,
        structural_hmac=evidence.digest.hexdigest(),
    )


def _extract_parts(member_name: str) -> tuple[str, ...]:
    normalized = member_name[2:] if member_name.startswith("./") else member_name
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or not normalized or any(part in {"", ".", ".."} for part in candidate.parts):
        raise SemanticContinuityError("archive_member_invalid")
    return candidate.parts


def _safe_extract_tar(tar_path: Path, destination: Path) -> None:
    seen: set[tuple[str, ...]] = set()
    try:
        with tarfile.open(tar_path, "r:") as archive:
            for member in archive:
                if member.name in {".", "./"} and member.isdir():
                    continue
                parts = _extract_parts(member.name)
                if parts in seen or not (member.isdir() or member.isreg()):
                    raise SemanticContinuityError("archive_member_invalid")
                seen.add(parts)
                target = destination.joinpath(*parts)
                if member.isdir():
                    _secure_mkdir(target)
                    continue
                _secure_mkdir(target.parent)
                source = archive.extractfile(member)
                if source is None:
                    raise SemanticContinuityError("archive_member_invalid")
                with source, target.open("xb", buffering=0) as output:
                    os.chmod(target, 0o600)
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
    except (OSError, tarfile.TarError) as error:
        raise SemanticContinuityError("archive_restore_failed") from error


def _copy_semantic_clone(source: Path, destination: Path) -> None:
    _secure_mkdir(destination)
    for path, relative, is_directory in _source_entries(source):
        target = destination / relative
        if is_directory:
            _secure_mkdir(target)
            continue
        _secure_mkdir(target.parent)
        sensitive = _sensitive_path(PurePosixPath(relative))
        try:
            with path.open("rb") as stream:
                is_sqlite = stream.read(16) == b"SQLite format 3\x00"
        except OSError as error:
            raise SemanticContinuityError("migration_copy_failed") from error
        if is_sqlite and not sensitive:
            _backup_sqlite_opaquely(path, target)
        else:
            _copy_regular_opaquely(path, target, None)
    for path in sorted(destination.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    os.chmod(destination, 0o700)


def _is_internal_sqlite_table(table: str) -> bool:
    lowered = table.lower()
    return lowered in {"schema_version", "state_meta"} or "_fts" in lowered or lowered.startswith("fts_")


def _json_object_extends(before: object, after: object) -> bool:
    if before in {None, ""}:
        if after in {None, "", "{}"}:
            return True
        try:
            parsed_after = json.loads(str(after))
        except (TypeError, ValueError):
            return False
        return isinstance(parsed_after, dict) and set(parsed_after) <= {"_delegate_from", "_branched_from", "_reset_from"}
    try:
        parsed_before = json.loads(str(before))
        parsed_after = json.loads(str(after))
    except (TypeError, ValueError):
        return before == after
    if not isinstance(parsed_before, dict) or not isinstance(parsed_after, dict):
        return parsed_before == parsed_after
    return all(key in parsed_after and parsed_after[key] == value for key, value in parsed_before.items())


def _compare_session_table(before: sqlite3.Connection, after: sqlite3.Connection, key: bytes) -> tuple[int, int, set[str], str]:
    checks = 0
    failed = 0
    discrepancies: set[str] = set()
    digest = hmac.new(key, digestmod=hashlib.sha256)
    source_columns = [str(row[1]) for row in _table_columns(before, "sessions")]
    target_columns = {str(row[1]) for row in _table_columns(after, "sessions")}
    checks += 1
    if not set(source_columns) <= target_columns:
        return checks, 1, {"target_schema_dropped_field"}, digest.hexdigest()
    exact_columns = [column for column in source_columns if column not in {"model_config", "system_prompt", "system_prompt_hash"}]
    exact_projection = ",".join(f"s.{_quote_identifier(column)}" for column in exact_columns)
    source_rows = {row[0]: tuple(row) for row in before.execute(f"SELECT {exact_projection} FROM sessions s")}
    target_rows = {row[0]: tuple(row) for row in after.execute(f"SELECT {exact_projection} FROM sessions s")}
    checks += 1
    if source_rows != target_rows:
        failed += 1
        discrepancies.add("target_record_semantics_changed")
    for identifier in sorted(source_rows, key=lambda value: str(value)):
        digest.update(b"session\0" + b"\x1f".join(_canonical_cell(value) for value in source_rows[identifier]) + b"\0")
    if "system_prompt" in source_columns:
        has_prompt_table = bool(
            after.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_prompts'").fetchone()
        )
        source_prompts = dict(before.execute("SELECT id, system_prompt FROM sessions"))
        if has_prompt_table and "system_prompt_hash" in target_columns:
            target_prompts = dict(
                after.execute(
                    "SELECT s.id, COALESCE(p.prompt, s.system_prompt) FROM sessions s "
                    "LEFT JOIN system_prompts p ON p.hash=s.system_prompt_hash"
                )
            )
        else:
            target_prompts = dict(after.execute("SELECT id, system_prompt FROM sessions"))
        checks += 1
        if source_prompts != target_prompts:
            failed += 1
            discrepancies.add("persona_prompt_continuity_changed")
        for identifier in sorted(source_prompts, key=lambda value: str(value)):
            digest.update(b"prompt\0" + _canonical_cell(source_prompts[identifier]) + b"\0")
    if "model_config" in source_columns:
        source_configs = dict(before.execute("SELECT id, model_config FROM sessions"))
        target_configs = dict(after.execute("SELECT id, model_config FROM sessions"))
        checks += 1
        if set(source_configs) != set(target_configs) or any(
            not _json_object_extends(value, target_configs.get(identifier))
            for identifier, value in source_configs.items()
        ):
            failed += 1
            discrepancies.add("session_configuration_continuity_changed")
        for identifier in sorted(source_configs, key=lambda value: str(value)):
            digest.update(b"config\0" + _canonical_cell(source_configs[identifier]) + b"\0")
    return checks, failed, discrepancies, digest.hexdigest()


def _compare_database_continuity(source: Path, target: Path, key: bytes) -> tuple[int, int, set[str], str]:
    checks = 0
    failed = 0
    discrepancies: set[str] = set()
    digest = hmac.new(key, digestmod=hashlib.sha256)
    try:
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro&immutable=1", uri=True) as before, sqlite3.connect(
            f"file:{target.as_posix()}?mode=ro&immutable=1", uri=True
        ) as after:
            source_tables = {
                str(row[0])
                for row in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            }
            target_tables = {
                str(row[0])
                for row in after.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            }
            checks += 1
            if not source_tables <= target_tables:
                failed += 1
                discrepancies.add("target_schema_dropped_surface")
            for table in sorted(source_tables & target_tables):
                if _is_internal_sqlite_table(table):
                    continue
                if table == "sessions":
                    session_checks, session_failed, session_discrepancies, session_digest = _compare_session_table(
                        before, after, key
                    )
                    checks += session_checks
                    failed += session_failed
                    discrepancies.update(session_discrepancies)
                    digest.update(session_digest.encode("ascii") + b"\0")
                    continue
                source_columns = [str(row[1]) for row in _table_columns(before, table)]
                target_columns = {str(row[1]) for row in _table_columns(after, table)}
                checks += 1
                if not set(source_columns) <= target_columns:
                    failed += 1
                    discrepancies.add("target_schema_dropped_field")
                    continue
                quoted = _quote_identifier(table)
                projection = ",".join(_quote_identifier(column) for column in source_columns)
                before_rows = [tuple(row) for row in before.execute(f"SELECT {projection} FROM {quoted}")]
                after_rows = [tuple(row) for row in after.execute(f"SELECT {projection} FROM {quoted}")]
                canonical_before = sorted(b"\x1f".join(_canonical_cell(value) for value in row) for row in before_rows)
                canonical_after = sorted(b"\x1f".join(_canonical_cell(value) for value in row) for row in after_rows)
                checks += 1
                if canonical_before != canonical_after:
                    failed += 1
                    discrepancies.add("target_record_semantics_changed")
                digest.update(table.encode("utf-8", "surrogateescape") + b"\0")
                for row in canonical_before:
                    digest.update(row + b"\0")
    except sqlite3.Error as error:
        raise SemanticContinuityError("target_continuity_compare_failed") from error
    return checks, failed, discrepancies, digest.hexdigest()


def _compare_profile_continuity(source: Path, target: Path, key: bytes) -> tuple[int, int, set[str], str]:
    checks = 0
    failed = 0
    discrepancies: set[str] = set()
    digest = hmac.new(key, digestmod=hashlib.sha256)
    target_entries = {relative: (path, is_directory) for path, relative, is_directory in _source_entries(target)}
    for source_path, relative, is_directory in _source_entries(source):
        target_entry = target_entries.get(relative)
        checks += 1
        if target_entry is None or target_entry[1] != is_directory:
            failed += 1
            discrepancies.add("target_artifact_missing")
            continue
        if is_directory:
            continue
        target_path = target_entry[0]
        with source_path.open("rb") as stream:
            is_sqlite = stream.read(16) == b"SQLite format 3\x00"
        if is_sqlite and not _sensitive_path(PurePosixPath(relative)):
            db_checks, db_failed, db_discrepancies, db_digest = _compare_database_continuity(
                source_path, target_path, key
            )
            checks += db_checks
            failed += db_failed
            discrepancies.update(db_discrepancies)
            digest.update(db_digest.encode("ascii") + b"\0")
        else:
            equal = _sha256_file(source_path) == _sha256_file(target_path)
            checks += 1
            if not equal:
                failed += 1
                discrepancies.add("target_opaque_artifact_changed")
            digest.update(hashlib.sha256(os.fsencode(relative)).digest())
            digest.update(_sha256_file(source_path).encode("ascii") + b"\0")
    return checks, failed, discrepancies, digest.hexdigest()


_SANDBOX_POLICY = "(version 1)(allow default)(deny network*)"


def _prove_network_denial(python_runtime: Path) -> None:
    probe = (
        "import errno,socket,sys; s=socket.socket(); s.settimeout(1); "
        "\ntry: s.connect(('1.1.1.1',53)); sys.exit(2)"
        "\nexcept PermissionError: sys.exit(0)"
        "\nexcept OSError as e: sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 3)"
    )
    try:
        result = subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", _SANDBOX_POLICY, os.fspath(python_runtime), "-B", "-c", probe],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SemanticContinuityError("network_isolation_unavailable") from error
    if result.returncode != 0:
        raise SemanticContinuityError("network_isolation_unproven")


def _run_candidate_migrations(candidate_source: Path, clone: Path, python_runtime: Path) -> int:
    candidate = Path(candidate_source).absolute()
    target = Path(clone).absolute()
    if not candidate.is_dir() or candidate.is_symlink() or not target.is_dir() or target.is_symlink():
        raise SemanticContinuityError("candidate_binding_invalid")
    state = target / "state.db"
    kanban_paths = [target / "kanban.db"]
    boards = target / "kanban" / "boards"
    if boards.is_dir() and not boards.is_symlink():
        kanban_paths.extend(path for path in boards.glob("*/kanban.db") if path.is_file() and not path.is_symlink())
    migration_count = int(state.is_file()) + sum(path.is_file() for path in kanban_paths)
    if not migration_count:
        return 0
    runtime = Path(python_runtime).absolute()
    try:
        resolved_runtime = runtime.resolve(strict=True)
    except OSError as error:
        raise SemanticContinuityError("candidate_runtime_invalid") from error
    if not resolved_runtime.is_file():
        raise SemanticContinuityError("candidate_runtime_invalid")
    _prove_network_denial(runtime)
    runtime_home = target.parent / "runtime-home"
    _secure_mkdir(runtime_home)
    program = """
import contextlib
import io
import json
import os
from pathlib import Path
import sys

candidate = Path(sys.argv[1]).resolve()
clone = Path(sys.argv[2]).resolve()
sys.path.insert(0, os.fspath(candidate))
os.environ["HERMES_HOME"] = os.fspath(clone)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    state = clone / "state.db"
    if state.is_file():
        from hermes_state import SessionDB
        database = SessionDB(db_path=state)
        database.close()
    from hermes_cli.kanban_db import init_db
    paths = [clone / "kanban.db"]
    boards = clone / "kanban" / "boards"
    if boards.is_dir() and not boards.is_symlink():
        paths.extend(path for path in boards.glob("*/kanban.db") if path.is_file() and not path.is_symlink())
    for path in paths:
        if path.is_file():
            init_db(db_path=path)
    jobs_path = clone / "cron" / "jobs.json"
    if jobs_path.is_file() and not jobs_path.is_symlink():
        from cron.jobs import compute_next_run
        payload = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        if not isinstance(jobs, list):
            raise ValueError("invalid schedule container")
        for job in jobs:
            if not isinstance(job, dict) or not isinstance(job.get("schedule"), dict):
                raise ValueError("invalid schedule record")
            schedule = job["schedule"]
            result = compute_next_run(schedule, job.get("last_run_at"))
            if job.get("enabled", True) and schedule.get("kind") in {"interval", "cron"} and result is None:
                raise ValueError("invalid recurring schedule")
"""
    env = {
        "CI": "1",
        "HOME": os.fspath(runtime_home),
        "HERMES_HOME": os.fspath(target),
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
    }
    try:
        result = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                _SANDBOX_POLICY,
                os.fspath(runtime),
                "-B",
                "-c",
                program,
                os.fspath(candidate),
                os.fspath(target),
            ],
            env=env,
            capture_output=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SemanticContinuityError("candidate_migration_execution_failed") from error
    if result.returncode != 0:
        raise SemanticContinuityError("candidate_migration_execution_failed")
    return migration_count


def _private_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


class SemanticRehearsalEngine:
    def __init__(
        self,
        *,
        attestor: MacOSStorageAttestor | None = None,
        candidate_migrator: Callable[[Path, Path, Path], int] = _run_candidate_migrations,
    ) -> None:
        self._attestor = attestor or MacOSStorageAttestor()
        self._candidate_migrator = candidate_migrator

    def execute(self, request: SemanticRehearsalRequest) -> SemanticRehearsalResult:
        snapshot_id = _safe_token(request.snapshot_id, "snapshot_id")
        rollback_handle = _safe_token(request.rollback_handle, "rollback_handle")
        rehearsal_id = _safe_token(request.rehearsal_id, "rehearsal_id")
        digests = (
            request.expected_snapshot_tree_sha256,
            request.expected_archive_sha256,
            request.expected_archive_hmac_sha256,
            request.architecture_contract_sha256,
            request.expected_candidate_manifest_sha256,
            request.expected_candidate_python_sha256,
        )
        if not all(_valid_digest(value) for value in digests):
            raise SemanticContinuityError("snapshot_binding_invalid")
        candidate_source = Path(request.candidate_source_root).absolute()
        candidate_manifest = Path(request.candidate_manifest_path).absolute()
        candidate_python = Path(request.candidate_python).absolute()
        try:
            resolved_candidate_python = candidate_python.resolve(strict=True)
        except OSError as error:
            raise SemanticContinuityError("candidate_runtime_invalid") from error
        if (
            not candidate_source.is_dir()
            or candidate_source.is_symlink()
            or not candidate_manifest.is_file()
            or candidate_manifest.is_symlink()
            or _sha256_file(candidate_manifest) != request.expected_candidate_manifest_sha256
            or not candidate_python.is_file()
            or not resolved_candidate_python.is_file()
            or _sha256_file(resolved_candidate_python) != request.expected_candidate_python_sha256
        ):
            raise SemanticContinuityError("candidate_binding_invalid")
        storage = Path(request.storage_root).expanduser().absolute()
        attestation = self._attestor.attest(storage, denied_roots=request.denied_roots)
        if not attestation.clear:
            raise SemanticContinuityError("storage_policy_blocked")
        paths = {
            "archive": storage / "backups" / snapshot_id / "snapshot.enc",
            "archive_hmac": storage / "backups" / snapshot_id / "snapshot.hmac",
            "key": storage / "keys" / f"{rollback_handle}.key",
            "source_receipt": storage / "receipts" / f"{snapshot_id}.json",
            "rehearsal": storage / "rehearsals" / rehearsal_id,
            "restored": storage / "rehearsals" / rehearsal_id / "restored",
            "migrated": storage / "rehearsals" / rehearsal_id / "migrated",
            "receipt": storage / "semantic-receipts" / f"{rehearsal_id}.json",
        }
        if paths["receipt"].exists():
            return self._verify_existing(request, paths)
        for parent in (storage / "rehearsals", storage / "semantic-receipts"):
            _secure_mkdir(parent)
        _secure_mkdir(paths["rehearsal"])
        archive_before = self._verify_snapshot_binding(request, paths)
        key_material = bytes.fromhex(paths["key"].read_text(encoding="ascii"))
        if len(key_material) != 64:
            raise SemanticContinuityError("snapshot_binding_invalid")
        _secure_mkdir(paths["restored"])
        temporary_tar = paths["rehearsal"] / ".restore.tar"
        try:
            subprocess.run(
                [
                    os.fspath(_OPENSSL),
                    "enc",
                    "-d",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-iter",
                    "600000",
                    "-md",
                    "sha256",
                    "-in",
                    os.fspath(paths["archive"]),
                    "-out",
                    os.fspath(temporary_tar),
                    "-pass",
                    f"file:{paths['key']}",
                ],
                check=True,
                capture_output=True,
                timeout=3600,
            )
            os.chmod(temporary_tar, 0o600)
            _safe_extract_tar(temporary_tar, paths["restored"])
        except (OSError, subprocess.SubprocessError) as error:
            raise SemanticContinuityError("archive_restore_failed") from error
        finally:
            if temporary_tar.exists():
                temporary_tar.unlink()
        restored_digest, restored_count, restored_bytes = _tree_digest(paths["restored"])
        if (
            restored_digest != request.expected_snapshot_tree_sha256
            or not _clone_permissions_clear(paths["restored"])
        ):
            raise SemanticContinuityError("restored_snapshot_invalid")
        source_scan = _scan_profile(paths["restored"], key_material[:32])
        _copy_semantic_clone(paths["restored"], paths["migrated"])
        migration_surfaces = self._candidate_migrator(candidate_source, paths["migrated"], candidate_python)
        migrated_digest, migrated_count, migrated_bytes = _tree_digest(paths["migrated"])
        migrated_scan = _scan_profile(paths["migrated"], key_material[:32])
        discrepancies = set(source_scan.discrepancies) | set(migrated_scan.discrepancies)
        continuity_keys = {
            "sensitive_artifacts_excluded",
            "session_records",
            "message_records",
            "tool_call_records",
            "tool_result_records",
            "custom_role_records",
            "task_records",
            "schedule_records",
            "persona_artifacts",
            "memory_artifacts",
        }
        if any(source_scan.counts[key] != migrated_scan.counts[key] for key in continuity_keys):
            discrepancies.add("aggregate_continuity_mismatch")
        continuity_checks, continuity_failed, continuity_discrepancies, continuity_hmac = _compare_profile_continuity(
            paths["restored"], paths["migrated"], key_material[:32]
        )
        discrepancies.update(continuity_discrepancies)
        if not _clone_permissions_clear(paths["migrated"]):
            discrepancies.add("migration_permissions_invalid")
        checks = source_scan.checks + migrated_scan.checks + continuity_checks + 10
        failed = source_scan.failed + migrated_scan.failed + continuity_failed + len(
            discrepancies - source_scan.discrepancies - migrated_scan.discrepancies
            - continuity_discrepancies
        )
        if _sha256_file(paths["archive"]) != archive_before:
            discrepancies.add("rollback_artifact_changed")
            failed += 1
        counts = {name: int(source_scan.counts[name]) for name in _COUNT_KEYS}
        receipt = SemanticRehearsalReceipt(
            schema_version=_SCHEMA,
            snapshot_id=snapshot_id,
            rehearsal_id=rehearsal_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            source_tree_sha256=request.expected_snapshot_tree_sha256,
            restored_tree_sha256=restored_digest,
            migrated_tree_sha256=migrated_digest,
            architecture_contract_sha256=request.architecture_contract_sha256,
            candidate_release_manifest_sha256=request.expected_candidate_manifest_sha256,
            candidate_python_sha256=request.expected_candidate_python_sha256,
            structural_hmac_sha256=hmac.new(
                key_material[:32],
                (source_scan.structural_hmac + migrated_scan.structural_hmac + continuity_hmac).encode("ascii"),
                hashlib.sha256,
            ).hexdigest(),
            aggregate_counts=counts,
            validation_counts={
                "checks": checks,
                "passed": checks - failed,
                "failed": failed,
                "retrieval_cases": source_scan.retrieval_cases + migrated_scan.retrieval_cases,
                "repairs": 0,
                "migration_surfaces": migration_surfaces,
                "target_sqlite_tables": int(migrated_scan.counts["sqlite_tables"]),
                "target_sqlite_rows": int(migrated_scan.counts["sqlite_rows"]),
            },
            discrepancy_classes=tuple(sorted(discrepancies)),
            permission_state="dirs-0700-files-0600",
            rollback_state="immutable-verified-unchanged",
            status="CLEAR" if not discrepancies and failed == 0 else "BLOCKED",
        )
        _private_json(paths["receipt"], receipt.to_dict())
        os.chmod(paths["receipt"], 0o400)
        if receipt.status != "CLEAR":
            raise SemanticContinuityError("semantic_validation_blocked")
        return SemanticRehearsalResult(receipt, paths["restored"], paths["migrated"], paths["receipt"])

    def _verify_snapshot_binding(self, request: SemanticRehearsalRequest, paths: dict[str, Path]) -> str:
        required = (paths["archive"], paths["archive_hmac"], paths["key"], paths["source_receipt"])
        if not all(path.exists() and path.is_file() and not path.is_symlink() for path in required):
            raise SemanticContinuityError("snapshot_binding_invalid")
        try:
            source_values = json.loads(paths["source_receipt"].read_text(encoding="utf-8"))
            source_receipt = OpaqueBackupReceipt(**source_values)
            key_material = bytes.fromhex(paths["key"].read_text(encoding="ascii"))
            recorded_hmac = paths["archive_hmac"].read_text(encoding="ascii").strip()
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise SemanticContinuityError("snapshot_binding_invalid") from error
        expected_receipt_hmac = hmac.new(key_material[:32], _receipt_payload(source_receipt), hashlib.sha256).hexdigest()
        archive_sha = _sha256_file(paths["archive"])
        archive_hmac = _hmac_file(paths["archive"], key_material[32:])
        clear = all(
            (
                source_receipt.snapshot_id == request.snapshot_id,
                source_receipt.rollback_handle == request.rollback_handle,
                source_receipt.snapshot_tree_sha256 == request.expected_snapshot_tree_sha256,
                archive_sha == request.expected_archive_sha256 == source_receipt.archive_sha256,
                archive_hmac == request.expected_archive_hmac_sha256 == source_receipt.archive_hmac_sha256,
                recorded_hmac == archive_hmac,
                hmac.compare_digest(expected_receipt_hmac, source_receipt.receipt_hmac_sha256),
                stat.S_IMODE(paths["archive"].stat().st_mode) == 0o400,
                stat.S_IMODE(paths["key"].stat().st_mode) == 0o400,
                stat.S_IMODE(paths["source_receipt"].stat().st_mode) == 0o400,
            )
        )
        if not clear:
            raise SemanticContinuityError("snapshot_binding_invalid")
        return archive_sha

    def _verify_existing(self, request: SemanticRehearsalRequest, paths: dict[str, Path]) -> SemanticRehearsalResult:
        try:
            values = json.loads(paths["receipt"].read_text(encoding="utf-8"))
            if set(values) != _RECEIPT_FIELDS:
                raise ValueError
            values["discrepancy_classes"] = tuple(values["discrepancy_classes"])
            receipt = SemanticRehearsalReceipt(**values)
            archive_before = self._verify_snapshot_binding(request, paths)
            key_material = bytes.fromhex(paths["key"].read_text(encoding="ascii"))
            restored_digest, _, _ = _tree_digest(paths["restored"])
            migrated_digest, _, _ = _tree_digest(paths["migrated"])
            restored_scan = _scan_profile(paths["restored"], key_material[:32])
            migrated_scan = _scan_profile(paths["migrated"], key_material[:32])
            _, continuity_failed, continuity_discrepancies, continuity_hmac = _compare_profile_continuity(
                paths["restored"], paths["migrated"], key_material[:32]
            )
            combined_hmac = hmac.new(
                key_material[:32],
                (restored_scan.structural_hmac + migrated_scan.structural_hmac + continuity_hmac).encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            clear = all(
                (
                    receipt.schema_version == _SCHEMA,
                    receipt.status == "CLEAR",
                    receipt.snapshot_id == request.snapshot_id,
                    receipt.rehearsal_id == request.rehearsal_id,
                    receipt.architecture_contract_sha256 == request.architecture_contract_sha256,
                    receipt.candidate_release_manifest_sha256 == request.expected_candidate_manifest_sha256,
                    receipt.candidate_python_sha256 == request.expected_candidate_python_sha256,
                    receipt.source_tree_sha256 == request.expected_snapshot_tree_sha256,
                    receipt.restored_tree_sha256 == restored_digest,
                    receipt.migrated_tree_sha256 == migrated_digest,
                    receipt.structural_hmac_sha256 == combined_hmac,
                    receipt.aggregate_counts == {name: int(restored_scan.counts[name]) for name in _COUNT_KEYS},
                    restored_scan.failed == migrated_scan.failed == 0,
                    continuity_failed == 0,
                    not continuity_discrepancies,
                    not restored_scan.discrepancies,
                    not migrated_scan.discrepancies,
                    _clone_permissions_clear(paths["restored"]),
                    _clone_permissions_clear(paths["migrated"]),
                    _sha256_file(paths["archive"]) == archive_before,
                    stat.S_IMODE(paths["receipt"].stat().st_mode) == 0o400,
                )
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, SemanticContinuityError):
            clear = False
        if not clear:
            raise SemanticContinuityError("existing_rehearsal_invalid")
        return SemanticRehearsalResult(receipt, paths["restored"], paths["migrated"], paths["receipt"])


def receipt_is_redacted(payload: dict[str, object]) -> bool:
    """Return whether a serialized receipt has only the aggregate schema."""

    if set(payload) != _RECEIPT_FIELDS:
        return False
    aggregate = payload.get("aggregate_counts")
    validation = payload.get("validation_counts")
    discrepancies = payload.get("discrepancy_classes")
    return bool(
        isinstance(aggregate, dict)
        and set(aggregate) == set(_COUNT_KEYS)
        and all(isinstance(value, int) and value >= 0 for value in aggregate.values())
        and isinstance(validation, dict)
        and set(validation)
        == {
            "checks",
            "passed",
            "failed",
            "retrieval_cases",
            "repairs",
            "migration_surfaces",
            "target_sqlite_tables",
            "target_sqlite_rows",
        }
        and all(isinstance(value, int) and value >= 0 for value in validation.values())
        and isinstance(discrepancies, (list, tuple))
        and all(isinstance(value, str) and value.isascii() and value.replace("_", "").isalnum() for value in discrepancies)
    )
