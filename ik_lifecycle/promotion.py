"""Fixture-safe paired pointer transaction; no service adapter executes here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


@dataclass(frozen=True)
class ApprovalReceipt:
    cell_id: str
    bundle_id: str
    expires_at: datetime
    digest: str


@dataclass(frozen=True)
class PromotionReceipt:
    previous_release: str
    previous_profile: str
    previous_generation: int
    release: str
    profile: str
    generation: int


class PairedPointers:
    def __init__(self, release_path: Path, profile_path: Path, journal_path: Path) -> None:
        self.release_path, self.profile_path, self.journal_path = release_path, profile_path, journal_path

    def initialize(self, release: str, profile: str, generation: int) -> None:
        _write(self.release_path, {"release": release, "generation": generation})
        _write(self.profile_path, {"profile": profile, "generation": generation})

    def read_pair(self) -> tuple[str, str, int]:
        release = json.loads(self.release_path.read_text()); profile = json.loads(self.profile_path.read_text())
        if release["generation"] != profile["generation"]:
            raise RuntimeError("mixed pointer generation")
        return release["release"], profile["profile"], release["generation"]

    def switch(self, release: str, profile: str, generation: int, *, crash_after_release: bool = False) -> None:
        previous = self.read_pair()
        _write(self.journal_path, {"state": "switching", "previous": previous, "next": [release, profile, generation]})
        _write(self.release_path, {"release": release, "generation": generation})
        if crash_after_release:
            raise RuntimeError("injected pointer-switch crash")
        _write(self.profile_path, {"profile": profile, "generation": generation})
        _write(self.journal_path, {"state": "complete", "previous": previous, "next": [release, profile, generation]})

    def recover(self) -> None:
        if not self.journal_path.is_file():
            return
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if journal.get("state") != "switching":
            return
        release, profile, generation = journal["previous"]
        _write(self.release_path, {"release": release, "generation": generation})
        _write(self.profile_path, {"profile": profile, "generation": generation})
        _write(self.journal_path, {**journal, "state": "recovered"})


def promote_pair(pointers: PairedPointers, release: str, profile: str, generation: int, approval: ApprovalReceipt, *, service_closed: bool) -> PromotionReceipt:
    if not service_closed: raise ValueError("service must be closed before promotion")
    if approval.expires_at <= datetime.now(timezone.utc) or not approval.digest or approval.bundle_id != release:
        raise ValueError("approval scope, digest, or expiry invalid")
    previous = pointers.read_pair()
    pointers.switch(release, profile, generation)
    return PromotionReceipt(*previous, release, profile, generation)
