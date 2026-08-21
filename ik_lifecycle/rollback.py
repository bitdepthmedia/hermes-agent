from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .promotion import PairedPointers, PromotionReceipt


class RollbackMode(StrEnum):
    PRE_TRAFFIC = "pre-traffic"
    POST_WRITE = "post-write"


@dataclass(frozen=True)
class RollbackReceipt:
    status: str
    mode: RollbackMode


def rollback_pair(pointers: PairedPointers, promotion: PromotionReceipt, mode: RollbackMode, *, delta_reconciled: bool) -> RollbackReceipt:
    if mode == RollbackMode.POST_WRITE and not delta_reconciled:
        return RollbackReceipt("APPROVAL_REQUIRED", mode)
    pointers.switch(promotion.previous_release, promotion.previous_profile, promotion.previous_generation)
    return RollbackReceipt("ROLLED_BACK", mode)
