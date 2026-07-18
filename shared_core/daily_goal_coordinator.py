"""Exactly-once daily Bert-Ernie coordination and receipt formatting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .daily_goal import (
    ActionKind,
    AgentStatus,
    CycleState,
    DailyReceipt,
    ImprovementCandidate,
    WorkStatus,
    receipt_integrity_hash,
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


def _fallback_candidate(evidence: tuple[str, ...]) -> ImprovementCandidate:
    return ImprovementCandidate(
        "daily-process-health-audit",
        "Audit the Bert-Ernie daily coordination path",
        "reliability",
        evidence,
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
            f"OPERATOR ALERT: Daily tracker unknown. {tracker} "
            "Improvement fallback did not run. "
            + "; ".join(receipt.blockers)
        )
    if receipt.outcome == "blocked":
        return (
            f"OPERATOR ALERT: Daily improvement blocked. {tracker}\n"
            f"Goal: {receipt.selected_goal}\n"
            f"Owner: {receipt.owner}; collaborator: {receipt.collaborator}\n"
            f"Blockers: {'; '.join(receipt.blockers) or 'review or execution failed'}"
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


def _review_receipt_is_valid(
    execution: ExecutionOutcome,
    counterpart: ExecutionOutcome,
) -> bool:
    statement = counterpart.review_statement
    review_hash = counterpart.review_hash
    source = counterpart.review_source
    metrics_hash = counterpart.review_metrics_hash
    observations = counterpart.review_observations
    if not all(
        isinstance(value, str) and value
        for value in (statement, review_hash, source, metrics_hash)
    ):
        return False
    expected_metrics = hashlib.sha256(
        execution.summary.encode("utf-8")
    ).hexdigest()
    if metrics_hash != expected_metrics or not source.startswith(
        "bert-no-tools:"
    ):
        return False
    if not observations:
        return False
    observation_map = dict(observations)
    if execution.summary.startswith("system-health audit:"):
        expected_keys = {"offline_capable", "services_up", "services_total"}
    elif execution.summary.startswith("scheduler-health audit:"):
        expected_keys = {
            "session_count",
            "queue_item_count",
            "queue_status_buckets",
        }
    else:
        expected_keys = set(observation_map)
    if (
        set(observation_map) != expected_keys
        or any(value not in execution.summary for value in observation_map.values())
        or not any(
            key in statement.lower() and value.lower() in statement.lower()
            for key, value in observation_map.items()
        )
    ):
        return False
    expected_review = hashlib.sha256(
        json.dumps(
            {
                "metrics_hash": metrics_hash,
                "source": source,
                "statement": statement,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return review_hash == expected_review


def _collect_status(
    agent: str, collector, now: datetime
) -> tuple[AgentStatus, str | None]:
    try:
        result = collector()
        if not isinstance(result, AgentStatus):
            raise TypeError("collector returned an invalid status")
        return result, None
    except Exception as exc:
        blocker = f"{agent.capitalize()} status collection failed: {type(exc).__name__}"
        return (
            AgentStatus(
                agent,
                WorkStatus.UNKNOWN,
                blocker,
                (blocker,),
                now.astimezone(UTC).isoformat(),
                (),
            ),
            blocker,
        )


def _normalize_status(
    status: AgentStatus, expected_agent: str, now: datetime
) -> AgentStatus:
    valid = (
        status.agent == expected_agent
        and bool(status.evidence)
        and bool(status.source_receipts)
        and status.history_complete
    )
    try:
        fresh = datetime.fromisoformat(status.freshness_at.replace("Z", "+00:00"))
        if fresh.tzinfo is None:
            raise ValueError
        fresh = fresh.astimezone(UTC)
        current = now.astimezone(UTC)
        valid = valid and timedelta(0) <= current - fresh <= timedelta(hours=24)
        valid = valid and fresh.astimezone(NEW_YORK).date() == now.astimezone(
            NEW_YORK
        ).date()
    except (AttributeError, TypeError, ValueError):
        valid = False
    if valid:
        return status
    return AgentStatus(
        expected_agent,
        WorkStatus.UNKNOWN,
        f"{expected_agent.capitalize()} provenance is stale or incomplete",
        ("provenance:invalid",),
        now.astimezone(UTC).isoformat(),
        (),
        history_complete=False,
        source_receipts=("provenance:invalid",),
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

    if not store.try_claim(
        cycle.cycle_id,
        claim_kind,
        now=now.astimezone(UTC),
    ):
        return CoordinatorResult(None, "[SILENT]", False, cycle.cycle_id)

    ernie, ernie_collection_blocker = _collect_status("ernie", collect_ernie, now)
    bert, bert_collection_blocker = _collect_status("bert", collect_bert, now)
    ernie = _normalize_status(ernie, "ernie", now)
    bert = _normalize_status(bert, "bert", now)
    collection_blockers = tuple(
        blocker
        for blocker in (ernie_collection_blocker, bert_collection_blocker)
        if blocker is not None
    )
    trigger = resolve_trigger(ernie, bert, now=now)
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
            outcome="normal_work",
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
            collection_blockers or ("missing or ambiguous agent status",),
            "pending",
            outcome="unknown",
        )
    else:
        ranked = [
            value
            for value in rank_candidates((ernie, bert))
            if value.candidate.candidate_id != "daily-process-health-audit"
        ]
        attempts = [value.candidate for value in ranked]
        specific_refs = tuple(
            value
            for value in ernie.evidence + bert.evidence
            if value.startswith(("session:", "cron:", "queue:"))
            and value.count(":") >= 1
            and not value.endswith((":clear", ":verified"))
        )
        if not specific_refs and not ranked:
            specific_refs = (
                f"ernie-complete:{hashlib.sha256('|'.join(ernie.source_receipts).encode()).hexdigest()}",
                f"bert-complete:{hashlib.sha256('|'.join(bert.source_receipts).encode()).hexdigest()}",
            )
        if specific_refs:
            attempts.append(_fallback_candidate(specific_refs))
        if not attempts:
            receipt = DailyReceipt(
                cycle.cycle_id,
                ernie.status.value,
                bert.status.value,
                "improvement",
                (),
                None,
                "No record-bound improvement candidate was attested",
                None,
                None,
                (),
                (),
                ("no eligible record-bound improvement candidate",),
                "pending",
                outcome="blocked",
            )
            store.update_cycle(
                cycle.cycle_id,
                CycleState.BLOCKED,
                {**payload, "ranked": []},
            )
            attempts = []

        candidate = attempts[0] if attempts else None
        if candidate is None:
            execution = None
            counterpart = None
        else:
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
            if counterpart.ok and not _review_receipt_is_valid(
                execution,
                counterpart,
            ):
                counterpart = ExecutionOutcome(
                    False,
                    counterpart.actor,
                    "",
                    counterpart.evidence,
                    "counterpart review receipt failed integrity validation",
                )
            if not counterpart.ok:
                rejected.append(
                    f"{candidate.candidate_id}:"
                    f"{counterpart.blocker or 'review failed'}"
                )
                break

            ok = True
            break

        if not attempts:
            candidate = None
        else:
            assert execution is not None
        if candidate is None:
            selected = None
        else:
            selected = next(
                (
                    value
                    for value in ranked
                    if value.candidate.candidate_id == candidate.candidate_id
                ),
                None,
            )
        if candidate is None:
            pass
        else:
            if selected is None:
                selection_reason = (
                    "No eligible history candidate; used deterministic "
                    "process-health audit"
                )
            elif ranked and selected is ranked[0]:
                selection_reason = (
                    f"{candidate.candidate_id} ranked highest at score "
                    f"{selected.score}"
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
                execution.evidence
                + (() if counterpart is None else counterpart.evidence),
                tuple(rejected),
                "pending",
                outcome="completed" if ok else "blocked",
                review_statement=(
                    counterpart.review_statement
                    if counterpart is not None
                    else None
                ),
                review_hash=(
                    counterpart.review_hash if counterpart is not None else None
                ),
                review_source=(
                    counterpart.review_source if counterpart is not None else None
                ),
                review_metrics_hash=(
                    counterpart.review_metrics_hash
                    if counterpart is not None
                    else None
                ),
            )
            store.update_cycle(
                cycle.cycle_id,
                CycleState.COMPLETED if ok else CycleState.BLOCKED,
                {
                    **payload,
                    "ranked": [value.candidate.candidate_id for value in ranked],
                },
            )

    receipt = replace(
        receipt,
        local_date=local_date.isoformat(),
        ernie_evidence=ernie.evidence,
        bert_evidence=bert.evidence,
        ernie_freshness_at=ernie.freshness_at,
        bert_freshness_at=bert.freshness_at,
        ernie_source_receipts=ernie.source_receipts,
        bert_source_receipts=bert.source_receipts,
        review_observations=(
            counterpart.review_observations
            if trigger is CycleState.IMPROVEMENT_SELECTING
            and counterpart is not None
            else ()
        ),
    )
    receipt = replace(
        receipt,
        decision_integrity_hash=receipt_integrity_hash(receipt),
    )
    receipt = store.save_receipt(receipt)
    if claim_kind == "unknown_retry":
        store.complete_unknown_retry(cycle.cycle_id)
    return CoordinatorResult(
        receipt,
        format_telegram_summary(receipt),
        True,
        cycle.cycle_id,
    )
