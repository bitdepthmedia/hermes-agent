"""Exactly-once daily Bert-Ernie coordination and receipt formatting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .daily_goal import (
    ActionKind,
    CycleState,
    DailyReceipt,
    ImprovementCandidate,
    resolve_trigger,
)
from .daily_goal_execution import ExecutionOutcome
from .daily_goal_selection import assign_roles, rank_candidates


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class CoordinatorResult:
    receipt: DailyReceipt | None
    message: str
    reran_work: bool
    cycle_id: str


def _fallback_candidate() -> ImprovementCandidate:
    return ImprovementCandidate(
        "daily-process-health-audit",
        "Audit the Bert-Ernie daily coordination path",
        "reliability",
        ("both agents explicitly reported no pending work",),
        4,
        3,
        5,
        1,
        0,
        ActionKind.READ_ONLY_AUDIT,
        "ernie",
        "scheduler-health",
    )


def format_telegram_summary(receipt: DailyReceipt) -> str:
    tracker = f"Ernie: {receipt.ernie_status}; Bert: {receipt.bert_status}."
    if receipt.trigger == "normal_work":
        return (
            f"Daily tracker: {tracker} Pending work confirmed; "
            "improvement fallback did not run."
        )
    if receipt.trigger == "unknown":
        return (
            f"Daily tracker: {tracker} Improvement fallback did not run. "
            + "; ".join(receipt.blockers)
        )
    return (
        f"Daily tracker: {tracker}\n"
        f"Daily improvement: {receipt.selected_goal}\n"
        f"Owner: {receipt.owner}; collaborator: {receipt.collaborator}\n"
        f"Verification: {'; '.join(receipt.verification) or 'blocked'}"
    )


def _failed_outcome(actor: str, exc: Exception) -> ExecutionOutcome:
    return ExecutionOutcome(
        False,
        actor,
        "",
        (),
        f"{type(exc).__name__}: {exc}",
    )


def run_daily_cycle(
    *,
    mode,
    now: datetime,
    store,
    collect_ernie,
    collect_bert,
    execute,
    review,
) -> CoordinatorResult:
    if mode not in {"checkin", "watchdog"}:
        raise ValueError("mode must be checkin or watchdog")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    local_date = now.astimezone(NEW_YORK).date()
    cycle = store.get_or_create_cycle(local_date)
    existing = store.get_receipt(cycle.cycle_id)
    retry_unknown = (
        mode == "watchdog" and existing is not None and existing.trigger == "unknown"
    )
    if existing is not None:
        if not retry_unknown:
            return CoordinatorResult(
                existing,
                format_telegram_summary(existing),
                False,
                cycle.cycle_id,
            )
        claim_kind = "unknown_retry"
    else:
        claim_kind = "checkin"

    if not store.try_claim(cycle.cycle_id, claim_kind):
        return CoordinatorResult(None, "[SILENT]", False, cycle.cycle_id)

    ernie = collect_ernie()
    bert = collect_bert()
    trigger = resolve_trigger(ernie, bert)
    payload = {
        "ernie": ernie.status.value,
        "bert": bert.status.value,
        "mode": mode,
    }
    store.update_cycle(cycle.cycle_id, trigger, payload)

    if trigger is CycleState.NORMAL_WORK:
        receipt = DailyReceipt(
            cycle.cycle_id,
            ernie.status.value,
            bert.status.value,
            "normal_work",
            (),
            None,
            None,
            None,
            None,
            (),
            ernie.evidence + bert.evidence,
            (),
            "pending",
        )
    elif trigger is CycleState.UNKNOWN:
        receipt = DailyReceipt(
            cycle.cycle_id,
            ernie.status.value,
            bert.status.value,
            "unknown",
            (),
            None,
            None,
            None,
            None,
            (),
            (),
            ("missing or ambiguous agent status",),
            "pending",
        )
    else:
        ranked = [
            value
            for value in rank_candidates((ernie, bert))
            if value.candidate.candidate_id != "daily-process-health-audit"
        ]
        attempts = [value.candidate for value in ranked]
        fallback = _fallback_candidate()
        attempts.append(fallback)

        candidate = attempts[0]
        owner, collaborator = assign_roles(candidate)
        execution: ExecutionOutcome | None = None
        counterpart: ExecutionOutcome | None = None
        rejected: list[str] = []
        ok = False

        for candidate in attempts:
            owner, collaborator = assign_roles(candidate)
            counterpart = None
            store.update_cycle(
                cycle.cycle_id,
                CycleState.IMPROVEMENT_RUNNING,
                {**payload, "candidate_id": candidate.candidate_id},
            )
            try:
                execution = execute(candidate, owner)
                if not isinstance(execution, ExecutionOutcome):
                    raise TypeError("execute returned an invalid outcome")
            except Exception as exc:
                execution = _failed_outcome(owner, exc)
            if not execution.ok:
                rejected.append(
                    f"{candidate.candidate_id}:"
                    f"{execution.blocker or 'execution failed'}"
                )
                continue

            try:
                counterpart = review(candidate, owner, execution.summary)
                if not isinstance(counterpart, ExecutionOutcome):
                    raise TypeError("review returned an invalid outcome")
            except Exception as exc:
                counterpart = _failed_outcome(collaborator, exc)
            if not counterpart.ok:
                rejected.append(
                    f"{candidate.candidate_id}:"
                    f"{counterpart.blocker or 'review failed'}"
                )
                break

            ok = True
            break

        assert execution is not None
        selected = next(
            (
                value
                for value in ranked
                if value.candidate.candidate_id == candidate.candidate_id
            ),
            None,
        )
        if selected is None:
            selection_reason = (
                "No eligible history candidate; used deterministic "
                "process-health audit"
            )
        elif ranked and selected is ranked[0]:
            selection_reason = (
                f"{candidate.candidate_id} ranked highest at score " f"{selected.score}"
            )
        else:
            selection_reason = (
                f"{candidate.candidate_id} selected after higher-ranked "
                f"candidates were blocked; score {selected.score}"
            )
        receipt = DailyReceipt(
            cycle.cycle_id,
            ernie.status.value,
            bert.status.value,
            "improvement",
            tuple(value.candidate.candidate_id for value in ranked),
            candidate.title,
            selection_reason,
            owner,
            collaborator,
            (execution.summary,) if execution.summary else (),
            execution.evidence + (() if counterpart is None else counterpart.evidence),
            tuple(rejected),
            "pending",
        )
        store.update_cycle(
            cycle.cycle_id,
            CycleState.COMPLETED if ok else CycleState.BLOCKED,
            {
                **payload,
                "ranked": [value.candidate.candidate_id for value in ranked],
            },
        )

    receipt = store.save_receipt(receipt)
    return CoordinatorResult(
        receipt,
        format_telegram_summary(receipt),
        True,
        cycle.cycle_id,
    )
