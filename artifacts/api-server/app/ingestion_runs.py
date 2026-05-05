"""Background ingestion runs.

A run row is created in ``ingestion_runs`` when an admin approves a document.
The actual ingest (chunking + embedding + dedup + insert) executes in a
FastAPI BackgroundTask, so the HTTP request returns immediately and large
documents do not time out the worker.

Run rows are queryable via ``GET /api/documents/{id}/runs/{runId}`` so the
admin UI can poll status. Failures are recorded with the exception text and
the document is left in ``pending`` so the admin can retry.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from app.db import get_db, next_id

log = logging.getLogger("api-server.ingestion_runs")

RunStatus = str  # "queued" | "running" | "succeeded" | "failed"

_MAX_LOG_LINES = 200


async def create_run(
    *,
    document_id: int,
    tenant_id: str,
    actor: str,
    document_version: int,
) -> dict[str, Any]:
    db = await get_db()
    now = datetime.now(timezone.utc)
    run_id = await next_id("ingestion_runs")
    doc = {
        "_id": run_id,
        "documentId": document_id,
        "tenantId": tenant_id,
        "actor": actor,
        "documentVersion": document_version,
        "status": "queued",
        "progress": 0,
        "startedAt": None,
        "finishedAt": None,
        "error": None,
        "log": [],
        "createdAt": now,
        "updatedAt": now,
    }
    await db.ingestion_runs.insert_one(doc)
    return doc


async def mark_running(run_id: int) -> None:
    db = await get_db()
    now = datetime.now(timezone.utc)
    await db.ingestion_runs.update_one(
        {"_id": run_id},
        {"$set": {"status": "running", "startedAt": now, "updatedAt": now}},
    )


async def append_log(run_id: int, line: str, *, progress: Optional[int] = None) -> None:
    db = await get_db()
    update: dict[str, Any] = {
        "$push": {
            "log": {"$each": [{"ts": datetime.now(timezone.utc), "msg": line[:500]}], "$slice": -_MAX_LOG_LINES}
        },
        "$set": {"updatedAt": datetime.now(timezone.utc)},
    }
    if progress is not None:
        update["$set"]["progress"] = max(0, min(100, int(progress)))
    await db.ingestion_runs.update_one({"_id": run_id}, update)


async def mark_succeeded(run_id: int, *, summary: dict[str, Any]) -> None:
    db = await get_db()
    now = datetime.now(timezone.utc)
    await db.ingestion_runs.update_one(
        {"_id": run_id},
        {
            "$set": {
                "status": "succeeded",
                "progress": 100,
                "finishedAt": now,
                "updatedAt": now,
                "summary": summary,
            }
        },
    )


async def mark_failed(run_id: int, *, error: str) -> None:
    db = await get_db()
    now = datetime.now(timezone.utc)
    await db.ingestion_runs.update_one(
        {"_id": run_id},
        {
            "$set": {
                "status": "failed",
                "finishedAt": now,
                "updatedAt": now,
                "error": error[:2000],
            }
        },
    )


async def get_run(run_id: int) -> Optional[dict[str, Any]]:
    db = await get_db()
    return await db.ingestion_runs.find_one({"_id": run_id})


async def list_runs_for_document(document_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    db = await get_db()
    cursor = (
        db.ingestion_runs.find({"documentId": document_id})
        .sort("createdAt", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


def serialize_run(r: dict[str, Any]) -> dict[str, Any]:
    def _iso(dt: Any) -> Optional[str]:
        if not isinstance(dt, datetime):
            return None
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    log_entries = r.get("log") or []
    return {
        "id": r.get("_id"),
        "documentId": r.get("documentId"),
        "tenantId": r.get("tenantId"),
        "actor": r.get("actor"),
        "documentVersion": r.get("documentVersion"),
        "status": r.get("status"),
        "progress": r.get("progress", 0),
        "error": r.get("error"),
        "summary": r.get("summary"),
        "log": [
            {"ts": _iso(e.get("ts")), "msg": e.get("msg")}
            for e in log_entries
            if isinstance(e, dict)
        ],
        "startedAt": _iso(r.get("startedAt")),
        "finishedAt": _iso(r.get("finishedAt")),
        "createdAt": _iso(r.get("createdAt")),
        "updatedAt": _iso(r.get("updatedAt")),
    }


async def execute(
    run_id: int,
    work: Callable[["RunContext"], Awaitable[dict[str, Any]]],
) -> None:
    """Run a unit of work inside a tracked ingestion_run row.

    The callable receives a ``RunContext`` it can use to log progress. Any
    exception is recorded against the run; the caller decides what state the
    document should land in.
    """
    ctx = RunContext(run_id=run_id)
    try:
        await mark_running(run_id)
        summary = await work(ctx)
        await mark_succeeded(run_id, summary=summary or {})
    except asyncio.CancelledError:
        await mark_failed(run_id, error="cancelled")
        raise
    except Exception as err:
        log.exception("ingestion run %s failed", run_id)
        await mark_failed(run_id, error=f"{type(err).__name__}: {err}")


class RunContext:
    def __init__(self, *, run_id: int) -> None:
        self.run_id = run_id

    async def log(self, msg: str, *, progress: Optional[int] = None) -> None:
        await append_log(self.run_id, msg, progress=progress)
