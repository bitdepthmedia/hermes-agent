import pytest

from shared_core.daily_goal import ActionKind, ImprovementCandidate
from shared_core.daily_goal_execution import ExecutionOutcome, execute_goal, review_goal


class FakeErnie:
    def __init__(self):
        self.posts = []

    def post(self, path, payload):
        self.posts.append((path, payload))
        return {"answer": "local audit completed", "verification": {"decision": "passed"}}


def candidate(kind=ActionKind.READ_ONLY_AUDIT, executor_id="system-health"):
    return ImprovementCandidate(
        "goal-1", "Audit scheduler health", "reliability", ("failed check-in",),
        5, 5, 5, 1, 1, kind, "ernie", executor_id,
    )


def test_read_only_ernie_goal_uses_local_gateway():
    ernie = FakeErnie()
    result = execute_goal(candidate(), owner="ernie", ernie=ernie, call_orchestrator=lambda **_: "")
    assert result.ok is True
    assert ernie.posts[0][0] == "/api/ernie/agent/run"
    assert ernie.posts[0][1]["mode"] == "auto"


def test_unknown_executor_is_blocked_before_any_call():
    with pytest.raises(ValueError, match="executor"):
        execute_goal(candidate(executor_id="shell:anything"), owner="ernie", ernie=FakeErnie(), call_orchestrator=lambda **_: "")


def test_counterpart_review_is_required():
    raw = '{"success":true,"content":"REVIEW_PASS: evidence is sufficient"}'
    review = review_goal(candidate(), owner="ernie", execution_summary="done", ernie=FakeErnie(), call_orchestrator=lambda **_: raw)
    assert review.ok is True
    assert review.reviewer == "bert"


def test_review_without_counterpart_evidence_fails():
    review = review_goal(
        candidate(),
        owner="ernie",
        execution_summary="done",
        ernie=FakeErnie(),
        call_orchestrator=lambda **_: '{"success":true,"content":""}',
    )
    assert review.ok is False
    assert review.blocker == "Bert review failed"


def test_approval_gated_candidate_is_blocked_before_any_call():
    blocked = ImprovementCandidate(
        **{**candidate().__dict__, "title": "Deploy scheduler health fix"}
    )
    ernie = FakeErnie()
    with pytest.raises(ValueError, match="approval-gated"):
        execute_goal(blocked, owner="ernie", ernie=ernie, call_orchestrator=lambda **_: "")
    assert ernie.posts == []
