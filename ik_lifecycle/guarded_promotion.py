"""Single-host promotion with final-artifact gates and state-aware paired rollback."""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from .guarded_release import provenance_from_plan, verify_artifact
from .models import LifecycleBlockedError
from .service_control import PairedSymlinks


def durable_fingerprint(profile: Path) -> dict[str, str]:
    result = {}
    transient = {
        "gateway_state.json",
        "gateway.pid",
        "gateway.lock",
        "gateway.sock.path",
        "gateway.heartbeat",
        "gateway.heartbeat.lock",
    }
    for path in profile.rglob("*"):
        relative = path.relative_to(profile)
        if path.is_symlink():
            raise LifecycleBlockedError(
                "profile_link",
                "profile snapshot requires independent, non-symlink state",
            )
        if "logs" in relative.parts or path.name in transient or path.is_socket():
            continue
        if path.is_file():
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _observe(service, health, release, profile, started, prior_pids, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if service.preflight().running:
                pids = health(release, profile, started, prior_pids)
                if pids and not (set(pids) & set(prior_pids)):
                    return pids
        except (OSError, ValueError, KeyError, LifecycleBlockedError):
            pass
        time.sleep(min(0.1, timeout))
    return None


def run_promotion(
    *,
    pointers,
    service,
    release: Path,
    profile: Path,
    expected,
    preflight,
    health,
    timeout: float = 180,
    final_check=lambda: None,
    snapshot_record: Path | None = None,
) -> dict:
    preflight()
    previous = pointers.read_pair()
    if tuple(expected) != previous or profile.exists() or profile.is_symlink():
        raise LifecycleBlockedError(
            "promotion_generation",
            "current generation changed or candidate profile already exists",
        )
    if not service.preflight().running:
        raise LifecycleBlockedError(
            "promotion_service", "all current services must be healthy before promotion"
        )
    old_release, old_profile = Path(previous[0]), Path(previous[1])
    if profile.resolve().is_relative_to(old_profile) or old_profile.is_relative_to(
        profile.resolve()
    ):
        raise LifecycleBlockedError(
            "promotion_profile_overlap",
            "candidate profile must not overlap current profile",
        )
    durable_fingerprint(old_profile)
    baseline = None
    switched = False
    try:
        service.close()
        if not service.closed():
            raise LifecycleBlockedError(
                "promotion_close", "service group did not close"
            )
        shutil.copytree(
            old_profile,
            profile,
            symlinks=True,
            ignore=lambda directory, names: [
                n for n in names if (Path(directory) / n).is_socket()
            ],
        )
        if os.geteuid() == 0:
            for path in (profile, *profile.rglob("*")):
                original = old_profile / path.relative_to(profile)
                metadata = original.stat()
                os.chown(path, metadata.st_uid, metadata.st_gid)
        baseline = durable_fingerprint(profile)
        if baseline != durable_fingerprint(old_profile):
            raise LifecycleBlockedError(
                "promotion_snapshot", "profile snapshot changed during capture"
            )
        if snapshot_record is not None:
            snapshot_record.write_text(
                json.dumps(
                    {
                        "previous": previous,
                        "candidate_profile": str(profile),
                        "durable_state": baseline,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            snapshot_record.chmod(0o600)
        service.candidate_definitions()
        pointers.switch(release, profile, previous[2] + 1, service_closed=True)
        switched = True
        started = time.time()
        service.open()
        first = _observe(service, health, release, profile, started, set(), timeout)
        if first:
            service.close()
            if not service.closed():
                raise LifecycleBlockedError(
                    "promotion_restart", "service group did not close for restart check"
                )
            started = time.time()
            service.open()
            if _observe(service, health, release, profile, started, first, timeout):
                final_check()
                return {
                    "status": "PROMOTED_CLEAR",
                    "generation": previous[2] + 1,
                    "restart_verified": True,
                }
    except Exception:
        # The recovery path closes partial starts before inspecting any state.
        pass
    service.close()
    if not service.closed():
        raise LifecycleBlockedError(
            "rollback_not_closed", "services must close before rollback"
        )
    if switched and (baseline is None or durable_fingerprint(profile) != baseline):
        raise LifecycleBlockedError(
            "rollback_reconciliation_required",
            "durable candidate state changed; reconciliation required; candidate retained and services closed",
        )
    service.restore_definitions()
    if switched:
        pointers.switch(old_release, old_profile, previous[2], service_closed=True)
    else:
        pointers.recover(service_closed=True)
    started = time.time()
    service.open()
    if not _observe(service, health, old_release, old_profile, started, set(), timeout):
        raise LifecycleBlockedError(
            "rollback_health",
            "previous pair restored but rollback health could not be proven",
        )
    return {
        "status": "ROLLED_BACK_PRE_TRAFFIC",
        "generation": previous[2],
        "restart_verified": False,
    }


def promote_plan(plan: dict) -> dict:
    from .host_release import NativeServiceGroup, observe_host, runtime_root
    from .release_discovery import (
        discover_one_behind,
        GitHubReleaseSource,
        LsRemoteGitRefs,
    )

    cell = Path(plan["cell_root"]).resolve(strict=True)
    release = Path(plan["release"]).resolve(strict=True)
    deployment = Path(plan.get("deployment", plan["release"])).resolve(strict=True)
    if runtime_root(deployment) != release:
        raise LifecycleBlockedError(
            "promotion_deployment", "cell deployment does not bind the approved runtime"
        )
    profile = Path(plan["profile"]).resolve()
    expected = tuple(plan["expected_current"])
    if (
        len(expected) != 3
        or type(expected[2]) is not int
        or not plan["health"]["profiles"]
        or not plan["health"]["ports"]
    ):
        raise LifecycleBlockedError(
            "promotion_plan",
            "promotion needs exact generation and host health bindings",
        )
    if any(not item["platforms"] for item in plan["health"]["profiles"]):
        raise LifecycleBlockedError(
            "promotion_plan", "connected platform evidence is required"
        )
    if any(
        profile.is_relative_to(p) or p.is_relative_to(profile)
        for p in (release, deployment)
    ):
        raise LifecycleBlockedError(
            "promotion_profile_overlap", "profile cannot overlap sealed artifacts"
        )
    if not any(
        deployment.is_relative_to(Path(root).resolve())
        for root in plan["release_roots"]
    ):
        raise LifecycleBlockedError(
            "promotion_release_root", "candidate deployment outside declared roots"
        )
    pointers = PairedSymlinks(
        cell / "current-release",
        cell / "current-profile",
        cell / "promotion-journal.json",
        allowed_release_roots=tuple(Path(p) for p in plan["release_roots"]),
        allowed_profile_roots=tuple(Path(p) for p in plan["profile_roots"]),
    )
    # Validate output roots before service mutation (the candidate profile is new).
    if not any(
        profile.is_relative_to(Path(root).resolve()) for root in plan["profile_roots"]
    ):
        raise LifecycleBlockedError(
            "promotion_profile_root", "candidate profile outside declared roots"
        )
    service = NativeServiceGroup(plan["services"], plan.get("definition_changes", []))

    def preflight():
        evidence = verify_artifact(
            release, plan["manifest_sha256"], provenance_from_plan(plan["provenance"])
        )
        selection = discover_one_behind(GitHubReleaseSource(), LsRemoteGitRefs())
        if evidence["target_commit_sha"] != selection.target.commit_sha:
            raise LifecycleBlockedError(
                "promotion_stale_target", "target selection changed before promotion"
            )
        peer = json.loads(Path(plan["peer_receipt"]).read_text(encoding="utf-8"))
        age = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(peer["observed_at"].replace("Z", "+00:00"))
        ).total_seconds()
        import socket

        if (
            peer.get("status") != "CLEAR"
            or peer.get("host") == socket.gethostname()
            or not peer.get("host")
            or not 0 <= age <= 900
            or peer.get("source_tree_sha256") != evidence["source_tree_sha256"]
        ):
            raise LifecycleBlockedError(
                "promotion_peer_evidence",
                "fresh independent peer source verification is required",
            )
        from .deployable_runtime import validate_deployable_runtime

        validate_deployable_runtime(runtime_root(Path(expected[0])))
        service.validate_changes()
        service.persist_backups(evidence_dir)

    def health(current_release, current_profile, started, prior_pids):
        return observe_host(
            current_release,
            current_profile,
            started,
            prior_pids,
            plan["health"],
            service,
        )

    lock_path = cell / "guarded-promotion.lock"
    if lock_path.is_symlink():
        raise LifecycleBlockedError(
            "promotion_lock", "promotion lock cannot be a symlink"
        )
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LifecycleBlockedError(
                "promotion_busy", "another guarded promotion owns this cell"
            ) from exc
        evidence_dir = cell / "promotion-evidence" / f"guarded-{time.time_ns()}"
        evidence_dir.mkdir(parents=True, mode=0o700)
        evidence_path = evidence_dir / "result.json"
        result = {"status": "BLOCKED", "code": "promotion_incomplete"}
        evidence_path.write_text(json.dumps(result) + "\n")
        evidence_path.chmod(0o600)
        try:
            result = run_promotion(
                pointers=pointers,
                service=service,
                release=deployment,
                profile=profile,
                expected=expected,
                preflight=preflight,
                health=health,
                timeout=180,
                snapshot_record=evidence_dir / "profile-snapshot.json",
                final_check=lambda: verify_artifact(
                    release,
                    plan["manifest_sha256"],
                    provenance_from_plan(plan["provenance"]),
                ),
            )
            return result
        except LifecycleBlockedError as exc:
            result["code"] = exc.code
            raise
        finally:
            evidence_path.write_text(
                json.dumps(
                    {
                        **result,
                        "manifest_sha256": plan["manifest_sha256"],
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            evidence_path.chmod(0o600)
