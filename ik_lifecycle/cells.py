from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class CellSpec:
    cell_id: str
    trust_zone: str
    state_root_key: str
    profile_root_key: str
    release_root_key: str
    nate_os_write_allowed: bool
    transport_role: str
    external_approval_gates: tuple[str, ...]


def load_cell_spec(path: Path) -> CellSpec:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"cell_id", "trust_zone", "state_root_key", "profile_root_key", "release_root_key", "nate_os_write_allowed", "transport_role", "external_approval_gates"}
    if not required.issubset(document):
        raise ValueError("cell manifest incomplete")
    values = {key: document[key] for key in required}
    if not isinstance(values["external_approval_gates"], list) or not all(isinstance(item, str) for item in values["external_approval_gates"]):
        raise ValueError("cell external approval gates are invalid")
    values["external_approval_gates"] = tuple(values["external_approval_gates"])
    return CellSpec(**values)
