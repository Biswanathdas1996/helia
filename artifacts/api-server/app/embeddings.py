"""Embedding helpers — batched generation, cosine similarity, vector index."""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Iterable

from app import llm

log = logging.getLogger("api-server.embeddings")

_EMBED_BATCH = 64


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings, batching transparently. Empty input → empty output."""
    if not texts:
        return []
    out: list[list[float]] = []
    target_dim = llm.embedding_dim()
    warned_dim_mismatch = False
    for i in range(0, len(texts), _EMBED_BATCH):
        chunk = texts[i : i + _EMBED_BATCH]
        vectors = await llm.embed(chunk)
        for vec in vectors:
            if vec and len(vec) != target_dim and not warned_dim_mismatch:
                warned_dim_mismatch = True
                action = "truncating" if len(vec) > target_dim else "padding"
                log.warning(
                    "Embedding dimension mismatch (received=%d expected=%d); %s to match index",
                    len(vec),
                    target_dim,
                    action,
                )
            out.append(_fit_vector_dim(vec, target_dim))
    return out


def _fit_vector_dim(vec: list[float], target_dim: int) -> list[float]:
    if target_dim <= 0 or not vec:
        return vec
    size = len(vec)
    if size == target_dim:
        return vec
    if size > target_dim:
        return vec[:target_dim]
    return vec + [0.0] * (target_dim - size)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def embedding_version() -> int:
    """Active embedding generation. Bump this when rotating model / dim.

    Each chunk records ``embeddingVersion`` so old generations can co-exist
    while a backfill rolls forward. Vector index name is suffixed with the
    version so legacy and new indexes can be queried independently.
    """
    raw = os.environ.get("EMBEDDING_VERSION", "1")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def index_name(version: int | None = None) -> str:
    """Versioned Atlas vector index name.

    Defaults to the active version. Pass an explicit version to query a
    legacy generation during a migration.
    """
    base = os.environ.get("MONGODB_VECTOR_INDEX", "chunks_vector_index")
    v = version if version is not None else embedding_version()
    return f"{base}_v{v}"


def vector_search_enabled() -> bool:
    """True only when embeddings provider AND Atlas Vector Search are usable."""
    if not llm.embeddings_available():
        return False
    return os.environ.get("MONGODB_VECTOR_SEARCH", "").lower() in {"1", "true", "yes"}


async def vector_search(
    db,
    query_embedding: list[float],
    *,
    limit: int = 10,
    doc_ids: Iterable[int] | None = None,
    tenant_id: str | None = None,
    version: int | None = None,
) -> list[dict]:
    """Run an Atlas ``$vectorSearch`` aggregation against ``chunks``.

    Atlas Vector Search must be configured outside the app (see
    ``infra/atlas-vector-index.json``). On failure the caller should fall
    back to BM25-only retrieval.
    """
    query_vector = _fit_vector_dim(query_embedding, llm.embedding_dim())
    if not query_vector:
        return []
    active_version = version if version is not None else embedding_version()
    pipeline: list[dict] = [
        {
            "$vectorSearch": {
                "index": index_name(active_version),
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": max(50, limit * 10),
                "limit": limit,
            }
        }
    ]
    filt: dict[str, object] = {}
    if doc_ids is not None:
        ids = list(doc_ids)
        if not ids:
            return []
        filt["documentId"] = {"$in": ids}
    if tenant_id:
        filt["tenantId"] = tenant_id
    if filt:
        pipeline[0]["$vectorSearch"]["filter"] = filt

    pipeline.append(
        {
            "$project": {
                "_id": 1,
                "documentId": 1,
                "tenantId": 1,
                "position": 1,
                "content": 1,
                "tokenCount": 1,
                "metadata": 1,
                "embeddingVersion": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        }
    )
    return await db.chunks.aggregate(pipeline).to_list(length=limit)


# ---------------------------------------------------------------------------
# Atlas Vector Search index management
# ---------------------------------------------------------------------------

def vector_index_definition(version: int | None = None) -> dict[str, Any]:
    return {
        "name": index_name(version),
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": llm.embedding_dim(),
                    "similarity": "cosine",
                },
                {"type": "filter", "path": "documentId"},
                {"type": "filter", "path": "tenantId"},
                {"type": "filter", "path": "embeddingVersion"},
            ]
        },
    }


async def list_search_indexes(db) -> list[dict[str, Any]]:
    """List all Atlas Search indexes on the chunks collection."""
    try:
        cursor = db.chunks.list_search_indexes()
        return await cursor.to_list(length=None)
    except Exception as err:
        log.warning("list_search_indexes failed: %s", err)
        return []


async def ensure_vector_index(db) -> dict[str, Any]:
    """Create the Atlas Vector Search index if it doesn't already exist.

    Returns a dict describing what happened: ``{"status": "created"|"exists"|"error", ...}``.
    Atlas takes 1–5 minutes to build the index after creation; the app does
    not block on that.
    """
    indexes = await list_search_indexes(db)
    target = index_name()
    for idx in indexes:
        if idx.get("name") == target:
            return {
                "status": "exists",
                "name": target,
                "state": idx.get("status") or idx.get("state"),
                "queryable": idx.get("queryable", False),
            }

    spec = vector_index_definition()
    try:
        await db.chunks.create_search_index(spec)
    except Exception as err:
        log.exception("create_search_index failed: %s", err)
        return {"status": "error", "name": target, "error": str(err)}
    return {"status": "created", "name": target, "definition": spec}


async def embedding_coverage(db) -> dict[str, int]:
    """Diagnostic: how many chunks have / lack the embedding field."""
    pipeline = [
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "withEmbedding": {
                    "$sum": {"$cond": [{"$gt": [{"$size": {"$ifNull": ["$embedding", []]}}, 0]}, 1, 0]}
                },
            }
        }
    ]
    rows = await db.chunks.aggregate(pipeline).to_list(length=1)
    if not rows:
        return {"total": 0, "withEmbedding": 0, "missingEmbedding": 0, "missing": 0, "percent": 0}
    total = int(rows[0].get("total", 0) or 0)
    with_emb = int(rows[0].get("withEmbedding", 0) or 0)
    missing = total - with_emb
    percent = int(round((with_emb / total) * 100)) if total > 0 else 0
    return {
        "total": total,
        "withEmbedding": with_emb,
        "missingEmbedding": missing,
        "missing": missing,
        "percent": percent,
    }


async def backfill_embeddings(
    db,
    *,
    batch: int = 64,
    target_version: int | None = None,
    tenant_id: str | None = None,
) -> dict[str, int]:
    """Backfill / migrate embeddings to ``target_version`` (default: active).

    Picks up any chunk where ``embeddingVersion`` is missing or below
    ``target_version``, or where the ``embedding`` field is missing/empty.
    Writes the new embedding and stamps ``embeddingVersion`` + the active
    ``embeddingModel``. Safe to run alongside live traffic — old generations
    keep serving until each chunk is rewritten.
    """
    if not llm.embeddings_available():
        return {"updated": 0, "skipped": 0, "error": 1}

    target = target_version if target_version is not None else embedding_version()

    query: dict[str, object] = {
        "$or": [
            {"embedding": {"$exists": False}},
            {"embedding": {"$size": 0}},
            {"embeddingVersion": {"$exists": False}},
            {"embeddingVersion": {"$lt": target}},
        ]
    }
    if tenant_id:
        query["tenantId"] = tenant_id

    cursor = db.chunks.find(query, {"_id": 1, "content": 1})
    updated = 0
    skipped = 0
    pending: list[dict] = []

    async for row in cursor:
        pending.append(row)
        if len(pending) >= batch:
            updated += await _flush_backfill(db, pending, target_version=target)
            pending = []
    if pending:
        updated += await _flush_backfill(db, pending, target_version=target)
    return {"updated": updated, "skipped": skipped, "targetVersion": target}


async def _flush_backfill(db, rows: list[dict], *, target_version: int | None = None) -> int:
    texts = [r["content"] for r in rows]
    try:
        vectors = await embed_batch(texts)
    except Exception as err:
        log.warning("backfill embed failed for %d chunks: %s", len(rows), err)
        return 0
    target = target_version if target_version is not None else embedding_version()
    model_name = llm.embedding_model()
    for row, vec in zip(rows, vectors):
        if vec:
            await db.chunks.update_one(
                {"_id": row["_id"]},
                {
                    "$set": {
                        "embedding": vec,
                        "embeddingVersion": target,
                        "embeddingModel": model_name,
                    }
                },
            )
    return len([v for v in vectors if v])


