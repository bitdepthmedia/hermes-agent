from __future__ import annotations

import json
from pathlib import Path

import pytest

from ik_extensions.model_workers.runtime_router import (
    RouterRequest,
    load_router_config,
    prepare_worker_request,
)


def _config(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_id": "ik.hermes.model-router.v1",
                "cell_id": "ernie",
                "primary": {
                    "model_id": "qwen38-27b-q4km",
                    "runtime_model": "ik-qwen38-eval:31629f53165a",
                    "artifact_sha256": "31629f53165ab6a7dad8c9847dcfd1fdf55829dac1e6e748f4a68581b0033d34",
                    "projector_sha256": "2e968a6af97ce35d8971890b257b9b7edabf20ad91450501fa53162a19ee33eb",
                    "reasoning_mode": "capability-aware",
                    "supports_tools": True,
                    "supports_parallel_tools": True,
                    "supports_vision": True,
                    "context_tokens": 32768,
                    "concurrency": 2,
                },
                "specialists": {
                    "coding": {
                        "model_id": "ernie-qwen-coding-ctx",
                        "runtime_model": "ernie-qwen-coding-ctx",
                        "artifact_sha256": "b" * 64,
                        "reasoning_mode": "capability-aware",
                        "supports_tools": True,
                        "supports_parallel_tools": False,
                        "supports_vision": False,
                        "context_tokens": 32768,
                        "concurrency": 1,
                    },
                    "reasoning": {
                        "model_id": "ernie-deepseek-r1-ctx",
                        "runtime_model": "ernie-deepseek-r1-ctx",
                        "artifact_sha256": "c" * 64,
                        "reasoning_mode": "capability-aware",
                        "supports_tools": False,
                        "supports_parallel_tools": False,
                        "supports_vision": False,
                        "context_tokens": 32768,
                        "concurrency": 1,
                    },
                },
                "selection": {
                    "mode": "task-boundary-only",
                    "mid_conversation_keyword_switching": False,
                    "tools_force_primary": False,
                },
                "approval_contract": {
                    "schema_id": "ik.hermes.approval-result.v1",
                    "sha256": "2f50d00f2266ace102e0b35b721e8fded0da4331f9e538210447adb0151f9e64",
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_qwen_is_primary_and_tool_history_uses_qwen_adapter(tmp_path: Path) -> None:
    config = load_router_config(_config(tmp_path / "router.json"))
    request = RouterRequest(
        task_boundary="conversation",
        bounded_specialist_task=False,
        pinned_model_id=None,
        reasoning_enabled=True,
        messages=(
            {"role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "x", "arguments": '{"a":1}'}}]},
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
        ),
    )
    prepared = prepare_worker_request(request, config)

    assert prepared.runtime_model == "ik-qwen38-eval:31629f53165a"
    assert prepared.model_id == "qwen38-27b-q4km"
    assert prepared.messages[1]["tool_calls"][0]["function"]["arguments"] == {"a": 1}
    assert "Reasoning is enabled" in prepared.messages[0]["content"]
    assert "Typed approval result" in prepared.messages[0]["content"]


def test_specialists_are_selected_only_at_explicit_bounded_task_boundaries(tmp_path: Path) -> None:
    config = load_router_config(_config(tmp_path / "router.json"))
    conversation = prepare_worker_request(
        RouterRequest("coding", False, None, True, ({"role": "user", "content": "code this"},)), config
    )
    specialist = prepare_worker_request(
        RouterRequest("coding", True, None, True, ({"role": "user", "content": "bounded task"},)), config
    )
    pinned = prepare_worker_request(
        RouterRequest("reasoning", True, conversation.model_id, True, ({"role": "user", "content": "continue"},)), config
    )
    assert conversation.model_id == "qwen38-27b-q4km"
    assert specialist.model_id == "ernie-qwen-coding-ctx"
    assert pinned.model_id == "qwen38-27b-q4km"


def test_router_rejects_gemma_primary_reasoning_disable_and_keyword_switching(tmp_path: Path) -> None:
    path = _config(tmp_path / "router.json")
    document = json.loads(path.read_text())
    for mutation in (
        lambda value: value["primary"].update(model_id="ernie-gemma4-ctx"),
        lambda value: value["primary"].update(reasoning_mode="disabled"),
        lambda value: value["selection"].update(mid_conversation_keyword_switching=True),
    ):
        changed = json.loads(json.dumps(document))
        mutation(changed)
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError):
            load_router_config(path)


def test_committed_ernie_router_is_the_verified_qwen_primary() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_router_config(root / "ik_cells/ernie-router.json")
    assert config.primary.capability.model_id == "qwen38-27b-q4km"
    assert config.primary.runtime_model == "ik-qwen38-eval:31629f53165a"
    assert config.primary.capability.supports_reasoning is True


def test_committed_model_manifest_binds_selected_qwen_artifacts() -> None:
    root = Path(__file__).resolve().parents[2]
    document = json.loads((root / "ik_cells/ernie-model.json").read_text(encoding="utf-8"))
    assert document["status"] == "SELECTED_FOR_ERNIE_DEPLOYMENT"
    assert document["model"]["sha256"] == "31629f53165ab6a7dad8c9847dcfd1fdf55829dac1e6e748f4a68581b0033d34"
    assert document["projector"]["sha256"] == "2e968a6af97ce35d8971890b257b9b7edabf20ad91450501fa53162a19ee33eb"
    assert document["import_manifest_sha256"] == "26e0a3a36561ea7d0dfa6fd27356292d3dcc0888da2ba7d8f5beb17c35a4ec5a"


def test_runtime_router_requires_typed_approval_contract_binding(tmp_path: Path) -> None:
    path = _config(tmp_path / "router.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    document.pop("approval_contract")
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="approval contract"):
        load_router_config(path)
