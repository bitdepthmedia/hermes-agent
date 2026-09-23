"""Canonical, immutable, non-secret lifecycle receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import LifecycleBlockedError, LifecycleReceipt


_SENSITIVE_FIELD = re.compile(
    r"(?:api[_-]?key|password|access[_-]?token|refresh[_-]?token|client[_-]?secret|credential|cookie|authorization)",
    re.IGNORECASE,
)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise LifecycleBlockedError("invalid_receipt_time", "Receipt time must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _screen_fields(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if _SENSITIVE_FIELD.search(key_text):
                location = ".".join((*path, key_text))
                raise LifecycleBlockedError(
                    "receipt_contains_sensitive_field",
                    f"Sensitive receipt field is not allowed: {location}",
                )
            _screen_fields(child, (*path, key_text))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _screen_fields(child, (*path, str(index)))


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def receipt_document(receipt: LifecycleReceipt) -> dict[str, object]:
    """Return the canonical receipt envelope and body digest."""

    _screen_fields(receipt.data)
    body = {
        "data": dict(receipt.data),
        "kind": receipt.kind,
        "observed_at": _utc_text(receipt.observed_at),
        "schema_version": receipt.schema_version,
        "status": receipt.status,
    }
    digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    return {"receipt": body, "sha256": digest}


def write_receipt(path: Path, receipt: LifecycleReceipt) -> None:
    """Create a receipt exactly once; existing evidence is never overwritten."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(receipt_document(receipt)) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise LifecycleBlockedError("receipt_exists", f"Receipt already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
