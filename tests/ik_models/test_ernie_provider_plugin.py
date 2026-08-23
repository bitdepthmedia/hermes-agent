from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest


PLUGIN = Path(__file__).parents[2] / "plugins/model-providers/ik-ernie-local/__init__.py"


def _load(endpoint: str):
    os.environ["IK_MODEL_BASE_URL"] = endpoint
    name = "_ik_ernie_provider_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, PLUGIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ik_ernie_local


def test_provider_is_loopback_only_qwen_primary_and_adapts_tool_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IK_MODEL_BASE_URL", "http://127.0.0.1:18422/v1")
    profile = _load("http://127.0.0.1:18422/v1")
    messages = profile.prepare_messages([
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ])
    assert profile.name == "ik-ernie-local"
    assert profile.base_url == "http://127.0.0.1:18422/v1"
    assert profile.fallback_models == ("ik-qwen38-eval:31629f53165a",)
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == {}
    assert "Typed approval result" in messages[0]["content"]


@pytest.mark.parametrize("endpoint", ["https://example.com/v1", "http://0.0.0.0:18422/v1", "http://localhost:18422/v1", ""])
def test_provider_rejects_non_exact_loopback_endpoint(endpoint: str) -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        _load(endpoint)
