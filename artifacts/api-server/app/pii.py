"""PII detection / masking — direct port of src/lib/pii.ts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Tuple


@dataclass
class PiiFinding:
    type: str
    value: str
    replacement: str


_PATTERNS: List[Tuple[str, re.Pattern[str], Callable[[str], str]]] = [
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), lambda _: "[REDACTED_EMAIL]"),
    (
        "phone",
        re.compile(r"(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"),
        lambda _: "[REDACTED_PHONE]",
    ),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), lambda _: "[REDACTED_SSN]"),
    ("credit_card", re.compile(r"\b(?:\d[ \-]*?){13,16}\b"), lambda _: "[REDACTED_CC]"),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), lambda _: "[REDACTED_IP]"),
]


def detect_and_mask_pii(text: str) -> tuple[str, list[PiiFinding]]:
    cleaned = text
    findings: list[PiiFinding] = []
    for kind, regex, mask in _PATTERNS:
        def _replace(m: re.Match[str], _kind: str = kind, _mask: Callable[[str], str] = mask) -> str:
            match = m.group(0)
            if _kind == "credit_card":
                digits = re.sub(r"[\s\-]", "", match)
                if len(digits) < 13 or len(digits) > 19:
                    return match
            replacement = _mask(match)
            findings.append(PiiFinding(type=_kind, value=match, replacement=replacement))
            return replacement

        cleaned = regex.sub(_replace, cleaned)
    return cleaned, findings
