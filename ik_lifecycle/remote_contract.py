"""Read-only validation for the writable-fork/read-only-upstream contract."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import RemoteContractResult


_BITDEPTH_URL = "https://github.com/bitdepthmedia/hermes-agent.git"
_UPSTREAM_URL = "https://github.com/NousResearch/hermes-agent.git"
_UPSTREAM_PUSH_DISABLED = "DISABLED_DO_NOT_PUSH_TO_NOUSRESEARCH_HERMES_AGENT"
_UPSTREAM_MAIN_REFSPEC = "+refs/heads/main:refs/remotes/upstream/main"
_UPSTREAM_TAG_REFSPEC = "+refs/tags/*:refs/upstream/tags/*"


def _config_values(repo: Path, key: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(repo), "config", "--get-all", key],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 1:
        return ()
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git config failed for {key}")
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _blocked(code: str, detail: str) -> RemoteContractResult:
    return RemoteContractResult(status="BLOCKED", code=code, details=(detail,))


def validate_remote_contract(repo: Path) -> RemoteContractResult:
    """Validate the configured remotes without changing refs or Git config."""

    repo = Path(repo)
    if not (repo / ".git").exists():
        return _blocked("not_git_repository", f"No .git directory at {repo}")

    bitdepth_urls = _config_values(repo, "remote.bitdepth.url")
    if bitdepth_urls != (_BITDEPTH_URL,):
        return _blocked("writable_remote_ambiguous", "bitdepth must have exactly one canonical fetch URL")

    bitdepth_push_urls = _config_values(repo, "remote.bitdepth.pushurl")
    if bitdepth_push_urls and bitdepth_push_urls != (_BITDEPTH_URL,):
        return _blocked("writable_remote_ambiguous", "bitdepth push URL must match its canonical fetch URL")

    if _config_values(repo, "remote.pushDefault") != ("bitdepth",):
        return _blocked("writable_remote_not_default", "remote.pushDefault must be bitdepth")

    upstream_urls = _config_values(repo, "remote.upstream.url")
    if upstream_urls != (_UPSTREAM_URL,):
        return _blocked("upstream_fetch_ambiguous", "upstream must have exactly one canonical NousResearch fetch URL")

    upstream_push_urls = _config_values(repo, "remote.upstream.pushurl")
    if upstream_push_urls != (_UPSTREAM_PUSH_DISABLED,):
        return _blocked("upstream_push_enabled", "upstream push URL must be the disabled sentinel")

    fetch_refspecs = _config_values(repo, "remote.upstream.fetch")
    if _UPSTREAM_MAIN_REFSPEC not in fetch_refspecs:
        return _blocked("upstream_main_not_tracked", "upstream/main fetch tracking is required")
    if _UPSTREAM_TAG_REFSPEC not in fetch_refspecs:
        return _blocked("upstream_tags_not_namespaced", "upstream tags must be namespaced")
    if any(refspec.endswith(":refs/tags/*") for refspec in fetch_refspecs):
        return _blocked("upstream_tags_not_namespaced", "upstream tags cannot populate the local tag namespace")
    expected = {_UPSTREAM_MAIN_REFSPEC, _UPSTREAM_TAG_REFSPEC}
    if set(fetch_refspecs) != expected or len(fetch_refspecs) != len(expected):
        return _blocked("upstream_refspec_ambiguous", "unexpected upstream fetch refspec")

    return RemoteContractResult(
        status="CLEAR",
        code="remote_contract_valid",
        details=("bitdepth writable/default", "upstream fetch-only and namespaced"),
    )
