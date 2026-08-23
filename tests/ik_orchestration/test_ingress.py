from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ik_extensions.persona_orchestration.envelope import Owner
from ik_extensions.persona_orchestration.ingress import IngressCoordinator, classify_ingress_text
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
