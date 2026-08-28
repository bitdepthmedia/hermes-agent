"""OpenAI-compatible, task-boundary model router for the Ernie cell."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .history import normalize_tool_history
from .runtime_router import RouterRequest, RuntimeRouterConfig, load_router_config, prepare_worker_request


@dataclass(frozen=True)
class PreparedProxyPayload:
    upstream: dict[str, Any]
    public_model: str
    model_id: str
    selection_reason: str


def prepare_proxy_payload(payload: Mapping[str, Any], config: RuntimeRouterConfig) -> PreparedProxyPayload:
    """Prepare one request without keyword-based or mid-conversation switching."""

    upstream = dict(payload)
    messages_value = upstream.get("messages", [])
    if not isinstance(messages_value, list) or any(not isinstance(item, dict) for item in messages_value):
        raise ValueError("router messages are invalid")
    task_boundary = str(upstream.pop("ik_task_boundary", "conversation"))
    bounded = upstream.pop("ik_bounded_specialist_task", False)
    pinned = upstream.pop("ik_pinned_model_id", None)
    if not isinstance(bounded, bool) or (pinned is not None and not isinstance(pinned, str)):
        raise ValueError("router boundary metadata is invalid")
    reasoning_effort = str(upstream.get("reasoning_effort", "medium")).strip().lower()
    reasoning_enabled = reasoning_effort not in {"none", "off", "disabled"}
    tools = upstream.get("tools")
    prepared = prepare_worker_request(
        RouterRequest(
            task_boundary=task_boundary,
            bounded_specialist_task=bounded,
            pinned_model_id=pinned,
            reasoning_enabled=reasoning_enabled,
            messages=tuple(messages_value),
            needs_tools=isinstance(tools, list) and bool(tools),
        ),
        config,
    )
    public_model = str(upstream.get("model") or os.getenv("ERNIE_ROUTER_MODEL_NAME", "ernie-local"))
    upstream["model"] = prepared.runtime_model
    # The Qwen adapter keeps parsed argument mappings internally, but this
    # service speaks the OpenAI wire protocol to Ollama.  Ollama requires
    # function.arguments to be a JSON string in historical tool calls.
    upstream["messages"] = list(normalize_tool_history(prepared.messages, dialect="openai"))
    if prepared.reasoning_enabled:
        effective = reasoning_effort if reasoning_effort in {"low", "medium", "high"} else "medium"
        upstream["reasoning_effort"] = effective
        upstream["reasoning"] = {"effort": effective}
    else:
        upstream["reasoning_effort"] = "none"
        upstream["reasoning"] = {"effort": "none"}
    return PreparedProxyPayload(upstream, public_model, prepared.model_id, prepared.selection_reason)


def create_app(*, config_path: Path | None = None, upstream_base_url: str | None = None) -> FastAPI:
    router_config_path = config_path or Path(os.environ["IK_ROUTER_CONFIG"])
    config = load_router_config(router_config_path)
    upstream_url = (upstream_base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
    api_key = os.getenv("ERNIE_ROUTER_API_KEY", "")
    model_name = os.getenv("ERNIE_ROUTER_MODEL_NAME", "ernie-local")
    timeout = float(os.getenv("ERNIE_ROUTER_TIMEOUT_SECONDS", "900"))
    app = FastAPI(title="Ernie Router", version="2.0.0")
    logger = logging.getLogger("ernie-router")
    public = {"/", "/health", "/v1/health", "/version", "/props", "/v1/props", "/v1/models", "/api/v1/models", "/api/tags"}

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if request.url.path not in public and not request.url.path.startswith("/v1/models/"):
            if api_key and request.headers.get("authorization", "") != f"Bearer {api_key}":
                return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
        return await call_next(request)

    @app.get("/")
    async def root() -> dict[str, str]: return {"name": "ernie-router", "model": model_name}

    @app.get("/health")
    @app.get("/v1/health")
    async def health() -> dict[str, str]: return {"status": "ok"}

    @app.get("/version")
    async def version() -> dict[str, str]: return {"version": "ernie-router-2.0.0"}

    @app.get("/props")
    @app.get("/v1/props")
    async def props() -> dict[str, list[str]]: return {"models": [model_name]}

    @app.get("/v1/models")
    @app.get("/api/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": model_name, "object": "model", "created": int(time.time()), "owned_by": "ernie-router", "context_length": config.primary.capability.max_validated_context}]}

    @app.get("/v1/models/{model_id}")
    async def model(model_id: str) -> dict[str, Any]:
        if model_id != model_name: raise HTTPException(status_code=404, detail="Model not found")
        return {"id": model_name, "object": "model", "owned_by": "ernie-router", "context_length": config.primary.capability.max_validated_context}

    @app.get("/api/tags")
    async def tags() -> dict[str, Any]: return {"models": [{"name": model_name, "model": model_name, "size": 0}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        try:
            prepared = prepare_proxy_payload(await request.json(), config)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid router request") from exc
        target = f"{upstream_url}/v1/chat/completions"
        client_timeout = httpx.Timeout(timeout)
        if prepared.upstream.get("stream"):
            client = httpx.AsyncClient(timeout=client_timeout)
            stream_context = client.stream("POST", target, json=prepared.upstream)
            try:
                response = await stream_context.__aenter__()
            except httpx.HTTPError as exc:
                await client.aclose()
                logger.exception("model worker stream request failed")
                raise HTTPException(status_code=502, detail="Model worker unavailable") from exc

            if response.status_code >= 400:
                try:
                    body = await response.aread()
                finally:
                    await stream_context.__aexit__(None, None, None)
                    await client.aclose()
                return Response(
                    body,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type", "application/json"),
                )

            chunks = response.aiter_bytes()
            first_chunk = None
            try:
                async for chunk in chunks:
                    if chunk:
                        first_chunk = chunk
                        break
            except httpx.HTTPError as exc:
                await stream_context.__aexit__(type(exc), exc, exc.__traceback__)
                await client.aclose()
                logger.exception("model worker stream failed before first chunk")
                raise HTTPException(status_code=502, detail="Model worker unavailable") from exc

            if first_chunk is None:
                await stream_context.__aexit__(None, None, None)
                await client.aclose()
                raise HTTPException(status_code=502, detail="Model worker returned an empty stream")

            async def events():
                try:
                    yield first_chunk
                    async for chunk in chunks:
                        if chunk:
                            yield chunk
                finally:
                    await stream_context.__aexit__(None, None, None)
                    await client.aclose()

            return StreamingResponse(events(), media_type="text/event-stream")
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                response = await client.post(target, json=prepared.upstream)
        except httpx.HTTPError as exc:
            logger.exception("model worker request failed")
            raise HTTPException(status_code=502, detail="Model worker unavailable") from exc
        if response.status_code >= 400:
            return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type", "application/json"))
        body = response.json()
        if isinstance(body, dict): body["model"] = prepared.public_model
        return JSONResponse(body)

    return app


app = create_app() if os.environ.get("IK_ROUTER_CONFIG") else FastAPI(title="Ernie Router (unconfigured)")
