from shared_core.daily_goal import ActionKind, AgentStatus, ImprovementCandidate, WorkStatus
from shared_core.daily_goal_selection import assign_roles, select_goal


def candidate(candidate_id, category, impact, risk, owner="ernie", action=ActionKind.READ_ONLY_AUDIT):
    return ImprovementCandidate(
        candidate_id, candidate_id, category, ("verified",),
        impact, 4, 5, 1, risk, action, owner, "system-health",
    )


def status(agent, candidates):
    return AgentStatus(
        agent,
        WorkStatus.NO_PENDING_WORK,
        "idle",
        ("verified",),
        "2026-07-18T09:05:00-04:00",
        tuple(candidates),
        history_complete=True,
    )


def test_highest_impact_evidence_backed_goal_wins():
    reliability = candidate("reliability", "reliability", 5, 1)
    docs = candidate("docs", "docs", 5, 0)
    selected = select_goal((status("ernie", [docs]), status("bert", [reliability])))
    assert selected.candidate.candidate_id == "reliability"


def test_bert_can_own_only_read_only_work():
    proposal = candidate("proposal", "docs", 5, 0, owner="bert", action=ActionKind.PATCH_PROPOSAL)
    assert assign_roles(proposal) == ("ernie", "bert")


def test_candidate_without_evidence_is_rejected():
    bad = candidate("bad", "reliability", 5, 0)
    bad = ImprovementCandidate(**{**bad.__dict__, "evidence": ()})
    assert select_goal((status("ernie", [bad]), status("bert", []))) is None


def test_candidate_from_incomplete_history_is_rejected():
    incomplete = AgentStatus(
        "ernie",
        WorkStatus.NO_PENDING_WORK,
        "idle",
        ("verified",),
        "2026-07-18T09:05:00-04:00",
        (candidate("unsafe", "reliability", 5, 0),),
        history_complete=False,
    )
    assert select_goal((incomplete, status("bert", []))) is None
