"""Outbound data policy for Ernie-to-Bert handoffs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


BASE_RULES = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "password": r"\b(?:password|passphrase|pwd)\s*[:=]\s*[^\s,;]+",
    "crypto-secret": r"\b(?:private key|seed phrase|mnemonic|wallet key)\s*[:=]?\s*[^\n]+",
    "financial": r"\b(?:\d[ -]*?){13,19}\b|\b(?:routing|account)\s*(?:number|no\.?)[\s:=]+[A-Za-z0-9-]+",
    "name": r"\b[A-Z][a-z]{1,}\s+[A-Z][a-z]{1,}\b",
}


@dataclass(frozen=True)
class SanitizedContent:
    content: str
    finding_kinds: set[str]


class DataPolicy:
    """Conservative sanitizer; raw content must never cross the local boundary."""

    def __init__(self, rules: Iterable[tuple[str, str]] | None = None):
        self._rules = list(BASE_RULES.items())
        if rules:
            self._rules.extend(rules)

    def sanitize(self, content: str) -> SanitizedContent:
        sanitized = content
        findings: set[str] = set()
        for kind, pattern in self._rules:
            flags = 0 if kind == "name" else re.IGNORECASE
            sanitized, replacements = re.subn(
                pattern,
                f"[REDACTED:{kind}]",
                sanitized,
                flags=flags,
            )
            if replacements:
                findings.add(kind)
        return SanitizedContent(content=sanitized, finding_kinds=findings)
