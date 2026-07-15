"""Regression tests for Last30Days install safety scanning."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "ik_profiles"
    / "orchestrator-access-node"
    / "scripts"
    / "last30days_sync.py"
)
SPEC = spec_from_file_location("last30days_sync", SCRIPT_PATH)
assert SPEC and SPEC.loader
last30days_sync = module_from_spec(SPEC)
sys.modules[SPEC.name] = last30days_sync
SPEC.loader.exec_module(last30days_sync)


def test_scan_tree_ignores_policy_and_session_references(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "Screen for axios@1.14.1 before installing dependencies.\n",
        encoding="utf-8",
    )
    session = tmp_path / "sessions" / "screening.log"
    session.parent.mkdir()
    session.write_text("plain-crypto-js@4.2.1 was screened.\n", encoding="utf-8")

    last30days_sync.scan_tree(tmp_path)


def test_scan_tree_blocks_explicit_package_manager_install(tmp_path):
    script = tmp_path / "bootstrap.sh"
    script.write_text("npm install axios@1.14.1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="axios@1.14.1"):
        last30days_sync.scan_tree(tmp_path)
