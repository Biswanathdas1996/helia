from __future__ import annotations

import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthedUser, require_auth
from app.db import get_db, next_id
from app.schemas import CreateTicketBody, UpdateTicketBody
from app.serialize import serialize_ticket

router = APIRouter()


def _parse_id(raw: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid id")
    if n <= 0:
        raise HTTPException(status_code=400, detail="Invalid id")
    return n


@router.get("/tickets")
async def list_tickets(user: AuthedUser = Depends(require_auth)) -> list[dict[str, object]]:
    db = await get_db()
    is_admin = user.role == "admin"
    flt: dict[str, object] = {} if is_admin else {"userId": user.userId}
    rows = await db.tickets.find(flt).sort("createdAt", -1).to_list(length=None)
    return [serialize_ticket(r) for r in rows]


@router.post("/tickets", status_code=201)
async def create_ticket(
    body: CreateTicketBody, user: AuthedUser = Depends(require_auth)
) -> dict[str, object]:
    db = await get_db()
    now = datetime.now(timezone.utc)
    t = {
        "_id": await next_id("tickets"),
        "userId": user.userId,
        "subject": body.subject,
        "description": body.description,
        "priority": body.priority,
        "status": "open",
        "externalId": f"HEL-{random.randint(10000, 99999)}",
        "relatedMessageId": body.relatedMessageId,
        "lastUpdate": "Ticket opened",
        "createdAt": now,
        "updatedAt": now,
    }
    await db.tickets.insert_one(t)
    return serialize_ticket(t)


@router.get("/tickets/{id}")
async def get_ticket(id: str, user: AuthedUser = Depends(require_auth)) -> dict[str, object]:
    tid = _parse_id(id)
    db = await get_db()
    t = await db.tickets.find_one({"_id": tid})
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if user.role != "admin" and t.get("userId") != user.userId:
        raise HTTPException(status_code=403, detail="Forbidden")
    return serialize_ticket(t)


@router.patch("/tickets/{id}")
async def update_ticket(
    id: str,
    body: UpdateTicketBody,
    user: AuthedUser = Depends(require_auth),
) -> dict[str, object]:
    tid = _parse_id(id)
    db = await get_db()
    existing = await db.tickets.find_one({"_id": tid})
    if not existing:
        raise HTTPException(status_code=404, detail="Ticket not found")
    is_admin = user.role == "admin"
    if not is_admin and existing.get("userId") != user.userId:
        raise HTTPException(status_code=403, detail="Forbidden")

    updates: dict[str, object] = {"updatedAt": datetime.now(timezone.utc)}
    if body.status is not None and is_admin:
        updates["status"] = body.status
    if body.lastUpdate is not None:
        updates["lastUpdate"] = body.lastUpdate

    r = await db.tickets.find_one_and_update(
        {"_id": tid}, {"$set": updates}, return_document=True
    )
    return serialize_ticket(r or existing)
