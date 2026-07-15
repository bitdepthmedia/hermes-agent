"""Offline-capable deterministic worker registry for the Ernie system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerResult:
    ok: bool
    payload: dict[str, Any]
    requires_network: bool = False


class WorkerRegistry:
    def __init__(self):
        self._workers: dict[str, Callable[..., WorkerResult]] = {
            "text.extract": self._extract_text,
        }

    def register(self, capability: str, worker: Callable[..., WorkerResult]) -> None:
        self._workers[capability] = worker

    def run(self, capability: str, content: bytes, *, mime_type: str) -> WorkerResult:
        worker = self._workers.get(capability)
        if worker is None:
            return WorkerResult(False, {"error": "unknown_capability"})
        return worker(content, mime_type=mime_type)

    @staticmethod
    def _extract_text(content: bytes, *, mime_type: str) -> WorkerResult:
        if mime_type != "text/plain":
            return WorkerResult(False, {"error": "unsupported_mime_type"})
        return WorkerResult(True, {"text": content.decode("utf-8", errors="replace")})
