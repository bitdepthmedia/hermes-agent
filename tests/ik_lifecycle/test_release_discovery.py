from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import pytest

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.release_discovery import discover_one_behind


def _release(
    tag: str,
    published_at: str,
    *,
    draft: bool = False,
    prerelease: bool = False,
) -> dict[str, object]:
    return {
        "tag_name": tag,
        "published_at": published_at,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/NousResearch/hermes-agent/releases/tag/{tag}",
    }


class FakeSource:
    def __init__(self, releases: list[dict[str, object]]) -> None:
        self._releases = releases

    def list_releases(self) -> list[dict[str, object]]:
        return list(self._releases)


class FakeRefs:
    def __init__(self, refs: dict[str, str | None | list[str | None]]) -> None:
        self._refs = refs
        self.calls: dict[str, int] = defaultdict(int)

    def resolve_tag(self, tag: str) -> str | None:
        value = self._refs.get(tag)
        call = self.calls[tag]
        self.calls[tag] += 1
        if isinstance(value, list):
            return value[min(call, len(value) - 1)]
        return value


SHA_19 = "1" * 40
SHA_18 = "2" * 40
SHA_16 = "3" * 40


def test_selects_exact_penultimate_published_stable_release() -> None:
    source = FakeSource(
        [
            _release("v2026.8.16.2", "2026-08-17T18:43:27Z"),
            _release("v2026.8.19-rc1", "2026-08-21T13:00:00Z", prerelease=True),
            _release("v2026.8.19", "2026-08-21T12:16:39Z"),
            _release("v2026.8.20-draft", "2026-08-22T00:00:00Z", draft=True),
            _release("v2026.8.18", "2026-08-18T07:26:46Z"),
        ]
    )
    refs = FakeRefs(
        {
            "v2026.8.19": SHA_19,
            "v2026.8.18": SHA_18,
            "v2026.8.16.2": SHA_16,
        }
    )
    observed = datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc)

    selection = discover_one_behind(source, refs, discovered_at=observed)

    assert selection.latest.tag == "v2026.8.19"
    assert selection.latest.commit_sha == SHA_19
    assert selection.target.tag == "v2026.8.18"
    assert selection.target.commit_sha == SHA_18
    assert selection.discovered_at == observed
    assert refs.calls["v2026.8.19"] == 2
    assert refs.calls["v2026.8.18"] == 2


def test_duplicate_publication_dates_are_blocked() -> None:
    source = FakeSource(
        [
            _release("v2026.8.19", "2026-08-21T12:16:39Z"),
            _release("v2026.8.18", "2026-08-21T12:16:39Z"),
        ]
    )

    with pytest.raises(LifecycleBlockedError) as error:
        discover_one_behind(source, FakeRefs({}))

    assert error.value.code == "ambiguous_release_order"


def test_missing_selected_tag_ref_is_blocked() -> None:
    source = FakeSource(
        [
            _release("v2026.8.19", "2026-08-21T12:16:39Z"),
            _release("v2026.8.18", "2026-08-18T07:26:46Z"),
        ]
    )

    with pytest.raises(LifecycleBlockedError) as error:
        discover_one_behind(source, FakeRefs({"v2026.8.19": SHA_19, "v2026.8.18": None}))

    assert error.value.code == "tag_ref_missing"


def test_moving_tag_is_blocked() -> None:
    source = FakeSource(
        [
            _release("v2026.8.19", "2026-08-21T12:16:39Z"),
            _release("v2026.8.18", "2026-08-18T07:26:46Z"),
        ]
    )
    refs = FakeRefs({"v2026.8.19": [SHA_19, "4" * 40], "v2026.8.18": SHA_18})

    with pytest.raises(LifecycleBlockedError) as error:
        discover_one_behind(source, refs)

    assert error.value.code == "moving_tag"


def test_only_one_stable_release_is_blocked() -> None:
    source = FakeSource([_release("v2026.8.19", "2026-08-21T12:16:39Z")])

    with pytest.raises(LifecycleBlockedError) as error:
        discover_one_behind(source, FakeRefs({"v2026.8.19": SHA_19}))

    assert error.value.code == "insufficient_stable_releases"


def test_invalid_stable_release_tag_is_not_silently_skipped() -> None:
    source = FakeSource(
        [
            _release("release-current", "2026-08-21T12:16:39Z"),
            _release("v2026.8.18", "2026-08-18T07:26:46Z"),
        ]
    )

    with pytest.raises(LifecycleBlockedError) as error:
        discover_one_behind(source, FakeRefs({}))

    assert error.value.code == "invalid_release_tag"
