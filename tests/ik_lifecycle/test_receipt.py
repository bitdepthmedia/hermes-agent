from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ik_lifecycle.models import LifecycleBlockedError, LifecycleReceipt
from ik_lifecycle.receipt import receipt_document, write_receipt


def _receipt(data: dict[str, object]) -> LifecycleReceipt:
    return LifecycleReceipt(
        kind="release_selection",
        status="CLEAR",
        observed_at=datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc),
        data=data,
    )


def test_receipt_digest_covers_canonical_body() -> None:
    first = receipt_document(_receipt({"target": "v2026.8.18", "latest": "v2026.8.19"}))
    second = receipt_document(_receipt({"latest": "v2026.8.19", "target": "v2026.8.18"}))

    assert first == second
    body = json.dumps(first["receipt"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert first["sha256"] == hashlib.sha256(body).hexdigest()


def test_write_receipt_is_compact_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "receipts" / "selection.json"

    write_receipt(path, _receipt({"target": "v2026.8.18"}))

    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert ": " not in raw
    with pytest.raises(LifecycleBlockedError) as error:
        write_receipt(path, _receipt({"target": "v2026.8.16.2"}))
    assert error.value.code == "receipt_exists"


@pytest.mark.parametrize("field", ["api_key", "password", "access_token", "client_secret"])
def test_sensitive_receipt_fields_are_blocked(field: str) -> None:
    with pytest.raises(LifecycleBlockedError) as error:
        receipt_document(_receipt({field: "must-not-be-written"}))

    assert error.value.code == "receipt_contains_sensitive_field"
