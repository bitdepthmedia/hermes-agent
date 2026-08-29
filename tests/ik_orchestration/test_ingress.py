from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ik_extensions.persona_orchestration.envelope import Owner
from ik_extensions.persona_orchestration.ingress import (
    IngressCoordinator,
    classify_ingress_text,
    guard_telegram_group_bot_message,
)
from ik_extensions.persona_orchestration.store import HandoffStore


class Gateway:
    def __init__(self, authorized: bool = True) -> None:
        self.authorized = authorized

    def _is_user_authorized(self, source: object) -> bool:
        del source
        return self.authorized


def _event(text: str, message_id: str = "m-1") -> SimpleNamespace:
    source = SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id="chat", user_id="user")
    return SimpleNamespace(text=text, message_id=message_id, source=source)


def _telegram_group_event(
    text: str,
    *,
    is_bot: bool = True,
    reply_to_own_bot: bool = False,
) -> SimpleNamespace:
    own_bot = SimpleNamespace(id=313, username="ernie313_bot", is_bot=True)
    other_bot = SimpleNamespace(id=314, username="bert313_bot", is_bot=True)
    reply = SimpleNamespace(from_user=own_bot) if reply_to_own_bot else None
    raw_message = SimpleNamespace(
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        reply_to_message=reply,
        from_user=other_bot,
    )
    source = SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        chat_id="group",
        chat_type="group",
        user_id="314",
        is_bot=is_bot,
    )
    return SimpleNamespace(
        text=text,
        source=source,
        raw_message=raw_message,
        reply_to_message_id="previous" if reply else None,
    )


def _telegram_gateway(mode: str | None) -> SimpleNamespace:
    extra = {"mention_patterns": [r"\bErnie\b", r"@ernie313_bot\b"]}
    if mode is not None:
        extra["group_bot_messages"] = mode
    telegram = SimpleNamespace(
        extra=extra
    )
    adapter = SimpleNamespace(_bot=SimpleNamespace(id=313, username="ernie313_bot"))
    return SimpleNamespace(
        config=SimpleNamespace(platforms={"telegram": telegram}),
        adapters={"telegram": adapter},
    )


def test_classification_is_conservative_and_mixed_is_explicit() -> None:
    assert classify_ingress_text("implement the repository fix") == ("work",)
    assert classify_ingress_text("schedule a dentist appointment") == ("personal",)
    assert classify_ingress_text("implement the fix and schedule my appointment") == ("work", "personal")
    assert classify_ingress_text("help me think about tomorrow") == ()


def test_ernie_work_is_enqueued_once_without_private_text_reaching_codex_or_persona(tmp_path: Path) -> None:
    store = HandoffStore(tmp_path / "handoff.sqlite")
    coordinator = IngressCoordinator(cell=Owner.ERNIE, store=store)
    private_text = "implement the repository fix for private_canary_NATE"

    first = coordinator.handle(_event(private_text), Gateway())
    second = coordinator.handle(_event(private_text), Gateway())

    assert first == second
    assert first["action"] == "rewrite"
    assert private_text not in first["text"]
    assert "transferred to Codex" in first["text"]
    assert store.count_pending() == 1
    stored = store.due(coordinator.now(), 10)[0].envelope
    assert stored.owner == Owner.CODEX
    assert private_text not in str(stored.to_dict())
    assert stored.local_payload_ref.startswith("ernie-local:")


def test_bert_work_uses_existing_sanitized_input_and_mixed_never_duplicates_work(tmp_path: Path) -> None:
    store = HandoffStore(tmp_path / "handoff.sqlite")
    coordinator = IngressCoordinator(cell=Owner.BERT, store=store)
    result = coordinator.handle(_event("implement the repository fix", "work-1"), Gateway())
    assert result["action"] == "rewrite"
    assert "implement the repository fix" not in result["text"]
    assert store.count_pending() == 1

    mixed = coordinator.handle(_event("implement the fix and schedule my appointment", "mixed-1"), Gateway())
    assert mixed["action"] == "rewrite"
    assert "implement the fix" not in mixed["text"]
    assert "bounded clarification" in mixed["text"]
    assert store.count_pending() == 2


def test_unauthorized_or_missing_message_identity_cannot_persist(tmp_path: Path) -> None:
    store = HandoffStore(tmp_path / "handoff.sqlite")
    coordinator = IngressCoordinator(cell=Owner.BERT, store=store)
    assert coordinator.handle(_event("implement this"), Gateway(False)) == {"action": "allow"}
    with pytest.raises(ValueError, match="message identity"):
        coordinator.handle(_event("implement this", ""), Gateway(True))
    assert store.count_pending() == 0


def test_telegram_bot_chatter_requires_an_explicit_direct_mention() -> None:
    gateway = _telegram_gateway("mentions_only")

    assert guard_telegram_group_bot_message(_telegram_group_event("model retry 3/5"), gateway) == {
        "action": "skip",
        "reason": "telegram_group_bot_message_not_explicitly_addressed",
    }
    assert guard_telegram_group_bot_message(
        _telegram_group_event("@ernie313_bot can you inspect this?"), gateway
    ) is None


def test_telegram_bot_reply_to_this_bot_is_a_hard_chain_boundary() -> None:
    result = guard_telegram_group_bot_message(
        _telegram_group_event(
            "@ernie313_bot still retrying",
            reply_to_own_bot=True,
        ),
        _telegram_gateway("mentions_only"),
    )

    assert result == {"action": "skip", "reason": "telegram_bot_reply_chain"}


@pytest.mark.parametrize(
    ("mode", "expected_reason"),
    [
        ("none", "telegram_group_bot_messages_disabled"),
        ("invalid", "telegram_group_bot_messages_invalid_policy"),
    ],
)
def test_telegram_bot_policy_is_fail_closed(mode: str, expected_reason: str) -> None:
    assert guard_telegram_group_bot_message(
        _telegram_group_event("@ernie313_bot hello"),
        _telegram_gateway(mode),
    ) == {"action": "skip", "reason": expected_reason}


def test_telegram_bot_policy_all_and_human_messages_preserve_normal_dispatch() -> None:
    assert guard_telegram_group_bot_message(
        _telegram_group_event("routine bot chatter"),
        _telegram_gateway("all"),
    ) is None
    assert guard_telegram_group_bot_message(
        _telegram_group_event("ordinary human message", is_bot=False),
        _telegram_gateway("none"),
    ) is None


def test_telegram_bot_policy_reads_the_profile_key_that_upstream_drops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.yaml").write_text(
        "telegram:\n  group_bot_messages: mentions_only\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    assert guard_telegram_group_bot_message(
        _telegram_group_event("@ernie313_bot one bounded request"),
        _telegram_gateway(None),
    ) is None
