"""Agentic Hybrid RAG retrieval pipeline.

Stages, in order:

1. **Query rewrite + intent detection** — ask the LLM for a search-friendly
   rewrite and a coarse intent label. Falls back to the raw query if the
   LLM call fails.
2. **Hybrid retrieval** — BM25 (Mongo ``$text``) ∪ semantic (Atlas Vector
   Search). Skips the vector leg when embeddings or vector search are not
   configured.
3. **Chunk dedup** — drop near-duplicates by Jaccard token similarity to
   keep the context window dense.
4. **Reranking** — reciprocal-rank fusion across the two retrieval legs,
   with an optional LLM rerank pass on the top-N candidates.
5. **Context assembly** — top-K survivors, formatted with citation markers
   for the answer-generation prompt.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app import embeddings, llm
from app.text import jaccard, top_key_phrases, tokenize

log = logging.getLogger("api-server.retrieval")

_RRF_K = 60
_DEDUP_JACCARD = 0.85
_BM25_LIMIT = 12
_VECTOR_LIMIT = 12
_RERANK_LIMIT = 8
_FINAL_K = 5


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_name: str
    content: str
    score: float
    position: int | None = None
    token_count: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_citation(self) -> dict[str, object]:
        return {
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            "documentName": self.document_name,
            "snippet": self.content[:280],
            "score": round(float(self.score), 3),
            "metadata": self.metadata,
        }


@dataclass
class RetrievalResult:
    rewritten_query: str
    intent: str
    chunks: list[RetrievedChunk]

    def context_block(self) -> str:
        if not self.chunks:
            return "(no documents indexed yet)"
        return "\n\n".join(
            f"[{i + 1}] ({c.document_name}) {c.content}"
            for i, c in enumerate(self.chunks)
        )

    def citations(self) -> list[dict[str, object]]:
        return [c.to_citation() for c in self.chunks]


async def retrieve(
    db: AsyncIOMotorDatabase,
    query: str,
    *,
    tenant_id: str | None = None,
    pre_rewritten: str | None = None,
    pre_intent: str | None = None,
) -> RetrievalResult:
    if pre_rewritten:
        rewritten, intent = pre_rewritten, (pre_intent or "general")
    else:
        rewritten, intent = await _rewrite_query(query)
    search_query = rewritten or query

    doc_query: dict[str, object] = {"status": "approved"}
    if tenant_id:
        doc_query["tenantId"] = tenant_id
    approved = await db.documents.find(
        doc_query, {"_id": 1, "name": 1, "sourceType": 1, "governance": 1}
    ).to_list(length=None)
    doc_by_id: dict[int, dict[str, object]] = {d["_id"]: d for d in approved}
    approved_ids = list(doc_by_id.keys())
    if not approved_ids:
        return RetrievalResult(rewritten_query=search_query, intent=intent, chunks=[])

    bm25_hits, vector_hits = await _hybrid_search(db, search_query, approved_ids, tenant_id=tenant_id)
    fused = _reciprocal_rank_fusion(bm25_hits, vector_hits)
    deduped = _dedup_by_jaccard(fused)
    top = deduped[:_RERANK_LIMIT]
    # Prefer Cohere/cross-encoder reranker if configured; fall back to LLM rerank.
    reranked = await _external_rerank(search_query, top)
    if reranked is None:
        reranked = await _llm_rerank(search_query, top)
    final = reranked[:_FINAL_K]

    return RetrievalResult(
        rewritten_query=search_query,
        intent=intent,
        chunks=[
            RetrievedChunk(
                chunk_id=c["_id"],
                document_id=c["documentId"],
                document_name=str(
                    (doc_by_id.get(c["documentId"]) or {}).get("name") or "Untitled"
                ),
                content=c["content"],
                score=float(c.get("score") or 0.0),
                position=int(c["position"]) if isinstance(c.get("position"), int) else None,
                token_count=int(c["tokenCount"]) if isinstance(c.get("tokenCount"), int) else None,
                metadata=_chunk_metadata(c, doc_by_id.get(c["documentId"])),
            )
            for c in final
        ],
    )


def _chunk_metadata(row: dict, doc: dict[str, object] | None) -> dict[str, object]:
    raw = row.get("metadata")
    existing = dict(raw) if isinstance(raw, dict) else {}

    document_name = str((doc or {}).get("name") or existing.get("fileName") or "Untitled")
    source_type = (doc or {}).get("sourceType") or existing.get("sourceType")

    position = row.get("position")
    if not isinstance(position, int):
        position = existing.get("chunkPosition") if isinstance(existing.get("chunkPosition"), int) else None

    token_count = row.get("tokenCount")
    if not isinstance(token_count, int):
        token_count = existing.get("tokenCount") if isinstance(existing.get("tokenCount"), int) else None

    page_number = existing.get("pageNumber")
    if not isinstance(page_number, int):
        page_number = (position + 1) if isinstance(position, int) else None

    key_phrases_raw = existing.get("keyPhrases")
    content_text = str(row.get("content") or "")
    if isinstance(key_phrases_raw, list):
        key_phrases = [str(k).strip() for k in key_phrases_raw if str(k).strip() and " " in str(k).strip()]
    else:
        key_phrases = []
    if not key_phrases:
        key_phrases = top_key_phrases(content_text, 6)

    metadata: dict[str, object] = {
        "fileName": document_name,
        "pageNumber": page_number,
        "keyPhrases": key_phrases,
        "chunkPosition": position,
        "tokenCount": token_count,
        "sourceType": source_type if isinstance(source_type, str) else None,
    }
    for k, v in existing.items():
        if k not in metadata:
            metadata[k] = v
    return metadata


# ---------------------------------------------------------------------------
# Stage 1 — query rewriting
# ---------------------------------------------------------------------------

async def _rewrite_query(query: str) -> tuple[str, str]:
    """Return (rewritten, intent). Falls back to (query, "general") on error."""
    if os.environ.get("DISABLE_QUERY_REWRITE", "").lower() in {"1", "true", "yes"}:
        return query, "general"
    sys_prompt = (
        "You optimise customer support questions for retrieval over a knowledge base. "
        'Reply as JSON: {"rewritten": "<keyword-rich rewrite>", "intent": "<one of: '
        'how_to | troubleshooting | billing | account | policy | general>"}. '
        "Strip pleasantries. Keep proper nouns. No more than 25 words in the rewrite."
    )
    try:
        raw = await llm.chat(
            [{"role": "system", "content": sys_prompt}, {"role": "user", "content": query}],
            json_mode=True,
            temperature=0.0,
            max_tokens=200,
        )
        obj = json.loads(raw)
        rewritten = (obj.get("rewritten") or "").strip() or query
        intent = (obj.get("intent") or "general").strip() or "general"
        return rewritten, intent
    except Exception as err:
        log.debug("query rewrite failed, using raw query: %s", err)
        return query, "general"


# ---------------------------------------------------------------------------
# Stage 2 — hybrid search
# ---------------------------------------------------------------------------

async def _hybrid_search(
    db: AsyncIOMotorDatabase,
    query: str,
    approved_ids: list[int],
    *,
    tenant_id: str | None = None,
) -> tuple[list[dict], list[dict]]:
    bm25 = await _bm25(db, query, approved_ids, tenant_id=tenant_id)
    vec: list[dict] = []
    if embeddings.vector_search_enabled():
        try:
            embs = await embeddings.embed_batch([query])
            if embs:
                vec = await embeddings.vector_search(
                    db, embs[0], limit=_VECTOR_LIMIT, doc_ids=approved_ids, tenant_id=tenant_id
                )
        except Exception as err:
            log.warning("vector search failed, falling back to BM25 only: %s", err)
            vec = []
    return bm25, vec


async def _bm25(
    db: AsyncIOMotorDatabase,
    query: str,
    approved_ids: list[int],
    *,
    tenant_id: str | None = None,
) -> list[dict]:
    base: dict[str, object] = {"documentId": {"$in": approved_ids}}
    if tenant_id:
        base["tenantId"] = tenant_id
    try:
        cursor = (
            db.chunks.find(
                {**base, "$text": {"$search": query}},
                {
                    "_id": 1,
                    "documentId": 1,
                    "position": 1,
                    "content": 1,
                    "tokenCount": 1,
                    "metadata": 1,
                    "score": {"$meta": "textScore"},
                },
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(_BM25_LIMIT)
        )
        rows = await cursor.to_list(length=_BM25_LIMIT)
    except Exception as err:
        log.warning("$text search failed: %s", err)
        rows = []
    if rows:
        return rows
    fallback = (
        await db.chunks.find(base)
        .sort("_id", -1)
        .limit(_BM25_LIMIT)
        .to_list(length=_BM25_LIMIT)
    )
    return [{**c, "score": 0.0} for c in fallback]


# ---------------------------------------------------------------------------
# Optional Cohere / cross-encoder reranker
# ---------------------------------------------------------------------------

async def _external_rerank(query: str, rows: list[dict]) -> list[dict] | None:
    """Call Cohere Rerank if ``COHERE_API_KEY`` is set. Returns reordered rows
    or ``None`` to indicate the LLM-fallback path should run.
    """
    if len(rows) <= 1:
        return rows
    api_key = os.environ.get("COHERE_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("COHERE_RERANK_MODEL", "rerank-english-v3.0")
    try:
        import httpx  # local import; httpx is already a dep

        body = {
            "model": model,
            "query": query,
            "documents": [r.get("content", "") for r in rows],
            "top_n": min(len(rows), _RERANK_LIMIT),
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                "https://api.cohere.com/v2/rerank",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        results = data.get("results") or []
        out: list[dict] = []
        seen: set[int] = set()
        for item in results:
            idx = item.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(rows) or idx in seen:
                continue
            seen.add(idx)
            score = float(item.get("relevance_score") or 0.0)
            row = {**rows[idx], "score": score}
            out.append(row)
        for i, r in enumerate(rows):
            if i not in seen:
                out.append(r)
        return out
    except Exception as err:
        log.warning("cohere rerank failed, falling back to LLM rerank: %s", err)
        return None


# ---------------------------------------------------------------------------
# Stage 3/4 — fusion + dedup + rerank
# ---------------------------------------------------------------------------

def _reciprocal_rank_fusion(*lists: Iterable[dict]) -> list[dict]:
    fused: dict[int, dict] = {}
    for lst in lists:
        for rank, row in enumerate(lst):
            cid = row["_id"]
            existing = fused.get(cid)
            contribution = 1.0 / (_RRF_K + rank + 1)
            if existing:
                existing["score"] = float(existing["score"]) + contribution
            else:
                copy = {**row, "score": contribution}
                fused[cid] = copy
    return sorted(fused.values(), key=lambda r: r["score"], reverse=True)


def _dedup_by_jaccard(rows: list[dict]) -> list[dict]:
    kept: list[dict] = []
    kept_sets: list[set[str]] = []
    for r in rows:
        s = set(tokenize(r["content"]))
        if any(jaccard(s, k) >= _DEDUP_JACCARD for k in kept_sets):
            continue
        kept.append(r)
        kept_sets.append(s)
    return kept


async def _llm_rerank(query: str, rows: list[dict]) -> list[dict]:
    """Ask the LLM to rerank candidates by relevance.

    Returns the input list reordered. If the call fails, returns ``rows`` unchanged.
    """
    if len(rows) <= 1 or os.environ.get("DISABLE_RERANK", "").lower() in {"1", "true", "yes"}:
        return rows
    sys_prompt = (
        "Rank the following snippets by their relevance to the user query. "
        'Reply as JSON: {"order": [<snippet number>, ...]} where the first '
        "number is the most relevant. Use every snippet exactly once."
    )
    snippet_block = "\n\n".join(
        f"[{i + 1}] {row['content'][:400]}" for i, row in enumerate(rows)
    )
    user_prompt = f"Query: {query}\n\nSnippets:\n{snippet_block}"
    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=200,
        )
        obj = json.loads(raw)
        order = obj.get("order")
        if not isinstance(order, list):
            return rows
        seen: set[int] = set()
        reordered: list[dict] = []
        for n in order:
            try:
                idx = int(n) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(rows) and idx not in seen:
                seen.add(idx)
                reordered.append(rows[idx])
        for i, r in enumerate(rows):
            if i not in seen:
                reordered.append(r)
        return reordered
    except Exception as err:
        log.debug("rerank failed: %s", err)
        return rows
