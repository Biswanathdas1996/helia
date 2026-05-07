from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import embeddings as emb_lib
from app import llm
from app.audit import audit_log
from app.auth import AuthedUser, require_admin
from app.db import get_db
from app.serialize import iso
from app.tenant import tenant_for, tenant_from_email

router = APIRouter()


def _tenant_id_from_user_doc(doc: dict[str, object]) -> str:
    tid = doc.get("tenantId")
    if isinstance(tid, str) and tid.strip():
        return tid.strip()
    email = doc.get("email")
    return tenant_from_email(email if isinstance(email, str) else None)


class SetUserRoleBody(BaseModel):
    email: str = Field(min_length=1)
    role: Literal["admin", "user"]


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


@router.get("/admin/insights")
async def get_admin_insights(_: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    """Aggregated AI/RAG telemetry powering the admin dashboard insights panels."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=14)

    cursor = db.messages.find(
        {"role": "assistant", "createdAt": {"$gte": since}},
        {
            "content": 1,
            "citations": 1,
            "canAnswer": 1,
            "finalVerdict": 1,
            "rating": 1,
            "feedbackComment": 1,
            "latencyMs": 1,
            "createdAt": 1,
            "conversationId": 1,
            "kind": 1,
            "ticketId": 1,
        },
    ).sort("createdAt", -1)
    messages = await cursor.to_list(length=5000)

    LOW_CONF = 0.60
    total = len(messages)
    latencies: list[int] = []
    top_scores: list[float] = []
    citation_counts: list[int] = []
    grounded = refused = cited = verdicted = up = down = rated = 0
    low_conf = no_results = 0
    by_day_count: dict[str, int] = {}
    by_day_lat_sum: dict[str, int] = {}
    by_day_lat_n: dict[str, int] = {}

    # Business outcome counters
    ai_resolved = 0  # final grounded answers from the assistant
    ticket_offers = 0  # assistant proposed escalation
    tickets_from_chat = 0  # ticket actually created in-conversation
    feedback_comments: list[dict[str, object]] = []

    failed: list[dict[str, object]] = []

    for m in messages:
        lat = m.get("latencyMs")
        lat_val = int(lat) if isinstance(lat, (int, float)) and lat > 0 else None
        if lat_val is not None:
            latencies.append(lat_val)

        cits = m.get("citations") or []
        citation_counts.append(len(cits))
        top = 0.0
        if cits:
            cited += 1
            scores = [float(c.get("score", 0)) for c in cits if isinstance(c, dict)]
            if scores:
                top = max(scores)
                top_scores.append(top)
                if top < LOW_CONF:
                    low_conf += 1
        else:
            no_results += 1
            low_conf += 1

        ca = m.get("canAnswer")
        if ca is True:
            grounded += 1
        elif ca is False:
            refused += 1

        if m.get("finalVerdict") is True:
            verdicted += 1

        rating = m.get("rating")
        if rating == "up":
            up += 1
            rated += 1
        elif rating == "down":
            down += 1
            rated += 1

        comment = (m.get("feedbackComment") or "").strip() if isinstance(m.get("feedbackComment"), str) else ""
        if comment and rating in ("up", "down") and len(feedback_comments) < 8:
            feedback_comments.append(
                {
                    "id": str(m.get("_id")),
                    "rating": rating,
                    "comment": comment[:240],
                    "createdAt": iso(m.get("createdAt")) if isinstance(m.get("createdAt"), datetime) else None,
                }
            )

        kind = m.get("kind")
        if kind == "ticket_created" or m.get("ticketId"):
            tickets_from_chat += 1
        elif kind == "ticket_offer":
            ticket_offers += 1
        elif kind == "answer" and (m.get("finalVerdict") is True or m.get("canAnswer") is True):
            ai_resolved += 1

        d = m.get("createdAt")
        if isinstance(d, datetime):
            key = d.strftime("%Y-%m-%d")
            by_day_count[key] = by_day_count.get(key, 0) + 1
            if lat_val is not None:
                by_day_lat_sum[key] = by_day_lat_sum.get(key, 0) + lat_val
                by_day_lat_n[key] = by_day_lat_n.get(key, 0) + 1

        if (ca is False) or (not cits) or (top and top < LOW_CONF):
            if len(failed) < 60:
                failed.append({"_msg": m, "_top": top})

    def pct(n: int, d: int) -> float:
        return round(n / d * 100, 1) if d else 0.0

    def percentile(vals: list[int], p: float) -> int:
        if not vals:
            return 0
        s = sorted(vals)
        k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
        return s[k]

    avg_top_score = round(sum(top_scores) / len(top_scores), 3) if top_scores else 0.0
    avg_citations = round(sum(citation_counts) / len(citation_counts), 2) if citation_counts else 0.0
    avg_latency = round(sum(latencies) / len(latencies)) if latencies else 0

    # Knowledge gaps: walk failed assistant messages, attach the prior user question, dedupe.
    gap_items: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in failed:
        fa = entry["_msg"]
        prev = await db.messages.find_one(
            {
                "conversationId": fa.get("conversationId"),
                "role": "user",
                "createdAt": {"$lt": fa.get("createdAt")},
            },
            sort=[("createdAt", -1)],
        )
        question = ((prev or {}).get("content") or "").strip()
        if not question:
            continue
        norm = question.lower()[:120]
        if norm in seen:
            continue
        seen.add(norm)
        cits = fa.get("citations") or []
        gap_items.append(
            {
                "id": str(fa.get("_id")),
                "question": question[:240],
                "topScore": round(float(entry["_top"] or 0), 3),
                "citationCount": len(cits),
                "canAnswer": fa.get("canAnswer"),
                "createdAt": iso(fa.get("createdAt")),
            }
        )
        if len(gap_items) >= 10:
            break

    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily: list[dict[str, object]] = []
    for i in range(6, -1, -1):
        d = today0 - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        n = by_day_lat_n.get(key, 0)
        lat = round(by_day_lat_sum.get(key, 0) / n) if n else 0
        daily.append({"date": key, "queries": by_day_count.get(key, 0), "avgLatencyMs": lat})

    # Tickets — resolved by human agent vs still pending the queue
    tickets_resolved = await db.tickets.count_documents(
        {"status": {"$in": ["resolved", "closed"]}, "createdAt": {"$gte": since}}
    )
    tickets_open = await db.tickets.count_documents(
        {"status": {"$in": ["open", "in_progress"]}}
    )
    tickets_total = await db.tickets.count_documents({"createdAt": {"$gte": since}})

    deflection_denominator = ai_resolved + tickets_from_chat
    deflection_rate = pct(ai_resolved, deflection_denominator) if deflection_denominator else 0.0

    return {
        "windowDays": 14,
        "business": {
            "totalQueries": total,
            "aiResolved": ai_resolved,
            "ticketOffers": ticket_offers,
            "ticketsCreatedFromChat": tickets_from_chat,
            "ticketsResolvedByAgent": tickets_resolved,
            "ticketsOpen": tickets_open,
            "ticketsTotal": tickets_total,
            "deflectionRate": deflection_rate,
            "feedback": {
                "up": up,
                "down": down,
                "rated": rated,
                "helpfulRate": pct(up, rated),
                "comments": feedback_comments,
            },
        },
        "ragHealth": {
            "avgTopScore": avg_top_score,
            "lowConfidenceRate": pct(low_conf, total),
            "noResultsRate": pct(no_results, total),
            "avgCitationsUsed": avg_citations,
            "sampleSize": total,
            "lowConfidenceThreshold": LOW_CONF,
        },
        "llmTelemetry": {
            "chatModel": llm.chat_model(),
            "embeddingModel": llm.embedding_model(),
            "avgLatencyMs": avg_latency,
            "p50LatencyMs": percentile(latencies, 0.50),
            "p95LatencyMs": percentile(latencies, 0.95),
            "totalQueries": total,
            "daily": daily,
        },
        "knowledgeGaps": gap_items,
        "grounding": {
            "totalAnswers": total,
            "groundedAnswers": grounded,
            "refusedAnswers": refused,
            "citedAnswers": cited,
            "groundingVerdicts": verdicted,
            "helpfulCount": up,
            "downvoteCount": down,
            "ratedCount": rated,
            "groundedRate": pct(grounded, total),
            "citedRate": pct(cited, total),
            "refusalRate": pct(refused, total),
            "helpfulRate": pct(up, rated),
        },
    }


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
# User roles (within tenant)
# ---------------------------------------------------------------------------


@router.patch("/admin/users/role")
async def set_user_role(body: SetUserRoleBody, admin: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    """Promote or demote a user in your organization so they can access admin workflows."""
    normalized = body.email.strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid email")

    db = await get_db()
    target = await db.users.find_one({"email": normalized})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    actor_tenant = tenant_for(admin)
    if _tenant_id_from_user_doc(target) != actor_tenant:
        raise HTTPException(status_code=403, detail="Not allowed for users outside your organization")

    if str(target["_id"]) == admin.userId and body.role == "user":
        raise HTTPException(status_code=400, detail="Use another admin to remove your own admin access")

    await db.users.update_one(
        {"_id": target["_id"]},
        {"$set": {"role": body.role, "updatedAt": datetime.now(timezone.utc)}},
    )
    await audit_log(
        action="admin.users.set_role",
        actor=admin.email or admin.userId,
        target=str(target["_id"]),
        meta={"email": normalized, "role": body.role},
    )
    return {"userId": str(target["_id"]), "email": normalized, "role": body.role}


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
