"""Deterministic ownership routing above model-proposed classifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .envelope import Owner, PrivacyClass


@dataclass(frozen=True)
class IntakeRequest:
    request_id: str
    requester_persona: Owner
    declared_domains: tuple[str, ...]
    contains_private_context: bool
    text: str
    local_payload_ref: str | None
    accepted_owner: Owner | None = None


@dataclass(frozen=True)
class RoutingPolicy:
    version: str = "nate-os-routing-v1"


@dataclass(frozen=True)
class Classification:
    task_class: str
    owner: Owner
    privacy_class: PrivacyClass
    safe_payload: Mapping[str, str]
    local_payload_ref: str | None
    evidence_code: str
    clarification_required: bool = False


@dataclass(frozen=True)
class RoutedChild:
    task_id: str
    parent_task_id: str
    owner: Owner
    task_class: str


def classify_request(request: IntakeRequest, policy: RoutingPolicy) -> Classification:
    del policy
    domains = frozenset(request.declared_domains)
    if request.accepted_owner == Owner.CODEX:
        return Classification("work", Owner.CODEX, PrivacyClass.SANITIZED_CLOUD, {"request": request.text}, request.local_payload_ref, "accepted-codex-owner")
    if not domains:
        return Classification("ambiguous", request.requester_persona, PrivacyClass.LOCAL_PRIVATE if request.requester_persona == Owner.ERNIE else PrivacyClass.SANITIZED_CLOUD, {}, request.local_payload_ref, "bounded-clarification", True)
    if domains == {"work"}:
        payload = {"request": "private work request; use local reference"} if request.contains_private_context else {"request": request.text}
        return Classification("work", Owner.CODEX, PrivacyClass.SANITIZED_CLOUD, payload, request.local_payload_ref, "declared-work-owner")
    if domains == {"personal"}:
        privacy = PrivacyClass.LOCAL_PRIVATE if request.requester_persona == Owner.ERNIE else PrivacyClass.SANITIZED_CLOUD
        return Classification("personal", request.requester_persona, privacy, {"request": request.text}, request.local_payload_ref, "personal-persona-owner")
    if domains == {"work", "personal"}:
        return Classification("mixed", request.requester_persona, PrivacyClass.SANITIZED_CLOUD, {"request": request.text}, request.local_payload_ref, "mixed-decomposition")
    return Classification("ambiguous", request.requester_persona, PrivacyClass.SANITIZED_CLOUD, {}, request.local_payload_ref, "unsupported-domain", True)


def decompose_mixed(request: IntakeRequest, classification: Classification) -> tuple[RoutedChild, ...]:
    if classification.task_class != "mixed":
        raise ValueError("only mixed requests can be decomposed")
    return (
        RoutedChild(f"{request.request_id}:work", request.request_id, Owner.CODEX, "work"),
        RoutedChild(f"{request.request_id}:personal", request.request_id, request.requester_persona, "personal"),
    )
