"""Exception-only approval items for the disabled lifecycle monitor."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from .models import LifecycleBlockedError
from .monitor import ExceptionDecision, LifecycleState


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class ApprovalItem:
    schema_id: str
    status: str
    item_id: str
    tier: str
    evidence_digest: str
    candidate_digest: str
    cell_id: str
    required_decision: str
    created_at: str
    grants_authority: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovalGrant:
    schema_id: str
    status: str
    item_id: str
    evidence_digest: str
    candidate_digest: str
    cell_id: str
    scope: str
    approved_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ApprovalValidation:
    status: str
    item_id: str
    scope: str


def build_approval_item(
    decision: ExceptionDecision,
    state: LifecycleState,
    *,
    created_at: datetime | None = None,
) -> ApprovalItem | None:
    if not decision.notify or decision.tier == "CLEAR":
        return None
    observed = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    unsigned = {
        "tier": decision.tier,
        "evidence_digest": decision.evidence_digest,
        "candidate_digest": decision.candidate_digest,
        "cell_id": decision.cell_id,
        "required_decision": decision.required_decision,
    }
    return ApprovalItem(
        schema_id="ik.hermes.lifecycle-approval-item.v1",
        status="DECISION_REQUIRED",
        item_id=hashlib.sha256(_canonical(unsigned)).hexdigest()[:24],
        created_at=observed.isoformat(),
        grants_authority=False,
        **unsigned,
    )


def _privacy_clear(item: ApprovalItem) -> bool:
    rendered = json.dumps(item.to_dict(), sort_keys=True).lower()
    prohibited = ("/users/", "/home/", "\\users\\", "token", "password", "secret", "private value")
    return not any(marker in rendered for marker in prohibited)


def write_exception_once(root: Path, item: ApprovalItem) -> Path:
    if not _privacy_clear(item) or item.grants_authority:
        raise LifecycleBlockedError("approval_item_privacy_invalid", "approval item contains unsafe or authority-bearing data")
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    path = destination / f"{item.item_id}.json"
    payload = _canonical(item.to_dict()) + b"\n"
    if path.exists():
        if path.is_symlink() or path.read_bytes() != payload:
            raise LifecycleBlockedError("approval_item_collision", "existing approval item does not match exact evidence")
        return path
    temporary = destination / f".{item.item_id}.{os.getpid()}.tmp"
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path


def validate_approval_grant(item: ApprovalItem, grant: ApprovalGrant, *, now: datetime | None = None) -> ApprovalValidation:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if grant.approved_at.tzinfo is None or grant.expires_at.tzinfo is None:
        raise LifecycleBlockedError("lifecycle_approval_invalid", "approval timestamps require timezones")
    exact = (
        grant.schema_id == "ik.hermes.lifecycle-approval.v1"
        and grant.status == "APPROVED"
        and grant.item_id == item.item_id
        and grant.evidence_digest == item.evidence_digest
        and grant.candidate_digest == item.candidate_digest
        and grant.cell_id == item.cell_id
        and grant.scope == item.required_decision
        and grant.approved_at <= current <= grant.expires_at
    )
    if not exact:
        raise LifecycleBlockedError("lifecycle_approval_mismatch", "approval is generic, stale, or bound to different evidence")
    return ApprovalValidation("VALIDATED_NOT_EXECUTED", item.item_id, grant.scope)
