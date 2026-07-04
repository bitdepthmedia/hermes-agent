"""Narrow local bridge to the primary orchestrator runtime.

This tool intentionally exposes only one capability: ask the locally running
orchestrator access endpoint to perform a bounded task through its own harness.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

from tools.registry import registry, tool_error


DEFAULT_ORCHESTRATOR_BASE_URL = "http://127.0.0.1:8643/v1"
DEFAULT_ORCHESTRATOR_MODEL = "hermes-agent"
MAX_TASK_CHARS = 8000
MIN_MAX_TOKENS = 64
MAX_MAX_TOKENS = 2000
DEFAULT_MAX_TOKENS = 1000
DEFAULT_TIMEOUT_SECONDS = 180
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_PORT = 8643


CALL_ORCHESTRATOR_SCHEMA = {
    "name": "call_orchestrator",
    "description": (
        "Ask the primary orchestrator runtime to perform a bounded task through "
        "the local Hermes access endpoint."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The complete, specific task for the primary orchestrator runtime to perform.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Maximum response tokens to request. Default 1000, maximum 2000.",
            },
        },
        "required": ["task"],
    },
}


def _orchestrator_base_url() -> str:
    return (
        os.getenv("HERMES_ORCHESTRATOR_BASE_URL")
        or os.getenv("ORCHESTRATOR_API_BASE_URL")
        or DEFAULT_ORCHESTRATOR_BASE_URL
    )


def _orchestrator_api_key() -> str:
    return os.getenv("HERMES_ORCHESTRATOR_API_KEY") or os.getenv("ORCHESTRATOR_API_KEY") or ""


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http":
        raise ValueError("Orchestrator access URL must use plain local http.")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("Orchestrator access URL must point to localhost or 127.0.0.1.")
    if parsed.port != ALLOWED_PORT:
        raise ValueError(f"Orchestrator access URL must use port {ALLOWED_PORT}.")
    normalized_path = parsed.path.rstrip("/")
    if normalized_path not in ("", "/v1"):
        raise ValueError("Orchestrator access URL path must be empty or /v1.")
    return base_url.rstrip("/") if normalized_path == "/v1" else base_url.rstrip("/") + "/v1"


def _bounded_max_tokens(value) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_MAX_TOKENS
    except (TypeError, ValueError):
        parsed = DEFAULT_MAX_TOKENS
    return max(MIN_MAX_TOKENS, min(MAX_MAX_TOKENS, parsed))


def check_call_orchestrator_requirements() -> bool:
    if not _orchestrator_api_key():
        return False
    try:
        _validate_base_url(_orchestrator_base_url())
    except ValueError:
        return False
    return True


def call_orchestrator(task: str, max_tokens=None) -> str:
    if not isinstance(task, str) or not task.strip():
        return tool_error("task is required", success=False)

    task = task.strip()
    if len(task) > MAX_TASK_CHARS:
        return tool_error(f"task exceeds {MAX_TASK_CHARS} characters", success=False)

    try:
        base_url = _validate_base_url(_orchestrator_base_url())
    except ValueError as exc:
        return tool_error(str(exc), success=False)

    api_key = _orchestrator_api_key()
    if not api_key:
        return tool_error("HERMES_ORCHESTRATOR_API_KEY or ORCHESTRATOR_API_KEY is not configured", success=False)

    payload = {
        "model": os.getenv("HERMES_ORCHESTRATOR_MODEL", DEFAULT_ORCHESTRATOR_MODEL),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the primary orchestrator runtime reached through a local access endpoint. "
                    "Complete only the requested task using your configured Hermes tools. "
                    "Return the result concisely. Do not tell the user to call another agent directly."
                ),
            },
            {"role": "user", "content": task},
        ],
        "max_tokens": _bounded_max_tokens(max_tokens),
    }

    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return tool_error(f"Orchestrator API HTTP {exc.code}: {body[:1000]}", success=False)
    except urllib.error.URLError as exc:
        return tool_error(f"Orchestrator API request failed: {exc.reason}", success=False)
    except TimeoutError:
        return tool_error("Orchestrator API request timed out", success=False)

    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return tool_error(f"Orchestrator API returned an unexpected response: {exc}", success=False)

    return json.dumps({"success": True, "content": content})


registry.register(
    name="call_orchestrator",
    toolset="orchestrator",
    schema=CALL_ORCHESTRATOR_SCHEMA,
    handler=lambda args, **kw: call_orchestrator(
        task=args.get("task", ""),
        max_tokens=args.get("max_tokens"),
    ),
    check_fn=check_call_orchestrator_requirements,
    emoji="🔁",
    max_result_size_chars=50_000,
)
