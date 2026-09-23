from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ik_lifecycle.migration import migrate_profile
from ik_lifecycle.profile_inventory import InventoryPolicy, inventory_profile
from ik_lifecycle.semantic_validation import ContinuityCases, validate_semantics
from ik_lifecycle.sqlite_backup import online_backup


class ContinuityMigrationTests(unittest.TestCase):
    def test_inventory_is_non_secret_and_migration_is_to_a_clone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "persona.md").write_text("Synthetic Persona", encoding="utf-8")
            (source / ".env").write_text("TOKEN=SYNTHETIC_PRIVATE_CANARY", encoding="utf-8")
            (source / "credentials.json").write_text("SYNTHETIC_PRIVATE_CANARY", encoding="utf-8")
            inventory = inventory_profile(source, InventoryPolicy())
            rendered = json.dumps(inventory.to_dict())
            self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", rendered)
            self.assertEqual(inventory.excluded_paths, (".env", "credentials.json"))

            destination = root / "clone"
            migrated = migrate_profile(source, destination)
            self.assertTrue((destination / "persona.md").is_file())
            self.assertFalse((destination / ".env").exists())
            self.assertEqual((source / "persona.md").read_text(), "Synthetic Persona")
            self.assertNotEqual(migrated.source_root, migrated.destination_root)

    def test_online_backup_and_semantic_validation_prove_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sessions.sqlite"
            with sqlite3.connect(source) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("CREATE TABLE task(id TEXT PRIMARY KEY, owner TEXT, status TEXT, provenance TEXT)")
                db.execute("INSERT INTO task VALUES('t1','codex','completed','fixture')")
            receipt = online_backup(source, root / "backup.sqlite")
            self.assertEqual((receipt.integrity_check, receipt.foreign_key_violations), ("ok", ()))
            gates = validate_semantics(receipt, receipt, ContinuityCases(required_ids=("t1",)))
            self.assertEqual(gates.status, "CLEAR")


if __name__ == "__main__":
    unittest.main()
