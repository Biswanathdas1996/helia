"""Response shaping helpers shared across routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def iso(dt: Any) -> str:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # JS-style: YYYY-MM-DDTHH:MM:SS.mmmZ
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return str(dt)


def serialize_document(doc: dict[str, Any]) -> dict[str, Any]:
    from app.tenant import serialize_governance  # local import to avoid cycle

    return {
        "id": doc["_id"],
        "name": doc["name"],
        "sourceType": doc["sourceType"],
        "status": doc["status"],
        "originalSize": doc["originalSize"],
        "finalSize": doc["finalSize"],
        "piiCount": doc["piiCount"],
        "duplicateCount": doc["duplicateCount"],
        "chunkCount": doc["chunkCount"],
        "tags": doc.get("tags", []),
        "keywords": doc.get("keywords", []),
        "rootDocumentId": doc.get("rootDocumentId", doc.get("_id")),
        "parentDocumentId": doc.get("parentDocumentId"),
        "documentVersion": int(doc.get("documentVersion") or 0),
        "lastIngestionRunId": doc.get("lastIngestionRunId"),
        "createdBy": doc.get("createdBy"),
        "rejectionReason": doc.get("rejectionReason"),
        "tenantId": doc.get("tenantId"),
        "governance": serialize_governance(doc.get("governance")),
        "embeddingVersion": doc.get("embeddingVersion"),
        "embeddingModel": doc.get("embeddingModel"),
        "createdAt": iso(doc["createdAt"]),
        "updatedAt": iso(doc["updatedAt"]),
    }


def serialize_conversation(
    c: dict[str, Any], *, last_preview: str | None = None, message_count: int = 0
) -> dict[str, Any]:
    return {
        "id": c["_id"],
        "title": c["title"],
        "lastMessagePreview": last_preview,
        "messageCount": message_count,
        "createdAt": iso(c["createdAt"]),
        "updatedAt": iso(c["updatedAt"]),
    }


def serialize_message(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": m["_id"],
        "conversationId": m["conversationId"],
        "role": m["role"],
        "kind": m.get("kind"),
        "content": m["content"],
        "citations": m.get("citations", []),
        "canAnswer": m.get("canAnswer"),
        "latencyMs": m.get("latencyMs"),
        "rating": m.get("rating"),
        "rewrittenQuery": m.get("rewrittenQuery"),
        "ticketId": m.get("ticketId"),
        "imageDataUrl": m.get("imageDataUrl"),
        "createdAt": iso(m["createdAt"]),
    }


def serialize_ticket(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t["_id"],
        "subject": t["subject"],
        "description": t["description"],
        "priority": t["priority"],
        "status": t["status"],
        "externalId": t.get("externalId"),
        "createdBy": t["userId"],
        "relatedMessageId": t.get("relatedMessageId"),
        "lastUpdate": t.get("lastUpdate"),
        "createdAt": iso(t["createdAt"]),
        "updatedAt": iso(t["updatedAt"]),
    }
