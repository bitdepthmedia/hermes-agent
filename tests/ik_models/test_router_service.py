from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ik_extensions.model_workers import router_service
from ik_extensions.model_workers.router_service import create_app, prepare_proxy_payload
from ik_extensions.model_workers.runtime_router import load_router_config


ROOT = Path(__file__).parents[2]
CONFIG = load_router_config(ROOT / "ik_cells/ernie-router.json")


def test_proxy_defaults_to_qwen_primary_without_keyword_switching() -> None:
    payload = {
        "model": "ernie-local",
        "messages": [{"role": "user", "content": "debug this Python function step by step"}],
    }
    prepared = prepare_proxy_payload(payload, CONFIG)

    assert prepared.upstream["model"] == "ik-qwen38-eval:31629f53165a"
    assert prepared.public_model == "ernie-local"
    assert prepared.selection_reason == "primary-generalist"
    assert CONFIG.primary.capability.max_validated_context == 65536


def test_proxy_routes_only_an_explicit_bounded_task_boundary() -> None:
    payload = {
        "model": "ernie-local",
        "ik_task_boundary": "coding",
        "ik_bounded_specialist_task": True,
        "messages": [{"role": "user", "content": "bounded implementation child"}],
    }
    prepared = prepare_proxy_payload(payload, CONFIG)

    assert prepared.upstream["model"] == "ernie-qwen-coding-ctx:latest"
    assert "ik_task_boundary" not in prepared.upstream
    assert "ik_bounded_specialist_task" not in prepared.upstream


def test_proxy_keeps_tool_history_on_qwen_and_preserves_reasoning() -> None:
    payload = {
        "model": "ernie-local",
        "reasoning_effort": "medium",
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        "messages": [
            {"role": "user", "content": "use the tool"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ],
    }
    prepared = prepare_proxy_payload(payload, CONFIG)

    assert prepared.upstream["model"] == "ik-qwen38-eval:31629f53165a"
    assert prepared.upstream["reasoning_effort"] == "medium"
    assert prepared.upstream["reasoning"] == {"effort": "medium"}
    assistant = next(message for message in prepared.upstream["messages"] if message.get("tool_calls"))
    call = assistant["tool_calls"][0]["function"]
    assert call["arguments"] == '{"q":"x"}'


def test_streaming_upstream_error_keeps_status_and_body() -> None:
    class ErrorResponse:
        status_code = 400
        headers = {"content-type": "application/json"}
        content = b'{"error":{"message":"tool arguments must be a string"}}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def aiter_bytes(self):
            if False:
                yield b""

        async def aread(self):
            return self.content

    class ErrorClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def stream(self, *args, **kwargs):
            return ErrorResponse()

        async def aclose(self):
            pass

    original = router_service.httpx.AsyncClient
    router_service.httpx.AsyncClient = ErrorClient
    try:
        app = create_app(config_path=ROOT / "ik_cells/ernie-router.json", upstream_base_url="http://worker")
        response = TestClient(app, raise_server_exceptions=False).post(
            "/v1/chat/completions",
            json={
                "model": "ernie-local",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
    finally:
        router_service.httpx.AsyncClient = original

    assert response.status_code == 400
    assert response.json() == {"error": {"message": "tool arguments must be a string"}}


def test_empty_successful_upstream_stream_becomes_bad_gateway() -> None:
    class EmptyResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def aiter_bytes(self):
            if False:
                yield b""

    class EmptyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def stream(self, *args, **kwargs):
            return EmptyResponse()

        async def aclose(self):
            pass

    original = router_service.httpx.AsyncClient
    router_service.httpx.AsyncClient = EmptyClient
    try:
        app = create_app(config_path=ROOT / "ik_cells/ernie-router.json", upstream_base_url="http://worker")
        response = TestClient(app, raise_server_exceptions=False).post(
            "/v1/chat/completions",
            json={
                "model": "ernie-local",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
    finally:
        router_service.httpx.AsyncClient = original

    assert response.status_code == 502
    assert response.json() == {"detail": "Model worker returned an empty stream"}


def test_proxy_rejects_unbounded_specialist_selection() -> None:
    payload = {
        "ik_task_boundary": "coding",
        "ik_bounded_specialist_task": False,
        "messages": [{"role": "user", "content": "child"}],
    }
    prepared = prepare_proxy_payload(payload, CONFIG)
    assert prepared.upstream["model"] == "ik-qwen38-eval:31629f53165a"
