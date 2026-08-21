"""Task-boundary selection; never keyword-swap a live persona conversation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .capabilities import ModelCapability


@dataclass(frozen=True)
class ModelCatalog:
    primary: ModelCapability
    specialists: Mapping[str, ModelCapability]


@dataclass(frozen=True)
class TaskRequirements:
    task_boundary: str
    needs_tools: bool
    bounded_specialist_task: bool
    pinned_model_id: str | None


@dataclass(frozen=True)
class WorkerSelection:
    model: ModelCapability
    reason: str


def select_worker(task: TaskRequirements, catalog: ModelCatalog) -> WorkerSelection:
    if task.pinned_model_id:
        candidates = (catalog.primary, *catalog.specialists.values())
        for candidate in candidates:
            if candidate.model_id == task.pinned_model_id:
                if task.needs_tools and not candidate.supports_tools:
                    raise ValueError("pinned model lacks required tool capability")
                return WorkerSelection(candidate, "conversation-pinned")
        raise ValueError("pinned model is not provenance-qualified")
    if task.bounded_specialist_task and task.task_boundary in catalog.specialists:
        selected = catalog.specialists[task.task_boundary]
        if not task.needs_tools or selected.supports_tools:
            return WorkerSelection(selected, "bounded-specialist")
    if task.needs_tools and not catalog.primary.supports_tools:
        raise ValueError("primary model lacks required tool capability")
    return WorkerSelection(catalog.primary, "primary-generalist")
