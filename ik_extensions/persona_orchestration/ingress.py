"""Authenticated gateway ingress for durable, non-duplicating handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from .envelope import Owner, validate_envelope
from .store import HandoffStore


_WORK = re.compile(r"\b(?:implement|build|develop|deploy|debug|repository|repo|code|automate|fix\s+(?:the\s+)?(?:bug|workflow|system))\b", re.I)
_PERSONAL = re.compile(r"\b(?:calendar|schedule|appointment|reminder|household|dentist|doctor|personal\s+follow[- ]?up)\b", re.I)
_TELEGRAM_GROUP_BOT_MESSAGE_MODES = frozenset({"none", "mentions_only", "all"})


def classify_ingress_text(text: str) -> tuple[str, ...]:
    domains: list[str] = []
    if _WORK.search(text):
        domains.append("work")
    if _PERSONAL.search(text):
        domains.append("personal")
    return tuple(domains)


def _platform_name(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _telegram_runtime_part(gateway: object, attribute: str) -> object | None:
    values = getattr(gateway, attribute, None)
    if not isinstance(values, dict):
        return None
    for key, value in values.items():
        if _platform_name(key) == "telegram":
            return value
    return None


def _telegram_extra(gateway: object) -> dict[str, object]:
    config = getattr(gateway, "config", None)
    platform_config = _telegram_runtime_part(config, "platforms")
    extra = getattr(platform_config, "extra", None)
    return extra if isinstance(extra, dict) else {}


def _telegram_adapter(gateway: object) -> object | None:
    return _telegram_runtime_part(gateway, "adapters")


def _telegram_group_bot_message_mode(gateway: object) -> str:
    """Read the policy from runtime extras or the active profile YAML.

    Hermes v2026.8.18 preserves ``mention_patterns`` in ``PlatformConfig`` but
    drops the sibling ``group_bot_messages`` key. Reading the active profile is
    the compatibility path until that upstream config bridge exists.
    """
    runtime_value = _telegram_extra(gateway).get("group_bot_messages")
    if runtime_value is not None:
        return str(runtime_value).strip().lower()
    home = os.environ.get("HERMES_HOME", "").strip()
    if not home:
        return "none"
    try:
        import yaml

        document = yaml.safe_load((Path(home) / "config.yaml").read_text(encoding="utf-8"))
    except Exception:
        return "none"
    if not isinstance(document, dict):
        return "none"
    candidates = [document.get("telegram")]
    platforms = document.get("platforms")
    if isinstance(platforms, dict):
        candidates.append(platforms.get("telegram"))
    gateway_config = document.get("gateway")
    if isinstance(gateway_config, dict):
        nested_platforms = gateway_config.get("platforms")
        if isinstance(nested_platforms, dict):
            candidates.append(nested_platforms.get("telegram"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("group_bot_messages") is not None:
            return str(candidate["group_bot_messages"]).strip().lower()
    return "none"


def _telegram_message_text(event: object) -> str:
    raw = getattr(event, "raw_message", None)
    for value in (
        getattr(raw, "text", None),
        getattr(raw, "caption", None),
        getattr(event, "text", None),
    ):
        if isinstance(value, str) and value:
            return value
    return ""


def _telegram_own_bot_identity(gateway: object) -> tuple[object | None, str]:
    adapter = _telegram_adapter(gateway)
    bot = getattr(adapter, "_bot", None)
    bot_id = getattr(bot, "id", None)
    username = ""
    current_username = getattr(adapter, "_current_bot_username", None)
    if callable(current_username):
        try:
            username = str(current_username() or "")
        except Exception:
            username = ""
    if not username:
        username = str(getattr(bot, "username", "") or "")
    return bot_id, username.lstrip("@").lower()


def _telegram_bot_explicitly_mentions_this_cell(event: object, gateway: object) -> bool:
    text = _telegram_message_text(event)
    if not text:
        return False
    _bot_id, username = _telegram_own_bot_identity(gateway)
    if username and re.search(rf"(?i)(?<![A-Za-z0-9_])@{re.escape(username)}\b", text):
        return True
    patterns = _telegram_extra(gateway).get("mention_patterns", ())
    if not isinstance(patterns, (list, tuple)):
        return False
    for pattern in patterns:
        candidate = str(pattern)
        if "@" not in candidate:
            continue
        try:
            if re.search(candidate, text, re.I):
                return True
        except re.error:
            continue
    return False


def _telegram_bot_replies_to_this_cell(event: object, gateway: object) -> bool:
    raw = getattr(event, "raw_message", None)
    reply = getattr(raw, "reply_to_message", None)
    if reply is None:
        return False
    reply_user = getattr(reply, "from_user", None)
    bot_id, username = _telegram_own_bot_identity(gateway)
    reply_user_id = getattr(reply_user, "id", None)
    if bot_id is not None and reply_user_id is not None:
        return bot_id == reply_user_id
    reply_username = str(getattr(reply_user, "username", "") or "").lstrip("@").lower()
    if username and reply_username:
        return username == reply_username
    # Missing identity must not reopen a bot reply chain.
    return getattr(event, "reply_to_message_id", None) is not None


def guard_telegram_group_bot_message(event: object, gateway: object) -> dict[str, str] | None:
    """Enforce the configured Telegram bot-to-bot group ingress contract.

    ``mentions_only`` permits one explicitly addressed top-level bot message.
    A reply from another bot to this cell is always the end of that exchange,
    even when it repeats the mention, so Telegram replies cannot recurse.
    """
    source = getattr(event, "source", None)
    if (
        _platform_name(getattr(source, "platform", None)) != "telegram"
        or getattr(source, "chat_type", None) not in {"group", "supergroup", "thread"}
        or not bool(getattr(source, "is_bot", False))
    ):
        return None

    mode = _telegram_group_bot_message_mode(gateway)
    if mode not in _TELEGRAM_GROUP_BOT_MESSAGE_MODES:
        return {"action": "skip", "reason": "telegram_group_bot_messages_invalid_policy"}
    if mode == "none":
        return {"action": "skip", "reason": "telegram_group_bot_messages_disabled"}
    if mode == "all":
        return None
    if _telegram_bot_replies_to_this_cell(event, gateway):
        return {"action": "skip", "reason": "telegram_bot_reply_chain"}
    if not _telegram_bot_explicitly_mentions_this_cell(event, gateway):
        return {
            "action": "skip",
            "reason": "telegram_group_bot_message_not_explicitly_addressed",
        }
    return None


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
