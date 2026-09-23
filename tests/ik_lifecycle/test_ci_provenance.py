import pytest
import os
from pathlib import Path
import subprocess
import yaml

from ik_lifecycle.models import LifecycleBlockedError


def test_reviewed_ci_patch_is_exact_and_cannot_exempt_runtime_code():
    from ik_lifecycle.source_provenance import verify_upstream_bindings

    path = ".github/workflows/contributor-check.yml"
    original = {path: ("100644", "a" * 40), "agent/display.py": ("100644", "c" * 40)}
    changed = {**original, path: ("100644", "b" * 40)}
    patches = {
        path: {
            "upstream_blob": "a" * 40,
            "implementation_blob": "b" * 40,
            "authority": "approved CI repair",
        }
    }
    verify_upstream_bindings(changed, original, patches)
    for candidate, rules in [
        (changed, {}),
        ({**changed, path: ("100644", "d" * 40)}, patches),
        ({**changed, path: ("100755", "b" * 40)}, patches),
        ({**changed, "agent/display.py": ("100644", "d" * 40)}, patches),
        (changed, {**patches, "agent/display.py": patches[path]}),
        (changed, {path: {**patches[path], "authority": ""}}),
    ]:
        with pytest.raises(LifecycleBlockedError):
            verify_upstream_bindings(candidate, original, rules)


def test_workflow_checks_actual_pr_commits_not_historical_main(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "history@example.invalid")
    (tmp_path / "file").write_text("baseline", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    (tmp_path / "file").write_text("historical", encoding="utf-8")
    git("commit", "-qam", "unrelated historic author")
    base = git("rev-parse", "HEAD")
    git("config", "user.email", "reviewed@example.invalid")
    mapping = tmp_path / "contributors/emails/reviewed@example.invalid"
    mapping.parent.mkdir(parents=True)
    mapping.write_text("fixture\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "actual PR")
    head = git("rev-parse", "HEAD")
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/contributor-check.yml"
    )
    steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"][
        "check-attribution"
    ]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "check-emails")
    env = {
        **os.environ,
        "BASE_SHA": base,
        "HEAD_SHA": head,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }
    result = subprocess.run(
        ["bash", "-eu", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    env["BASE_SHA"] = ""
    assert (
        subprocess.run(
            ["bash", "-eu", "-c", script], cwd=tmp_path, env=env, capture_output=True
        ).returncode
        != 0
    )
