"""Online SQLite backup with semantic integrity evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class DatabaseBackupReceipt:
    path: Path
    integrity_check: str
    foreign_key_violations: tuple[tuple[object, ...], ...]
    user_version: int
    row_counts: tuple[tuple[str, int], ...]
    id_digest: str


def _evidence(db: sqlite3.Connection) -> tuple[str, tuple[tuple[object, ...], ...], int, tuple[tuple[str, int], ...], str]:
    integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
    foreign = tuple(tuple(row) for row in db.execute("PRAGMA foreign_key_check"))
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts: list[tuple[str, int]] = []
    ids: list[str] = []
    for table in tables:
        quoted = table.replace('"', '""')
        counts.append((table, int(db.execute(f'SELECT count(*) FROM "{quoted}"').fetchone()[0])))
        columns = [row[1] for row in db.execute(f'PRAGMA table_info("{quoted}")')]
        if "id" in columns:
            ids.extend(f"{table}:{row[0]}" for row in db.execute(f'SELECT id FROM "{quoted}" ORDER BY id'))
    return integrity, foreign, version, tuple(counts), hashlib.sha256("\n".join(ids).encode()).hexdigest()


def online_backup(source: Path, destination: Path) -> DatabaseBackupReceipt:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        raise ValueError("backup destination must differ from source")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path) as source_db, sqlite3.connect(destination_path) as destination_db:
        source_db.backup(destination_db)
        evidence = _evidence(destination_db)
    if evidence[0] != "ok" or evidence[1]:
        raise ValueError("SQLite backup integrity failed")
    return DatabaseBackupReceipt(destination_path, *evidence)
