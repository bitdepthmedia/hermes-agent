from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ik_lifecycle.remote_contract import validate_remote_contract


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def valid_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "bitdepth", "https://github.com/bitdepthmedia/hermes-agent.git")
    _git(repo, "remote", "add", "upstream", "https://github.com/NousResearch/hermes-agent.git")
    _git(repo, "config", "remote.upstream.pushurl", "DISABLED_DO_NOT_PUSH_TO_NOUSRESEARCH_HERMES_AGENT")
    _git(repo, "config", "--unset-all", "remote.upstream.fetch")
    _git(repo, "config", "--add", "remote.upstream.fetch", "+refs/heads/main:refs/remotes/upstream/main")
    _git(repo, "config", "--add", "remote.upstream.fetch", "+refs/tags/*:refs/upstream/tags/*")
    _git(repo, "config", "remote.pushDefault", "bitdepth")
    return repo


def test_valid_remote_contract_is_clear_and_read_only(valid_repo: Path) -> None:
    config_path = valid_repo / ".git" / "config"
    before = config_path.read_bytes()

    result = validate_remote_contract(valid_repo)

    assert result.status == "CLEAR"
    assert result.code == "remote_contract_valid"
    assert config_path.read_bytes() == before


def test_upstream_must_be_push_disabled(valid_repo: Path) -> None:
    _git(valid_repo, "config", "remote.upstream.pushurl", "git@github.com:NousResearch/hermes-agent.git")

    result = validate_remote_contract(valid_repo)

    assert result.status == "BLOCKED"
    assert result.code == "upstream_push_enabled"


def test_upstream_fetch_must_be_authoritative_repo(valid_repo: Path) -> None:
    _git(valid_repo, "config", "remote.upstream.url", "https://github.com/example/hermes-agent.git")

    result = validate_remote_contract(valid_repo)

    assert result.status == "BLOCKED"
    assert result.code == "upstream_fetch_ambiguous"


def test_bitdepth_must_be_default_writable_remote(valid_repo: Path) -> None:
    _git(valid_repo, "config", "remote.pushDefault", "upstream")

    result = validate_remote_contract(valid_repo)

    assert result.status == "BLOCKED"
    assert result.code == "writable_remote_not_default"


def test_upstream_main_tracking_is_required(valid_repo: Path) -> None:
    _git(valid_repo, "config", "--unset-all", "remote.upstream.fetch")
    _git(valid_repo, "config", "--add", "remote.upstream.fetch", "+refs/tags/*:refs/upstream/tags/*")

    result = validate_remote_contract(valid_repo)

    assert result.status == "BLOCKED"
    assert result.code == "upstream_main_not_tracked"


def test_upstream_tags_must_be_namespaced(valid_repo: Path) -> None:
    _git(valid_repo, "config", "--unset-all", "remote.upstream.fetch")
    _git(valid_repo, "config", "--add", "remote.upstream.fetch", "+refs/heads/main:refs/remotes/upstream/main")
    _git(valid_repo, "config", "--add", "remote.upstream.fetch", "+refs/tags/*:refs/tags/*")

    result = validate_remote_contract(valid_repo)

    assert result.status == "BLOCKED"
    assert result.code == "upstream_tags_not_namespaced"


def test_duplicate_upstream_url_is_ambiguous(valid_repo: Path) -> None:
    _git(valid_repo, "config", "--add", "remote.upstream.url", "https://github.com/mirror/hermes-agent.git")

    result = validate_remote_contract(valid_repo)

    assert result.status == "BLOCKED"
    assert result.code == "upstream_fetch_ambiguous"


def test_source_checkout_entrypoint_can_import_lifecycle_package() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [str(repo_root / "scripts" / "ik-hermes-lifecycle"), "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "release-select" in result.stdout
