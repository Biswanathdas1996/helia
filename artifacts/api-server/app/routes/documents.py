from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from app import embeddings as emb_lib
from app import ingestion_runs
from app import llm
from app.audit import audit_log
from app.auth import AuthedUser, require_admin
from app.db import get_db, next_id
from app.embeddings import cosine
from app.pii import detect_and_mask_pii
from app.schemas import CreateDocumentBody, RejectDocumentBody
from app.serialize import serialize_document
from app.tenant import normalize_governance, tenant_for
from app.text import chunk_text, count_tokens, jaccard, term_frequency, tokenize, top_key_phrases, top_keywords

router = APIRouter()
log = logging.getLogger("api-server.documents")

_DEDUP_JACCARD = 0.85
_DEDUP_COSINE = 0.92
_PREVIEW_JACCARD = 0.15
_PREVIEW_COSINE = 0.68

# Cap concurrent $vectorSearch aggregations during a single ingest so we don't
# saturate Atlas with hundreds of in-flight queries on long documents.
_VECTOR_SEARCH_CONCURRENCY = 8


def _dedup_debug_payload(*, preview_mode: bool, dedup_method: str | None = None) -> dict[str, object]:
    return {
        "mode": "preview" if preview_mode else "final",
        "thresholds": {
            "jaccard": _PREVIEW_JACCARD if preview_mode else _DEDUP_JACCARD,
            "cosine": _PREVIEW_COSINE if preview_mode else _DEDUP_COSINE,
        },
        "embeddingsAvailable": llm.embeddings_available(),
        "vectorSearchEnabled": emb_lib.vector_search_enabled(),
        "dedupMethod": dedup_method,
    }


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


_HEADING_PREFIX_RE = __import__("re").compile(r"^\s*\[[^\]\n]{1,500}\]\s*\n+")


def _strip_heading_prefix(text: str) -> str:
    """Drop the leading ``[Heading > Sub]\\n\\n`` decoration the structural
    chunker prepends, so hashes/jaccard compare bodies, not section labels.
    """
    if not text:
        return ""
    return _HEADING_PREFIX_RE.sub("", text, count=1)


