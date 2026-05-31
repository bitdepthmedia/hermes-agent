"""Narrow local Bert bridge for Ernie.

This tool intentionally exposes only one capability: ask the locally running
Bert Hermes API server to perform a bounded task through Bert's own harness.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

from tools.registry import registry, tool_error


DEFAULT_BERT_BASE_URL = "http://127.0.0.1:8643/v1"
DEFAULT_BERT_MODEL = "hermes-agent"
MAX_TASK_CHARS = 8000
MIN_MAX_TOKENS = 64
MAX_MAX_TOKENS = 2000
DEFAULT_MAX_TOKENS = 1000
DEFAULT_TIMEOUT_SECONDS = 180
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_PORT = 8643


CALL_BERT_SCHEMA = {
    "name": "call_bert",
    "description": (
        "Ask Bert to perform a task through Bert's local Hermes API server. "
        "Use this when Ernie needs Bert's Codex-backed execution capability."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The complete, specific task for Bert to perform.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Maximum response tokens to request from Bert. Default 1000, maximum 2000.",
            },
        },
        "required": ["task"],
    },
}


def _bert_base_url() -> str:
    return os.getenv("HERMES_BERT_BASE_URL") or os.getenv("BERT_API_BASE_URL") or DEFAULT_BERT_BASE_URL


def _bert_api_key() -> str:
    return os.getenv("HERMES_BERT_API_KEY") or os.getenv("BERT_API_KEY") or ""


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http":
        raise ValueError("Bert base URL must use plain local http.")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("Bert base URL must point to localhost or 127.0.0.1.")
    if parsed.port != ALLOWED_PORT:
        raise ValueError(f"Bert base URL must use port {ALLOWED_PORT}.")
    normalized_path = parsed.path.rstrip("/")
    if normalized_path not in ("", "/v1"):
        raise ValueError("Bert base URL path must be empty or /v1.")
    return base_url.rstrip("/") if normalized_path == "/v1" else base_url.rstrip("/") + "/v1"


def _bounded_max_tokens(value) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_MAX_TOKENS
    except (TypeError, ValueError):
        parsed = DEFAULT_MAX_TOKENS
    return max(MIN_MAX_TOKENS, min(MAX_MAX_TOKENS, parsed))


def check_call_bert_requirements() -> bool:
    if not _bert_api_key():
        return False
    try:
        _validate_base_url(_bert_base_url())
    except ValueError:
        return False
    return True


def call_bert(task: str, max_tokens=None) -> str:
    if not isinstance(task, str) or not task.strip():
        return tool_error("task is required", success=False)

    task = task.strip()
    if len(task) > MAX_TASK_CHARS:
        return tool_error(f"task exceeds {MAX_TASK_CHARS} characters", success=False)

    try:
        base_url = _validate_base_url(_bert_base_url())
    except ValueError as exc:
        return tool_error(str(exc), success=False)

    api_key = _bert_api_key()
    if not api_key:
        return tool_error("HERMES_BERT_API_KEY or BERT_API_KEY is not configured", success=False)

    payload = {
        "model": os.getenv("HERMES_BERT_MODEL", DEFAULT_BERT_MODEL),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Bert, called by Ernie through the call_bert tool. "
                    "Complete only the requested task using your configured Hermes tools. "
                    "Return the result concisely. Do not tell the user to call Bert directly."
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
        return tool_error(f"Bert API HTTP {exc.code}: {body[:1000]}", success=False)
    except urllib.error.URLError as exc:
        return tool_error(f"Bert API request failed: {exc.reason}", success=False)
    except TimeoutError:
        return tool_error("Bert API request timed out", success=False)

    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return tool_error(f"Bert API returned an unexpected response: {exc}", success=False)

    return json.dumps({"success": True, "content": content})


registry.register(
    name="call_bert",
    toolset="bert",
    schema=CALL_BERT_SCHEMA,
    handler=lambda args, **kw: call_bert(
        task=args.get("task", ""),
        max_tokens=args.get("max_tokens"),
    ),
    check_fn=check_call_bert_requirements,
    emoji="🔁",
    max_result_size_chars=50_000,
)
