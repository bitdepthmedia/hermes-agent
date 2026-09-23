"""Read-only audit and separately approved guarded release operations."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .models import LifecycleBlockedError, LifecycleReceipt
from .receipt import receipt_document, write_receipt
from .release_discovery import GitHubReleaseSource, LsRemoteGitRefs, discover_one_behind
from .remote_contract import validate_remote_contract
from .supply_chain import inspect_manifests
from .guarded_release import (
    read_approved_plan,
    seal_plan,
    verify_artifact,
    provenance_from_plan,
)
from .health import classify_release_drift
from .deployable_runtime import validate_deployable_runtime


def _selection_data(selection) -> dict[str, object]:
    return {
        "discovered_at": selection.discovered_at
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "latest": {
            "commit_sha": selection.latest.commit_sha,
            "html_url": selection.latest.html_url,
            "published_at": selection.latest.published_at
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "tag": selection.latest.tag,
        },
        "reason": "immediately_previous_published_stable_release",
        "target": {
            "commit_sha": selection.target.commit_sha,
            "html_url": selection.target.html_url,
            "published_at": selection.target.published_at
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "tag": selection.target.tag,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the staged Hermes lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    remote = subparsers.add_parser(
        "remote-audit", help="validate remotes without mutation"
    )
    remote.add_argument("--repo", type=Path, default=Path.cwd())
    release = subparsers.add_parser(
        "release-select", help="select exact one stable release behind"
    )
    release.add_argument("--repo", type=Path, default=Path.cwd())
    release.add_argument("--receipt", type=Path)
    supply_chain = subparsers.add_parser(
        "supply-chain", help="inspect candidate dependency surfaces without execution"
    )
    supply_chain.add_argument("--candidate", type=Path, required=True)
    supply_chain.add_argument("--base", type=Path)
    supply_chain.add_argument("--receipt", type=Path)
    for name in ("seal", "verify-artifact", "promote"):
        operation = subparsers.add_parser(
            name, help="execute an exact, separately approved release plan"
        )
        operation.add_argument("--plan", type=Path, required=True)
        operation.add_argument("--approve-sha256", required=True)
    digest = subparsers.add_parser(
        "plan-digest", help="inspect plan digest without execution"
    )
    digest.add_argument("--plan", type=Path, required=True)
    audit = subparsers.add_parser(
        "drift-audit", help="read-only integrity and release currency classification"
    )
    audit.add_argument("--release", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan-digest":
            import hashlib

            print(
                json.dumps({
                    "sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest()
                })
            )
            return 0
        if args.command in ("seal", "verify-artifact", "promote"):
            plan = read_approved_plan(args.plan, args.approve_sha256, args.command)
            if args.command == "seal":
                result = seal_plan(
                    plan, discover_one_behind(GitHubReleaseSource(), LsRemoteGitRefs())
                )
            elif args.command == "verify-artifact":
                result = verify_artifact(
                    Path(plan["release"]),
                    plan["manifest_sha256"],
                    provenance_from_plan(plan["provenance"]),
                )
            else:
                from .guarded_promotion import promote_plan

                result = promote_plan(plan)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] in {"CLEAR", "PROMOTED_CLEAR"} else 2
        if args.command == "drift-audit":
            selection = discover_one_behind(GitHubReleaseSource(), LsRemoteGitRefs())
            document = json.loads((args.release / "runtime-manifest.json").read_text())
            identity = document.get("identity", {})
            try:
                validate_deployable_runtime(args.release)
                integrity = "CLEAR"
            except LifecycleBlockedError as exc:
                integrity = (
                    "BLOCKED"
                    if exc.code
                    in {"runtime_artifact_tampered", "runtime_identity_invalid"}
                    else "UNVERIFIED"
                )
            status = classify_release_drift(
                integrity=integrity,
                deployed_target=identity.get("target_commit_sha", ""),
                required_target=selection.target.commit_sha,
                provenance=bool(identity.get("provenance")),
            )
            print(
                json.dumps({
                    "status": status,
                    "integrity": integrity,
                    "required_target": selection.target.commit_sha,
                    "deployed_target": identity.get("target_commit_sha"),
                    "mutation_performed": False,
                })
            )
            return 0 if status == "CLEAR" else 2
        if args.command == "supply-chain":
            report = inspect_manifests(args.candidate, args.base)
            receipt = LifecycleReceipt(
                kind="supply_chain_static",
                status=report.status,
                observed_at=datetime.now(timezone.utc),
                data={
                    "code": report.code,
                    "dependency_execution_performed": False,
                    "findings": [item.__dict__ for item in report.findings],
                    "hook_changes": [item.__dict__ for item in report.hook_changes],
                    "planned_commands": [
                        {"workdir": item.workdir, "argv": list(item.argv)}
                        for item in report.planned_commands
                    ],
                    "artifact_sha256": dict(report.artifact_sha256),
                },
            )
            document = receipt_document(receipt)
            if args.receipt:
                write_receipt(args.receipt, receipt)
            stream = sys.stdout if report.status == "CLEAR" else sys.stderr
            print(
                json.dumps(
                    document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
                file=stream,
            )
            return 0 if report.status == "CLEAR" else 2

        remote_result = validate_remote_contract(args.repo)
        if remote_result.status != "CLEAR":
            raise LifecycleBlockedError(
                remote_result.code, "; ".join(remote_result.details)
            )
        if args.command == "remote-audit":
            print(
                json.dumps(
                    remote_result.__dict__, sort_keys=True, separators=(",", ":")
                )
            )
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
        print(
            json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
        )
        return 0
    except LifecycleBlockedError as exc:
        print(
            json.dumps(
                {"code": exc.code, "message": str(exc), "status": "BLOCKED"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps({
                "status": "BLOCKED",
                "code": "release_input_invalid",
                "message": "input is unreadable or malformed",
            }),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
