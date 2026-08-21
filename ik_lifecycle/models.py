"""Value objects shared by the Hermes staged lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


class LifecycleBlockedError(RuntimeError):
    """A fail-closed lifecycle decision with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StableRelease:
    tag: str
    commit_sha: str
    published_at: datetime
    html_url: str


@dataclass(frozen=True)
class ReleaseSelection:
    latest: StableRelease
    target: StableRelease
    discovered_at: datetime


@dataclass(frozen=True)
class RemoteContractResult:
    status: str
    code: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleReceipt:
    kind: str
    status: str
    observed_at: datetime
    data: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1
