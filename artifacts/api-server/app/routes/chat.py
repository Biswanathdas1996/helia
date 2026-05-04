from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response

from app.auth import AuthedUser, require_auth
from app.db import get_db, next_id
from app.pwc_ai import ChatTurn, chat as llm_chat
from app.schemas import CreateConversationBody, SendMessageBody
from app.serialize import serialize_conversation, serialize_message

router = APIRouter()
log = logging.getLogger("api-server.chat")


def _parse_id(raw: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid id")
    if n <= 0:
        raise HTTPException(status_code=400, detail="Invalid id")
    return n


@router.get("/chat/conversations")
async def list_conversations(user: AuthedUser = Depends(require_auth)) -> list[dict[str, object]]:
    db = await get_db()
    convos = (
        await db.conversations.find({"userId": user.userId})
        .sort("updatedAt", -1)
        .to_list(length=None)
    )
    if not convos:
        return []

    ids = [c["_id"] for c in convos]
    counts_pipe = [
        {"$match": {"conversationId": {"$in": ids}}},
        {"$group": {"_id": "$conversationId", "count": {"$sum": 1}}},
    ]
    count_rows = await db.messages.aggregate(counts_pipe).to_list(length=None)
    count_map = {r["_id"]: r["count"] for r in count_rows}

    recent = (
        await db.messages.find(
            {"conversationId": {"$in": ids}},
            {"conversationId": 1, "content": 1, "createdAt": 1},
        )
        .sort("createdAt", -1)
        .to_list(length=None)
    )
    previews: dict[int, str] = {}
    for r in recent:
        cid = r["conversationId"]
        if cid not in previews:
            previews[cid] = (r.get("content") or "")[:120]

    return [
        serialize_conversation(
            c, message_count=count_map.get(c["_id"], 0), last_preview=previews.get(c["_id"])
        )
        for c in convos
    ]


@router.post("/chat/conversations", status_code=201)
async def create_conversation(
    body: CreateConversationBody | None = None,
    user: AuthedUser = Depends(require_auth),
) -> dict[str, object]:
    db = await get_db()
    now = datetime.now(timezone.utc)
    title = (body.title if body and body.title else "New conversation")
    convo = {
        "_id": await next_id("conversations"),
        "userId": user.userId,
        "title": title,
        "createdAt": now,
        "updatedAt": now,
    }
    await db.conversations.insert_one(convo)
    return serialize_conversation(convo, message_count=0)


@router.get("/chat/conversations/{id}")
async def get_conversation(id: str, user: AuthedUser = Depends(require_auth)) -> dict[str, object]:
    cid = _parse_id(id)
    db = await get_db()
    c = await db.conversations.find_one({"_id": cid, "userId": user.userId})
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = await db.messages.find({"conversationId": cid}).sort("createdAt", 1).to_list(length=None)
    last_preview = (msgs[-1]["content"][:120] if msgs else None)
    return {
        "conversation": serialize_conversation(c, message_count=len(msgs), last_preview=last_preview),
        "messages": [serialize_message(m) for m in msgs],
    }


@router.delete("/chat/conversations/{id}", status_code=204)
async def delete_conversation(id: str, user: AuthedUser = Depends(require_auth)) -> Response:
    cid = _parse_id(id)
    db = await get_db()
    r = await db.conversations.delete_one({"_id": cid, "userId": user.userId})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.messages.delete_many({"conversationId": cid})
    return Response(status_code=204)


@router.post("/chat/conversations/{id}/messages")
async def send_message(
    id: str,
    body: SendMessageBody,
    user: AuthedUser = Depends(require_auth),
) -> dict[str, object]:
    cid = _parse_id(id)
    db = await get_db()
    c = await db.conversations.find_one({"_id": cid, "userId": user.userId})
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")

    started = time.time()
    user_msg = {
        "_id": await next_id("messages"),
        "conversationId": cid,
        "role": "user",
        "content": body.content,
        "citations": [],
        "canAnswer": None,
        "latencyMs": None,
        "rating": None,
        "feedbackComment": None,
        "createdAt": datetime.now(timezone.utc),
    }
    await db.messages.insert_one(user_msg)

    if c["title"] == "New conversation":
        new_title = " ".join(body.content.split())[:60].strip()
        await db.conversations.update_one(
            {"_id": cid},
            {"$set": {"title": new_title, "updatedAt": datetime.now(timezone.utc)}},
        )
    else:
        await db.conversations.update_one(
            {"_id": cid}, {"$set": {"updatedAt": datetime.now(timezone.utc)}}
        )

    # Retrieve top chunks via Mongo $text across approved docs only.
    approved_docs = await db.documents.find(
        {"status": "approved"}, {"_id": 1, "name": 1}
    ).to_list(length=None)
    doc_name_by_id = {d["_id"]: d["name"] for d in approved_docs}
    approved_ids = list(doc_name_by_id.keys())

    citations: list[dict[str, object]] = []
    context = ""
    scored: list[dict] = []
    if approved_ids:
        try:
            cursor = (
                db.chunks.find(
                    {
                        "documentId": {"$in": approved_ids},
                        "$text": {"$search": body.content},
                    },
                    {
                        "_id": 1,
                        "documentId": 1,
                        "position": 1,
                        "content": 1,
                        "tokenCount": 1,
                        "createdAt": 1,
                        "score": {"$meta": "textScore"},
                    },
                )
                .sort([("score", {"$meta": "textScore"})])
                .limit(5)
            )
            scored = await cursor.to_list(length=5)
        except Exception as err:
            log.warning("$text search failed; falling back to recent chunks: %s", err)
            scored = []
        if not scored:
            fallback = (
                await db.chunks.find({"documentId": {"$in": approved_ids}})
                .sort("_id", -1)
                .limit(5)
                .to_list(length=5)
            )
            scored = [{**c, "score": 0} for c in fallback]

        for c2 in scored:
            citations.append(
                {
                    "chunkId": c2["_id"],
                    "documentId": c2["documentId"],
                    "documentName": doc_name_by_id.get(c2["documentId"], "Untitled"),
                    "snippet": c2["content"][:280],
                    "score": round(float(c2.get("score") or 0), 3),
                }
            )
        context = "\n\n".join(
            f'[{i+1}] ({doc_name_by_id.get(c2["documentId"])}) {c2["content"]}'
            for i, c2 in enumerate(scored)
        )

    recent = (
        await db.messages.find({"conversationId": cid})
        .sort("createdAt", 1)
        .to_list(length=None)
    )
    recent_slice = recent[-6:]

    sys_prompt = (
        "You are Helia, an AI customer support assistant. Answer the user's question using "
        "ONLY the numbered context snippets below.\n"
        "- Cite sources inline using [n] notation matching the snippets you used.\n"
        "- If the answer cannot be found in the context, set canAnswer to false and suggest "
        "opening a support ticket.\n"
        "- Keep answers concise, friendly, and accurate.\n\n"
        'Respond as JSON with this exact shape:\n'
        '{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }\n\n'
        f"Context:\n{context or '(no documents indexed yet)'}"
    )

    turns: list[ChatTurn] = [{"role": "system", "content": sys_prompt}]
    for m in recent_slice[:-1]:
        turns.append(
            {
                "role": "assistant" if m["role"] == "assistant" else "user",
                "content": m["content"],
            }
        )
    turns.append({"role": "user", "content": body.content})

    answer = ""
    can_answer: bool | None = None
    used_idx: list[int] = []
    try:
        raw = await llm_chat(turns, json_mode=True)
        parsed = json.loads(raw)
        answer = parsed.get("answer") or ""
        ca = parsed.get("canAnswer")
        can_answer = ca if isinstance(ca, bool) else None
        ui = parsed.get("usedCitations")
        used_idx = [int(n) for n in ui] if isinstance(ui, list) else []
    except Exception as err:
        log.exception("LLM call failed: %s", err)
        answer = (
            "I'm having trouble reaching the model right now. "
            "Please try again in a moment, or open a support ticket."
        )
        can_answer = False

    if used_idx:
        filtered = [citations[n - 1] for n in used_idx if 1 <= n <= len(citations)]
    else:
        filtered = citations

    assistant_msg = {
        "_id": await next_id("messages"),
        "conversationId": cid,
        "role": "assistant",
        "content": answer,
        "citations": filtered,
        "canAnswer": can_answer,
        "latencyMs": int((time.time() - started) * 1000),
        "rating": None,
        "feedbackComment": None,
        "createdAt": datetime.now(timezone.utc),
    }
    await db.messages.insert_one(assistant_msg)

    return {
        "userMessage": serialize_message(user_msg),
        "assistantMessage": serialize_message(assistant_msg),
    }
