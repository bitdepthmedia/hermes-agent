from __future__ import annotations

from pathlib import Path
import subprocess


SCRIPT = Path(__file__).parents[2] / "scripts/ik-cell-service"


def test_cell_service_uses_only_paired_cell_pointers_and_frozen_runtime() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert subprocess.run(["/bin/sh", "-n", str(SCRIPT)], check=False).returncode == 0
    assert '"$cell_root/current-release"' in source
    assert '"$cell_root/current-profile"' in source
    assert 'surfaces/python-runtime/bin/python' in source
    assert "hermes_cli.main gateway run --replace" in source
    assert "~/.hermes" not in source
    assert "pip " not in source and "npm " not in source and "uv " not in source
