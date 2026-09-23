"""Pure legacy record transforms used only against destination clones."""

from __future__ import annotations

from typing import Mapping, Any


def transform_task(record: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result.setdefault("owner", "ernie")
    result.setdefault("approval", {"state": "legacy-imported"})
    result.setdefault("provenance", {"source": "legacy-0.15"})
    return result
