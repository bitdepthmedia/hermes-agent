"""Read-only command line for Hermes lifecycle discovery and receipts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timezone
from pathlib import Path
from typing import Sequence

from .models import LifecycleBlockedError, LifecycleReceipt
from .receipt import receipt_document, write_receipt
from .release_discovery import GitHubReleaseSource, LsRemoteGitRefs, discover_one_behind
from .remote_contract import validate_remote_contract


def _selection_data(selection) -> dict[str, object]:
    return {
        "discovered_at": selection.discovered_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "latest": {
            "commit_sha": selection.latest.commit_sha,
            "html_url": selection.latest.html_url,
            "published_at": selection.latest.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tag": selection.latest.tag,
        },
        "reason": "immediately_previous_published_stable_release",
        "target": {
            "commit_sha": selection.target.commit_sha,
            "html_url": selection.target.html_url,
            "published_at": selection.target.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tag": selection.target.tag,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the staged Hermes lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    remote = subparsers.add_parser("remote-audit", help="validate remotes without mutation")
    remote.add_argument("--repo", type=Path, default=Path.cwd())
    release = subparsers.add_parser("release-select", help="select exact one stable release behind")
    release.add_argument("--repo", type=Path, default=Path.cwd())
    release.add_argument("--receipt", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        remote_result = validate_remote_contract(args.repo)
        if remote_result.status != "CLEAR":
            raise LifecycleBlockedError(remote_result.code, "; ".join(remote_result.details))
        if args.command == "remote-audit":
            print(json.dumps(remote_result.__dict__, sort_keys=True, separators=(",", ":")))
            return 0

        selection = discover_one_behind(GitHubReleaseSource(), LsRemoteGitRefs())
        receipt = LifecycleReceipt(
            kind="release_selection",
            status="CLEAR",
            observed_at=selection.discovered_at,
            data=_selection_data(selection),
        )
        document = receipt_document(receipt)
        if args.receipt:
            write_receipt(args.receipt, receipt)
        print(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0
    except LifecycleBlockedError as exc:
        print(
            json.dumps({"code": exc.code, "message": str(exc), "status": "BLOCKED"}, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
