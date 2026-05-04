from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth import AuthedUser, require_admin
from app.db import get_db, next_id
from app.pii import detect_and_mask_pii
from app.schemas import CreateDocumentBody, RejectDocumentBody
from app.serialize import serialize_document
from app.text import chunk_text, jaccard, term_frequency, tokenize, top_keywords

router = APIRouter()


def _parse_id(raw: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid id")
    if n <= 0:
        raise HTTPException(status_code=400, detail="Invalid id")
    return n


@router.get("/documents")
async def list_documents(_: AuthedUser = Depends(require_admin)) -> list[dict[str, object]]:
    db = await get_db()
    rows = await db.documents.find({}).sort("createdAt", -1).to_list(length=None)
    return [serialize_document(r) for r in rows]


@router.post("/documents", status_code=201)
async def create_document(
    body: CreateDocumentBody,
    request: Request,
    user: AuthedUser = Depends(require_admin),
) -> dict[str, object]:
    db = await get_db()
    cleaned, findings = detect_and_mask_pii(body.content)
    original_size = len(body.content)
    final_size = len(cleaned)

    raw_chunks = chunk_text(cleaned)
    new_sets = [set(tokenize(c)) for c in raw_chunks]

    approved_docs = await db.documents.find(
        {"status": "approved"}, {"_id": 1, "name": 1}
    ).to_list(length=None)
    doc_name_by_id: dict[int, str] = {d["_id"]: d["name"] for d in approved_docs}
    approved_ids = list(doc_name_by_id.keys())

    existing = []
    if approved_ids:
        existing = await db.chunks.find(
            {"documentId": {"$in": approved_ids}},
            {"_id": 1, "content": 1, "documentId": 1},
        ).to_list(length=None)
    existing_prepared = [
        {"id": c["_id"], "documentId": c["documentId"], "set": set(tokenize(c["content"]))}
        for c in existing
    ]

    duplicate_findings: list[dict[str, object]] = []
    keep_idx: list[int] = []
    for i in range(len(raw_chunks)):
        is_dup = False
        for k in keep_idx:
            sim = jaccard(new_sets[i], new_sets[k])
            if sim >= 0.85:
                is_dup = True
                duplicate_findings.append(
                    {
                        "snippet": raw_chunks[i][:160],
                        "similarity": round(sim, 3),
                        "matchedDocumentId": None,
                        "matchedDocumentName": "(within this document)",
                    }
                )
                break
        if not is_dup:
            for e in existing_prepared:
                sim = jaccard(new_sets[i], e["set"])  # type: ignore[arg-type]
                if sim >= 0.85:
                    is_dup = True
                    duplicate_findings.append(
                        {
                            "snippet": raw_chunks[i][:160],
                            "similarity": round(sim, 3),
                            "matchedDocumentId": e["documentId"],
                            "matchedDocumentName": doc_name_by_id.get(e["documentId"]),  # type: ignore[arg-type]
                        }
                    )
                    break
        if not is_dup:
            keep_idx.append(i)

    kept_chunks = [raw_chunks[i] for i in keep_idx]
    overall_tf = term_frequency(tokenize(cleaned))
    keywords = top_keywords(overall_tf, 12)
    now = datetime.now(timezone.utc)

    doc_id = await next_id("documents")
    doc = {
        "_id": doc_id,
        "name": body.name,
        "sourceType": body.sourceType,
        "status": "pending",
        "originalText": body.content,
        "cleanedText": cleaned,
        "originalSize": original_size,
        "finalSize": final_size,
        "piiCount": len(findings),
        "duplicateCount": len(duplicate_findings),
        "chunkCount": len(kept_chunks),
        "piiFindings": [f.__dict__ for f in findings],
        "duplicateFindings": duplicate_findings,
        "tags": body.tags or [],
        "keywords": keywords,
        "createdBy": user.email or user.userId,
        "rejectionReason": None,
        "createdAt": now,
        "updatedAt": now,
    }
    await db.documents.insert_one(doc)

    if kept_chunks:
        chunk_docs = []
        for idx, content in enumerate(kept_chunks):
            tokens = tokenize(content)
            chunk_docs.append(
                {
                    "_id": await next_id("chunks"),
                    "documentId": doc_id,
                    "position": idx,
                    "content": content,
                    "tokenCount": len(tokens),
                    "createdAt": now,
                }
            )
        await db.chunks.insert_many(chunk_docs)

    return serialize_document(doc)


@router.get("/documents/{id}")
async def get_document(id: str, _: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    doc_id = _parse_id(id)
    db = await get_db()
    doc = await db.documents.find_one({"_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    base = serialize_document(doc)
    base.update(
        {
            "originalText": doc.get("originalText", ""),
            "cleanedText": doc.get("cleanedText", ""),
            "piiFindings": doc.get("piiFindings", []),
            "duplicateFindings": doc.get("duplicateFindings", []),
        }
    )
    return base


@router.delete("/documents/{id}", status_code=204)
async def delete_document(id: str, _: AuthedUser = Depends(require_admin)) -> Response:
    doc_id = _parse_id(id)
    db = await get_db()
    await db.chunks.delete_many({"documentId": doc_id})
    await db.documents.delete_one({"_id": doc_id})
    return Response(status_code=204)


@router.post("/documents/{id}/approve")
async def approve_document(id: str, _: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    doc_id = _parse_id(id)
    db = await get_db()
    r = await db.documents.find_one_and_update(
        {"_id": doc_id},
        {
            "$set": {
                "status": "approved",
                "rejectionReason": None,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
        return_document=True,
    )
    if not r:
        raise HTTPException(status_code=404, detail="Document not found")
    return serialize_document(r)


@router.post("/documents/{id}/reject")
async def reject_document(
    id: str,
    body: RejectDocumentBody | None = None,
    _: AuthedUser = Depends(require_admin),
) -> dict[str, object]:
    doc_id = _parse_id(id)
    db = await get_db()
    reason = body.reason if body else None
    r = await db.documents.find_one_and_update(
        {"_id": doc_id},
        {
            "$set": {
                "status": "rejected",
                "rejectionReason": reason,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
        return_document=True,
    )
    if not r:
        raise HTTPException(status_code=404, detail="Document not found")
    return serialize_document(r)
