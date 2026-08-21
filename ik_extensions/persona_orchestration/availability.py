"""Quiet, idempotent offline-Ernie retry state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib

from .store import HandoffStore
from .transport import LoopbackTransport


@dataclass(frozen=True)
class AvailabilityConfig:
    base_interval_minutes: int = 25
    jitter_minutes: int = 5
    per_tick_limit: int = 1
    expiry_required: bool = True
    escalation_required: bool = True


@dataclass(frozen=True)
class TickReceipt:
    status: str
    delivered: int
    notifications: int


def _delay(task_id: str, config: AvailabilityConfig) -> timedelta:
    width = config.jitter_minutes * 2 + 1
    offset = int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) % width - config.jitter_minutes
    return timedelta(minutes=config.base_interval_minutes + offset)


def run_availability_tick(now: datetime, config: AvailabilityConfig, store: HandoffStore, transport: LoopbackTransport) -> TickReceipt:
    if not config.expiry_required or not config.escalation_required:
        return TickReceipt("BLOCKED", 0, 1)
    due = store.due(now, config.per_tick_limit)
    delivered = 0
    retried = 0
    for item in due:
        retry = item.envelope.retry
        if not retry.get("expires_at") or retry.get("escalation") in {None, "none"}:
            return TickReceipt("BLOCKED", delivered, 1)
        try:
            ack = transport.deliver(item.envelope)
        except ConnectionError:
            store.schedule_retry(item.task_id, now + _delay(item.task_id, config))
            retried += 1
            continue
        store.acknowledge(ack)
        delivered += 1
    status = "CLEAR" if not due else ("DELIVERED" if delivered else "QUIET_RETRY")
    return TickReceipt(status, delivered, 0)
