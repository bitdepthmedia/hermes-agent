"""Authenticated transport contract with a deterministic loopback fixture."""

from __future__ import annotations

from dataclasses import dataclass

from .envelope import DelegationEnvelope, Owner, canonical_digest


@dataclass(frozen=True)
class TransportAck:
    task_id: str
    sequence: int
    envelope_digest: str
    recipient: Owner
    status: str


class LoopbackTransport:
    def __init__(self, *, available: bool, authenticated_sender: Owner, recipient: Owner) -> None:
        self.available = available
        self.authenticated_sender = authenticated_sender
        self.recipient = recipient
        self._seen: set[str] = set()

    def deliver(self, envelope: DelegationEnvelope) -> TransportAck:
        if not self.available:
            raise ConnectionError("recipient_unavailable")
        if envelope.owner != self.recipient or self.authenticated_sender == self.recipient:
            raise ValueError("transport sender or recipient binding invalid")
        digest = canonical_digest(envelope)
        if digest in self._seen:
            raise ValueError("transport replay rejected")
        self._seen.add(digest)
        return TransportAck(envelope.task_id, int(envelope.integrity["sequence"]), digest, self.recipient, "acknowledged")