def _chunk_hash(text: str) -> str:
    # Lower + whitespace-collapse so identical-but-reformatted chunks match.
    # Heading-path prefix is stripped so the same body under different
    # heading paths still dedupes by hash.
    body = _strip_heading_prefix(text or "")
    normalized = " ".join(body.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _atlas_score_to_cosine(score: float) -> float:
    # Atlas $vectorSearch returns score = (1 + cosine) / 2 for cosine similarity.
    return max(0.0, min(1.0, score * 2 - 1))


def _valid_cached_embeddings(doc: dict, cleaned_text: str) -> list[list[float]] | None:
    """Return cached preview embeddings iff they were produced from this exact cleanedText."""
    cached = doc.get("cachedEmbeddings")
    cached_hash = doc.get("cachedEmbeddingsHash")
    if not isinstance(cached, list) or not cached:
        return None
    if not isinstance(cached_hash, str) or cached_hash != _content_hash(cleaned_text):
        return None
    return cached  # type: ignore[return-value]


async def _hash_match_existing(
    db,
    chunk_hashes: list[str],
    excluded_positions: set[int],
    candidate_ids: list[int],
    doc_name_by_id: dict[int, str],
    raw_chunks: list[str],
    *,
    tenant_id: str | None = None,
) -> dict[int, dict]:
    """B: exact-match dedup via the chunkHash index. O(1) per chunk in Mongo."""
    if not (chunk_hashes and candidate_ids):
        return {}
    wanted = {h for i, h in enumerate(chunk_hashes) if i not in excluded_positions}
    if not wanted:
        return {}
    query: dict[str, object] = {"chunkHash": {"$in": list(wanted)}, "documentId": {"$in": candidate_ids}}
    if tenant_id:
        query["tenantId"] = tenant_id
    cursor = db.chunks.find(
        query,
        {"_id": 1, "documentId": 1, "chunkHash": 1},
    )
    existing_by_hash: dict[str, dict] = {}
    async for ec in cursor:
        h = ec.get("chunkHash")
        if h and h not in existing_by_hash:
            existing_by_hash[h] = ec

    matches: dict[int, dict] = {}
    for i, h in enumerate(chunk_hashes):
        if i in excluded_positions:
            continue
        ec = existing_by_hash.get(h)
        if not ec:
            continue
        matches[i] = {
            "snippet": raw_chunks[i],
            "similarity": 1.0,
            "method": "hash",
            "matchedDocumentId": ec["documentId"],
            "matchedDocumentName": doc_name_by_id.get(ec["documentId"]),
            "matchedChunkId": ec["_id"],
            "sourceChunkPosition": i,
        }
    return matches


async def _vector_search_match(
    db,
    embedding: list[float],
    candidate_ids: list[int],
    threshold: float,
    *,
    tenant_id: str | None = None,
) -> dict | None:
    """A: ask Atlas for the single nearest existing chunk; return it if above threshold."""
    rows = await emb_lib.vector_search(
        db, embedding, limit=1, doc_ids=candidate_ids, tenant_id=tenant_id
    )
    if not rows:
        return None
    row = rows[0]
    cos_sim = _atlas_score_to_cosine(float(row.get("score") or 0.0))
    if cos_sim < threshold:
        return None
    return {
        "row": row,
        "similarity": cos_sim,
    }


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
    cached_embeddings: list[list[float]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, object]:
    """Prepare chunks, dedup findings, and embeddings for an approval-time ingest.

    Layered dedup:
      1. chunkHash exact-match via index (fastest, catches verbatim copies).
      2. Atlas $vectorSearch as candidate generator when configured.
      3. Fallback: load corpus chunks and run cosine/Jaccard in Python.
    """
    raw_chunks = chunk_text(cleaned_text)
    excluded_positions = excluded_source_positions or set()
    active_jaccard = _PREVIEW_JACCARD if preview_mode else _DEDUP_JACCARD
    active_cosine = _PREVIEW_COSINE if preview_mode else _DEDUP_COSINE

    chunk_hashes = [_chunk_hash(c) for c in raw_chunks]

    # C: reuse pre-computed embeddings when caller passes them and shapes match.
    use_embeddings = llm.embeddings_available()
    new_embeddings: list[list[float]] = []
    embeddings_reused = False
    if use_embeddings and raw_chunks:
        if cached_embeddings is not None and len(cached_embeddings) == len(raw_chunks):
            new_embeddings = cached_embeddings
            embeddings_reused = True
        else:
            try:
                new_embeddings = await emb_lib.embed_batch(raw_chunks)
            except Exception as err:
                log.warning("embedding failed during ingest, falling back to Jaccard: %s", err)
                new_embeddings = []
                use_embeddings = False

    new_token_sets = [set(tokenize(_strip_heading_prefix(c))) for c in raw_chunks]

    doc_query: dict[str, object] = {"status": {"$in": list(candidate_statuses)}}
    if doc_id > 0:
        doc_query["_id"] = {"$ne": doc_id}
    if tenant_id:
        doc_query["tenantId"] = tenant_id
    candidate_docs = await db.documents.find(doc_query, {"_id": 1, "name": 1}).to_list(length=None)
    doc_name_by_id: dict[int, str] = {d["_id"]: d["name"] for d in candidate_docs}
    candidate_ids = list(doc_name_by_id.keys())

    # Stage 1 — hash-based exact matches.
    hash_matches = await _hash_match_existing(
        db, chunk_hashes, excluded_positions, candidate_ids, doc_name_by_id, raw_chunks,
        tenant_id=tenant_id,
    )

    # Stage 2 — decide whether to use $vectorSearch as candidate generator.
    use_vector_search = (
        use_embeddings
        and bool(new_embeddings)
        and emb_lib.vector_search_enabled()
        and bool(candidate_ids)
    )

    external_matches: dict[int, dict] = dict(hash_matches)
    positions_needing_external = [
        i for i in range(len(raw_chunks))
        if i not in excluded_positions and i not in hash_matches
    ]

    method_label_external = "hash"
    if use_vector_search and positions_needing_external:
        sem = asyncio.Semaphore(_VECTOR_SEARCH_CONCURRENCY)

        async def _vsearch_one(i: int) -> tuple[int, dict | None]:
            async with sem:
                hit = await _vector_search_match(
                    db, new_embeddings[i], candidate_ids, active_cosine,
                    tenant_id=tenant_id,
                )
            if not hit:
                return i, None
            row = hit["row"]
            return i, {
                "snippet": raw_chunks[i],
                "similarity": round(hit["similarity"], 3),
                "method": "embedding",
                "matchedDocumentId": row["documentId"],
                "matchedDocumentName": doc_name_by_id.get(row["documentId"]),
                "matchedChunkId": row["_id"],
                "sourceChunkPosition": i,
            }

        try:
            results = await asyncio.gather(
                *[_vsearch_one(i) for i in positions_needing_external]
            )
            for pos, match in results:
                if match:
                    external_matches[pos] = match
            method_label_external = "hash+vector-search"
        except Exception as err:
            log.warning("vector-search dedup failed, falling back to in-memory scan: %s", err)
            use_vector_search = False

    # Stage 3 — fallback: load remaining-doc chunks into memory only if we actually need to.
    existing_prepared: list[dict] = []
    if not use_vector_search and positions_needing_external and candidate_ids:
        proj: dict[str, object] = {"_id": 1, "content": 1, "documentId": 1}
        if use_embeddings:
            proj["embedding"] = 1
        chunk_query: dict[str, object] = {"documentId": {"$in": candidate_ids}}
        if tenant_id:
            chunk_query["tenantId"] = tenant_id
        existing_chunks = await db.chunks.find(chunk_query, proj).to_list(length=None)
        for c in existing_chunks:
            existing_content = _strip_heading_prefix(str(c.get("content") or ""))
            existing_prepared.append({
                "id": c["_id"],
                "documentId": c["documentId"],
                "set": set(tokenize(existing_content)),
                "embedding": c.get("embedding"),
            })
        method_label_external = "hash+embedding+jaccard" if use_embeddings else "hash+jaccard"

    def _legacy_embedding_match(i: int) -> dict | None:
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

    def _legacy_jaccard_match(i: int) -> dict | None:
        for e in existing_prepared:
            sim = jaccard(new_token_sets[i], e["set"])
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

    duplicate_findings: list[dict[str, object]] = []
    keep_idx: list[int] = []

    for i in range(len(raw_chunks)):
        if i in excluded_positions:
            continue

        # Within-doc dedup (small, O(N²) is fine here).
        is_dup = False
        for k in keep_idx:
            sim = jaccard(new_token_sets[i], new_token_sets[k])
            if sim >= active_jaccard:
                is_dup = True
                duplicate_findings.append({
                    "snippet": raw_chunks[i],
                    "similarity": round(sim, 3),
                    "method": "jaccard",
                    "matchedDocumentId": None,
                    "matchedDocumentName": "(within this document)",
                    "matchedChunkId": None,
                    "sourceChunkPosition": i,
                })
                break
        if is_dup:
            continue

        # External match: hash → vector-search → fallback Jaccard scan.
        match = external_matches.get(i)
        if match is None and existing_prepared:
            match = _legacy_embedding_match(i) or _legacy_jaccard_match(i)
        if match:
            duplicate_findings.append(match)
            continue

        keep_idx.append(i)

    kept_chunks = [raw_chunks[i] for i in keep_idx]
    kept_chunk_hashes = [chunk_hashes[i] for i in keep_idx]
    kept_embeddings = [new_embeddings[i] for i in keep_idx] if new_embeddings else []

    overall_tf = term_frequency(tokenize(cleaned_text))
    keywords = top_keywords(overall_tf, 12)

    dedup_method = method_label_external
    if preview_mode:
        dedup_method = f"{dedup_method} (preview)"
    if embeddings_reused:
        dedup_method = f"{dedup_method} (cached-embeddings)"

    return {
        "kept_chunks": kept_chunks,
        "kept_chunk_hashes": kept_chunk_hashes,
        "kept_embeddings": kept_embeddings,
        "all_embeddings": new_embeddings,
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
            "dedupMethod": dedup_method,
        },
    }


