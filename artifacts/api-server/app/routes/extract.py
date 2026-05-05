from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import AuthedUser, require_admin
from app.local_extract import extract_text_locally
from app.pwc_ai import extract_text_from_base64

router = APIRouter()
log = logging.getLogger("api-server.extract")

MIME_MAP: dict[str, str] = {
    # Images
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
    "image/tiff": "image/tiff",
    # Documents
    "application/pdf": "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword": "application/msword",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint": "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel": "application/vnd.ms-excel",
    # Plain text
    "text/plain": "text/plain",
    "text/markdown": "text/plain",
    "text/csv": "text/plain",
    "application/json": "text/plain",
}

EXT_MAP: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    "txt": "text/plain",
    "md": "text/plain",
    "csv": "text/plain",
    "json": "text/plain",
}

MAX_BYTES = 50 * 1024 * 1024
AI_FALLBACK_MAX_BYTES = 250 * 1024


def _ext_to_mime(filename: str) -> str | None:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_MAP.get(ext)


@router.post("/documents/extract")
async def extract_document(
    file: UploadFile = File(...),
    _: AuthedUser = Depends(require_admin),
) -> dict[str, str]:
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    detected = MIME_MAP.get(file.content_type or "")
    if detected is None and file.filename:
        guessed = _ext_to_mime(file.filename)
        if guessed:
            detected = MIME_MAP.get(guessed)

    if not detected:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                "Supported: PDF, DOCX, PPTX, XLSX, images, TXT, MD, CSV, JSON."
            ),
        )

    if detected == "text/plain":
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return {"text": text, "filename": file.filename or ""}

    # Prefer deterministic parsers for office/pdf docs to avoid LLM token limits.
    try:
        local_text = extract_text_locally(detected, raw)
        if local_text and local_text.strip():
            return {"text": local_text, "filename": file.filename or ""}
    except Exception as err:
        log.warning("local extraction failed for %s: %s", file.filename, err)

    if len(raw) > AI_FALLBACK_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "File is too large for AI fallback extraction after local parsing failed. "
                "Please upload a smaller file or paste the text content directly."
            ),
        )

    try:
        b64 = base64.b64encode(raw).decode("ascii")
        text = await extract_text_from_base64(detected, b64, file.filename or "")
        return {"text": text, "filename": file.filename or ""}
    except Exception as err:
        msg = str(err)
        if "ContextWindowExceededError" in msg or "maximum number of tokens" in msg:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Extraction exceeded model context window. "
                    "Please upload a smaller file or paste text directly."
                ),
            )
        raise HTTPException(status_code=502, detail="Extraction service failed. Please try again.")
