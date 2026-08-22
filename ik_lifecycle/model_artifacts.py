"""Provenance and integrity gates for isolated offline model artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Iterable


_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_OFFICIAL_SOURCE = "Qwen/Qwen3.8-27B"
_PROVENANCE_QUANTIZATION = "ggml-org/Qwen3.8-27B-GGUF"


class ModelArtifactError(RuntimeError):
    """A deliberately non-sensitive, fail-closed artifact error."""


@dataclass(frozen=True)
class ArtifactFile:
    filename: str
    size: int
    sha256: str
    role: str


@dataclass(frozen=True)
class ArtifactSpec:
    source_repository: str
    source_revision: str
    artifact_repository: str
    artifact_revision: str
    license_id: str
    model_card_sha256: str
    license_sha256: str
    config_sha256: str
    chat_template_sha256: str
    source_link_sha256: str
    converter_log_sha256: str
    quantizer_revision: str
    runtime_id: str
    runtime_sha256: str
    files: tuple[ArtifactFile, ...]


def _safe_filename(value: str) -> bool:
    path = Path(value)
    return bool(value) and path.name == value and value not in {".", ".."} and not path.is_absolute()


def validate_artifact_spec(spec: ArtifactSpec) -> str:
    if spec.source_repository != _OFFICIAL_SOURCE:
        raise ModelArtifactError("source_repository_ineligible")
    if spec.artifact_repository != _PROVENANCE_QUANTIZATION:
        raise ModelArtifactError("artifact_repository_ineligible")
    if not _HEX40.fullmatch(spec.source_revision):
        raise ModelArtifactError("source_revision_invalid")
    if not _HEX40.fullmatch(spec.artifact_revision):
        raise ModelArtifactError("artifact_revision_invalid")
    if spec.license_id.lower() != "apache-2.0":
        raise ModelArtifactError("license_ineligible")
    digests = (
        spec.model_card_sha256,
        spec.license_sha256,
        spec.config_sha256,
        spec.chat_template_sha256,
        spec.source_link_sha256,
        spec.converter_log_sha256,
        spec.runtime_sha256,
    )
    if any(not _HEX64.fullmatch(value) for value in digests) or not _HEX40.fullmatch(spec.quantizer_revision):
        raise ModelArtifactError("provenance_digest_invalid")
    roles = {artifact.role for artifact in spec.files}
    if not {"model", "projector"}.issubset(roles):
        raise ModelArtifactError("required_roles_missing")
    if len({artifact.filename for artifact in spec.files}) != len(spec.files):
        raise ModelArtifactError("artifact_filename_duplicate")
    for artifact in spec.files:
        if not _safe_filename(artifact.filename):
            raise ModelArtifactError("artifact_filename_invalid")
        if artifact.size <= 0 or not _HEX64.fullmatch(artifact.sha256):
            raise ModelArtifactError("artifact_metadata_invalid")
    return "CLEAR"


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _symlink_in_existing_ancestry(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    while True:
        if candidate.is_symlink():
            return True
        if candidate == candidate.parent:
            return False
        candidate = candidate.parent


def prepare_artifact_root(root: Path, *, forbidden_roots: Iterable[Path]) -> Path:
    requested = Path(root)
    if not requested.is_absolute():
        raise ModelArtifactError("artifact_root_not_absolute")
    if _symlink_in_existing_ancestry(requested):
        raise ModelArtifactError("artifact_root_symlink")
    resolved = requested.resolve(strict=False)
    for forbidden in forbidden_roots:
        blocked = Path(forbidden).resolve(strict=False)
        if _within(resolved, blocked) or _within(blocked, resolved):
            raise ModelArtifactError("artifact_root_forbidden")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    if resolved.is_symlink() or resolved.owner() != Path.home().owner():
        raise ModelArtifactError("artifact_root_ownership_invalid")
    os.chmod(resolved, 0o700)
    if stat.S_IMODE(resolved.stat().st_mode) != 0o700:
        raise ModelArtifactError("artifact_root_permissions_invalid")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact_files(root: Path, spec: ArtifactSpec) -> dict[str, object]:
    validate_artifact_spec(spec)
    base = Path(root).resolve(strict=True)
    files: list[dict[str, object]] = []
    for artifact in spec.files:
        path = base / artifact.filename
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ModelArtifactError("artifact_integrity_failed") from error
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise ModelArtifactError("artifact_integrity_failed")
        if metadata.st_size != artifact.size or _sha256_file(path) != artifact.sha256:
            raise ModelArtifactError("artifact_integrity_failed")
        if stat.S_IMODE(metadata.st_mode) & 0o277:
            raise ModelArtifactError("artifact_permissions_invalid")
        files.append({"role": artifact.role, "size": artifact.size, "sha256": artifact.sha256})
    return {
        "schema_id": "ik.hermes.model-artifact-integrity.v1",
        "status": "CLEAR",
        "source_revision": spec.source_revision,
        "artifact_revision": spec.artifact_revision,
        "runtime_sha256": spec.runtime_sha256,
        "files": files,
        "aggregate_bytes": sum(artifact.size for artifact in spec.files),
    }


def _clone_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        completed = subprocess.run(
            ("/bin/cp", "-c", os.fspath(source), os.fspath(temporary)),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ModelArtifactError("baseline_clone_failed") from error
    if completed.returncode != 0:
        raise ModelArtifactError("baseline_clone_failed")
    os.chmod(temporary, 0o400)
    os.replace(temporary, destination)


def clone_ollama_model(
    source_store: Path,
    destination_store: Path,
    model: str,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    """Clone one content-addressed Ollama model without reading prompt payloads."""

    if not re.fullmatch(r"[A-Za-z0-9._-]+:[A-Za-z0-9._-]+", model):
        raise ModelArtifactError("baseline_model_id_invalid")
    if not _HEX64.fullmatch(expected_manifest_sha256):
        raise ModelArtifactError("baseline_manifest_digest_invalid")
    name, tag = model.split(":", 1)
    source_root = Path(source_store).resolve(strict=True)
    destination_root = prepare_artifact_root(Path(destination_store), forbidden_roots=(source_root,))
    manifest = source_root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if manifest.is_symlink() or not manifest.is_file() or _sha256_file(manifest) != expected_manifest_sha256:
        raise ModelArtifactError("baseline_manifest_integrity_failed")
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelArtifactError("baseline_manifest_invalid") from error
    entries = [document.get("config"), *document.get("layers", [])]
    blobs: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ModelArtifactError("baseline_manifest_invalid")
        digest = entry.get("digest")
        size = entry.get("size")
        if not isinstance(digest, str) or not digest.startswith("sha256:") or not _HEX64.fullmatch(digest[7:]) or not isinstance(size, int) or size <= 0:
            raise ModelArtifactError("baseline_manifest_invalid")
        blobs[digest[7:]] = size
    total = 0
    for digest, expected_size in sorted(blobs.items()):
        source_blob = source_root / "blobs" / f"sha256-{digest}"
        if source_blob.is_symlink() or not source_blob.is_file():
            raise ModelArtifactError("baseline_blob_integrity_failed")
        if source_blob.stat().st_size != expected_size or _sha256_file(source_blob) != digest:
            raise ModelArtifactError("baseline_blob_integrity_failed")
        destination_blob = destination_root / "blobs" / f"sha256-{digest}"
        if destination_blob.exists():
            if destination_blob.is_symlink() or destination_blob.stat().st_size != expected_size or _sha256_file(destination_blob) != digest:
                raise ModelArtifactError("baseline_destination_tamper")
        else:
            _clone_file(source_blob, destination_blob)
        total += expected_size
    destination_manifest = destination_root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if destination_manifest.exists():
        if _sha256_file(destination_manifest) != expected_manifest_sha256:
            raise ModelArtifactError("baseline_destination_tamper")
    else:
        _clone_file(manifest, destination_manifest)
    return {
        "status": "CLEAR",
        "model": model,
        "manifest_sha256": expected_manifest_sha256,
        "blob_count": len(blobs),
        "aggregate_bytes": total,
    }
