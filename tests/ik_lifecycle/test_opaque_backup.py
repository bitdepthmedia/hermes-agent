from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import unittest

from ik_lifecycle.opaque_backup import (
    MacOSStorageAttestor,
    OpaqueBackupEngine,
    OpaqueBackupError,
    OpaqueBackupRequest,
    StorageAttestation,
    _backup_sqlite_opaquely,
    derive_ernie_profile_root,
)


PRIVATE_CANARY = "SYNTHETIC_PRIVATE_CANARY_NEVER_DISCLOSE"


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
    def __init__(self, result: StorageAttestation | None = None) -> None:
        self.result = result or clear_attestation()

    def attest(self, storage_root: Path, *, denied_roots: tuple[Path, ...]) -> StorageAttestation:
        return self.result


class OpaqueBackupTests(unittest.TestCase):
    def test_ernie_root_derivation_uses_only_declared_repository_topology(self) -> None:
        common = Path("/synthetic/stack/runtime/hermes-agent/.git")
        self.assertEqual(
            derive_ernie_profile_root(common),
            Path("/synthetic/stack/config/ik-agents/hermes-ernie"),
        )

    def request(self, root: Path, **overrides: object) -> OpaqueBackupRequest:
        root = root.resolve()
        source = root / "private-source-name"
        storage = root / "secure-storage"
        values: dict[str, object] = {
            "source_root": source,
            "storage_root": storage,
            "source_alias": "ernie-private-cell",
            "storage_alias": "local-encrypted-continuity",
            "idempotency_key": "synthetic-run-001",
        }
        values.update(overrides)
        return OpaqueBackupRequest(**values)

    def populate_source(self, source: Path) -> None:
        source.mkdir(mode=0o700, parents=True)
        (source / "persona-private-name.md").write_text(PRIVATE_CANARY, encoding="utf-8")
        nested = source / "private-folder-name"
        nested.mkdir(mode=0o700)
        (nested / "private-config-name.bin").write_bytes(b"opaque\x00bytes")

    def test_receipt_has_only_allowlisted_opaque_fields_and_no_private_leak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)

            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)
            rendered = json.dumps(result.receipt.to_dict(), sort_keys=True)

            self.assertEqual(
                set(result.receipt.to_dict()),
                {
                    "schema_version",
                    "snapshot_id",
                    "source_alias",
                    "storage_alias",
                    "source_path_sha256",
                    "storage_attestation_sha256",
                    "created_at",
                    "aggregate_file_count",
                    "aggregate_bytes",
                    "snapshot_tree_sha256",
                    "clone_tree_sha256",
                    "archive_sha256",
                    "archive_hmac_sha256",
                    "receipt_hmac_sha256",
                    "encryption",
                    "permission_state",
                    "rollback_handle",
                    "status",
                },
            )
            for forbidden in (
                PRIVATE_CANARY,
                str(request.source_root),
                str(request.storage_root),
                "persona-private-name.md",
                "private-folder-name",
                "private-config-name.bin",
            ):
                self.assertNotIn(forbidden, rendered)

    def test_archive_is_encrypted_backup_is_sealed_and_clone_is_restrictive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)

            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

            self.assertNotIn(PRIVATE_CANARY.encode(), result.archive_path.read_bytes())
            self.assertEqual(stat.S_IMODE(result.backup_root.stat().st_mode), 0o500)
            self.assertEqual(stat.S_IMODE(result.archive_path.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(result.clone_root.stat().st_mode), 0o700)
            for path in result.clone_root.rglob("*"):
                expected = 0o700 if path.is_dir() else 0o600
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)
            self.assertEqual(stat.S_IMODE(result.key_path.stat().st_mode), 0o400)
            self.assertRegex(result.key_path.read_text(encoding="ascii"), r"^[0-9a-f]{128}$")

    def test_encrypted_rollback_package_round_trips_synthetic_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            request = self.request(root)
            self.populate_source(request.source_root)
            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)
            decrypted_tar = root / "synthetic-rollback.tar"
            restored = root / "synthetic-restored"
            restored.mkdir(mode=0o700)

            subprocess.run(
                [
                    "/usr/bin/openssl",
                    "enc",
                    "-d",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-iter",
                    "600000",
                    "-md",
                    "sha256",
                    "-in",
                    str(result.archive_path),
                    "-out",
                    str(decrypted_tar),
                    "-pass",
                    f"file:{result.key_path}",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["/usr/bin/tar", "-xf", str(decrypted_tar), "-C", str(restored)],
                check=True,
                capture_output=True,
            )
            self.assertEqual((restored / "persona-private-name.md").read_text(encoding="utf-8"), PRIVATE_CANARY)

    def test_sqlite_wal_is_captured_by_online_backup_without_query_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            request.source_root.mkdir(mode=0o700)
            database = request.source_root / "private-state.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE synthetic(value TEXT)")
                connection.commit()
                connection.execute("INSERT INTO synthetic VALUES(?)", (PRIVATE_CANARY,))
                connection.commit()
                self.assertTrue(database.with_name(database.name + "-wal").exists())

                result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

                clone = sqlite3.connect(result.clone_root / database.name)
                try:
                    self.assertEqual(clone.execute("SELECT value FROM synthetic").fetchone()[0], PRIVATE_CANARY)
                finally:
                    clone.close()
            finally:
                connection.close()

            parsed = ast.parse(inspect.getsource(_backup_sqlite_opaquely))
            forbidden_calls = [
                node.attr
                for node in ast.walk(parsed)
                if isinstance(node, ast.Attribute) and node.attr in {"execute", "executemany", "executescript", "cursor"}
            ]
            self.assertEqual(forbidden_calls, [])

    def test_undeclared_db_suffix_is_copied_opaquely_not_parsed_as_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            request.source_root.mkdir(mode=0o700)
            opaque_db = request.source_root / "non-sqlite-artifact.db"
            opaque_db.write_bytes(b"not a sqlite database\x00" + PRIVATE_CANARY.encode())

            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

            self.assertEqual((result.clone_root / opaque_db.name).read_bytes(), opaque_db.read_bytes())

    def test_declared_database_matching_is_path_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            request.source_root.mkdir(mode=0o700)
            nested = request.source_root / "unrelated"
            nested.mkdir(mode=0o700)
            nested_state = nested / "state.db"
            nested_state.write_bytes(b"opaque non-sqlite state")
            top_state = request.source_root / "state.db"
            connection = sqlite3.connect(top_state)
            try:
                connection.execute("CREATE TABLE synthetic(value TEXT)")
                connection.execute("INSERT INTO synthetic VALUES(?)", (PRIVATE_CANARY,))
                connection.commit()
            finally:
                connection.close()

            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

            self.assertEqual((result.clone_root / "unrelated" / "state.db").read_bytes(), nested_state.read_bytes())
            clone = sqlite3.connect(result.clone_root / "state.db")
            try:
                self.assertEqual(clone.execute("SELECT value FROM synthetic").fetchone()[0], PRIVATE_CANARY)
            finally:
                clone.close()

    def test_declared_non_sqlite_failure_is_redacted_and_classified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            request.source_root.mkdir(mode=0o700)
            (request.source_root / "state.db").write_bytes(b"not actually sqlite")

            with self.assertRaisesRegex(OpaqueBackupError, "^sqlite_online_backup_not_database$"):
                OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

    def test_sqlite_source_open_failure_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with self.assertRaisesRegex(OpaqueBackupError, "^sqlite_source_open_failed$"):
                _backup_sqlite_opaquely(root / "missing.sqlite", root / "backup.sqlite")

    def test_sqlite_without_wal_uses_online_backup_from_read_only_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source_dir = root / "read-only-source"
            source_dir.mkdir(mode=0o700)
            source = source_dir / "state.db"
            connection = sqlite3.connect(source)
            try:
                connection.execute("CREATE TABLE synthetic(value TEXT)")
                connection.execute("INSERT INTO synthetic VALUES(?)", (PRIVATE_CANARY,))
                connection.commit()
            finally:
                connection.close()
            os.chmod(source, 0o400)
            os.chmod(source_dir, 0o500)

            destination = root / "backup.db"
            _backup_sqlite_opaquely(source, destination)

            clone = sqlite3.connect(destination)
            try:
                self.assertEqual(clone.execute("SELECT value FROM synthetic").fetchone()[0], PRIVATE_CANARY)
            finally:
                clone.close()

    def test_symlink_source_or_member_fails_closed_without_path_leak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)
            (request.source_root / "private-link-name").symlink_to(request.source_root / "persona-private-name.md")

            with self.assertRaisesRegex(OpaqueBackupError, "source_symlink_rejected") as raised:
                OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

            self.assertNotIn(str(request.source_root), str(raised.exception))
            self.assertNotIn("private-link-name", str(raised.exception))

    def test_concurrent_non_database_mutation_fails_and_retains_failure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)
            mutated = False

            def mutate_once(source: Path) -> None:
                nonlocal mutated
                if not mutated and source.suffix == ".md":
                    source.write_text(PRIVATE_CANARY + "-changed", encoding="utf-8")
                    mutated = True

            with self.assertRaisesRegex(OpaqueBackupError, "concurrent_source_mutation"):
                OpaqueBackupEngine(attestor=StaticAttestor(), after_regular_copy=mutate_once).execute(request)

            failure_markers = list((request.storage_root / "failures").glob("*/failure.json"))
            self.assertEqual(len(failure_markers), 1)
            failure_text = failure_markers[0].read_text(encoding="utf-8")
            self.assertNotIn(PRIVATE_CANARY, failure_text)
            self.assertNotIn(str(request.source_root), failure_text)

    def test_idempotent_replay_returns_same_snapshot_and_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)
            engine = OpaqueBackupEngine(attestor=StaticAttestor())

            first = engine.execute(request)
            second = engine.execute(request)
            self.assertEqual(first.receipt.to_dict(), second.receipt.to_dict())

            os.chmod(first.archive_path, 0o600)
            with first.archive_path.open("ab") as archive:
                archive.write(b"tamper")
            os.chmod(first.archive_path, 0o400)
            with self.assertRaisesRegex(OpaqueBackupError, "existing_snapshot_tampered"):
                engine.execute(request)

    def test_storage_policy_rejects_unencrypted_synced_git_shared_or_foreign_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = clear_attestation().__dict__
            for field in (
                "encrypted_at_rest",
                "local",
                "non_synced",
                "current_user_owned",
                "symlink_safe",
                "outside_git",
                "outside_shared_memory",
            ):
                request = self.request(root / field)
                self.populate_source(request.source_root)
                request = self.request(root / field)
                blocked_attestor = StaticAttestor(StorageAttestation(**{**base, field: False}))
                with self.assertRaisesRegex(OpaqueBackupError, "storage_policy_blocked"):
                    OpaqueBackupEngine(attestor=blocked_attestor).execute(request)

    def test_macos_attestor_rejects_cloud_components_and_git_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            cloud = root / "CloudStorage" / "opaque"
            attestor = MacOSStorageAttestor(volume_probe=lambda _: (True, True, "apfs"))
            cloud_result = attestor.attest(cloud, denied_roots=())
            self.assertFalse(cloud_result.non_synced)

            git = root / "repo"
            git.mkdir()
            (git / ".git").mkdir()
            git_result = attestor.attest(git / "opaque", denied_roots=())
            self.assertFalse(git_result.outside_git)

    def test_clone_tree_digest_detects_metadata_or_content_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)
            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

            target = next(path for path in result.clone_root.rglob("*") if path.is_file())
            target.write_bytes(b"modified")
            with self.assertRaisesRegex(OpaqueBackupError, "existing_snapshot_tampered"):
                OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

    def test_clone_permission_or_backup_sidecar_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)
            engine = OpaqueBackupEngine(attestor=StaticAttestor())
            result = engine.execute(request)

            clone_file = next(path for path in result.clone_root.rglob("*") if path.is_file())
            os.chmod(clone_file, 0o644)
            with self.assertRaisesRegex(OpaqueBackupError, "existing_snapshot_tampered"):
                engine.execute(request)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)
            engine = OpaqueBackupEngine(attestor=StaticAttestor())
            result = engine.execute(request)
            sidecar = result.backup_root / "snapshot.hmac"
            os.chmod(sidecar, 0o600)
            sidecar.write_text("0" * 64 + "\n", encoding="ascii")
            os.chmod(sidecar, 0o400)
            with self.assertRaisesRegex(OpaqueBackupError, "existing_snapshot_tampered"):
                engine.execute(request)

    def test_receipt_digests_have_expected_shape_and_match_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            self.populate_source(request.source_root)
            result = OpaqueBackupEngine(attestor=StaticAttestor()).execute(request)

            receipt = result.receipt
            self.assertEqual(hashlib.sha256(result.archive_path.read_bytes()).hexdigest(), receipt.archive_sha256)
            for digest in (
                receipt.snapshot_tree_sha256,
                receipt.clone_tree_sha256,
                receipt.archive_sha256,
                receipt.archive_hmac_sha256,
                receipt.receipt_hmac_sha256,
                receipt.source_path_sha256,
                receipt.storage_attestation_sha256,
            ):
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(receipt.snapshot_tree_sha256, receipt.clone_tree_sha256)


if __name__ == "__main__":
    unittest.main()
