from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app import embeddings as emb_lib
from app import llm
from app.audit import audit_log
from app.auth import AuthedUser, require_admin
from app.db import get_db, next_id
from app.embeddings import cosine
from app.pii import detect_and_mask_pii
from app.schemas import CreateDocumentBody, RejectDocumentBody
from app.serialize import serialize_document
from app.text import chunk_text, jaccard, term_frequency, tokenize, top_key_phrases, top_keywords

router = APIRouter()
log = logging.getLogger("api-server.documents")

_DEDUP_JACCARD = 0.85
_DEDUP_COSINE = 0.92


def _parse_id(raw: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid id")
    if n <= 0:
        raise HTTPException(status_code=400, detail="Invalid id")
    return n


async def _build_ingestion_plan(
    db,
    doc_id: int,
    cleaned_text: str,
    *,
    original_size: int,
    final_size: int,
    pii_count: int,
) -> dict[str, object]:
    """Prepare chunks, dedup findings, and embeddings for an approval-time ingest."""
    raw_chunks = chunk_text(cleaned_text)

    # Decide whether we can do semantic dedup. If embeddings aren't available we
    # fall back to Jaccard token overlap (the legacy behavior).
    use_embeddings = llm.embeddings_available()
    new_embeddings: list[list[float]] = []
    if use_embeddings and raw_chunks:
        try:
            new_embeddings = await emb_lib.embed_batch(raw_chunks)
        except Exception as err:
            log.warning("embedding failed during approval ingest, falling back to Jaccard: %s", err)
            new_embeddings = []
            use_embeddings = False

    new_token_sets = [set(tokenize(c)) for c in raw_chunks]

    approved_docs = await db.documents.find(
        {"status": "approved", "_id": {"$ne": doc_id}}, {"_id": 1, "name": 1}
    ).to_list(length=None)
    doc_name_by_id: dict[int, str] = {d["_id"]: d["name"] for d in approved_docs}
    approved_ids = list(doc_name_by_id.keys())

    existing_chunks: list[dict] = []
    if approved_ids:
        proj: dict[str, object] = {"_id": 1, "content": 1, "documentId": 1}
        if use_embeddings:
            proj["embedding"] = 1
        existing_chunks = await db.chunks.find(
            {"documentId": {"$in": approved_ids}}, proj
        ).to_list(length=None)

    existing_prepared: list[dict] = []
    for c in existing_chunks:
        prep: dict = {
            "id": c["_id"],
            "documentId": c["documentId"],
            "set": set(tokenize(c["content"])),
            "embedding": c.get("embedding"),
        }
        existing_prepared.append(prep)

    duplicate_findings: list[dict[str, object]] = []
    keep_idx: list[int] = []

    def _matches_existing_via_embedding(i: int) -> dict | None:
        if not (use_embeddings and i < len(new_embeddings)):
            return None
        ne = new_embeddings[i]
        for e in existing_prepared:
            ee = e.get("embedding")
            if not ee:
                continue
            sim = cosine(ne, ee)
            if sim >= _DEDUP_COSINE:
                return {
                    "snippet": raw_chunks[i][:160],
                    "similarity": round(sim, 3),
                    "method": "embedding",
                    "matchedDocumentId": e["documentId"],
                    "matchedDocumentName": doc_name_by_id.get(e["documentId"]),
                }
        return None

    def _matches_existing_via_jaccard(i: int) -> dict | None:
        for e in existing_prepared:
            sim = jaccard(new_token_sets[i], e["set"])  # type: ignore[arg-type]
            if sim >= _DEDUP_JACCARD:
                return {
                    "snippet": raw_chunks[i][:160],
                    "similarity": round(sim, 3),
                    "method": "jaccard",
                    "matchedDocumentId": e["documentId"],
                    "matchedDocumentName": doc_name_by_id.get(e["documentId"]),
                }
        return None

    for i in range(len(raw_chunks)):
        is_dup = False
        for k in keep_idx:
            sim = jaccard(new_token_sets[i], new_token_sets[k])
            if sim >= _DEDUP_JACCARD:
                is_dup = True
                duplicate_findings.append(
                    {
                        "snippet": raw_chunks[i][:160],
                        "similarity": round(sim, 3),
                        "method": "jaccard",
                        "matchedDocumentId": None,
                        "matchedDocumentName": "(within this document)",
                    }
                )
                break
        if not is_dup:
            match = _matches_existing_via_embedding(i) or _matches_existing_via_jaccard(i)
            if match:
                is_dup = True
                duplicate_findings.append(match)
        if not is_dup:
            keep_idx.append(i)

    kept_chunks = [raw_chunks[i] for i in keep_idx]
    kept_embeddings = [new_embeddings[i] for i in keep_idx] if new_embeddings else []

    overall_tf = term_frequency(tokenize(cleaned_text))
    keywords = top_keywords(overall_tf, 12)

    return {
        "kept_chunks": kept_chunks,
        "kept_embeddings": kept_embeddings,
        "duplicate_findings": duplicate_findings,
        "keywords": keywords,
        "embedding_model": llm.embedding_model() if use_embeddings else None,
        "ingestion_report": {
            "totalChunks": len(raw_chunks),
            "keptChunks": len(kept_chunks),
            "duplicateChunksRemoved": len(duplicate_findings),
            "piiFindingsRemoved": pii_count,
            "originalSize": original_size,
            "finalSize": final_size,
            "embeddingModel": llm.embedding_model() if use_embeddings else None,
            "embeddingDim": llm.embedding_dim() if use_embeddings else None,
            "embeddingsGenerated": len(kept_embeddings),
            "dedupMethod": "embedding+jaccard" if use_embeddings else "jaccard",
        },
    }


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
        "duplicateCount": 0,
        "chunkCount": 0,
        "piiFindings": [f.__dict__ for f in findings],
        "duplicateFindings": [],
        "tags": body.tags or [],
        "keywords": keywords,
        "createdBy": user.email or user.userId,
        "rejectionReason": None,
        "embeddingModel": None,
        "ingestionReport": {
            "status": "pending_approval",
            "message": "Ingestion and indexing run after approval.",
            "totalChunks": 0,
            "keptChunks": 0,
            "duplicateChunksRemoved": 0,
            "piiFindingsRemoved": len(findings),
            "originalSize": original_size,
            "finalSize": final_size,
            "embeddingModel": None,
            "embeddingDim": None,
            "embeddingsGenerated": 0,
            "dedupMethod": None,
        },
        "createdAt": now,
        "updatedAt": now,
    }
    await db.documents.insert_one(doc)

    await audit_log(
        action="document.create",
        actor=user.email or user.userId,
        target=str(doc_id),
        meta={"status": "pending", "pii": len(findings)},
    )

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
            "ingestionReport": doc.get("ingestionReport", {}),
        }
    )
    return base


