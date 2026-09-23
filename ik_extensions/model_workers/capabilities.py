from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    runtime: str
    supports_tools: bool
    supports_parallel_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    max_validated_context: int
    max_validated_concurrency: int
    artifact_digest: str
