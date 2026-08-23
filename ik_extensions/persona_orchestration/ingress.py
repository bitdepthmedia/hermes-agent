"""Authenticated gateway ingress for durable, non-duplicating handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from .envelope import Owner, validate_envelope
from .store import HandoffStore


_WORK = re.compile(r"\b(?:implement|build|develop|deploy|debug|repository|repo|code|automate|fix\s+(?:the\s+)?(?:bug|workflow|system))\b", re.I)
_PERSONAL = re.compile(r"\b(?:calendar|schedule|appointment|reminder|household|dentist|doctor|personal\s+follow[- ]?up)\b", re.I)


def classify_ingress_text(text: str) -> tuple[str, ...]:
    domains: list[str] = []
    if _WORK.search(text):
        domains.append("work")
    if _PERSONAL.search(text):
        domains.append("personal")
    return tuple(domains)


@dataclass
class IngressCoordinator:
    cell: Owner
    store: HandoffStore
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        if self.cell not in {Owner.BERT, Owner.ERNIE}:
            raise ValueError("ingress cell must be Bert or Ernie")

    def now(self) -> datetime:
        return self.clock()

    def _source_identity(self, event: object) -> tuple[str, str, str, str]:
        source = getattr(event, "source", None)
        platform = getattr(getattr(source, "platform", None), "value", None)
        chat_id = getattr(source, "chat_id", None)
        user_id = getattr(source, "user_id", None) or getattr(event, "user_id", None)
        message_id = getattr(event, "message_id", None) or getattr(source, "message_id", None)
        if not all(isinstance(value, str) and value for value in (platform, chat_id, user_id, message_id)):
            raise ValueError("stable message identity is required for durable ingress")
        return platform, chat_id, user_id, message_id

    def _idempotency_key(self, event: object) -> str:
        platform, chat_id, _user_id, message_id = self._source_identity(event)
        stable = f"ik-hermes:{self.cell.value}:{platform}:{chat_id}:{message_id}"
        return hashlib.sha256(stable.encode()).hexdigest()

    def _envelope(self, *, event: object, mixed: bool) -> object:
        platform, chat_id, user_id, message_id = self._source_identity(event)
        stable = f"ik-hermes:{self.cell.value}:{platform}:{chat_id}:{message_id}"
        task_id = str(uuid5(NAMESPACE_URL, stable))
        idempotency = hashlib.sha256(stable.encode()).hexdigest()
        text = str(getattr(event, "text", ""))
        if self.cell == Owner.ERNIE:
            payload = {
                "request_class": "mixed-work" if mixed else "work",
                "content_state": "requires-local-sanitization",
            }
            local_ref = f"ernie-local:{idempotency[:20]}"
        else:
            payload = {
                "request_class": "mixed-work" if mixed else "work",
                "sanitized_request": text,
            }
            local_ref = None
        now = self.now()
        return validate_envelope(
            {
                "schema_version": "1.0",
                "task_id": task_id,
                "parent_task_id": None,
                "owner": Owner.CODEX.value,
                "requester_persona": self.cell.value,
                "task_class": "work",
                "privacy_class": "sanitized-cloud",
                "payload": payload,
                "local_payload_ref": local_ref,
                "provenance": {
                    "platform": platform,
                    "chat_digest": hashlib.sha256(chat_id.encode()).hexdigest(),
                    "user_digest": hashlib.sha256(user_id.encode()).hexdigest(),
                    "message_id": message_id,
                    "source_payload_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "evidence_at": now.isoformat(),
                    "sanitizer_version": "ik-ingress-v1",
                },
                "constraints": {
                    "origin_persona_tracks": True,
                    "duplicate_execution_forbidden": True,
                    "private_content_requires_local_reintegration": self.cell == Owner.ERNIE,
                },
                "approval": {"state": "not_required", "scope": ["handoff-only"]},
                "expected_result": {"schema_id": "ik.hermes.task-result.v1"},
                "completion": "pending",
                "idempotency_key": idempotency,
                "lineage": {
                    "hop_count": 1,
                    "max_hops": 4,
                    "visited_owners": [self.cell.value, Owner.CODEX.value],
                    "prior_digest": None,
                },
                "retry": {
                    "attempt": 0,
                    "next_attempt_at": now.isoformat(),
                    "expires_at": (now + timedelta(days=7)).isoformat(),
                    "last_ack_sequence": 0,
                    "escalation": "approval-inbox",
                },
                "integrity": {
                    "sender": self.cell.value,
                    "sequence": 1,
                    "signature_metadata": "cell-local-ingress",
                    "envelope_digest": None,
                },
            }
        )

    def handle(self, event: object, gateway: object) -> dict[str, str]:
        source = getattr(event, "source", None)
        authorize = getattr(gateway, "_is_user_authorized", None)
        if not callable(authorize) or not authorize(source):
            return {"action": "allow"}
        domains = classify_ingress_text(str(getattr(event, "text", "")))
        if domains == ("personal",) or not domains:
            return {"action": "allow"}
        mixed = domains == ("work", "personal")
        idempotency_key = self._idempotency_key(event)
        existing = self.store.by_idempotency_key(idempotency_key)
        source_digest = hashlib.sha256(str(getattr(event, "text", "")).encode()).hexdigest()
        if existing is not None and existing.envelope.provenance.get("source_payload_sha256") != source_digest:
            raise ValueError("idempotency conflict")
        if existing is None:
            envelope = self._envelope(event=event, mixed=mixed)
            self.store.enqueue_once(envelope, now=self.now())
        if mixed:
            text = (
                "The work portion was transferred to Codex exactly once. Do not execute it here. "
                "The personal portion needs one bounded clarification before local execution."
            )
        else:
            text = (
                "The substantive work request was transferred to Codex exactly once. "
                "Do not execute it here; remain the conversational interface and report handoff status."
            )
        return {"action": "rewrite", "text": text}
