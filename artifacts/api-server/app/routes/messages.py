from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthedUser, require_auth
from app.db import get_db
from app.schemas import RateMessageBody
from app.serialize import serialize_message

router = APIRouter()


@router.post("/messages/{id}/feedback")
async def rate_message(
    id: str,
    body: RateMessageBody,
    user: AuthedUser = Depends(require_auth),
) -> dict[str, object]:
    try:
        msg_id = int(id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid id")
    if msg_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid id")

    db = await get_db()
    msg = await db.messages.find_one({"_id": msg_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    convo = await db.conversations.find_one({"_id": msg["conversationId"]})
    if not convo or convo.get("userId") != user.userId:
        raise HTTPException(status_code=403, detail="Forbidden")

    r = await db.messages.find_one_and_update(
        {"_id": msg_id},
        {"$set": {"rating": body.rating, "feedbackComment": body.comment}},
        return_document=True,
    )
    if not r:
        raise HTTPException(status_code=404, detail="Message not found")
    return serialize_message(r)
