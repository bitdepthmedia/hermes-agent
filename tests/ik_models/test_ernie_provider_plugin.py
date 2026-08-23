from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
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
    assert profile.env_vars == ("ERNIE_ROUTER_API_KEY",)
    assert profile.fallback_models == ("ik-qwen38-eval:31629f53165a",)
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == {}
    assert "Typed approval result" in messages[0]["content"]


@pytest.mark.parametrize("endpoint", ["https://example.com/v1", "http://0.0.0.0:18422/v1", "http://localhost:18422/v1", ""])
def test_provider_rejects_non_exact_loopback_endpoint(endpoint: str) -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        _load(endpoint)


def test_runtime_resolver_binds_the_router_credential_without_falling_back(
    tmp_path: Path,
) -> None:
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: ik-ernie-local\n"
        "  default: ik-qwen38-eval:31629f53165a\n"
        "  base_url: http://127.0.0.1:18422/v1\n"
        "  api_mode: chat_completions\n",
        encoding="utf-8",
    )
    script = (
        "import json; "
        "from hermes_cli.runtime_provider import resolve_runtime_provider; "
        "r=resolve_runtime_provider(requested='ik-ernie-local'); "
        "print(json.dumps({k:r.get(k) for k in "
        "('provider','base_url','api_mode','source','requested_provider')})); "
        "raise SystemExit(0 if r.get('api_key') == 'synthetic-router-key' else 9)"
    )
    environment = {
        **os.environ,
        "HERMES_HOME": str(home),
        "IK_MODEL_BASE_URL": "http://127.0.0.1:18422/v1",
        "ERNIE_ROUTER_API_KEY": "synthetic-router-key",
        "PYTHONPATH": str(PLUGIN.parents[2]),
    }

    result = subprocess.run(
        (sys.executable, "-c", script),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    runtime = json.loads(result.stdout)
    assert runtime == {
        "provider": "ik-ernie-local",
        "base_url": "http://127.0.0.1:18422/v1",
        "api_mode": "chat_completions",
        "source": "env:ERNIE_ROUTER_API_KEY",
        "requested_provider": "ik-ernie-local",
    }
    assert "synthetic-router-key" not in result.stdout + result.stderr
