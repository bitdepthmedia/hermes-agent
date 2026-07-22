import json

import pytest

from tools.live_runtime_guard import (
    protected_terminal_error,
    protected_write_error,
)


@pytest.fixture
def protected_checkout(tmp_path, monkeypatch):
    root = tmp_path / "hermes-agent"
    root.mkdir()
    monkeypatch.setenv("HERMES_PROTECTED_CHECKOUT", str(root))
    return root


def test_protected_write_denies_live_checkout_file(protected_checkout):
    target = protected_checkout / "cron" / "scheduler.py"

    error = protected_write_error(target)

    assert error is not None
    assert "protected live checkout" in error


def test_protected_write_allows_other_paths(protected_checkout, tmp_path):
    target = tmp_path / "scratch" / "note.txt"

    assert protected_write_error(target) is None


def test_terminal_allows_read_only_git_status_in_protected_checkout(protected_checkout):
    error = protected_terminal_error("git status --short", str(protected_checkout))

    assert error is None


def test_terminal_blocks_git_push_in_protected_checkout(protected_checkout):
    error = protected_terminal_error("git push bitdepth main", str(protected_checkout))

    assert error is not None
    assert "read-only from gateway sessions" in error


def test_terminal_blocks_cd_then_patch_attempt(protected_checkout):
    command = f"cd {protected_checkout} && python - <<'PY'\nopen('x.py', 'w').write('x')\nPY"

    error = protected_terminal_error(command, "/home/bert/.hermes")

    assert error is not None


def test_terminal_allows_unrelated_workdir(protected_checkout, tmp_path):
    workdir = tmp_path / "scratch"
    workdir.mkdir()

    assert protected_terminal_error("python -m pytest", str(workdir)) is None


def test_terminal_tool_blocks_protected_checkout_command(monkeypatch, protected_checkout):
    import tools.terminal_tool as terminal_tool

    monkeypatch.setattr(
        terminal_tool,
        "_get_env_config",
        lambda: {
            "env_type": "local",
            "cwd": str(protected_checkout),
            "timeout": 180,
            "docker_image": "",
            "singularity_image": "",
            "modal_image": "",
            "daytona_image": "",
        },
    )

    result = json.loads(terminal_tool.terminal_tool("git push bitdepth main"))

    assert result["status"] == "blocked"
    assert "protected live checkout" in result["error"]
