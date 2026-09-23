from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from ik_lifecycle.opaque_backup import (
    OpaqueBackupEngine,
    OpaqueBackupRequest,
    StorageAttestation,
)
from ik_lifecycle.semantic_rehearsal import (
    SemanticContinuityError,
    SemanticRehearsalEngine,
    SemanticRehearsalRequest,
)


PRIVATE_CANARY = "SYNTHETIC_PRIVATE_SEMANTIC_CANARY"


def clear_attestation() -> StorageAttestation:
    return StorageAttestation(
        encrypted_at_rest=True,
        local=True,
        non_synced=True,
        current_user_owned=True,
        symlink_safe=True,
        outside_git=True,
        outside_shared_memory=True,
        verifier_digest="a" * 64,
    )


class StaticAttestor:
    def attest(self, storage_root: Path, *, denied_roots: tuple[Path, ...]) -> StorageAttestation:
        return clear_attestation()


class SemanticRehearsalTests(unittest.TestCase):
    def _engine(self) -> SemanticRehearsalEngine:
        return SemanticRehearsalEngine(attestor=StaticAttestor(), candidate_migrator=lambda _source, _clone, _runtime: 0)

    def _profile(self, root: Path) -> Path:
        profile = root / "private-profile"
        profile.mkdir(mode=0o700)
        profile = profile.resolve()
        (profile / "persona.md").write_text("Synthetic stable persona", encoding="utf-8")
        (profile / "memory.md").write_text("Synthetic retrieval memory", encoding="utf-8")
        (profile / ".env").write_text(f"TOKEN={PRIVATE_CANARY}", encoding="utf-8")
        (profile / "credentials.json").write_text(json.dumps({"secret": PRIVATE_CANARY}), encoding="utf-8")
        cron = profile / "cron"
        cron.mkdir()
        (cron / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": "job-opaque-1",
                            "enabled": False,
                            "state": "paused",
                            "schedule": {"kind": "interval", "minutes": 25},
                            "next_run_at": "2026-08-22T00:00:00+00:00",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        database = profile / "state.db"
        with sqlite3.connect(database) as db:
            db.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE sessions(
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    started_at REAL NOT NULL
                );
                CREATE TABLE messages(
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_call_id TEXT,
                    tool_calls TEXT,
                    tool_name TEXT,
                    timestamp REAL NOT NULL
                );
                INSERT INTO sessions VALUES('session-opaque-1','fixture',1);
                INSERT INTO messages VALUES(1,'session-opaque-1','user','hello',NULL,NULL,NULL,1);
                INSERT INTO messages VALUES(
                    2,'session-opaque-1','assistant',NULL,NULL,
                    '[{"id":"call-opaque-1","type":"function","function":{"name":"fixture","arguments":"{}"}}]',
                    NULL,2
                );
                INSERT INTO messages VALUES(3,'session-opaque-1','tool','ok','call-opaque-1',NULL,'fixture',3);
                INSERT INTO messages VALUES(4,'session-opaque-1','assistant','done',NULL,NULL,NULL,4);
                """
            )
        kanban = profile / "kanban.db"
        with sqlite3.connect(kanban) as db:
            db.executescript(
                """
                CREATE TABLE tasks(
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    assignee TEXT,
                    created_by TEXT,
                    idempotency_key TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                CREATE TABLE task_events(
                    id INTEGER PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                INSERT INTO tasks VALUES('task-opaque-1','done','ernie','fixture','idem-1',1,2);
                INSERT INTO task_events VALUES(1,'task-opaque-1','completed',2);
                """
            )
        return profile

    def _snapshot(self, root: Path):
        profile = self._profile(root)
        storage = root / "encrypted-local-storage"
        return OpaqueBackupEngine(attestor=StaticAttestor()).execute(
            OpaqueBackupRequest(
                source_root=profile,
                storage_root=storage,
                source_alias="ernie-private-cell",
                storage_alias="local-encrypted-continuity",
                idempotency_key="semantic-fixture",
                denied_roots=(),
            )
        )

    def _request(self, result, rehearsal_id: str = "rehearsal-opaque-1") -> SemanticRehearsalRequest:
        candidate = result.archive_path.parents[2] / "synthetic-candidate"
        candidate.mkdir(mode=0o700, exist_ok=True)
        manifest = candidate / "release-manifest.json"
        if not manifest.exists():
            manifest.write_text('{"schema_id":"synthetic","status":"SEALED_CODE_ONLY"}\n', encoding="utf-8")
            os.chmod(manifest, 0o600)
        return SemanticRehearsalRequest(
            storage_root=result.archive_path.parents[2],
            snapshot_id=result.receipt.snapshot_id,
            rollback_handle=result.receipt.rollback_handle,
            rehearsal_id=rehearsal_id,
            expected_snapshot_tree_sha256=result.receipt.snapshot_tree_sha256,
            expected_archive_sha256=result.receipt.archive_sha256,
            expected_archive_hmac_sha256=result.receipt.archive_hmac_sha256,
            architecture_contract_sha256="b" * 64,
            candidate_source_root=candidate,
            candidate_manifest_path=manifest,
            expected_candidate_manifest_sha256=__import__("hashlib").sha256(manifest.read_bytes()).hexdigest(),
            candidate_python=Path(sys.executable),
            expected_candidate_python_sha256=__import__("hashlib").sha256(Path(sys.executable).read_bytes()).hexdigest(),
            denied_roots=(),
        )

    def test_restores_fresh_clone_and_preserves_semantics_without_touching_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))
            archive_before = result.archive_path.read_bytes()
            outcome = self._engine().execute(self._request(result))
            self.assertEqual(outcome.receipt.status, "CLEAR")
            self.assertEqual(outcome.receipt.source_tree_sha256, outcome.receipt.restored_tree_sha256)
            self.assertEqual(len(outcome.receipt.migrated_tree_sha256), 64)
            self.assertEqual(result.archive_path.read_bytes(), archive_before)
            self.assertNotEqual(outcome.restored_root, result.clone_root)
            self.assertNotEqual(outcome.restored_root, outcome.migrated_root)
            self.assertEqual(oct(outcome.restored_root.stat().st_mode & 0o777), "0o700")

    def test_receipt_is_aggregate_only_and_private_canary_never_leaks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))
            receipt = self._engine().execute(self._request(result)).receipt
            rendered = json.dumps(receipt.to_dict(), sort_keys=True)
            self.assertNotIn(PRIVATE_CANARY, rendered)
            self.assertNotIn("persona.md", rendered)
            self.assertNotIn("state.db", rendered)
            self.assertNotIn("sessions", rendered)
            self.assertNotIn(str(Path(directory)), rendered)
            self.assertEqual(
                set(receipt.to_dict()),
                {
                    "schema_version",
                    "snapshot_id",
                    "rehearsal_id",
                    "created_at",
                    "source_tree_sha256",
                    "restored_tree_sha256",
                    "migrated_tree_sha256",
                    "architecture_contract_sha256",
                    "candidate_release_manifest_sha256",
                    "candidate_python_sha256",
                    "structural_hmac_sha256",
                    "aggregate_counts",
                    "validation_counts",
                    "discrepancy_classes",
                    "permission_state",
                    "rollback_state",
                    "status",
                },
            )
            self.assertEqual(receipt.aggregate_counts["sensitive_artifacts_excluded"], 2)

    def test_expected_semantic_categories_and_relationships_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))
            receipt = self._engine().execute(self._request(result)).receipt
            self.assertEqual(receipt.aggregate_counts["session_records"], 1)
            self.assertEqual(receipt.aggregate_counts["message_records"], 4)
            self.assertEqual(receipt.aggregate_counts["tool_call_records"], 1)
            self.assertEqual(receipt.aggregate_counts["tool_result_records"], 1)
            self.assertEqual(receipt.aggregate_counts["task_records"], 1)
            self.assertEqual(receipt.aggregate_counts["schedule_records"], 1)
            self.assertEqual(receipt.validation_counts["failed"], 0)
            self.assertGreater(receipt.validation_counts["retrieval_cases"], 0)

    def test_nonempty_legacy_custom_role_is_preserved_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self._profile(root)
            with sqlite3.connect(profile / "state.db") as db:
                db.execute(
                    "INSERT INTO messages VALUES(5,'session-opaque-1','legacy-custom','preserved',NULL,NULL,NULL,5)"
                )
            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(
                OpaqueBackupRequest(
                    source_root=profile,
                    storage_root=(root / "custom-role-storage").resolve(),
                    source_alias="ernie-private-cell",
                    storage_alias="local-encrypted-continuity",
                    idempotency_key="custom-role-fixture",
                    denied_roots=(),
                )
            )
            receipt = self._engine().execute(self._request(result, "custom-role-rehearsal")).receipt
            self.assertEqual(receipt.status, "CLEAR")
            self.assertEqual(receipt.aggregate_counts["custom_role_records"], 1)

    def test_blank_message_role_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self._profile(root)
            with sqlite3.connect(profile / "state.db") as db:
                db.execute("INSERT INTO messages VALUES(5,'session-opaque-1',' ','invalid',NULL,NULL,NULL,5)")
            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(
                OpaqueBackupRequest(
                    source_root=profile,
                    storage_root=(root / "blank-role-storage").resolve(),
                    source_alias="ernie-private-cell",
                    storage_alias="local-encrypted-continuity",
                    idempotency_key="blank-role-fixture",
                    denied_roots=(),
                )
            )
            with self.assertRaisesRegex(SemanticContinuityError, "^semantic_validation_blocked$"):
                self._engine().execute(self._request(result, "blank-role-rehearsal"))

    def test_broken_foreign_key_or_tool_pairing_fails_closed_with_redacted_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._snapshot(root)
            clone_database = result.clone_root / "state.db"
            with sqlite3.connect(clone_database) as db:
                db.execute("PRAGMA foreign_keys=OFF")
                db.execute("DELETE FROM messages WHERE role='tool'")
            # Build a new immutable snapshot from the deliberately broken disposable fixture.
            broken_storage = root / "broken-storage"
            broken = OpaqueBackupEngine(attestor=StaticAttestor()).execute(
                OpaqueBackupRequest(
                    source_root=result.clone_root.resolve(),
                    storage_root=broken_storage,
                    source_alias="ernie-private-cell",
                    storage_alias="local-encrypted-continuity",
                    idempotency_key="broken-semantic-fixture",
                    denied_roots=(),
                )
            )
            with self.assertRaisesRegex(SemanticContinuityError, "^semantic_validation_blocked$") as caught:
                self._engine().execute(self._request(broken, "broken-rehearsal"))
            self.assertNotIn("call-opaque", str(caught.exception))

    def test_tampered_archive_or_binding_fails_before_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))
            request = replace(self._request(result), expected_archive_sha256="0" * 64)
            with self.assertRaisesRegex(SemanticContinuityError, "^snapshot_binding_invalid$"):
                self._engine().execute(request)

    def test_existing_rehearsal_is_idempotent_only_when_receipt_and_trees_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))
            engine = self._engine()
            request = self._request(result)
            first = engine.execute(request)
            second = engine.execute(request)
            self.assertEqual(first.receipt.to_dict(), second.receipt.to_dict())
            target = next(path for path in second.migrated_root.rglob("*") if path.is_file())
            os.chmod(target, 0o600)
            target.write_bytes(target.read_bytes() + b"tamper")
            with self.assertRaisesRegex(SemanticContinuityError, "^existing_rehearsal_invalid$"):
                engine.execute(request)

    def test_symlink_or_special_member_is_rejected_without_leaking_member_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self._profile(root)
            (profile / "private-link").symlink_to(profile / "persona.md")
            with self.assertRaisesRegex(Exception, "source_symlink_rejected"):
                OpaqueBackupEngine(attestor=StaticAttestor()).execute(
                    OpaqueBackupRequest(
                        source_root=profile,
                        storage_root=root / "storage",
                        source_alias="ernie-private-cell",
                        storage_alias="local-encrypted-continuity",
                        idempotency_key="symlink-fixture",
                        denied_roots=(),
                    )
                )

    def test_secret_named_artifacts_are_not_semantically_opened(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))
            # Removing read bits proves semantic validation does not open these artifacts.
            for name in (".env", "credentials.json"):
                os.chmod(result.clone_root / name, 0o000)
            # The rehearsal is restored from the archive, not the prior disposable clone.
            outcome = self._engine().execute(self._request(result))
            self.assertEqual(outcome.receipt.aggregate_counts["sensitive_artifacts_excluded"], 2)

    def test_candidate_manifest_drift_fails_before_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))
            request = replace(self._request(result), expected_candidate_manifest_sha256="0" * 64)
            with self.assertRaisesRegex(SemanticContinuityError, "^candidate_binding_invalid$"):
                self._engine().execute(request)

    def test_candidate_migration_may_add_schema_without_changing_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))

            def additive_migration(_candidate: Path, clone: Path, _runtime: Path) -> int:
                with sqlite3.connect(clone / "state.db") as database:
                    database.execute("ALTER TABLE sessions ADD COLUMN additive_target_field TEXT")
                return 1

            engine = SemanticRehearsalEngine(attestor=StaticAttestor(), candidate_migrator=additive_migration)
            receipt = engine.execute(self._request(result, "additive-migration-rehearsal")).receipt
            self.assertEqual(receipt.status, "CLEAR")
            self.assertEqual(receipt.validation_counts["repairs"], 0)
            self.assertEqual(receipt.validation_counts["migration_surfaces"], 1)

    def test_candidate_prompt_dedup_and_additive_lineage_are_semantically_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._snapshot(Path(directory))

            def target_migration(_candidate: Path, clone: Path, _runtime: Path) -> int:
                with sqlite3.connect(clone / "state.db") as database:
                    database.execute("ALTER TABLE sessions ADD COLUMN system_prompt_hash TEXT")
                    database.execute("CREATE TABLE system_prompts(hash TEXT PRIMARY KEY, prompt TEXT NOT NULL)")
                    row = database.execute("SELECT id, system_prompt, model_config FROM sessions").fetchone()
                    prompt = row[1]
                    digest = __import__("hashlib").sha256(prompt.encode()).hexdigest()
                    database.execute("INSERT INTO system_prompts VALUES(?,?)", (digest, prompt))
                    database.execute(
                        "UPDATE sessions SET system_prompt=NULL, system_prompt_hash=?, model_config=? WHERE id=?",
                        (digest, '{"_delegate_from":"synthetic-parent"}', row[0]),
                    )
                return 1

            # Seed the legacy source fields that the target normalizes.
            source_clone = result.clone_root
            with sqlite3.connect(source_clone / "state.db") as database:
                database.execute("ALTER TABLE sessions ADD COLUMN system_prompt TEXT")
                database.execute("ALTER TABLE sessions ADD COLUMN model_config TEXT")
                database.execute("UPDATE sessions SET system_prompt='synthetic stable prompt'")
            normalized = OpaqueBackupEngine(attestor=StaticAttestor()).execute(
                OpaqueBackupRequest(
                    source_root=source_clone.resolve(),
                    storage_root=(Path(directory) / "normalized-storage").resolve(),
                    source_alias="ernie-private-cell",
                    storage_alias="local-encrypted-continuity",
                    idempotency_key="prompt-dedup-fixture",
                    denied_roots=(),
                )
            )
            engine = SemanticRehearsalEngine(attestor=StaticAttestor(), candidate_migrator=target_migration)
            receipt = engine.execute(self._request(normalized, "prompt-dedup-rehearsal")).receipt
            self.assertEqual(receipt.status, "CLEAR")


if __name__ == "__main__":
    unittest.main()
