"""Audit log writes.

Best-effort: a Mongo write failure must never fail the parent request, so
all errors are swallowed with a warning.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("api-server.audit")


async def audit_log(
    *,
    action: str,
    actor: Optional[str],
    target: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    try:
        from app.db import get_db, next_id
        db = await get_db()
        await db.audit_logs.insert_one(
            {
                "_id": await next_id("audit_logs"),
                "action": action,
                "actor": actor,
                "target": target,
                "meta": meta or {},
                "createdAt": datetime.now(timezone.utc),
            }
        )
    except Exception as err:
        log.warning("audit log write failed for %s: %s", action, err)
