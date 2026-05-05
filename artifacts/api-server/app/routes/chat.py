from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app import agent_memory, cache, llm, metrics, rate_limit
from app.auth import AuthedUser, require_auth
from app.db import get_db, next_id
from app.retrieval import retrieve
from app.schemas import CreateConversationBody, SendMessageBody
from app.serialize import serialize_conversation, serialize_message
from app.tenant import tenant_for

router = APIRouter()
log = logging.getLogger("api-server.chat")

_RETRIEVAL_CACHE_TTL = 300
_MEMORY_GRAPH_QUERY_FALLBACK = "user preferences profile support history"
_MEMORY_STOP_WORDS = {
    "about",
    "again",
    "also",
    "been",
    "because",
    "between",
    "could",
    "have",
    "help",
    "into",
    "just",
    "more",
    "need",
    "please",
    "that",
    "their",
    "them",
    "there",
    "they",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "your",
    "from",
    "user",
}


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


@router.get("/chat/conversations/{id}/memory-graph")
async def get_memory_graph(id: str, user: AuthedUser = Depends(require_auth)) -> dict[str, object]:
    cid = _parse_id(id)
    db = await get_db()
    convo = await db.conversations.find_one({"_id": cid, "userId": user.userId})
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    recent_user_msgs = (
        await db.messages.find(
            {"conversationId": cid, "role": "user"},
            {"content": 1, "createdAt": 1},
        )
        .sort("createdAt", -1)
        .to_list(length=6)
    )
    query_seed = " ".join((m.get("content") or "").strip() for m in recent_user_msgs[:3]).strip()
    if not query_seed:
        query_seed = _MEMORY_GRAPH_QUERY_FALLBACK

    memories = await agent_memory.search_user_memory(user.userId, query_seed, limit=18)
    graph = _build_memory_graph(memories)
    graph["query"] = query_seed
    graph["memoryCount"] = len(memories)
    return graph


@router.delete("/chat/conversations/{id}", status_code=204)
async def delete_conversation(id: str, user: AuthedUser = Depends(require_auth)) -> Response:
    cid = _parse_id(id)
    db = await get_db()
    r = await db.conversations.delete_one({"_id": cid, "userId": user.userId})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.messages.delete_many({"conversationId": cid})
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Send message (non-streaming)
# ---------------------------------------------------------------------------

@router.post("/chat/conversations/{id}/messages")
async def send_message(
    id: str,
    body: SendMessageBody,
    request: Request,
    user: AuthedUser = Depends(require_auth),
) -> dict[str, object]:
    await rate_limit.enforce(request, scope="chat")
    cid = _parse_id(id)
    db = await get_db()
    c = await db.conversations.find_one({"_id": cid, "userId": user.userId})
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")

    started = time.time()
    user_msg = await _persist_user_message(db, cid, body.content)
    await _bump_conversation_title(db, c, body.content)

    retrieval = await _cached_retrieve(db, body.content, tenant_id=tenant_for(user))
    memory_snippets = await agent_memory.search_user_memory(user.userId, body.content)
    _, turns = await _build_chat_payload(
        db,
        cid,
        body.content,
        retrieval.context_block(),
        memory_snippets,
    )

    answer, can_answer, used_idx = await _generate_answer(turns)
    citations = retrieval.citations()
    filtered = (
        [citations[n - 1] for n in used_idx if 1 <= n <= len(citations)]
        if used_idx
        else citations
    )

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
        "rewrittenQuery": retrieval.rewritten_query,
        "intent": retrieval.intent,
        "createdAt": datetime.now(timezone.utc),
    }
    await db.messages.insert_one(assistant_msg)
    await agent_memory.add_exchange_memory(user.userId, body.content, answer)

    return {
        "userMessage": serialize_message(user_msg),
        "assistantMessage": serialize_message(assistant_msg),
    }


# ---------------------------------------------------------------------------
# Send message (streaming SSE)
# ---------------------------------------------------------------------------

