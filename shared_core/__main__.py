"""Run the local Shared Core service."""

from __future__ import annotations

import os
from pathlib import Path

from .core import SharedCore
from .server import create_server


def main() -> None:
    database_path = Path(
        os.getenv(
            "SHARED_CORE_DB",
            "/Users/react/Library/Application Support/ik-agents/shared-core/shared-core.db",
        )
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    port = int(os.getenv("SHARED_CORE_PORT", "8730"))
    create_server(SharedCore(database_path), port=port).serve_forever()


if __name__ == "__main__":
    main()
