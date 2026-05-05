"""PII detection / masking — direct port of src/lib/pii.ts."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, List, Tuple

try:
    import spacy  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    spacy = None


@dataclass
class PiiFinding:
    type: str
    value: str
    replacement: str
    detector: str = "regex"
    confidence: float = 0.99


_PATTERNS: List[Tuple[str, re.Pattern[str], Callable[[str], str]]] = [
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), lambda _: "<EMAIL>"),
    (
        "phone",
        re.compile(r"(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"),
        lambda _: "<PHONE>",
    ),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), lambda _: "<SSN>"),
    ("credit_card", re.compile(r"\b(?:\d[ \-]*?){13,16}\b"), lambda _: "<CARD_NUMBER>"),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), lambda _: "<IP_ADDRESS>"),
]

_NER_ENTITY_MAP: dict[str, tuple[str, str, float]] = {
    "PERSON": ("person_name", "<PERSON_NAME>", 0.72),
    "GPE": ("location", "<LOCATION>", 0.66),
    "LOC": ("location", "<LOCATION>", 0.66),
    "FAC": ("location", "<LOCATION>", 0.62),
}


def _looks_like_placeholder(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def _load_spacy_nlp():
    if spacy is None:
        return None
    model = os.environ.get("PII_NER_MODEL", "en_core_web_sm")
    try:
        return spacy.load(model)
    except Exception:
        return None


_SPACY_NLP = _load_spacy_nlp()


def _apply_regex_masks(text: str) -> tuple[str, list[PiiFinding]]:
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
            findings.append(
                PiiFinding(
                    type=_kind,
                    value=match,
                    replacement=replacement,
                    detector="regex",
                    confidence=0.99,
                )
            )
            return replacement

        cleaned = regex.sub(_replace, cleaned)
    return cleaned, findings


def _apply_spacy_ner_masks(text: str) -> tuple[str, list[PiiFinding]]:
    if _SPACY_NLP is None:
        return text, []

    try:
        doc = _SPACY_NLP(text)
    except Exception:
        return text, []

    spans: list[tuple[int, int, PiiFinding]] = []
    for ent in doc.ents:
        cfg = _NER_ENTITY_MAP.get(ent.label_)
        if not cfg:
            continue
        pii_type, replacement, confidence = cfg
        value = ent.text.strip()
        if len(value) < 3 or _looks_like_placeholder(value):
            continue
        spans.append(
            (
                ent.start_char,
                ent.end_char,
                PiiFinding(
                    type=pii_type,
                    value=value,
                    replacement=replacement,
                    detector="ner:spacy",
                    confidence=confidence,
                ),
            )
        )

    if not spans:
        return text, []

    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    non_overlapping: list[tuple[int, int, PiiFinding]] = []
    for start, end, finding in spans:
        if any(not (end <= s or start >= e) for s, e, _ in non_overlapping):
            continue
        non_overlapping.append((start, end, finding))

    if not non_overlapping:
        return text, []

    cleaned = text
    findings = [f for _, _, f in non_overlapping]
    for start, end, finding in sorted(non_overlapping, key=lambda s: s[0], reverse=True):
        cleaned = cleaned[:start] + finding.replacement + cleaned[end:]
    return cleaned, findings


def detect_and_mask_pii(text: str) -> tuple[str, list[PiiFinding]]:
    cleaned, regex_findings = _apply_regex_masks(text)
    cleaned, ner_findings = _apply_spacy_ner_masks(cleaned)
    return cleaned, [*regex_findings, *ner_findings]
