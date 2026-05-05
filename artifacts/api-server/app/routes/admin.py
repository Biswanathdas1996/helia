from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from app import embeddings as emb_lib
from app import llm
from app.audit import audit_log
from app.auth import AuthedUser, require_admin
from app.db import get_db
from app.serialize import iso

router = APIRouter()


@router.get("/admin/stats")
async def get_admin_stats(_: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    db = await get_db()

    doc_pipe = [
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "approved": {"$sum": {"$cond": [{"$eq": ["$status", "approved"]}, 1, 0]}},
                "pending": {"$sum": {"$cond": [{"$eq": ["$status", "pending"]}, 1, 0]}},
                "pii": {"$sum": "$piiCount"},
                "dupes": {"$sum": "$duplicateCount"},
            }
        }
    ]
    doc_agg_list = await db.documents.aggregate(doc_pipe).to_list(length=1)
    doc_agg = doc_agg_list[0] if doc_agg_list else {}

    total_chunks = await db.chunks.count_documents({})

    msg_pipe = [
        {"$match": {"role": "assistant"}},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "avgLatency": {"$avg": {"$ifNull": ["$latencyMs", 0]}},
                "up": {"$sum": {"$cond": [{"$eq": ["$rating", "up"]}, 1, 0]}},
                "rated": {"$sum": {"$cond": [{"$ifNull": ["$rating", False]}, 1, 0]}},
            }
        },
    ]
    msg_agg_list = await db.messages.aggregate(msg_pipe).to_list(length=1)
    msg_agg = msg_agg_list[0] if msg_agg_list else {}

    total_tickets = await db.tickets.count_documents({})
    open_tickets = await db.tickets.count_documents(
        {"status": {"$in": ["open", "in_progress"]}}
    )

    rated = msg_agg.get("rated", 0) or 0
    up = msg_agg.get("up", 0) or 0
    helpful_rate = round(up / rated, 2) if rated > 0 else 0

    return {
        "totalDocuments": doc_agg.get("total", 0) or 0,
        "approvedDocuments": doc_agg.get("approved", 0) or 0,
        "pendingDocuments": doc_agg.get("pending", 0) or 0,
        "totalChunks": total_chunks,
        "totalQueries": msg_agg.get("total", 0) or 0,
        "totalTickets": total_tickets,
        "openTickets": open_tickets,
        "piiRemovedTotal": doc_agg.get("pii", 0) or 0,
        "duplicateChunksRemoved": doc_agg.get("dupes", 0) or 0,
        "avgLatencyMs": round(msg_agg.get("avgLatency", 0) or 0),
        "helpfulRate": helpful_rate,
    }


@router.get("/admin/stats/trend")
async def get_admin_trend(_: AuthedUser = Depends(require_admin)) -> list[dict[str, object]]:
    db = await get_db()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    since = today - timedelta(days=13)

    async def group_by_day(collection: str, match: dict[str, object]) -> dict[str, int]:
        pipe = [
            {"$match": {"createdAt": {"$gte": since}, **match}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$createdAt",
                            "timezone": "UTC",
                        }
                    },
                    "n": {"$sum": 1},
                }
            },
        ]
        rows = await db[collection].aggregate(pipe).to_list(length=None)
        return {r["_id"]: r["n"] for r in rows}

    queries = await group_by_day("messages", {"role": "assistant"})
    documents = await group_by_day("documents", {})

    out: list[dict[str, object]] = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        out.append({"date": key, "queries": queries.get(key, 0), "documents": documents.get(key, 0)})
    return out


