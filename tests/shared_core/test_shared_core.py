from __future__ import annotations

from shared_core import (
    ActionClass,
    DataPolicy,
    SharedCore,
    TaskOwner,
    TaskState,
    WorkerRegistry,
)


def test_sensitive_handoff_is_sanitized_and_audited(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    task = core.create_task(
        owner=TaskOwner.BERT,
        session_id="telegram-1",
        request="Summarize the local statement",
        action_class=ActionClass.READ_ONLY,
    )

    handoff = core.create_handoff(
        task.id,
        recipient=TaskOwner.BERT,
        content="Alice Example SSN 123-45-6789 password=opensesame",
    )

    assert "Alice" not in handoff.sanitized_content
    assert "123-45-6789" not in handoff.sanitized_content
    assert "opensesame" not in handoff.sanitized_content
    assert handoff.finding_kinds == {"name", "ssn", "password"}
    assert core.audit_events(task.id)[-1].kind == "handoff.created"


def test_worker_lifecycle_cleans_up_after_a_validated_result(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    task = core.create_task(
        owner=TaskOwner.ERNIE,
        session_id="offline-1",
        request="Extract a document locally",
        action_class=ActionClass.READ_ONLY,
    )
    worker = core.start_worker(task.id, capability="document.extract", scope="one file")
    core.complete_worker(worker.id, evidence={"pages": 2}, result_valid=True)

    assert core.get_task(task.id).state is TaskState.READY_TO_SYNTHESIZE
    assert core.get_worker(worker.id).state.value == "cleaned_up"


def test_irreversible_action_is_never_auto_promoted(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    for session_id in ("s1", "s2", "s2"):
        task = core.create_task(
            owner=TaskOwner.BERT,
            session_id=session_id,
            request="Send a report externally",
            action_class=ActionClass.EXTERNAL_SIDE_EFFECT,
        )
        core.complete_task(task.id, success=True)

    workflow = core.evaluate_pattern("send a report externally")

    assert workflow is None


def test_reversible_pattern_auto_promotes_after_three_successes_across_two_sessions(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    for session_id in ("s1", "s2", "s2"):
        task = core.create_task(
            owner=TaskOwner.ERNIE,
            session_id=session_id,
            request="Classify a local document",
            action_class=ActionClass.READ_ONLY,
        )
        core.complete_task(task.id, success=True)

    workflow = core.evaluate_pattern("classify a local document")

    assert workflow is not None
    assert workflow.active is True
    assert workflow.reversible is True
    assert workflow.review_due_at is not None


def test_new_policy_rules_require_review_before_becoming_active(tmp_path):
    core = SharedCore(tmp_path / "core.db")

    proposal = core.propose_policy_rule("employee-id", r"EMP-[0-9]{6}")

    assert proposal.active is False
    assert DataPolicy().sanitize("EMP-123456").content == "EMP-123456"
    core.approve_policy_rule(proposal.id, reviewer="nate")
    assert "[REDACTED:employee-id]" in core.policy().sanitize("EMP-123456").content


def test_local_worker_registry_stays_available_without_cloud(tmp_path):
    registry = WorkerRegistry()
    result = registry.run("text.extract", b"hello offline", mime_type="text/plain")

    assert result.ok is True
    assert result.payload["text"] == "hello offline"
    assert result.requires_network is False
