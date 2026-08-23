"""Space-safe immutable tree copies for staged release artifacts."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

from .models import LifecycleBlockedError


def copy_immutable_tree(source: Path, destination: Path, *, materialize_symlinks: bool) -> None:
    """Copy one tree, using independent APFS copy-on-write clones on macOS."""

    if sys.platform != "darwin":
        shutil.copytree(source, destination, symlinks=not materialize_symlinks)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = "-cRL" if materialize_symlinks else "-cR"
    completed = subprocess.run(
        ("/bin/cp", flags, str(source), str(destination)),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise LifecycleBlockedError(
            "immutable_clone_failed",
            "immutable tree could not be copied with macOS copy-on-write",
        )
