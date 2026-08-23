from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys


SCRIPT = Path(__file__).parents[2] / "scripts/ik-bert-cell-service"


def test_bert_cell_launcher_uses_only_immutable_pair_and_frozen_runtime() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert subprocess.run(["/bin/sh", "-n", str(SCRIPT)], check=False).returncode == 0
    assert '"$cell_root/current-release"' in source
    assert '"$cell_root/current-profile"' in source
    assert "surfaces/python-runtime/bin/python" in source
    assert "SEALED_DEPLOYABLE_RUNTIME" in source
    assert "PYTHONPATH" in source
    assert "pip " not in source and "npm " not in source and "uv " not in source


def test_bert_cell_launcher_preserves_separate_gateway_and_dashboard_roles() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "hermes_cli.main gateway run --replace" in source
    assert "hermes_cli.main dashboard --host 127.0.0.1 --port" in source
    assert 'case "$IK_CELL_SERVICE_ROLE" in' in source
    assert "model)" not in source


def test_bert_cell_launcher_fails_closed_on_manifest_or_profile_drift(tmp_path: Path) -> None:
    cell = tmp_path / "cell"
    release = tmp_path / "releases/release-1"
    profile = tmp_path / "profiles/profile-1"
    python = release / "surfaces/python-runtime/bin/python"
    python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python)
    (release / "source").mkdir()
    profile.mkdir(parents=True)
    cell.mkdir()
    (cell / "current-release").symlink_to(release, target_is_directory=True)
    (cell / "current-profile").symlink_to(profile, target_is_directory=True)
    manifest = release / "runtime-manifest.json"
    manifest.write_text(
        json.dumps({"status": "SEALED_DEPLOYABLE_RUNTIME", "release_id": "release-1"}) + "\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "IK_CELL_ROOT": str(cell),
        "IK_CELL_SERVICE_ROLE": "invalid-fixture-role",
        "HERMES_HOME": str(cell / "current-profile"),
        "IK_PROFILE_ROOT": str(profile),
        "IK_RUNTIME_MANIFEST_SHA256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest(),
        "IK_RUNTIME_VERIFIER_SHA256": __import__("hashlib").sha256(
            (SCRIPT.parent / "ik-bert-runtime-verify").read_bytes()
        ).hexdigest(),
    }

    # The intentionally incomplete fixture never reaches role dispatch.
    assert subprocess.run([str(SCRIPT)], env=environment, check=False).returncode == 81
    manifest.write_text(json.dumps({"status": "SEALED_CODE_ONLY", "release_id": "release-1"}) + "\n")
    environment["IK_RUNTIME_MANIFEST_SHA256"] = __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    assert subprocess.run([str(SCRIPT)], env=environment, check=False).returncode == 81
    manifest.write_text(
        json.dumps({"status": "SEALED_DEPLOYABLE_RUNTIME", "release_id": "release-1"}) + "\n"
    )
    environment["IK_RUNTIME_MANIFEST_SHA256"] = __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    environment["IK_PROFILE_ROOT"] = str(tmp_path)
    assert subprocess.run([str(SCRIPT)], env=environment, check=False).returncode == 79


def test_bert_cell_launcher_rejects_runtime_manifest_digest_drift(tmp_path: Path) -> None:
    cell = tmp_path / "cell"; release = tmp_path / "releases/release-1"; profile = tmp_path / "profile"
    python = release / "surfaces/python-runtime/bin/python"; python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python); (release / "source").mkdir(); profile.mkdir(); cell.mkdir()
    (cell / "current-release").symlink_to(release, target_is_directory=True)
    (cell / "current-profile").symlink_to(profile, target_is_directory=True)
    (release / "runtime-manifest.json").write_text(
        json.dumps({"status": "SEALED_DEPLOYABLE_RUNTIME", "release_id": "release-1"}) + "\n"
    )
    result = subprocess.run(
        [str(SCRIPT)],
        env={**os.environ, "IK_CELL_ROOT": str(cell), "IK_CELL_SERVICE_ROLE": "invalid-fixture-role",
             "HERMES_HOME": str(cell / "current-profile"), "IK_PROFILE_ROOT": str(profile),
             "IK_RUNTIME_MANIFEST_SHA256": "0" * 64,
             "IK_RUNTIME_VERIFIER_SHA256": __import__("hashlib").sha256(
                 (SCRIPT.parent / "ik-bert-runtime-verify").read_bytes()
             ).hexdigest()},
        check=False,
    )
    assert result.returncode == 80
