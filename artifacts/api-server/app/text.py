"""Text utilities — direct port of src/lib/text.ts (tokenize, chunk, jaccard)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

try:
    import yake  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    yake = None

_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with", "is", "are",
    "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "this",
    "that", "these", "those", "i", "you", "he", "she", "it", "we", "they", "them", "their",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "as", "at", "by",
    "if", "not", "no", "yes", "so", "than", "then", "there", "here", "into", "from", "about",
    "can", "could", "will", "would", "should", "may", "might", "just", "also", "very",
}


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9\s']", " ", lowered)
    return [t for t in re.split(r"\s+", cleaned) if len(t) > 1 and t not in _STOP]


def term_frequency(tokens: Iterable[str]) -> dict[str, int]:
    return dict(Counter(tokens))


def top_keywords(tf: dict[str, int], k: int = 10) -> list[str]:
    return [w for w, _ in sorted(tf.items(), key=lambda kv: kv[1], reverse=True)[:k]]


def top_key_phrases(text: str, k: int = 6) -> list[str]:
    """Return top multi-word phrases using YAKE (fallback: 2-3 grams)."""
    if k <= 0:
        return []

    if yake is not None:
        try:
            extractor = yake.KeywordExtractor(lan="en", n=3, dedupLim=0.9, top=max(24, k * 4))
            seen: set[str] = set()
            out: list[str] = []
            for kw, _ in extractor.extract_keywords(text):
                phrase = " ".join(str(kw).strip().lower().split())
                if len(phrase.split()) < 2:
                    continue
                if phrase in seen:
                    continue
                seen.add(phrase)
                out.append(phrase)
                if len(out) >= k:
                    break
            if out:
                return out
        except Exception:
            pass

    return _fallback_key_phrases(text, k)


def _fallback_key_phrases(text: str, k: int) -> list[str]:
    tokens = tokenize(text)
    if len(tokens) < 2:
        return []

    counts: Counter[str] = Counter()
    for n in (3, 2):
        for i in range(len(tokens) - n + 1):
            phrase = " ".join(tokens[i : i + n])
            counts[phrase] += 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [p for p, _ in ranked[:k]]


def chunk_text(text: str, target_words: int = 180, overlap_words: int = 30) -> list[str]:
    words = [w for w in re.split(r"\s+", text) if w]
    if not words:
        return []
    chunks: list[str] = []
    i = 0
    while i < len(words):
        end = min(i + target_words, len(words))
        chunks.append(" ".join(words[i:end]))
        if end == len(words):
            break
        i = end - overlap_words
        if i < 0:
            i = 0
    return chunks


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a) + len(b) - inter
    return 0.0 if union == 0 else inter / union
