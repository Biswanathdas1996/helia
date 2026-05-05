from fastapi import APIRouter, Depends, HTTPException

from app import agent_memory
from app.audit import audit_log
from app.auth import AuthedUser, require_auth
from app.db import get_db

router = APIRouter()


@router.get("/me")
async def get_me(user: AuthedUser = Depends(require_auth)) -> dict[str, object]:
    return {
        "userId": user.userId,
        "email": user.email,
        "firstName": user.firstName,
        "lastName": user.lastName,
        "imageUrl": user.imageUrl,
        "role": user.role,
    }


@router.delete("/me/memory")
async def clear_me_memory(user: AuthedUser = Depends(require_auth)) -> dict[str, object]:
    try:
        mem0_cleared = await agent_memory.clear_user_memory(user.userId)
    except RuntimeError as err:
        raise HTTPException(status_code=502, detail="Failed to clear long-term memory") from err

    db = await get_db()
    convo_rows = await db.conversations.find({"userId": user.userId}, {"_id": 1}).to_list(length=None)
    conversation_ids = [row["_id"] for row in convo_rows]
    deleted_conversations = len(conversation_ids)
    deleted_messages = 0

    if conversation_ids:
        deleted_messages = await db.messages.count_documents({"conversationId": {"$in": conversation_ids}})
        await db.messages.delete_many({"conversationId": {"$in": conversation_ids}})

    if deleted_conversations:
        await db.conversations.delete_many({"userId": user.userId})

    await audit_log(
        action="me.memory.clear",
        actor=user.email,
        target=user.userId,
        meta={
            "deletedConversations": deleted_conversations,
            "deletedMessages": deleted_messages,
            "mem0Cleared": mem0_cleared,
        },
    )

    return {
        "deletedConversations": deleted_conversations,
        "deletedMessages": deleted_messages,
        "mem0Cleared": mem0_cleared,
    }
