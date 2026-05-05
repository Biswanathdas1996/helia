from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app import zoho
from app.audit import audit_log
from app.auth import AuthedUser, require_auth
from app.db import get_db, next_id
from app.schemas import CreateTicketBody, UpdateTicketBody
from app.serialize import serialize_ticket

router = APIRouter()
log = logging.getLogger("api-server.tickets")


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


@router.get("/tickets/active-summary")
async def active_summary(user: AuthedUser = Depends(require_auth)) -> dict[str, object]:
    """Proactive open-ticket banner data for the chat sidebar.

    Refreshes Zoho-side status for the user's open tickets so the UI shows
    the latest update without the user having to navigate away.
    """
    db = await get_db()
    rows = await db.tickets.find(
        {"userId": user.userId, "status": {"$in": ["open", "in_progress"]}}
    ).sort("updatedAt", -1).to_list(length=10)

    if zoho.is_configured():
        for r in rows:
            ext = r.get("externalId")
            if not ext or not str(ext).startswith("zoho:"):
                continue
            zid = str(ext).split(":", 1)[1]
            data = await zoho.get_ticket(zid)
            if not data:
                continue
            new_status = (data.get("status") or "").lower().replace(" ", "_") or r["status"]
            new_update = data.get("statusType") or data.get("status") or r.get("lastUpdate")
            await db.tickets.update_one(
                {"_id": r["_id"]},
                {
                    "$set": {
                        "status": new_status,
                        "lastUpdate": new_update,
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )
            r["status"] = new_status
            r["lastUpdate"] = new_update

    summary = [
        {
            "id": r["_id"],
            "subject": r["subject"],
            "status": r["status"],
            "lastUpdate": r.get("lastUpdate"),
            "externalId": r.get("externalId"),
            "updatedAt": r.get("updatedAt"),
        }
        for r in rows
    ]
    return {"openCount": len(summary), "tickets": summary}


@router.post("/tickets", status_code=201)
async def create_ticket(
    body: CreateTicketBody, user: AuthedUser = Depends(require_auth)
) -> dict[str, object]:
    db = await get_db()
    now = datetime.now(timezone.utc)

    external_id = f"HEL-{random.randint(10000, 99999)}"
    if zoho.is_configured():
        try:
            resp = await zoho.create_ticket(
                subject=body.subject,
                description=body.description,
                priority=body.priority,
                requester_email=user.email,
                requester_name=" ".join(filter(None, [user.firstName, user.lastName])).strip() or user.email,
            )
            if resp and resp.get("id"):
                external_id = f"zoho:{resp['id']}"
        except Exception as err:
            log.warning("zoho create_ticket failed, keeping local id: %s", err)

    t = {
        "_id": await next_id("tickets"),
        "userId": user.userId,
        "subject": body.subject,
        "description": body.description,
        "priority": body.priority,
        "status": "open",
        "externalId": external_id,
        "relatedMessageId": body.relatedMessageId,
        "lastUpdate": "Ticket opened",
        "createdAt": now,
        "updatedAt": now,
    }
    await db.tickets.insert_one(t)
    await audit_log(
        action="ticket.create",
        actor=user.email or user.userId,
        target=external_id,
        meta={"priority": body.priority, "subject": body.subject},
    )
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


@router.delete("/tickets/{id}", status_code=204)
async def delete_ticket(id: str, user: AuthedUser = Depends(require_auth)) -> None:
    tid = _parse_id(id)
    db = await get_db()
    existing = await db.tickets.find_one({"_id": tid})
    if not existing:
        raise HTTPException(status_code=404, detail="Ticket not found")

    is_admin = user.role == "admin"
    if not is_admin and existing.get("userId") != user.userId:
        raise HTTPException(status_code=403, detail="Forbidden")

    await db.tickets.delete_one({"_id": tid})
    await audit_log(
        action="ticket.delete",
        actor=user.email or user.userId,
        target=str(existing.get("externalId") or tid),
        meta={"ticketId": tid, "subject": existing.get("subject")},
    )
