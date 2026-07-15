from __future__ import annotations

from gateway.shared_core_adapter import SharedCoreAdapter, adapter_from_environment
from shared_core import ActionClass, SharedCore, TaskOwner


def test_shadow_adapter_routes_private_or_offline_work_to_ernie(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    adapter = SharedCoreAdapter(core, shadow_mode=True)

    task = adapter.ingest(
        session_id="tg-1",
        request="Extract this private local spreadsheet",
        requested_owner=TaskOwner.BERT,
        action_class=ActionClass.READ_ONLY,
        offline=True,
        contains_local_data=True,
    )

    assert task.owner is TaskOwner.ERNIE
    assert adapter.delivery_owner(task) is TaskOwner.BERT
    assert adapter.shadow_events[-1]["selected_owner"] == "ernie"


def test_shadow_adapter_keeps_cloud_specialist_work_with_bert(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    adapter = SharedCoreAdapter(core, shadow_mode=True)

    task = adapter.ingest(
        session_id="tg-2",
        request="Research current cloud deployment options",
        requested_owner=TaskOwner.BERT,
        action_class=ActionClass.READ_ONLY,
        offline=False,
        contains_local_data=False,
    )

    assert task.owner is TaskOwner.BERT
    assert adapter.delivery_owner(task) is TaskOwner.BERT


def test_adapter_from_environment_is_opt_in_and_profile_scoped(tmp_path, monkeypatch):
    monkeypatch.delenv("SHARED_CORE_SHADOW_MODE", raising=False)
    assert adapter_from_environment() is None

    monkeypatch.setenv("SHARED_CORE_SHADOW_MODE", "true")
    monkeypatch.setenv("SHARED_CORE_DB", str(tmp_path / "core.db"))
    monkeypatch.setenv("SHARED_CORE_PRIMARY", "ernie")
    adapter = adapter_from_environment()

    assert adapter is not None
    assert adapter.primary is TaskOwner.ERNIE
