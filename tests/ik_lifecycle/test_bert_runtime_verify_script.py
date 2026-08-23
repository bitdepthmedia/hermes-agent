from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess

from ik_lifecycle.composed_source import tree_digest


SCRIPT = Path(__file__).parents[2] / "scripts/ik-bert-runtime-verify"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    for relative in (
        "source",
        "surfaces/python-runtime/bin",
        "surfaces/built-assets",
        "surfaces/service-definitions",
        "surfaces/model-runtime",
        "config/locks",
    ):
        (staging / relative).mkdir(parents=True, exist_ok=True)
    (staging / "source/run_agent.py").write_text("pass\n")
    (staging / "surfaces/python-runtime/bin/python").write_text("python\n")
    (staging / "surfaces/built-assets/index.html").write_text("asset\n")
    (staging / "surfaces/service-definitions/unit.service").write_text("unit\n")
    (staging / "surfaces/model-runtime/ollama").write_text("model\n")
    (staging / "config/router.json").write_text("{}\n")
    (staging / "config/model.json").write_text("{}\n")
    (staging / "config/locks/python-uv.lock").write_text("lock\n")
    identity = {
        "source_tree_sha256": tree_digest(staging / "source"),
        "surfaces": {
            name: {"tree_sha256": tree_digest(staging / "surfaces" / name)}
            for name in ("python-runtime", "built-assets", "service-definitions", "model-runtime")
        },
        "router_config_sha256": _sha(staging / "config/router.json"),
        "model_manifest_sha256": _sha(staging / "config/model.json"),
        "lockfiles": {"python-uv": _sha(staging / "config/locks/python-uv.lock")},
    }
    release_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    release = tmp_path / release_id
    staging.rename(release)
    (release / "runtime-manifest.json").write_text(json.dumps({
        "status": "SEALED_DEPLOYABLE_RUNTIME", "release_id": release_id, "identity": identity,
    }, sort_keys=True, separators=(",", ":")) + "\n")
    for path in sorted(release.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    release.chmod(0o500)
    return release


def test_runtime_verifier_rejects_any_source_drift_under_bound_manifest(tmp_path: Path) -> None:
    release = _release(tmp_path)
    script_sha = _sha(SCRIPT)
    argv = ["python3", str(SCRIPT), "--release", str(release), "--manifest-sha", _sha(release / "runtime-manifest.json"), "--self-sha", script_sha]
    assert subprocess.run(argv, check=False).returncode == 0

    source = release / "source/run_agent.py"
    source.chmod(0o600); source.write_text("tampered\n"); source.chmod(0o400)
    assert subprocess.run(argv, check=False).returncode != 0


def test_runtime_verifier_rejects_self_or_manifest_binding_drift(tmp_path: Path) -> None:
    release = _release(tmp_path)
    good_manifest = _sha(release / "runtime-manifest.json")
    assert subprocess.run(["python3", str(SCRIPT), "--release", str(release), "--manifest-sha", "0" * 64, "--self-sha", _sha(SCRIPT)], check=False).returncode != 0
    assert subprocess.run(["python3", str(SCRIPT), "--release", str(release), "--manifest-sha", good_manifest, "--self-sha", "0" * 64], check=False).returncode != 0
