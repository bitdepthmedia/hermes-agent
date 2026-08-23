"""Deployment router bound to task boundaries and verified model adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .capabilities import ModelCapability
from .history import normalize_tool_history
from .qwen38_adapter import adapt_qwen38_messages
from .router import ModelCatalog, TaskRequirements, select_worker


_QWEN_MODEL_ID = "qwen38-27b-q4km"
_QWEN_ARTIFACT = "31629f53165ab6a7dad8c9847dcfd1fdf55829dac1e6e748f4a68581b0033d34"
_QWEN_PROJECTOR = "2e968a6af97ce35d8971890b257b9b7edabf20ad91450501fa53162a19ee33eb"


@dataclass(frozen=True)
class RuntimeWorker:
    capability: ModelCapability
    runtime_model: str
    reasoning_mode: str


@dataclass(frozen=True)
class RuntimeRouterConfig:
    cell_id: str
    primary: RuntimeWorker
    specialists: Mapping[str, RuntimeWorker]


@dataclass(frozen=True)
class RouterRequest:
    task_boundary: str
    bounded_specialist_task: bool
    pinned_model_id: str | None
    reasoning_enabled: bool
    messages: tuple[Mapping[str, Any], ...]
    needs_tools: bool = False


@dataclass(frozen=True)
class PreparedWorkerRequest:
    model_id: str
    runtime_model: str
    reasoning_enabled: bool
    messages: tuple[dict[str, Any], ...]
    selection_reason: str


def _worker(value: object) -> RuntimeWorker:
    if not isinstance(value, dict):
        raise ValueError("router worker is invalid")
    required = {
        "model_id",
        "runtime_model",
        "artifact_sha256",
        "reasoning_mode",
        "supports_tools",
        "supports_parallel_tools",
        "supports_vision",
        "context_tokens",
        "concurrency",
    }
    if not required.issubset(value):
        raise ValueError("router worker is incomplete")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value["artifact_sha256"])):
        raise ValueError("router worker artifact digest is invalid")
    if value["reasoning_mode"] not in {"capability-aware", "not-supported"}:
        raise ValueError("router reasoning mode is invalid")
    capability = ModelCapability(
        model_id=str(value["model_id"]),
        runtime=str(value["runtime_model"]),
        supports_tools=bool(value["supports_tools"]),
        supports_parallel_tools=bool(value["supports_parallel_tools"]),
        supports_vision=bool(value["supports_vision"]),
        supports_reasoning=value["reasoning_mode"] == "capability-aware",
        max_validated_context=int(value["context_tokens"]),
        max_validated_concurrency=int(value["concurrency"]),
        artifact_digest=str(value["artifact_sha256"]),
    )
    return RuntimeWorker(capability, str(value["runtime_model"]), str(value["reasoning_mode"]))


def load_router_config(path: Path) -> RuntimeRouterConfig:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_id") != "ik.hermes.model-router.v1" or document.get("cell_id") != "ernie":
        raise ValueError("router identity is invalid")
    selection = document.get("selection", {})
    if selection != {
        "mode": "task-boundary-only",
        "mid_conversation_keyword_switching": False,
        "tools_force_primary": False,
    }:
        raise ValueError("router selection contract is invalid")
    primary_document = document.get("primary", {})
    primary = _worker(primary_document)
    if (
        primary.capability.model_id != _QWEN_MODEL_ID
        or primary.capability.artifact_digest != _QWEN_ARTIFACT
        or primary.reasoning_mode != "capability-aware"
        or primary_document.get("projector_sha256") != _QWEN_PROJECTOR
        or not primary.capability.supports_tools
    ):
        raise ValueError("router primary is not the selected verified Qwen candidate")
    specialists_document = document.get("specialists", {})
    if not isinstance(specialists_document, dict):
        raise ValueError("router specialists are invalid")
    specialists = {str(boundary): _worker(value) for boundary, value in specialists_document.items()}
    return RuntimeRouterConfig("ernie", primary, specialists)


def prepare_worker_request(request: RouterRequest, config: RuntimeRouterConfig) -> PreparedWorkerRequest:
    has_tool_history = any(bool(message.get("tool_calls")) for message in request.messages)
    catalog = ModelCatalog(
        config.primary.capability,
        {name: worker.capability for name, worker in config.specialists.items()},
    )
    selection = select_worker(
        TaskRequirements(
            task_boundary=request.task_boundary,
            needs_tools=request.needs_tools or has_tool_history,
            bounded_specialist_task=request.bounded_specialist_task,
            pinned_model_id=request.pinned_model_id,
        ),
        catalog,
    )
    workers = {config.primary.capability.model_id: config.primary}
    workers.update({worker.capability.model_id: worker for worker in config.specialists.values()})
    worker = workers[selection.model.model_id]
    reasoning_enabled = request.reasoning_enabled and worker.capability.supports_reasoning
    if worker.capability.model_id == _QWEN_MODEL_ID:
        messages = adapt_qwen38_messages(request.messages, reasoning_enabled=reasoning_enabled)
    else:
        messages = normalize_tool_history(request.messages, dialect="openai")
    return PreparedWorkerRequest(
        model_id=worker.capability.model_id,
        runtime_model=worker.runtime_model,
        reasoning_enabled=reasoning_enabled,
        messages=messages,
        selection_reason=selection.reason,
    )
