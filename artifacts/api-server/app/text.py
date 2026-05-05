"""Text utilities — direct port of src/lib/text.ts (tokenize, chunk, jaccard)."""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Iterable

try:
    import yake  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    yake = None

try:
    import tiktoken  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    tiktoken = None

_TIKTOKEN_ENC = None


def _get_encoder():
    """Return a token encoder if tiktoken is available, else None."""
    global _TIKTOKEN_ENC
    if tiktoken is None:
        return None
    if _TIKTOKEN_ENC is not None:
        return _TIKTOKEN_ENC
    name = os.environ.get("TIKTOKEN_ENCODING", "cl100k_base")
    try:
        _TIKTOKEN_ENC = tiktoken.get_encoding(name)
    except Exception:
        try:
            _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TIKTOKEN_ENC = None
    return _TIKTOKEN_ENC


def count_tokens(text: str) -> int:
    """Token count using tiktoken when available, else word count fallback."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is None:
        return len([w for w in re.split(r"\s+", text) if w])
    try:
        return len(enc.encode(text))
    except Exception:
        return len([w for w in re.split(r"\s+", text) if w])

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


# ---------------------------------------------------------------------------
# Structural / token-aware chunking
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(
    r"""(?xm)
    ^(?:
        \#{1,6}\s+\S.*$                |    # Markdown headings: # ... ######
        [A-Z0-9][^\n]{0,120}\n[=\-]{3,}$ |  # Setext h1/h2
        (?:[A-Z][A-Z0-9 \t\-_/&,]{4,80})$|  # ALL-CAPS section titles
        (?:\d+(?:\.\d+){0,3}\s+\S.*$)       # Numbered: "1. ", "1.2 ", "1.2.3 "
    )
    """
)

_SENT_SPLIT_RE = re.compile(r"(?<=[\.\?\!])\s+(?=[A-Z0-9\"\'\(\[])")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def _split_into_sentences(block: str) -> list[str]:
    block = block.strip()
    if not block:
        return []
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(block) if p.strip()]
    return parts or [block]


def _split_structural_blocks(text: str) -> list[dict]:
    """Split source text into structural blocks (heading sections, paragraphs,
    tables, code fences). Each block carries optional context (current heading
    path) so chunks can preserve it.
    """
    if not text:
        return []

    lines = text.splitlines()
    blocks: list[dict] = []
    heading_stack: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Code fence: keep intact, attach surrounding heading.
        if line.lstrip().startswith("```"):
            j = i + 1
            buf = [line]
            while j < len(lines):
                buf.append(lines[j])
                if lines[j].lstrip().startswith("```"):
                    j += 1
                    break
                j += 1
            blocks.append(
                {"kind": "code", "text": "\n".join(buf), "heading": " > ".join(heading_stack)}
            )
            i = j
            continue

        # Markdown table or pipe-table: keep contiguous lines together.
        if _TABLE_LINE_RE.match(line):
            j = i
            buf: list[str] = []
            while j < len(lines) and (_TABLE_LINE_RE.match(lines[j]) or not lines[j].strip()):
                if not lines[j].strip() and buf:
                    break
                if lines[j].strip():
                    buf.append(lines[j])
                j += 1
            if buf:
                blocks.append(
                    {"kind": "table", "text": "\n".join(buf), "heading": " > ".join(heading_stack)}
                )
            i = j
            continue

        stripped = line.rstrip()
        # ATX markdown heading.
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            heading_stack = heading_stack[: level - 1] + [title]
            blocks.append({"kind": "heading", "text": title, "level": level, "heading": " > ".join(heading_stack)})
            i += 1
            continue

        # Setext heading: a line followed by ====/----.
        if (
            i + 1 < len(lines)
            and re.match(r"^[=\-]{3,}\s*$", lines[i + 1])
            and stripped
        ):
            level = 1 if lines[i + 1].lstrip().startswith("=") else 2
            heading_stack = heading_stack[: level - 1] + [stripped]
            blocks.append({"kind": "heading", "text": stripped, "level": level, "heading": " > ".join(heading_stack)})
            i += 2
            continue

        # Paragraph: read until blank line.
        if stripped:
            buf = [stripped]
            j = i + 1
            while j < len(lines) and lines[j].strip():
                buf.append(lines[j].rstrip())
                j += 1
            blocks.append(
                {"kind": "paragraph", "text": " ".join(buf), "heading": " > ".join(heading_stack)}
            )
            i = j
            continue

        i += 1

    return blocks


def chunk_text(
    text: str,
    *,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
    target_words: int = 180,
    overlap_words: int = 30,
) -> list[str]:
    """Token-aware, structure-respecting chunker.

    Strategy:
      1. Split source into structural blocks (headings, paragraphs, tables, code).
      2. Greedily pack blocks into chunks up to ``target_tokens``.
      3. Never split a code fence or table; oversized paragraphs are split on
         sentence boundaries with token-bounded overlap.
      4. Each chunk is prefixed with its current heading path so retrieval
         keeps section context.

    ``target_words`` / ``overlap_words`` are used only when tiktoken is
    unavailable, preserving prior behavior.
    """
    text = text or ""
    if not text.strip():
        return []

    if target_tokens is None:
        target_tokens = int(os.environ.get("CHUNK_TARGET_TOKENS", "512"))
    if overlap_tokens is None:
        overlap_tokens = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "64"))

    has_encoder = _get_encoder() is not None
    if not has_encoder:
        return _legacy_word_chunks(text, target_words=target_words, overlap_words=overlap_words)

    blocks = _split_structural_blocks(text)
    if not blocks:
        return _legacy_word_chunks(text, target_words=target_words, overlap_words=overlap_words)

    chunks: list[str] = []
    cur_parts: list[str] = []
    cur_tokens = 0
    cur_heading = ""

    def _flush() -> None:
        nonlocal cur_parts, cur_tokens
        if not cur_parts:
            return
        body = "\n\n".join(cur_parts).strip()
        if not body:
            cur_parts = []
            cur_tokens = 0
            return
        prefix = f"[{cur_heading}]\n\n" if cur_heading else ""
        chunks.append(prefix + body)
        cur_parts = []
        cur_tokens = 0

    for block in blocks:
        kind = block["kind"]
        body = block["text"]
        heading = block.get("heading") or ""

        if kind == "heading":
            # Heading on its own does not produce a chunk; it primes future blocks.
            cur_heading = heading
            continue

        block_tokens = count_tokens(body)
        atomic = kind in {"code", "table"}

        if heading != cur_heading and cur_parts:
            _flush()
            cur_heading = heading
        elif not cur_heading:
            cur_heading = heading

        # Oversized paragraph → split on sentences. Atomic blocks stay intact.
        if block_tokens > target_tokens and not atomic:
            sentences = _split_into_sentences(body)
            sent_buf: list[str] = []
            sent_tok = 0
            for s in sentences:
                st = count_tokens(s)
                if sent_buf and sent_tok + st > target_tokens:
                    if cur_parts and cur_tokens + sent_tok > target_tokens:
                        _flush()
                    cur_parts.append(" ".join(sent_buf))
                    cur_tokens += sent_tok
                    if cur_tokens >= target_tokens:
                        _flush()
                    # carry overlap
                    overlap_buf: list[str] = []
                    overlap_tok = 0
                    for s2 in reversed(sent_buf):
                        t2 = count_tokens(s2)
                        if overlap_tok + t2 > overlap_tokens:
                            break
                        overlap_buf.insert(0, s2)
                        overlap_tok += t2
                    sent_buf = overlap_buf
                    sent_tok = overlap_tok
                sent_buf.append(s)
                sent_tok += st
            if sent_buf:
                if cur_parts and cur_tokens + sent_tok > target_tokens:
                    _flush()
                cur_parts.append(" ".join(sent_buf))
                cur_tokens += sent_tok
                if cur_tokens >= target_tokens:
                    _flush()
            continue

        # Normal pack-or-flush.
        if cur_tokens + block_tokens > target_tokens and cur_parts:
            _flush()

        if atomic and block_tokens > target_tokens:
            # Atomic block bigger than budget: keep it whole on its own chunk.
            cur_parts.append(body)
            _flush()
            continue

        cur_parts.append(body)
        cur_tokens += block_tokens

    _flush()
    return [c for c in chunks if c.strip()]


def _legacy_word_chunks(text: str, *, target_words: int, overlap_words: int) -> list[str]:
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
