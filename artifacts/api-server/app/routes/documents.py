from __future__ import annotations

import hashlib
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
_PREVIEW_JACCARD = 0.2
_PREVIEW_COSINE = 0.78


def _parse_id(raw: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid id")
    if n <= 0:
        raise HTTPException(status_code=400, detail="Invalid id")
    return n


def _parse_non_negative_int(raw: str, *, field_name: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    if n < 0:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return n


def _parse_source_positions(value: object) -> set[int]:
    if not isinstance(value, list):
        return set()
    out: set[int] = set()
    for v in value:
        if isinstance(v, int) and v >= 0:
            out.add(v)
    return out


def _decrement_report_count(value: object) -> int:
    if not isinstance(value, int):
        return 0
    return value - 1 if value > 0 else 0


def _content_hash(text: str) -> str:
    # Collapse whitespace so line-ending differences do not break exact-match detection.
    normalized = " ".join((text or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def _build_ingestion_plan(
    db,
    doc_id: int,
    cleaned_text: str,
    *,
    original_size: int,
    final_size: int,
    pii_count: int,
    candidate_statuses: tuple[str, ...] = ("approved",),
    preview_mode: bool = False,
    excluded_source_positions: set[int] | None = None,
) -> dict[str, object]:
    """Prepare chunks, dedup findings, and embeddings for an approval-time ingest."""
    raw_chunks = chunk_text(cleaned_text)
    excluded_positions = excluded_source_positions or set()
    active_jaccard = _PREVIEW_JACCARD if preview_mode else _DEDUP_JACCARD
    active_cosine = _PREVIEW_COSINE if preview_mode else _DEDUP_COSINE

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

    doc_query: dict[str, object] = {"status": {"$in": list(candidate_statuses)}}
    if doc_id > 0:
        doc_query["_id"] = {"$ne": doc_id}
    candidate_docs = await db.documents.find(doc_query, {"_id": 1, "name": 1}).to_list(length=None)
    doc_name_by_id: dict[int, str] = {d["_id"]: d["name"] for d in candidate_docs}
    candidate_ids = list(doc_name_by_id.keys())

    existing_chunks: list[dict] = []
    if candidate_ids:
        proj: dict[str, object] = {"_id": 1, "content": 1, "documentId": 1}
        if use_embeddings:
            proj["embedding"] = 1
        existing_chunks = await db.chunks.find(
            {"documentId": {"$in": candidate_ids}}, proj
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
            if sim >= active_cosine:
                return {
                    "snippet": raw_chunks[i],
                    "similarity": round(sim, 3),
                    "method": "embedding",
                    "matchedDocumentId": e["documentId"],
                    "matchedDocumentName": doc_name_by_id.get(e["documentId"]),
                    "matchedChunkId": e["id"],
                    "sourceChunkPosition": i,
                }
        return None

    def _matches_existing_via_jaccard(i: int) -> dict | None:
        for e in existing_prepared:
            sim = jaccard(new_token_sets[i], e["set"])  # type: ignore[arg-type]
            if sim >= active_jaccard:
                return {
                    "snippet": raw_chunks[i],
                    "similarity": round(sim, 3),
                    "method": "jaccard",
                    "matchedDocumentId": e["documentId"],
                    "matchedDocumentName": doc_name_by_id.get(e["documentId"]),
                    "matchedChunkId": e["id"],
                    "sourceChunkPosition": i,
                }
        return None

    for i in range(len(raw_chunks)):
        if i in excluded_positions:
            continue
        is_dup = False
        for k in keep_idx:
            sim = jaccard(new_token_sets[i], new_token_sets[k])
            if sim >= active_jaccard:
                is_dup = True
                duplicate_findings.append(
                    {
                        "snippet": raw_chunks[i],
                        "similarity": round(sim, 3),
                        "method": "jaccard",
                        "matchedDocumentId": None,
                        "matchedDocumentName": "(within this document)",
                        "matchedChunkId": None,
                        "sourceChunkPosition": i,
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
            "duplicateChunksRemoved": len(duplicate_findings) + len(excluded_positions),
            "piiFindingsRemoved": pii_count,
            "originalSize": original_size,
            "finalSize": final_size,
            "embeddingModel": llm.embedding_model() if use_embeddings else None,
            "embeddingDim": llm.embedding_dim() if use_embeddings else None,
            "embeddingsGenerated": len(kept_embeddings),
            "dedupMethod": (
                "embedding+jaccard (preview)" if use_embeddings and preview_mode
                else "embedding+jaccard" if use_embeddings
                else "jaccard (preview)" if preview_mode
                else "jaccard"
            ),
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
    content_hash = _content_hash(cleaned)
    existing_exact = await db.documents.find_one(
        {
            "status": {"$in": ["pending", "approved"]},
            "$or": [{"contentHash": content_hash}, {"cleanedText": cleaned}],
        },
        {"_id": 1, "name": 1, "status": 1},
    )
    if existing_exact:
        matched_id = existing_exact.get("_id")
        matched_name = str(existing_exact.get("name") or "Untitled")
        matched_status = str(existing_exact.get("status") or "unknown")
        await audit_log(
            action="document.duplicate_blocked",
            actor=user.email or user.userId,
            target=str(matched_id),
            meta={"attemptedName": body.name, "matchedStatus": matched_status},
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "exact_duplicate",
                "message": f"Exact duplicate of document #{matched_id} ({matched_name}, {matched_status}).",
                "duplicateDocumentId": matched_id,
                "duplicateDocumentName": matched_name,
                "duplicateDocumentStatus": matched_status,
            },
        )
    original_size = len(body.content)
    final_size = len(cleaned)
    overall_tf = term_frequency(tokenize(cleaned))
    keywords = top_keywords(overall_tf, 12)

    # Precompute dedup findings for pending-review UX so admins can see likely
    # duplicate chunks before approval. Approval still recomputes the final plan.
    try:
        preview_plan = await _build_ingestion_plan(
            db,
            0,
            cleaned,
            original_size=original_size,
            final_size=final_size,
            pii_count=len(findings),
            candidate_statuses=("approved", "pending"),
            preview_mode=True,
        )
        preview_duplicate_findings = list(preview_plan.get("duplicate_findings") or [])
    except Exception as err:
        log.warning("dedup preview failed during create; continuing without preview: %s", err)
        preview_duplicate_findings = []

    now = datetime.now(timezone.utc)

    doc_id = await next_id("documents")
    root_document_id = doc_id
    doc = {
        "_id": doc_id,
        "name": body.name,
        "sourceType": body.sourceType,
        "status": "pending",
        "originalText": body.content,
        "cleanedText": cleaned,
        "contentHash": content_hash,
        "originalSize": original_size,
        "finalSize": final_size,
        "piiCount": len(findings),
        "duplicateCount": len(preview_duplicate_findings),
        "chunkCount": 0,
        "piiFindings": [f.__dict__ for f in findings],
        "duplicateFindings": preview_duplicate_findings,
        "manualExcludedChunkPositions": [],
        "tags": body.tags or [],
        "keywords": keywords,
        "rootDocumentId": root_document_id,
        "parentDocumentId": None,
        "documentVersion": 0,
        "lastIngestionRunId": None,
        "createdBy": user.email or user.userId,
        "rejectionReason": None,
        "embeddingModel": None,
        "ingestionReport": {
            "status": "pending_approval",
            "message": "Ingestion and indexing run after approval. Duplicate findings shown here are pre-approval estimates.",
            "totalChunks": 0,
            "keptChunks": 0,
            "duplicateChunksRemoved": len(preview_duplicate_findings),
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


@router.delete("/documents/{id}/duplicates/{source_position}", status_code=204)
async def exclude_document_duplicate_chunk(
    id: str,
    source_position: str,
    user: AuthedUser = Depends(require_admin),
) -> Response:
    doc_id = _parse_id(id)
    source_chunk_position = _parse_non_negative_int(source_position, field_name="source position")
    db = await get_db()

    doc = await db.documents.find_one({"_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    manual_excluded = _parse_source_positions(doc.get("manualExcludedChunkPositions"))
    manual_excluded.add(source_chunk_position)

    now = datetime.now(timezone.utc)
    update_set: dict[str, object] = {
        "manualExcludedChunkPositions": sorted(manual_excluded),
        "updatedAt": now,
    }

    if str(doc.get("status") or "") == "pending":
        cleaned_text = str(doc.get("cleanedText") or "")
        if not cleaned_text:
            cleaned_text = str(doc.get("originalText") or "")

        preview_plan = await _build_ingestion_plan(
            db,
            doc_id,
            cleaned_text,
            original_size=int(doc.get("originalSize") or len(cleaned_text)),
            final_size=int(doc.get("finalSize") or len(cleaned_text)),
            pii_count=int(doc.get("piiCount") or 0),
            candidate_statuses=("approved", "pending"),
            preview_mode=True,
            excluded_source_positions=manual_excluded,
        )

        preview_report = dict(preview_plan.get("ingestion_report") or {})
        preview_report.update(
            {
                "status": "pending_approval",
                "message": "Ingestion and indexing run after approval. Duplicate findings shown here are pre-approval estimates and account for manual exclusions.",
            }
        )

        update_set.update(
            {
                "duplicateFindings": preview_plan["duplicate_findings"],
                "duplicateCount": len(preview_plan["duplicate_findings"]),
                "ingestionReport": preview_report,
            }
        )
    else:
        delete_result = await db.chunks.delete_one(
            {
                "documentId": doc_id,
                "$or": [
                    {"position": source_chunk_position},
                    {"metadata.chunkPosition": source_chunk_position},
                ],
            }
        )

        current_chunk_count = int(doc.get("chunkCount") or 0)
        if delete_result.deleted_count > 0:
            update_set["chunkCount"] = current_chunk_count - 1 if current_chunk_count > 0 else 0

        current_findings = doc.get("duplicateFindings")
        if isinstance(current_findings, list):
            filtered_findings = []
            for finding in current_findings:
                if not isinstance(finding, dict):
                    filtered_findings.append(finding)
                    continue
                pos = finding.get("sourceChunkPosition")
                if isinstance(pos, int) and pos == source_chunk_position:
                    continue
                filtered_findings.append(finding)
            update_set["duplicateFindings"] = filtered_findings
            update_set["duplicateCount"] = len(filtered_findings)

        ingestion_report = doc.get("ingestionReport")
        if isinstance(ingestion_report, dict):
            updated_report = dict(ingestion_report)
            if delete_result.deleted_count > 0:
                updated_report["keptChunks"] = _decrement_report_count(updated_report.get("keptChunks"))
                updated_report["embeddingsGenerated"] = _decrement_report_count(updated_report.get("embeddingsGenerated"))
            update_set["ingestionReport"] = updated_report

    await db.documents.update_one({"_id": doc_id}, {"$set": update_set})

    await audit_log(
        action="document.duplicate.exclude",
        actor=user.email or user.userId,
        target=str(doc_id),
        meta={"sourceChunkPosition": source_chunk_position},
    )
    return Response(status_code=204)


@router.delete("/documents/{id}/chunks/{chunk_id}", status_code=204)
async def delete_document_chunk(
    id: str,
    chunk_id: str,
    user: AuthedUser = Depends(require_admin),
) -> Response:
    doc_id = _parse_id(id)
    parsed_chunk_id = _parse_id(chunk_id)
    db = await get_db()

    doc = await db.documents.find_one({"_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    chunk = await db.chunks.find_one({"_id": parsed_chunk_id, "documentId": doc_id})
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    await db.chunks.delete_one({"_id": parsed_chunk_id, "documentId": doc_id})

    current_chunk_count = int(doc.get("chunkCount") or 0)
    updated_chunk_count = current_chunk_count - 1 if current_chunk_count > 0 else 0

    ingestion_report = doc.get("ingestionReport")
    updated_report = None
    if isinstance(ingestion_report, dict):
        updated_report = dict(ingestion_report)
        updated_report["keptChunks"] = _decrement_report_count(updated_report.get("keptChunks"))
        updated_report["embeddingsGenerated"] = _decrement_report_count(updated_report.get("embeddingsGenerated"))

    update_set: dict[str, object] = {
        "chunkCount": updated_chunk_count,
        "updatedAt": datetime.now(timezone.utc),
    }
    if updated_report is not None:
        update_set["ingestionReport"] = updated_report

    await db.documents.update_one({"_id": doc_id}, {"$set": update_set})

    await audit_log(
        action="document.chunk.delete",
        actor=user.email or user.userId,
        target=str(doc_id),
        meta={"chunkId": parsed_chunk_id},
    )
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

    manual_excluded = _parse_source_positions(doc.get("manualExcludedChunkPositions"))

    plan = await _build_ingestion_plan(
        db,
        doc_id,
        cleaned_text,
        original_size=int(doc.get("originalSize") or len(cleaned_text)),
        final_size=int(doc.get("finalSize") or len(cleaned_text)),
        pii_count=int(doc.get("piiCount") or 0),
        excluded_source_positions=manual_excluded,
    )
    now = datetime.now(timezone.utc)
    next_document_version = int(doc.get("documentVersion") or 0) + 1
    ingestion_run_id = f"ingest-{doc_id}-{int(now.timestamp())}"

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
                    "lineage": {
                        "sourceDocumentId": doc_id,
                        "sourceDocumentVersion": next_document_version,
                        "sourceChunkPosition": idx,
                        "ingestionRunId": ingestion_run_id,
                    },
                },
                "sourceDocumentVersion": next_document_version,
                "ingestionRunId": ingestion_run_id,
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
                "documentVersion": next_document_version,
                "lastIngestionRunId": ingestion_run_id,
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