@router.delete("/documents/{id}", status_code=204)
async def delete_document(id: str, user: AuthedUser = Depends(require_admin)) -> Response:
    doc_id = _parse_id(id)
    db = await get_db()
    await db.chunks.delete_many({"documentId": doc_id})
    await db.documents.delete_one({"_id": doc_id})
    await audit_log(action="document.delete", actor=user.email or user.userId, target=str(doc_id))
    return Response(status_code=204)


@router.post("/documents/{id}/approve")
async def approve_document(id: str, user: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    doc_id = _parse_id(id)
    db = await get_db()

    doc = await db.documents.find_one({"_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    cleaned_text = str(doc.get("cleanedText") or "")
    if not cleaned_text:
        cleaned_text = str(doc.get("originalText") or "")

    plan = await _build_ingestion_plan(
        db,
        doc_id,
        cleaned_text,
        original_size=int(doc.get("originalSize") or len(cleaned_text)),
        final_size=int(doc.get("finalSize") or len(cleaned_text)),
        pii_count=int(doc.get("piiCount") or 0),
    )
    now = datetime.now(timezone.utc)

    await db.chunks.delete_many({"documentId": doc_id})

    kept_chunks = plan["kept_chunks"]
    kept_embeddings = plan["kept_embeddings"]
    document_name = str(doc.get("name") or "Untitled")
    source_type = str(doc.get("sourceType") or "text")
    if kept_chunks:
        chunk_docs: list[dict] = []
        for idx, content in enumerate(kept_chunks):
            tokens = tokenize(content)
            chunk_phrases = top_key_phrases(content, 6)
            entry: dict = {
                "_id": await next_id("chunks"),
                "documentId": doc_id,
                "position": idx,
                "content": content,
                "tokenCount": len(tokens),
                "metadata": {
                    "fileName": document_name,
                    "pageNumber": idx + 1,
                    "keyPhrases": chunk_phrases,
                    "chunkPosition": idx,
                    "tokenCount": len(tokens),
                    "sourceType": source_type,
                },
                "createdAt": now,
            }
            if kept_embeddings:
                entry["embedding"] = kept_embeddings[idx]
            chunk_docs.append(entry)
        await db.chunks.insert_many(chunk_docs)

    r = await db.documents.find_one_and_update(
        {"_id": doc_id},
        {
            "$set": {
                "status": "approved",
                "rejectionReason": None,
                "duplicateCount": len(plan["duplicate_findings"]),
                "chunkCount": len(kept_chunks),
                "duplicateFindings": plan["duplicate_findings"],
                "keywords": plan["keywords"],
                "embeddingModel": plan["embedding_model"],
                "ingestionReport": plan["ingestion_report"],
                "updatedAt": now,
            }
        },
        return_document=True,
    )
    if not r:
        raise HTTPException(status_code=404, detail="Document not found")

    await audit_log(
        action="document.approve",
        actor=user.email or user.userId,
        target=str(doc_id),
        meta={"chunks": len(kept_chunks), "dupes": len(plan["duplicate_findings"]), "indexed": True},
    )
    return serialize_document(r)


@router.post("/documents/{id}/reject")
async def reject_document(
    id: str,
    body: RejectDocumentBody | None = None,
    user: AuthedUser = Depends(require_admin),
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
    await audit_log(
        action="document.reject",
        actor=user.email or user.userId,
        target=str(doc_id),
        meta={"reason": reason},
    )
    return serialize_document(r)
