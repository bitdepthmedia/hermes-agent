from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from ik_lifecycle.ernie_canary import (
    CanaryError,
    CanaryRequest,
    CanaryRuntime,
    ErnieCanaryEngine,
    LoopbackOnlyMacOSSandbox,
    LoopbackProof,
    ProcessCanaryRuntime,
    discard_runtime_profile,
    receipt_is_redacted,
)
from ik_lifecycle.opaque_backup import StorageAttestation
from ik_lifecycle.composed_source import tree_digest as release_tree_digest


PRIVATE_CANARY = "SYNTHETIC_PRIVATE_RUNTIME_CANARY"


class StaticAttestor:
    def attest(self, storage_root: Path, *, denied_roots: tuple[Path, ...]) -> StorageAttestation:
        return StorageAttestation(True, True, True, True, True, True, True, "a" * 64)


class FakeRuntime(CanaryRuntime):
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.starts = 0
        self.stops = 0
        self.heartbeats = 0

    def start(self, request, *, profile_root: Path, run_root: Path, proof: LoopbackProof):
        if self.fail_start:
            raise CanaryError("runtime_start_failed")
        self.starts += 1
        return {"port": 49152 + self.starts, "pid": 1000 + self.starts, "version": "2026.8.18"}

    def health(self, handle) -> dict[str, object]:
        return {"ok": True, "version": handle["version"], "auth_required": False}

    def heartbeat(self, handle) -> dict[str, object]:
        self.heartbeats += 1
        return self.health(handle)

    def stop(self, handle) -> None:
        self.stops += 1


class FailingHealthRuntime(FakeRuntime):
    def health(self, handle) -> dict[str, object]:
        raise CanaryError("runtime_health_failed")


class FakeSandbox:
    def create_proof(self, proof_path: Path, *, ttl_seconds: int = 300) -> LoopbackProof:
        proof_path.write_text("{}", encoding="utf-8")
        os.chmod(proof_path, 0o600)
        return LoopbackProof(
            proof_sha256="b" * 64,
            policy_sha256="c" * 64,
            adapter_sha256="d" * 64,
            runtime_sha256="e" * 64,
            sandbox_exec_sha256="f" * 64,
            observed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        )

    def wrap(self, argv: tuple[str, ...], proof: LoopbackProof) -> tuple[str, ...]:
        return argv


