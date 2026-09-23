from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ArtifactManifest:
    source_id: str
    revision: str
    license_id: str
    model_card_digest: str
    source_revision_digest: str
    quantizer: str
    runtime_version: str
    file_size: int
    artifact_sha256: str
    official_source: bool


@dataclass(frozen=True)
class ProvenanceResult:
    status: str
    code: str


def verify_artifact_provenance(manifest: ArtifactManifest) -> ProvenanceResult:
    digest = re.compile(r"^[0-9a-f]{64}$")
    complete = (
        len(manifest.revision) == 40 and manifest.license_id and manifest.quantizer
        and manifest.runtime_version and manifest.file_size > 0
        and all(digest.fullmatch(value) for value in (manifest.model_card_digest, manifest.source_revision_digest, manifest.artifact_sha256))
    )
    if not manifest.official_source:
        return ProvenanceResult("REJECT_PRIMARY", "third-party-derivative")
    return ProvenanceResult("CLEAR", "official-artifact-bound") if complete else ProvenanceResult("BLOCKED", "provenance-incomplete")
