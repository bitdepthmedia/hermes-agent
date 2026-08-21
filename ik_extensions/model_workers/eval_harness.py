"""Frozen synthetic/public model-worker evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    critical: bool
    expected: Mapping[str, object]


@dataclass(frozen=True)
class EvalResult:
    status: str
    pass_rate: float
    failed_critical: tuple[str, ...]


def load_cases(root: Path) -> tuple[EvalCase, ...]:
    cases: list[EvalCase] = []
    for path in sorted(Path(root).glob("*-v1.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_id") != "ik.hermes.model-eval-suite.v1":
            raise ValueError(f"eval suite schema mismatch: {path.name}")
        for raw in document.get("cases", []):
            cases.append(EvalCase(raw["case_id"], raw["category"], bool(raw["critical"]), raw["expected"]))
    if not cases:
        raise ValueError("no frozen eval cases")
    return tuple(cases)


def score_fixture(cases: tuple[EvalCase, ...], outcomes: Mapping[str, str]) -> EvalResult:
    failed = tuple(case.case_id for case in cases if outcomes.get(case.case_id) != "PASS")
    critical = tuple(case.case_id for case in cases if case.critical and case.case_id in failed)
    rate = (len(cases) - len(failed)) / len(cases)
    return EvalResult("CLEAR" if not failed else "BLOCKED", rate, critical)
