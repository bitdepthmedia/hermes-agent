from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

from ik_lifecycle.bert_canary import (
    BertCanaryEngine,
    BertCanaryError,
    BertCanaryRequest,
    receipt_is_redacted,
)
from ik_lifecycle.composed_source import tree_digest


class BertCellFixtureTests(unittest.TestCase):
    def _request(self, root: Path) -> BertCanaryRequest:
        release = root / "release"
        source = release / "source"
        source.mkdir(parents=True)
        package = source / "hermes_cli"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "main.py").write_text(
            "import argparse,json,os\n"
            "from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer\n"
            "p=argparse.ArgumentParser(); p.add_argument('command'); p.add_argument('--host'); p.add_argument('--port',type=int); p.add_argument('--isolated',action='store_true'); p.add_argument('--skip-build',action='store_true'); a=p.parse_args()\n"
            "class H(BaseHTTPRequestHandler):\n"
            " def do_GET(self):\n"
            "  body=json.dumps({'ok':True,'auth_required':False,'version':'2026.8.18'}).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)\n"
            " def log_message(self,*args): pass\n"
            "s=ThreadingHTTPServer((a.host,a.port),H); open(os.environ['HERMES_DESKTOP_READY_FILE'],'w').write(json.dumps({'port':s.server_port})); s.serve_forever()\n",
            encoding="utf-8",
        )
        manifest = release / "release-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "bundle_id": "shared-bundle-v1",
                    "status": "SEALED_CODE_ONLY",
                    "identity": {
                        "target_tag": "v2026.8.18",
                        "target_commit_sha": "e624e9fde561e1add9388384012b295fde669ade",
                        "bindings": {"composed-source": {"tree_sha256": tree_digest(source)}},
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        runtime = Path(sys.executable).resolve()
        return BertCanaryRequest(
            canary_root=root / "bert-canary",
            candidate_release_root=release,
            candidate_source_root=source,
            candidate_manifest_path=manifest,
            expected_candidate_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            candidate_python=runtime,
            expected_candidate_python_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            architecture_contract_sha256="a" * 64,
            shared_bundle_id="shared-bundle-v1",
            official_target_tag="v2026.8.18",
            official_target_sha="e624e9fde561e1add9388384012b295fde669ade",
            canary_id="bert-synthetic-v1",
            denied_roots=(),
            require_read_only_release=False,
        )

    def test_acceptance_contract_requires_cloud_cell_gates_and_excludes_live_surfaces(self) -> None:
        root = Path(__file__).resolve().parents[2]
        document = json.loads((root / "evals/ik/bert-cell-acceptance-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(document["schema_id"], "ik.bert-cell-acceptance.v1")
        self.assertEqual(document["phase"], "bert-local-cloud-cell-fixture")
        self.assertTrue(
            {
                "shared_release_bundle",
                "sanitized_only",
                "nate_os_read_only",
                "codex_exactly_once",
                "offline_ernie_pending_handoff",
                "restart",
                "rp3",
            }
            <= set(document["required_gates"])
        )
        self.assertTrue(
            {"credentials", "private_content", "schedules", "live_bert", "live_ernie", "promotion", "deployment"}
            <= set(document["excluded_surfaces"])
        )

    def test_fixture_proves_cloud_boundaries_restart_and_rollback_on_shared_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outcome = BertCanaryEngine.for_synthetic_tests().execute(self._request(root))
            receipt = outcome.receipt.to_dict()
            self.assertEqual(receipt["status"], "CLEAR_SAFE_LOCAL_BERT_CANARY")
            self.assertEqual(receipt["cell_id"], "bert")
            self.assertEqual(receipt["shared_bundle_id"], "shared-bundle-v1")
            self.assertEqual(receipt["health_counts"], {"startups": 2, "heartbeats": 2, "stops": 2})
            self.assertEqual(receipt["behavior_gates"]["sanitized_only"], "CLEAR")
            self.assertEqual(receipt["behavior_gates"]["nate_os_read_only"], "CLEAR")
            self.assertEqual(receipt["behavior_gates"]["codex_exactly_once"], "CLEAR")
            self.assertEqual(receipt["behavior_gates"]["offline_ernie_pending_handoff"], "CLEAR")
            self.assertEqual(receipt["rollback_gates"], {"rp2": "CLEAR", "rp3_crash_recovery": "CLEAR", "rp3_pretraffic": "CLEAR"})
            self.assertFalse(receipt["promotion_eligible"])
            self.assertTrue(receipt_is_redacted(receipt))

    def test_receipt_never_contains_private_canary_paths_or_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = BertCanaryEngine.for_synthetic_tests().execute(self._request(root)).receipt.to_dict()
            rendered = json.dumps(receipt, sort_keys=True)
            self.assertNotIn("SYNTHETIC_PRIVATE_BERT_BOUNDARY_CANARY", rendered)
            self.assertNotIn("ernie-local:", rendered)
            self.assertNotIn(str(root), rendered)

    def test_wrong_bundle_or_source_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            with self.assertRaisesRegex(BertCanaryError, "shared_bundle_binding_invalid"):
                BertCanaryEngine.for_synthetic_tests().execute(replace(request, shared_bundle_id="other-bundle"))

            (request.candidate_source_root / "drift.txt").write_text("drift", encoding="utf-8")
            with self.assertRaisesRegex(BertCanaryError, "candidate_source_drift"):
                BertCanaryEngine.for_synthetic_tests().execute(replace(request, canary_id="bert-synthetic-v2"))

    def test_canary_root_must_be_new_symlink_free_and_outside_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            request.canary_root.mkdir()
            with self.assertRaisesRegex(BertCanaryError, "canary_root_invalid"):
                BertCanaryEngine.for_synthetic_tests().execute(request)

            with self.assertRaisesRegex(BertCanaryError, "canary_root_invalid"):
                BertCanaryEngine.for_synthetic_tests().execute(
                    replace(request, canary_root=request.candidate_release_root / "nested")
                )

    def test_unsafe_canary_id_and_writable_real_release_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            with self.assertRaisesRegex(BertCanaryError, "canary_id_invalid"):
                BertCanaryEngine.for_synthetic_tests().execute(replace(request, canary_id="../escape"))
            with self.assertRaisesRegex(BertCanaryError, "candidate_release_not_immutable"):
                BertCanaryEngine.for_synthetic_tests().execute(
                    replace(request, canary_root=root / "other-canary", require_read_only_release=True)
                )

    @unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "macOS sandbox required")
    def test_real_engine_requires_fresh_os_backed_network_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = self._request(Path(directory))
            engine = BertCanaryEngine()
            outcome = engine.execute(request)
            self.assertEqual(outcome.receipt.network_gate, "CLEAR_OS_BACKED_DENY_EXTERNAL")

    def test_entrypoint_requires_explicit_execute_and_resolves_repo_imports(self) -> None:
        runner = Path(__file__).resolve().parents[2] / "scripts/ik-bert-runtime-canary"
        completed = subprocess.run(
            (sys.executable, str(runner), "--help"),
            cwd=Path(tempfile.gettempdir()),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--execute", completed.stdout)


if __name__ == "__main__":
    unittest.main()