@router.get("/admin/activity")
async def get_admin_activity(_: AuthedUser = Depends(require_admin)) -> list[dict[str, object]]:
    db = await get_db()
    recent_docs = await db.documents.find({}).sort("updatedAt", -1).limit(10).to_list(length=10)
    recent_msgs = (
        await db.messages.find({"role": "assistant"})
        .sort("createdAt", -1)
        .limit(10)
        .to_list(length=10)
    )
    recent_tickets = await db.tickets.find({}).sort("createdAt", -1).limit(10).to_list(length=10)

    items: list[dict[str, object]] = []

    for d in recent_docs:
        items.append(
            {
                "id": f"doc-{d['_id']}-create",
                "kind": "document_uploaded",
                "title": f'Uploaded "{d["name"]}"',
                "subtitle": f'{d["chunkCount"]} chunks · {d["piiCount"]} PII removed',
                "actor": d.get("createdBy"),
                "createdAt": iso(d["createdAt"]),
            }
        )
        if d.get("status") == "approved":
            items.append(
                {
                    "id": f"doc-{d['_id']}-approve",
                    "kind": "document_approved",
                    "title": f'Approved "{d["name"]}"',
                    "subtitle": None,
                    "actor": d.get("createdBy"),
                    "createdAt": iso(d["updatedAt"]),
                }
            )
    for m in recent_msgs:
        items.append(
            {
                "id": f"msg-{m['_id']}",
                "kind": "query_answered",
                "title": (m.get("content") or "")[:80],
                "subtitle": f'{len(m.get("citations") or [])} sources · {m.get("latencyMs") or 0}ms',
                "actor": None,
                "createdAt": iso(m["createdAt"]),
            }
        )
    for t in recent_tickets:
        items.append(
            {
                "id": f"tkt-{t['_id']}",
                "kind": "ticket_opened",
                "title": t["subject"],
                "subtitle": f'{t["priority"]} priority · {t["status"]}',
                "actor": t.get("userId"),
                "createdAt": iso(t["createdAt"]),
            }
        )

    items.sort(key=lambda x: x["createdAt"], reverse=True)  # type: ignore[arg-type,return-value]
    return items[:25]


# ---------------------------------------------------------------------------
# Vector index management
# ---------------------------------------------------------------------------

@router.get("/admin/vector-index")
async def get_vector_index_status(_: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    """Diagnostics for the Atlas Vector Search setup."""
    db = await get_db()
    indexes = await emb_lib.list_search_indexes(db)
    coverage = await emb_lib.embedding_coverage(db)
    target = emb_lib.index_name()
    matched = next((idx for idx in indexes if idx.get("name") == target), None)
    return {
        "embeddingsAvailable": llm.embeddings_available(),
        "vectorSearchEnvFlag": emb_lib.vector_search_enabled(),
        "embeddingModel": llm.embedding_model(),
        "embeddingDim": llm.embedding_dim(),
        "indexName": target,
        "exists": matched is not None,
        "queryable": bool(matched and matched.get("queryable")),
        "state": matched.get("status") if matched else None,
        "indexes": [
            {"name": idx.get("name"), "status": idx.get("status"), "queryable": idx.get("queryable")}
            for idx in indexes
        ],
        "embeddingCoverage": coverage,
        "definition": emb_lib.vector_index_definition(),
    }


@router.post("/admin/vector-index", status_code=201)
async def create_vector_index(user: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    """Create the Atlas Vector Search index if it doesn't already exist.

    Atlas takes 1–5 minutes to build the index after creation. The endpoint
    returns immediately; poll ``GET /api/admin/vector-index`` to watch the
    ``state`` field transition to ``READY``.
    """
    db = await get_db()
    result = await emb_lib.ensure_vector_index(db)
    await audit_log(action="vector_index.ensure", actor=user.email or user.userId, meta=result)
    return result


@router.post("/admin/embeddings/backfill")
async def backfill_embeddings(user: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    """Embed any existing chunks that are missing the ``embedding`` field.

    Useful right after enabling embeddings on a database whose chunks were
    ingested before embedding generation was wired up.
    """
    db = await get_db()
    result = await emb_lib.backfill_embeddings(db)
    await audit_log(action="embeddings.backfill", actor=user.email or user.userId, meta=result)
    return result