class ErnieCellFixtureTests(unittest.TestCase):
    def _request(self, root: Path) -> CanaryRequest:
        storage = root / "encrypted-storage"
        source = storage / "migrated"
        source.mkdir(parents=True, mode=0o700)
        (source / "SOUL.md").write_text("private persona", encoding="utf-8")
        (source / ".env").write_text(f"TOKEN={PRIVATE_CANARY}", encoding="utf-8")
        (source / "config.yaml").write_text("model: private-model\n", encoding="utf-8")
        (source / "cron").mkdir()
        (source / "cron" / "jobs.json").write_text('{"jobs":[{"enabled":true}]}', encoding="utf-8")
        with sqlite3.connect(source / "state.db") as database:
            database.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
            database.execute("INSERT INTO sessions VALUES('private-session')")
        for path in (storage, source, source / "cron"):
            os.chmod(path, 0o700)
        for path in source.rglob("*"):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)

        candidate = root / "release"
        candidate.mkdir(mode=0o700)
        source_root = candidate / "source"
        source_root.mkdir(mode=0o700)
        manifest = candidate / "release-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "bundle_id": "bundle-v1",
                    "status": "SEALED_CODE_ONLY",
                    "identity": {
                        "target_tag": "v2026.8.18",
                        "target_commit_sha": "e624e9fde561e1add9388384012b295fde669ade",
                        "bindings": {"composed-source": {"tree_sha256": release_tree_digest(source_root)}},
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        runtime = Path(sys.executable).resolve()
        return CanaryRequest(
            storage_root=storage,
            migrated_profile_root=source,
            expected_migrated_tree_sha256="1" * 64,
            semantic_receipt_sha256="2" * 64,
            architecture_contract_sha256="3" * 64,
            candidate_release_root=candidate,
            candidate_source_root=source_root,
            candidate_manifest_path=manifest,
            expected_candidate_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            candidate_python=runtime,
            expected_candidate_python_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            bundle_id="bundle-v1",
            official_target_tag="v2026.8.18",
            official_target_sha="e624e9fde561e1add9388384012b295fde669ade",
            canary_id="synthetic-canary-v1",
            denied_roots=(),
        )

    def test_acceptance_contract_covers_task_11_and_keeps_live_surfaces_blocked(self) -> None:
        root = Path(__file__).resolve().parents[2]
        document = json.loads((root / "evals/ik/ernie-cell-acceptance-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(document["schema_id"], "ik.ernie-cell-acceptance.v1")
        self.assertEqual(document["phase"], "ernie-first-isolated-canary")
        self.assertTrue({"startup", "health", "restart", "rp2", "rp3", "runtime_code_parity"} <= set(document["required_gates"]))
        self.assertTrue({"model", "credentials", "schedules", "live_ernie", "live_bert", "promotion"} <= set(document["excluded_surfaces"]))

    def test_runtime_clone_excludes_credentials_configuration_and_schedules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = FakeRuntime()
            outcome = ErnieCanaryEngine(attestor=StaticAttestor(), sandbox=FakeSandbox(), runtime=runtime).execute(
                self._request(root), verify_source_digest=False
            )
            self.assertEqual(outcome.receipt.status, "CLEAR_SAFE_LOCAL_CANARY")
            self.assertEqual(outcome.receipt.excluded_surface_counts["credentials"], 1)
            self.assertEqual(outcome.receipt.excluded_surface_counts["configuration"], 1)
            self.assertEqual(outcome.receipt.excluded_surface_counts["schedules"], 1)
            self.assertEqual((runtime.starts, runtime.stops, runtime.heartbeats), (2, 2, 2))
            self.assertFalse(outcome.runtime_profile_root.exists())

    def test_receipt_is_redacted_and_never_contains_private_values_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outcome = ErnieCanaryEngine(attestor=StaticAttestor(), sandbox=FakeSandbox(), runtime=FakeRuntime()).execute(
                self._request(root), verify_source_digest=False
            )
            rendered = json.dumps(outcome.receipt.to_dict(), sort_keys=True)
            self.assertTrue(receipt_is_redacted(outcome.receipt.to_dict()))
            self.assertNotIn(PRIVATE_CANARY, rendered)
            self.assertNotIn("private-session", rendered)
            self.assertNotIn(str(root), rendered)
            self.assertNotIn("SOUL.md", rendered)
            self.assertNotIn("state.db", rendered)

    def test_failed_start_is_retained_and_never_switches_or_deletes_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            with self.assertRaisesRegex(CanaryError, "runtime_start_failed"):
                ErnieCanaryEngine(attestor=StaticAttestor(), sandbox=FakeSandbox(), runtime=FakeRuntime(fail_start=True)).execute(
                    request, verify_source_digest=False
                )
            self.assertTrue(request.migrated_profile_root.exists())
            self.assertTrue((request.storage_root / "canaries" / request.canary_id).exists())

    def test_failed_health_always_stops_the_isolated_runtime_and_retains_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = FailingHealthRuntime()
            request = self._request(root)
            with self.assertRaisesRegex(CanaryError, "runtime_health_failed"):
                ErnieCanaryEngine(attestor=StaticAttestor(), sandbox=FakeSandbox(), runtime=runtime).execute(
                    request, verify_source_digest=False
                )
            self.assertEqual((runtime.starts, runtime.stops), (1, 1))
            self.assertTrue((request.storage_root / "canaries" / request.canary_id).exists())

    def test_source_or_candidate_digest_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            bad = replace(request, expected_candidate_manifest_sha256="0" * 64)
            with self.assertRaisesRegex(CanaryError, "candidate_binding_invalid"):
                ErnieCanaryEngine(attestor=StaticAttestor(), sandbox=FakeSandbox(), runtime=FakeRuntime()).execute(
                    bad, verify_source_digest=False
                )

    def test_rp2_discards_only_an_owned_symlink_free_read_only_disposable_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory).resolve()
            profile = run_root / "runtime-profile"
            nested = profile / "seeded" / "fixture.txt"
            nested.parent.mkdir(parents=True)
            nested.write_text("fixture", encoding="utf-8")
            os.chmod(nested, 0o400)
            os.chmod(nested.parent, 0o500)
            os.chmod(profile, 0o500)
            discard_runtime_profile(profile, run_root)
            self.assertFalse(profile.exists())

    def test_rp2_rejects_a_symlink_instead_of_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory).resolve()
            profile = run_root / "runtime-profile"
            profile.mkdir()
            (profile / "escape").symlink_to(run_root.parent)
            with self.assertRaisesRegex(CanaryError, "rp2_symlink_rejected"):
                discard_runtime_profile(profile, run_root)

    @unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "macOS sandbox required")
    def test_os_sandbox_allows_loopback_but_denies_external_network_and_stale_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proof_path = Path(directory) / "proof.json"
            adapter = LoopbackOnlyMacOSSandbox(Path(sys.executable).resolve())
            proof = adapter.create_proof(proof_path, ttl_seconds=60)
            adapter.validate(proof_path)
            self.assertEqual(len(proof.proof_sha256), 64)
            with self.assertRaisesRegex(CanaryError, "network_runtime_drift"):
                adapter.wrap(("/bin/echo", "fixture"), proof)
            with self.assertRaisesRegex(CanaryError, "network_proof_stale"):
                adapter.validate(proof_path, now=proof.expires_at + timedelta(seconds=1))

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX process groups required")
    def test_early_parent_exit_cleans_surviving_process_group(self) -> None:
        program = "import subprocess; subprocess.Popen(['/bin/sleep','30'])"
        process = subprocess.Popen([sys.executable, "-B", "-c", program], start_new_session=True)
        process.wait(timeout=5)
        ProcessCanaryRuntime._terminate_process(process)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("surviving process group was not reaped")

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX process groups required")
    def test_runtime_start_early_exit_path_reaps_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            package = request.candidate_source_root / "hermes_cli"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            token = "synthetic-task11-descendant-canary"
            (package / "main.py").write_text(
                "import subprocess,sys\n"
                f"subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)',{token!r}])\n",
                encoding="utf-8",
            )
            run_root = root / "run"
            profile = run_root / "profile"
            run_root.mkdir()
            profile.mkdir()
            proof = FakeSandbox().create_proof(run_root / "proof.json")
            runtime = ProcessCanaryRuntime(FakeSandbox(), startup_timeout_seconds=2)
            with self.assertRaisesRegex(CanaryError, "runtime_start_failed"):
                runtime.start(request, profile_root=profile, run_root=run_root, proof=proof)
            processes = subprocess.run(
                ["/bin/ps", "ax", "-o", "command="], capture_output=True, text=True, check=True
            ).stdout
            self.assertNotIn(token, processes)


if __name__ == "__main__":
    unittest.main()
