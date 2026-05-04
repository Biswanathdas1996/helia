"""Text utilities — direct port of src/lib/text.ts (tokenize, chunk, jaccard)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

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