@router.post("/chat/conversations/{id}/messages/stream")
async def send_message_stream(
    id: str,
    body: SendMessageBody,
    request: Request,
    user: AuthedUser = Depends(require_auth),
) -> StreamingResponse:
    await rate_limit.enforce(request, scope="chat")
    cid = _parse_id(id)
    db = await get_db()
    c = await db.conversations.find_one({"_id": cid, "userId": user.userId})
    if not c:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def events() -> AsyncIterator[bytes]:
        started = time.time()
        user_msg = await _persist_user_message(db, cid, body.content)
        await _bump_conversation_title(db, c, body.content)

        yield _sse("user", serialize_message(user_msg))

        retrieval = await _cached_retrieve(db, body.content, tenant_id=tenant_for(user))
        citations = retrieval.citations()
        yield _sse("citations", citations)

        memory_snippets = await agent_memory.search_user_memory(user.userId, body.content)
        _, turns = await _build_chat_payload(
            db,
            cid,
            body.content,
            retrieval.context_block(),
            memory_snippets,
        )

        accum: list[str] = []
        try:
            async for delta in llm.chat_stream(turns):
                accum.append(delta)
                yield _sse("token", {"delta": delta})
        except Exception as err:
            log.exception("stream LLM failed: %s", err)
            accum = ["I'm having trouble reaching the model right now. Please try again, or open a support ticket."]

        full = "".join(accum).strip()
        answer, can_answer, used_idx = _try_parse_structured(full)
        if not answer:
            answer = full or "I couldn't generate a response."
            can_answer = can_answer if can_answer is not None else True

        filtered = (
            [citations[n - 1] for n in used_idx if 1 <= n <= len(citations)]
            if used_idx
            else citations
        )

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
            "rewrittenQuery": retrieval.rewritten_query,
            "intent": retrieval.intent,
            "createdAt": datetime.now(timezone.utc),
        }
        await db.messages.insert_one(assistant_msg)
        await agent_memory.add_exchange_memory(user.userId, body.content, answer)
        yield _sse("done", serialize_message(assistant_msg))

    return StreamingResponse(events(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


async def _persist_user_message(db, cid: int, content: str) -> dict:
    msg = {
        "_id": await next_id("messages"),
        "conversationId": cid,
        "role": "user",
        "content": content,
        "citations": [],
        "canAnswer": None,
        "latencyMs": None,
        "rating": None,
        "feedbackComment": None,
        "createdAt": datetime.now(timezone.utc),
    }
    await db.messages.insert_one(msg)
    return msg


async def _bump_conversation_title(db, convo: dict, first_user_content: str) -> None:
    cid = convo["_id"]
    if convo.get("title") == "New conversation":
        new_title = " ".join(first_user_content.split())[:60].strip()
        await db.conversations.update_one(
            {"_id": cid},
            {"$set": {"title": new_title, "updatedAt": datetime.now(timezone.utc)}},
        )
    else:
        await db.conversations.update_one(
            {"_id": cid}, {"$set": {"updatedAt": datetime.now(timezone.utc)}}
        )


async def _cached_retrieve(db, query: str, *, tenant_id: str | None = None):
    cache_seed = f"{tenant_id or '_'}::{query}"
    key = f"retr:{hashlib.sha1(cache_seed.encode('utf-8')).hexdigest()}"
    cached = await cache.get(key)
    if cached:
        from app.retrieval import RetrievalResult, RetrievedChunk
        try:
            metrics.RETRIEVAL_CALLS.labels(leg="cache", outcome="hit").inc()
            return RetrievalResult(
                rewritten_query=cached["rewritten_query"],
                intent=cached["intent"],
                chunks=[RetrievedChunk(**c) for c in cached["chunks"]],
            )
        except Exception:
            pass
    metrics.RETRIEVAL_CALLS.labels(leg="cache", outcome="miss").inc()
    result = await retrieve(db, query, tenant_id=tenant_id)
    try:
        await cache.set(
            key,
            {
                "rewritten_query": result.rewritten_query,
                "intent": result.intent,
                "chunks": [c.__dict__ for c in result.chunks],
            },
            ttl_seconds=_RETRIEVAL_CACHE_TTL,
        )
    except Exception as err:
        log.debug("retrieval cache write failed: %s", err)
    return result


async def _build_chat_payload(
    db,
    cid: int,
    current_user_content: str,
    context: str,
    memory_snippets: list[str] | None = None,
):
    memory_block = ""
    if memory_snippets:
        memory_lines = "\n".join(f"- {m}" for m in memory_snippets[:5])
        memory_block = (
            "\n\nKnown user memory (preferences and profile facts):\n"
            f"{memory_lines}\n"
            "Use this only when relevant to the current request."
        )

    sys_prompt = (
        "You are Helia, an AI customer support assistant. Answer the user's question using "
        "ONLY the numbered context snippets below.\n"
        "- Cite sources inline using [n] notation matching the snippets you used.\n"
        "- If the answer cannot be found in the context, set canAnswer to false and suggest "
        "opening a support ticket.\n"
        "- Keep answers concise, friendly, and accurate.\n\n"
        'Respond as JSON with this exact shape:\n'
        '{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }\n\n'
        f"{memory_block}"
        f"Context:\n{context}"
    )

    recent = (
        await db.messages.find({"conversationId": cid})
        .sort("createdAt", 1)
        .to_list(length=None)
    )
    recent_slice = recent[-6:]

    turns: list[llm.ChatTurn] = [{"role": "system", "content": sys_prompt}]
    for m in recent_slice[:-1]:
        turns.append(
            {
                "role": "assistant" if m["role"] == "assistant" else "user",
                "content": m["content"],
            }
        )
    turns.append({"role": "user", "content": current_user_content})
    return sys_prompt, turns


async def _generate_answer(turns: list[llm.ChatTurn]) -> tuple[str, bool | None, list[int]]:
    try:
        raw = await llm.chat(turns, json_mode=True)
        metrics.LLM_CALLS.labels(provider="auto", kind="chat", outcome="ok").inc()
    except Exception as err:
        metrics.LLM_CALLS.labels(provider="auto", kind="chat", outcome="error").inc()
        log.exception("LLM call failed: %s", err)
        return (
            "I'm having trouble reaching the model right now. "
            "Please try again in a moment, or open a support ticket.",
            False,
            [],
        )
    answer, can_answer, used_idx = _try_parse_structured(raw)
    return answer or raw, can_answer, used_idx


def _try_parse_structured(raw: str) -> tuple[str, bool | None, list[int]]:
    try:
        parsed = json.loads(raw)
        answer = parsed.get("answer") or ""
        ca = parsed.get("canAnswer")
        can_answer = ca if isinstance(ca, bool) else None
        ui = parsed.get("usedCitations")
        used_idx = [int(n) for n in ui] if isinstance(ui, list) else []
        return answer, can_answer, used_idx
    except Exception:
        return raw, None, []


def _build_memory_graph(memories: list[str]) -> dict[str, object]:
    nodes: list[dict[str, object]] = [{"id": "user", "label": "You", "type": "user"}]
    edges: list[dict[str, object]] = []
    concept_ids: dict[str, str] = {}

    for i, memory in enumerate(memories, start=1):
        memory_id = f"m{i}"
        compact = " ".join(memory.split())
        nodes.append(
            {
                "id": memory_id,
                "label": compact[:120],
                "type": "memory",
            }
        )
        edges.append({"source": "user", "target": memory_id, "type": "remembers"})

        for keyword in _memory_keywords(compact):
            key = keyword.lower()
            concept_id = concept_ids.get(key)
            if not concept_id:
                concept_id = f"c{len(concept_ids) + 1}"
                concept_ids[key] = concept_id
                nodes.append(
                    {
                        "id": concept_id,
                        "label": keyword.title(),
                        "type": "concept",
                    }
                )
            edges.append({"source": memory_id, "target": concept_id, "type": "mentions"})

    return {
        "nodes": nodes,
        "edges": edges,
    }


def _memory_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower())
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in _MEMORY_STOP_WORDS:
            continue
        if token.isdigit():
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= 3:
            break
    return out