_SOURCE_TYPE_FROM_MIME: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "docx",
    "application/vnd.ms-powerpoint": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "docx",
    "application/vnd.ms-excel": "docx",
    "text/plain": "txt",
}


@router.post("/documents/upload", status_code=201)
async def upload_document(
    request: Request,
    user: AuthedUser = Depends(require_admin),
):
    """Multipart upload → extract → create. Single hop replacing the old
    extract-then-POST dance.

    Form fields:
      - ``file``: required UploadFile
      - ``name``: optional, defaults to filename
      - ``tags``: optional comma-separated string
      - ``governance``: optional JSON-encoded governance block
    """
    from fastapi import UploadFile  # imported lazily to keep top of file lean

    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="`file` field is required")

    raw = await upload.read()
    max_bytes = int(__import__("os").environ.get("UPLOAD_MAX_BYTES", str(50 * 1024 * 1024)))
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_bytes} bytes)")
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Imported here to avoid a hard dependency at module import time.
    from app.routes.extract import EXT_MAP, MIME_MAP
    from app.local_extract import extract_text_locally

    detected = MIME_MAP.get(upload.content_type or "")
    if detected is None and upload.filename and "." in upload.filename:
        ext = upload.filename.rsplit(".", 1)[-1].lower()
        guessed = EXT_MAP.get(ext)
        if guessed:
            detected = MIME_MAP.get(guessed)
    if not detected:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {upload.content_type}")

    if detected == "text/plain":
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
    else:
        try:
            text = extract_text_locally(detected, raw) or ""
        except Exception as err:
            log.warning("local extraction failed for %s: %s", upload.filename, err)
            raise HTTPException(status_code=422, detail="Could not extract text from upload")

    if not text.strip():
        raise HTTPException(status_code=422, detail="Extracted text is empty")

    source_type = _SOURCE_TYPE_FROM_MIME.get(detected, "text")
    name = str(form.get("name") or upload.filename or "Untitled").strip() or "Untitled"
    tags_raw = form.get("tags")
    tags: list[str] = []
    if isinstance(tags_raw, str) and tags_raw.strip():
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    governance_raw = form.get("governance")
    governance_input = None
    if isinstance(governance_raw, str) and governance_raw.strip():
        try:
            import json as _json

            parsed = _json.loads(governance_raw)
            if isinstance(parsed, dict):
                from app.schemas import GovernanceInput

                governance_input = GovernanceInput(**parsed)
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Invalid governance JSON: {err}")

    body = CreateDocumentBody(
        name=name,
        sourceType=source_type,  # type: ignore[arg-type]
        content=text,
        tags=tags or None,
        governance=governance_input,
    )
    return await create_document(body, request, user)


