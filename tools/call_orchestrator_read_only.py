"""Authenticated loopback client for the technically enforced no-tools endpoint."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

from tools.registry import tool_error


DEFAULT_ORCHESTRATOR_BASE_URL = "http://127.0.0.1:8643/v1"
READ_ONLY_ENDPOINT = "/orchestrator/read-only"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_PORT = 8643
ALLOWED_PURPOSES = {"status", "review"}
MAX_INPUT_CHARS = 8000
MAX_RECEIPT_BYTES = 32_000
MIN_MAX_TOKENS = 64
MAX_MAX_TOKENS = 2000
DEFAULT_MAX_TOKENS = 1000
DEFAULT_TIMEOUT_SECONDS = 180


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _orchestrator_base_url() -> str:
    return (
        os.getenv("HERMES_ORCHESTRATOR_BASE_URL")
        or os.getenv("ORCHESTRATOR_API_BASE_URL")
        or DEFAULT_ORCHESTRATOR_BASE_URL
    )


def _orchestrator_api_key() -> str:
    return (
        os.getenv("HERMES_ORCHESTRATOR_API_KEY")
        or os.getenv("ORCHESTRATOR_API_KEY")
        or ""
    )


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http":
        raise ValueError("Read-only orchestrator URL must use plain local http.")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("Read-only orchestrator URL must use a loopback host.")
    if parsed.port != ALLOWED_PORT:
        raise ValueError(f"Read-only orchestrator URL must use port {ALLOWED_PORT}.")
    if parsed.params or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Read-only orchestrator URL must not contain credentials or URL parameters.")
    normalized_path = parsed.path.rstrip("/")
    if normalized_path not in ("", "/v1"):
        raise ValueError("Read-only orchestrator URL path must be empty or /v1.")
    return base_url.rstrip("/") if normalized_path == "/v1" else base_url.rstrip("/") + "/v1"


def _bounded_max_tokens(value: object) -> int:
    if value is None:
        return DEFAULT_MAX_TOKENS
    if isinstance(value, bool):
        raise ValueError("max_tokens must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_tokens must be an integer") from exc
    if not MIN_MAX_TOKENS <= parsed <= MAX_MAX_TOKENS:
        raise ValueError(
            f"max_tokens must be between {MIN_MAX_TOKENS} and {MAX_MAX_TOKENS}"
        )
    return parsed


def _validate_source_receipt(source_receipt: object) -> dict:
    if not isinstance(source_receipt, dict) or set(source_receipt) != {
        "content",
        "sha256",
    }:
        raise ValueError("review requires content and sha256 in source receipt")
    content = source_receipt["content"]
    if not isinstance(content, dict):
        raise ValueError("review source receipt content must be a structured object")
    encoded = _canonical_bytes(content)
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise ValueError("review source receipt content is too large")
    expected = _sha256_bytes(encoded)
    supplied = source_receipt["sha256"]
    if not isinstance(supplied, str) or supplied != expected:
        raise ValueError("review source receipt hash does not match content")
    return {"content": content, "sha256": supplied}


def _validate_attestation(
    data: object,
    *,
    payload: dict,
    input_text: str,
    purpose: str,
    source_receipt: dict | None,
) -> dict:
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ValueError("read-only response did not report success")
    content = data.get("content")
    receipts = data.get("source_receipts")
    attestation = data.get("attestation")
    if not isinstance(content, str) or not isinstance(receipts, dict):
        raise ValueError("read-only response is missing bounded content or receipts")
    if not isinstance(attestation, dict):
        raise ValueError("read-only response is missing attestation")

    expected_fixed = {
        "mode": "no_tools",
        "enabled_toolsets": [],
        "tool_names": [],
        "tool_calls": 0,
    }
    if any(attestation.get(key) != value for key, value in expected_fixed.items()):
        raise ValueError("read-only attestation does not prove zero tools")

    expected_hashes = {
        "request_sha256": _sha256_bytes(_canonical_bytes(payload)),
        "input_sha256": _sha256_text(input_text),
        "output_sha256": _sha256_text(content),
        "source_receipts_sha256": _sha256_bytes(_canonical_bytes(receipts)),
    }
    if any(attestation.get(key) != value for key, value in expected_hashes.items()):
        raise ValueError("read-only attestation digest validation failed")

    if receipts.get("purpose") != purpose or not isinstance(receipts.get("items"), list):
        raise ValueError("read-only source receipts do not match the request purpose")
    if purpose == "review":
        items = receipts["items"]
        if len(items) != 1 or items[0].get("kind") != "caller_source_receipt":
            raise ValueError("read-only review receipt is missing")
        echoed = {key: items[0].get(key) for key in ("content", "sha256")}
        if echoed != source_receipt:
            raise ValueError("read-only review receipt does not match caller evidence")
    return data


def check_call_orchestrator_read_only_requirements() -> bool:
    if not _orchestrator_api_key():
        return False
    try:
        _validate_base_url(_orchestrator_base_url())
    except ValueError:
        return False
    return True


def call_orchestrator_read_only(
    input_text: str,
    *,
    purpose: str,
    source_receipt: dict | None = None,
    max_tokens: object = None,
) -> str:
    """Call the authenticated loopback endpoint and accept only strict attestation."""
    try:
        if not isinstance(input_text, str) or not input_text.strip():
            raise ValueError("input_text is required")
        input_text = input_text.strip()
        if len(input_text) > MAX_INPUT_CHARS:
            raise ValueError(f"input_text exceeds {MAX_INPUT_CHARS} characters")
        if purpose not in ALLOWED_PURPOSES:
            raise ValueError("purpose must be status or review")

        validated_receipt = None
        if purpose == "review":
            validated_receipt = _validate_source_receipt(source_receipt)
        elif source_receipt is not None:
            raise ValueError("status purpose does not accept a caller source receipt")

        base_url = _validate_base_url(_orchestrator_base_url())
        api_key = _orchestrator_api_key()
        if not api_key:
            raise ValueError(
                "HERMES_ORCHESTRATOR_API_KEY or ORCHESTRATOR_API_KEY is not configured"
            )
        payload = {
            "purpose": purpose,
            "input": input_text,
            "max_tokens": _bounded_max_tokens(max_tokens),
        }
        if validated_receipt is not None:
            payload["source_receipt"] = validated_receipt
        request = urllib.request.Request(
            base_url + READ_ONLY_ENDPOINT,
            data=_canonical_bytes(payload),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(
            request,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        validated = _validate_attestation(
            parsed,
            payload=payload,
            input_text=input_text,
            purpose=purpose,
            source_receipt=validated_receipt,
        )
        return json.dumps(validated, ensure_ascii=False)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return tool_error(
            f"Read-only orchestrator API HTTP {exc.code}: {body[:1000]}",
            success=False,
        )
    except urllib.error.URLError as exc:
        return tool_error(
            f"Read-only orchestrator API request failed: {exc.reason}",
            success=False,
        )
    except TimeoutError:
        return tool_error("Read-only orchestrator API request timed out", success=False)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return tool_error(f"Read-only orchestrator attestation rejected: {exc}", success=False)
