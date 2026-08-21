"""Authoritative Hermes release discovery with exact one-behind selection."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence
from urllib.request import Request, urlopen

from .models import LifecycleBlockedError, ReleaseSelection, StableRelease


OFFICIAL_REPOSITORY_URL = "https://github.com/NousResearch/hermes-agent.git"
OFFICIAL_RELEASES_API = "https://api.github.com/repos/NousResearch/hermes-agent/releases?per_page=100"
_RELEASE_TAG = re.compile(r"^v\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


class ReleaseSource(Protocol):
    def list_releases(self) -> Sequence[Mapping[str, Any]]: ...


class GitRefs(Protocol):
    def resolve_tag(self, tag: str) -> str | None: ...


class GitHubReleaseSource:
    """Read official GitHub release metadata without package dependencies."""

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def list_releases(self) -> Sequence[Mapping[str, Any]]:
        request = Request(
            OFFICIAL_RELEASES_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ik-hermes-lifecycle/1"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - fixed official URL
            payload = json.load(response)
        if not isinstance(payload, list):
            raise LifecycleBlockedError("release_source_invalid", "Official release response must be a list")
        return payload


class LsRemoteGitRefs:
    """Resolve official tag commits through a read-only ls-remote call."""

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def resolve_tag(self, tag: str) -> str | None:
        if not _RELEASE_TAG.fullmatch(tag):
            raise LifecycleBlockedError("invalid_release_tag", f"Invalid release tag: {tag}")
        result = subprocess.run(
            [
                "git",
                "ls-remote",
                "--tags",
                OFFICIAL_REPOSITORY_URL,
                f"refs/tags/{tag}",
                f"refs/tags/{tag}^{{}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            raise LifecycleBlockedError(
                "tag_ref_query_failed",
                result.stderr.strip() or f"Unable to resolve {tag}",
            )
        refs: dict[str, str] = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 2:
                refs[fields[1]] = fields[0]
        return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


def _blocked(code: str, message: str) -> LifecycleBlockedError:
    return LifecycleBlockedError(code, message)


def _parse_published_at(value: object, tag: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _blocked("invalid_release_metadata", f"Release {tag} has no publication time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _blocked("invalid_release_metadata", f"Release {tag} has an invalid publication time") from exc
    if parsed.tzinfo is None:
        raise _blocked("invalid_release_metadata", f"Release {tag} publication time lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _resolve_stable_tag(git: GitRefs, tag: str) -> str:
    try:
        first = git.resolve_tag(tag)
        second = git.resolve_tag(tag)
    except LifecycleBlockedError:
        raise
    except Exception as exc:
        raise _blocked("tag_ref_query_failed", f"Unable to resolve tag {tag}: {exc}") from exc
    if first is None or second is None:
        raise _blocked("tag_ref_missing", f"Release tag {tag} is missing")
    if first != second:
        raise _blocked("moving_tag", f"Release tag {tag} changed during discovery")
    if not _COMMIT_SHA.fullmatch(first):
        raise _blocked("invalid_tag_ref", f"Release tag {tag} did not resolve to a commit SHA")
    return first.lower()


def discover_one_behind(
    source: ReleaseSource,
    git: GitRefs,
    *,
    discovered_at: datetime | None = None,
) -> ReleaseSelection:
    """Select the immediately previous published stable Hermes release."""

    try:
        raw_releases = source.list_releases()
    except LifecycleBlockedError:
        raise
    except Exception as exc:
        raise _blocked("release_source_failed", f"Unable to read official releases: {exc}") from exc

    if not isinstance(raw_releases, Sequence) or isinstance(raw_releases, (str, bytes)):
        raise _blocked("release_source_invalid", "Release source must return a sequence")

    candidates: list[tuple[str, datetime, str]] = []
    seen_tags: set[str] = set()
    for item in raw_releases:
        if not isinstance(item, Mapping):
            raise _blocked("invalid_release_metadata", "Release entry must be an object")
        draft = item.get("draft")
        prerelease = item.get("prerelease")
        if not isinstance(draft, bool) or not isinstance(prerelease, bool):
            raise _blocked("invalid_release_metadata", "Release draft/prerelease fields must be booleans")
        if draft or prerelease:
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not _RELEASE_TAG.fullmatch(tag):
            raise _blocked("invalid_release_tag", f"Invalid stable release tag: {tag!r}")
        if tag in seen_tags:
            raise _blocked("duplicate_release_tag", f"Duplicate stable release tag: {tag}")
        seen_tags.add(tag)
        published_at = _parse_published_at(item.get("published_at"), tag)
        html_url = item.get("html_url")
        expected_url = f"https://github.com/NousResearch/hermes-agent/releases/tag/{tag}"
        if html_url != expected_url:
            raise _blocked("invalid_release_metadata", f"Release {tag} has a non-authoritative URL")
        candidates.append((tag, published_at, html_url))

    if len(candidates) < 2:
        raise _blocked("insufficient_stable_releases", "At least two stable releases are required")
    publication_times = [published_at for _, published_at, _ in candidates]
    if len(set(publication_times)) != len(publication_times):
        raise _blocked("ambiguous_release_order", "Stable releases share a publication time")
    candidates.sort(key=lambda release: release[1], reverse=True)

    latest_tag, latest_published, latest_url = candidates[0]
    target_tag, target_published, target_url = candidates[1]
    latest_sha = _resolve_stable_tag(git, latest_tag)
    target_sha = _resolve_stable_tag(git, target_tag)

    observed = discovered_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise _blocked("invalid_discovery_time", "Discovery time must include a timezone")
    observed = observed.astimezone(timezone.utc)
    return ReleaseSelection(
        latest=StableRelease(latest_tag, latest_sha, latest_published, latest_url),
        target=StableRelease(target_tag, target_sha, target_published, target_url),
        discovered_at=observed,
    )