@router.get("/documents")
async def list_documents(user: AuthedUser = Depends(require_admin)) -> list[dict[str, object]]:
    db = await get_db()
    tenant_id = tenant_for(user)
    rows = await db.documents.find({"tenantId": tenant_id}).sort("createdAt", -1).to_list(length=None)
    return [serialize_document(r) for r in rows]


@router.post("/documents", status_code=201)
async def create_document(
    body: CreateDocumentBody,
    request: Request,
    user: AuthedUser = Depends(require_admin),
) -> dict[str, object]:
    db = await get_db()
    tenant_id = tenant_for(user)

    cleaned, findings = detect_and_mask_pii(body.content)
    content_hash = _content_hash(cleaned)
    existing_exact = await db.documents.find_one(
        {
            "tenantId": tenant_id,
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
    # duplicate chunks before approval. Approval still recomputes the final plan,
    # but we cache the preview's embeddings on the pending doc so it can skip
    # re-paying the LLM call when cleanedText is unchanged.
    preview_duplicate_findings: list[dict[str, object]] = []
    cached_embeddings: list[list[float]] | None = None
    cached_embeddings_hash: str | None = None
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
            tenant_id=tenant_id,
        )
        preview_duplicate_findings = list(preview_plan.get("duplicate_findings") or [])
        all_emb = preview_plan.get("all_embeddings") or []
        if isinstance(all_emb, list) and all_emb:
            cached_embeddings = all_emb
            cached_embeddings_hash = content_hash
    except Exception as err:
        log.warning("dedup preview failed during create; continuing without preview: %s", err)

    now = datetime.now(timezone.utc)

    doc_id = await next_id("documents")
    root_document_id = doc_id
    governance = normalize_governance(
        body.governance.model_dump() if body.governance else None,
        owner=user.email or user.userId,
        created_at=now,
    )
    doc = {
        "_id": doc_id,
        "name": body.name,
        "sourceType": body.sourceType,
        "status": "pending",
        "tenantId": tenant_id,
        "governance": governance,
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
        "embeddingVersion": None,
        "cachedEmbeddings": cached_embeddings,
        "cachedEmbeddingsHash": cached_embeddings_hash,
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
        meta={"status": "pending", "pii": len(findings), "tenantId": tenant_id},
    )

    return serialize_document(doc)


@router.get("/documents/{id}")
async def get_document(id: str, user: AuthedUser = Depends(require_admin)) -> dict[str, object]:
    doc_id = _parse_id(id)
    db = await get_db()
    tenant_id = tenant_for(user)
    doc = await db.documents.find_one({"_id": doc_id, "tenantId": tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    ingestion_report = doc.get("ingestionReport", {})
    dedup_method = None
    if isinstance(ingestion_report, dict):
        raw = ingestion_report.get("dedupMethod")
        if isinstance(raw, str) and raw.strip():
            dedup_method = raw

    base = serialize_document(doc)
    base.update(
        {
            "originalText": doc.get("originalText", ""),
            "cleanedText": doc.get("cleanedText", ""),
            "piiFindings": doc.get("piiFindings", []),
            "duplicateFindings": doc.get("duplicateFindings", []),
            "ingestionReport": ingestion_report,
            "dedupDebug": _dedup_debug_payload(
                preview_mode=str(doc.get("status") or "") == "pending",
                dedup_method=dedup_method,
            ),
        }
    )
    return base


@router.delete("/documents/{id}", status_code=204)
async def delete_document(id: str, user: AuthedUser = Depends(require_admin)) -> Response:
    doc_id = _parse_id(id)
    db = await get_db()
    tenant_id = tenant_for(user)
    doc = await db.documents.find_one({"_id": doc_id, "tenantId": tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    governance = doc.get("governance") or {}
    if governance.get("legalHold"):
        raise HTTPException(status_code=423, detail="Document is on legal hold")
    await db.chunks.delete_many({"documentId": doc_id, "tenantId": tenant_id})
    await db.documents.delete_one({"_id": doc_id, "tenantId": tenant_id})
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
    tenant_id = tenant_for(user)

    doc = await db.documents.find_one({"_id": doc_id, "tenantId": tenant_id})
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

        cached_emb = _valid_cached_embeddings(doc, cleaned_text)

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
            cached_embeddings=cached_emb,
            tenant_id=tenant_id,
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
                "tenantId": tenant_id,
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
    tenant_id = tenant_for(user)

    doc = await db.documents.find_one({"_id": doc_id, "tenantId": tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    chunk = await db.chunks.find_one({"_id": parsed_chunk_id, "documentId": doc_id, "tenantId": tenant_id})
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    await db.chunks.delete_one({"_id": parsed_chunk_id, "documentId": doc_id, "tenantId": tenant_id})

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


async def _execute_ingest(
    *,
    doc_id: int,
    tenant_id: str,
    run_id: int,
    actor: str,
) -> None:
    """Worker that performs chunking, embedding, dedup, and chunk insert.

    Runs inside a FastAPI BackgroundTask so the approve HTTP call returns
    immediately. State is reflected in ``ingestion_runs`` so the UI can poll.
    """
    async def _work(ctx: ingestion_runs.RunContext) -> dict[str, object]:
        db = await get_db()
        doc = await db.documents.find_one({"_id": doc_id, "tenantId": tenant_id})
        if not doc:
            raise RuntimeError(f"document {doc_id} disappeared before ingest")

        cleaned_text = str(doc.get("cleanedText") or "") or str(doc.get("originalText") or "")
        manual_excluded = _parse_source_positions(doc.get("manualExcludedChunkPositions"))

        await ctx.log("planning chunks + dedup", progress=10)
        plan = await _build_ingestion_plan(
            db,
            doc_id,
            cleaned_text,
            original_size=int(doc.get("originalSize") or len(cleaned_text)),
            final_size=int(doc.get("finalSize") or len(cleaned_text)),
            pii_count=int(doc.get("piiCount") or 0),
            excluded_source_positions=manual_excluded,
            cached_embeddings=_valid_cached_embeddings(doc, cleaned_text),
            tenant_id=tenant_id,
        )

        now = datetime.now(timezone.utc)
        next_document_version = int(doc.get("documentVersion") or 0) + 1
        ingestion_run_id = f"ingest-{doc_id}-{run_id}"
        active_emb_version = emb_lib.embedding_version()

        await ctx.log("clearing prior chunks", progress=40)
        await db.chunks.delete_many({"documentId": doc_id, "tenantId": tenant_id})

        kept_chunks = plan["kept_chunks"]
        kept_chunk_hashes = plan.get("kept_chunk_hashes") or []
        kept_embeddings = plan["kept_embeddings"]
        document_name = str(doc.get("name") or "Untitled")
        source_type = str(doc.get("sourceType") or "text")
        governance = doc.get("governance") or {}

        await ctx.log(f"writing {len(kept_chunks)} chunks", progress=70)
        if kept_chunks:
            chunk_docs: list[dict] = []
            for idx, content in enumerate(kept_chunks):
                tokens = tokenize(content)
                token_count = count_tokens(content) or len(tokens)
                chunk_phrases = top_key_phrases(content, 6)
                entry: dict = {
                    "_id": await next_id("chunks"),
                    "documentId": doc_id,
                    "tenantId": tenant_id,
                    "position": idx,
                    "content": content,
                    "chunkHash": kept_chunk_hashes[idx] if idx < len(kept_chunk_hashes) else _chunk_hash(content),
                    "tokenCount": token_count,
                    "embeddingVersion": active_emb_version if kept_embeddings else None,
                    "embeddingModel": plan["embedding_model"],
                    "metadata": {
                        "fileName": document_name,
                        "pageNumber": idx + 1,
                        "keyPhrases": chunk_phrases,
                        "chunkPosition": idx,
                        "tokenCount": token_count,
                        "sourceType": source_type,
                        "sensitivity": governance.get("sensitivity"),
                        "dataResidency": governance.get("dataResidency"),
                        "lineage": {
                            "sourceDocumentId": doc_id,
                            "sourceDocumentVersion": next_document_version,
                            "sourceChunkPosition": idx,
                            "ingestionRunId": ingestion_run_id,
                            "runRowId": run_id,
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

        await ctx.log("finalizing document", progress=90)
        await db.documents.update_one(
            {"_id": doc_id, "tenantId": tenant_id},
            {
                "$set": {
                    "status": "approved",
                    "rejectionReason": None,
                    "duplicateCount": len(plan["duplicate_findings"]),
                    "chunkCount": len(kept_chunks),
                    "duplicateFindings": plan["duplicate_findings"],
                    "keywords": plan["keywords"],
                    "embeddingModel": plan["embedding_model"],
                    "embeddingVersion": active_emb_version if kept_embeddings else None,
                    "ingestionReport": plan["ingestion_report"],
                    "documentVersion": next_document_version,
                    "lastIngestionRunId": ingestion_run_id,
                    "updatedAt": now,
                },
                "$unset": {"cachedEmbeddings": "", "cachedEmbeddingsHash": ""},
            },
        )

        await audit_log(
            action="document.approve.complete",
            actor=actor,
            target=str(doc_id),
            meta={
                "chunks": len(kept_chunks),
                "dupes": len(plan["duplicate_findings"]),
                "runId": run_id,
                "tenantId": tenant_id,
            },
        )
        return {
            "chunks": len(kept_chunks),
            "duplicates": len(plan["duplicate_findings"]),
            "embeddingVersion": active_emb_version if kept_embeddings else None,
        }

    try:
        await ingestion_runs.execute(run_id, _work)
    except Exception:
        # On failure leave the doc in pending so admin can retry.
        try:
            db = await get_db()
            await db.documents.update_one(
                {"_id": doc_id, "tenantId": tenant_id, "status": {"$ne": "approved"}},
                {"$set": {"status": "pending", "updatedAt": datetime.now(timezone.utc)}},
            )
        except Exception:
            log.exception("could not roll document %s back to pending", doc_id)
        raise


@router.post("/documents/{id}/approve", status_code=202)
async def approve_document(
    id: str,
    background: BackgroundTasks,
    user: AuthedUser = Depends(require_admin),
) -> dict[str, object]:
    doc_id = _parse_id(id)
    db = await get_db()
    tenant_id = tenant_for(user)

    doc = await db.documents.find_one({"_id": doc_id, "tenantId": tenant_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.get("status") == "running" or doc.get("status") == "ingesting":
        raise HTTPException(status_code=409, detail="Ingestion already in progress")

    run = await ingestion_runs.create_run(
        document_id=doc_id,
        tenant_id=tenant_id,
        actor=user.email or user.userId,
        document_version=int(doc.get("documentVersion") or 0) + 1,
    )

    await db.documents.update_one(
        {"_id": doc_id, "tenantId": tenant_id},
        {"$set": {"status": "ingesting", "lastIngestionRunId": run["_id"], "updatedAt": datetime.now(timezone.utc)}},
    )

    background.add_task(
        _execute_ingest,
        doc_id=doc_id,
        tenant_id=tenant_id,
        run_id=run["_id"],
        actor=user.email or user.userId,
    )

    await audit_log(
        action="document.approve.queued",
        actor=user.email or user.userId,
        target=str(doc_id),
        meta={"runId": run["_id"], "tenantId": tenant_id},
    )

    return {
        "status": "queued",
        "runId": run["_id"],
        "documentId": doc_id,
        "pollUrl": f"/api/documents/{doc_id}/runs/{run['_id']}",
    }


@router.get("/documents/{id}/runs")
async def list_document_runs(
    id: str,
    user: AuthedUser = Depends(require_admin),
) -> list[dict[str, object]]:
    doc_id = _parse_id(id)
    db = await get_db()
    tenant_id = tenant_for(user)
    doc = await db.documents.find_one({"_id": doc_id, "tenantId": tenant_id}, {"_id": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    runs = await ingestion_runs.list_runs_for_document(doc_id)
    return [ingestion_runs.serialize_run(r) for r in runs]


@router.get("/documents/{id}/runs/{run_id}")
async def get_document_run(
    id: str,
    run_id: str,
    user: AuthedUser = Depends(require_admin),
) -> dict[str, object]:
    doc_id = _parse_id(id)
    parsed_run_id = _parse_id(run_id)
    tenant_id = tenant_for(user)
    run = await ingestion_runs.get_run(parsed_run_id)
    if not run or run.get("documentId") != doc_id or run.get("tenantId") != tenant_id:
        raise HTTPException(status_code=404, detail="Ingestion run not found")
    return ingestion_runs.serialize_run(run)


@router.post("/documents/{id}/reject")
async def reject_document(
    id: str,
    body: RejectDocumentBody | None = None,
    user: AuthedUser = Depends(require_admin),
) -> dict[str, object]:
    doc_id = _parse_id(id)
    db = await get_db()
    tenant_id = tenant_for(user)
    reason = body.reason if body else None
    r = await db.documents.find_one_and_update(
        {"_id": doc_id, "tenantId": tenant_id},
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
