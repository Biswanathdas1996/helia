from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
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
_TICKET_INTENT_PATTERNS = [
    re.compile(r"\b(create|raise|open|file|submit|log|start)\s+(a\s+|an\s+|the\s+|new\s+)*(support\s+)?ticket\b", re.IGNORECASE),
    re.compile(r"\bnew\s+ticket\b", re.IGNORECASE),
    re.compile(r"\bescalate\b.*\b(ticket|human|agent|support)\b", re.IGNORECASE),
    re.compile(r"\b(want|need|like)\s+to\s+(create|open|raise|file|submit)\s+(a\s+|an\s+|the\s+|new\s+)*(support\s+)?ticket\b", re.IGNORECASE),
]
_TICKET_INTENT_RESPONSE = (
    "Of course — I can help you open a support ticket so a human teammate can follow up. "
    "Tap the **Create Ticket** button below and I'll prefill what we've discussed so far."
)
_UNANSWERABLE_PATTERNS = [
    "do not contain information",
    "cannot answer",
    "can't answer",
    "unable to answer",
    "not enough information",
    "don't have enough information",
    "couldn't find a confident answer",
    "open a support ticket",
    "having trouble reaching the model",
]
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

    if _detect_ticket_intent(body.content):
        assistant_msg = await _persist_ticket_intent_reply(db, cid, started)
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    enhanced_query, enhanced_intent = await _enhance_query(db, cid, body.content)
    retrieval = await _cached_retrieve(
        db,
        body.content,
        tenant_id=tenant_for(user),
        rewritten=enhanced_query,
        intent=enhanced_intent,
    )
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

    can_answer = _resolve_can_answer(answer, can_answer)

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

        yield _sse("process", {"name": "Saving user prompt", "status": "started"})
        user_msg = await _persist_user_message(db, cid, body.content)
        yield _sse("process", {"name": "Saving user prompt", "status": "completed"})

        yield _sse("process", {"name": "Updating conversation metadata", "status": "started"})
        await _bump_conversation_title(db, c, body.content)
        yield _sse("process", {"name": "Updating conversation metadata", "status": "completed"})

        yield _sse("user", serialize_message(user_msg))

        yield _sse("process", {"name": "Checking ticket escalation intent", "status": "started"})
        if _detect_ticket_intent(body.content):
            yield _sse("process", {"name": "Checking ticket escalation intent", "status": "completed"})
            yield _sse("process", {"name": "Preparing escalation guidance", "status": "started"})
            yield _sse("citations", [])
            for chunk in _TICKET_INTENT_RESPONSE.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_ticket_intent_reply(db, cid, started)
            yield _sse("process", {"name": "Preparing escalation guidance", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return
        yield _sse("process", {"name": "Checking ticket escalation intent", "status": "completed"})

        yield _sse("process", {"name": "Enhancing user query", "status": "started"})
        enhanced_query, enhanced_intent = await _enhance_query(db, cid, body.content)
        yield _sse("process", {"name": "Enhancing user query", "status": "completed"})

        yield _sse("process", {"name": "Retrieving relevant knowledge", "status": "started"})
        retrieval = await _cached_retrieve(
            db,
            body.content,
            tenant_id=tenant_for(user),
            rewritten=enhanced_query,
            intent=enhanced_intent,
        )
        citations = retrieval.citations()
        yield _sse("process", {"name": "Retrieving relevant knowledge", "status": "completed"})
        yield _sse("citations", citations)

        yield _sse("process", {"name": "Loading user memory", "status": "started"})
        memory_snippets = await agent_memory.search_user_memory(user.userId, body.content)
        yield _sse("process", {"name": "Loading user memory", "status": "completed"})

        yield _sse("process", {"name": "Composing model prompt", "status": "started"})
        _, turns = await _build_chat_payload(
            db,
            cid,
            body.content,
            retrieval.context_block(),
            memory_snippets,
        )
        yield _sse("process", {"name": "Composing model prompt", "status": "completed"})

        accum: list[str] = []
        llm_failed = False
        yield _sse("process", {"name": "Generating assistant response", "status": "started"})
        try:
            async for delta in llm.chat_stream(turns, reasoning=True):
                accum.append(delta)
                yield _sse("token", {"delta": delta})
        except Exception as err:
            log.exception("stream LLM failed: %s", err)
            accum = ["I'm having trouble reaching the model right now. Please try again, or open a support ticket."]
            llm_failed = True
            yield _sse("process", {"name": "Generating assistant response", "status": "error"})
        else:
            yield _sse("process", {"name": "Generating assistant response", "status": "completed"})

        full = "".join(accum).strip()
        answer, can_answer, used_idx = _try_parse_structured(full)
        if not answer:
            answer = full or "I couldn't generate a response."
            can_answer = can_answer if can_answer is not None else True
        can_answer = _resolve_can_answer(answer, can_answer, force_false=llm_failed)

        filtered = (
            [citations[n - 1] for n in used_idx if 1 <= n <= len(citations)]
            if used_idx
            else citations
        )

        yield _sse("process", {"name": "Saving assistant response", "status": "started"})
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
        yield _sse("process", {"name": "Saving assistant response", "status": "completed"})
        yield _sse("process", {"name": "Completed", "status": "completed"})
        yield _sse("done", serialize_message(assistant_msg))

    return StreamingResponse(events(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def _detect_ticket_intent(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _TICKET_INTENT_PATTERNS)


async def _persist_ticket_intent_reply(db, cid: int, started: float) -> dict:
    assistant_msg = {
        "_id": await next_id("messages"),
        "conversationId": cid,
        "role": "assistant",
        "content": _TICKET_INTENT_RESPONSE,
        "citations": [],
        "canAnswer": False,
        "latencyMs": int((time.time() - started) * 1000),
        "rating": None,
        "feedbackComment": None,
        "rewrittenQuery": None,
        "intent": "ticket_request",
        "createdAt": datetime.now(timezone.utc),
    }
    await db.messages.insert_one(assistant_msg)
    return assistant_msg


async def _enhance_query(db, cid: int, current: str) -> tuple[str, str]:
    """Resolve follow-up references and produce a retrieval-friendly rewrite.

    Uses the last few turns so pronouns and elliptical questions ("what about
    that one?") become standalone queries.
    """
    if os.environ.get("DISABLE_QUERY_REWRITE", "").lower() in {"1", "true", "yes"}:
        return current, "general"

    recent = (
        await db.messages.find(
            {"conversationId": cid}, {"role": 1, "content": 1, "createdAt": 1}
        )
        .sort("createdAt", -1)
        .to_list(length=6)
    )
    recent.reverse()
    history_block = "\n".join(
        f"{m['role']}: {(m.get('content') or '').strip()[:300]}"
        for m in recent
        if m.get("content")
    ) or "(no prior turns)"

    sys_prompt = (
        "You enhance customer support queries for a knowledge-base retrieval step. "
        "Given the recent conversation and the user's latest message, produce a single "
        "self-contained query that:\n"
        "- resolves pronouns and follow-up references using the prior turns,\n"
        "- expands obvious acronyms and adds 1-2 useful synonyms when helpful,\n"
        "- strips greetings and pleasantries,\n"
        "- preserves proper nouns, error codes, and product names verbatim,\n"
        "- stays under 30 words.\n"
        "Also classify the intent.\n"
        'Reply as JSON: {"rewritten": "<query>", "intent": "<one of: how_to | '
        'troubleshooting | billing | account | policy | general>"}'
    )
    user_prompt = (
        f"Recent conversation:\n{history_block}\n\n"
        f"Latest user message:\n{current}"
    )

    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=200,
        )
        obj = json.loads(raw)
        rewritten = (obj.get("rewritten") or "").strip() or current
        intent = (obj.get("intent") or "general").strip() or "general"
        return rewritten, intent
    except Exception as err:
        log.debug("query enhancement failed, using raw query: %s", err)
        return current, "general"


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


async def _cached_retrieve(
    db,
    query: str,
    *,
    tenant_id: str | None = None,
    rewritten: str | None = None,
    intent: str | None = None,
):
    cache_key_query = rewritten or query
    cache_seed = f"{tenant_id or '_'}::{cache_key_query}"
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
    result = await retrieve(
        db, query, tenant_id=tenant_id, pre_rewritten=rewritten, pre_intent=intent
    )
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
        "You are Helia, a warm and helpful AI customer support assistant. "
        "Answer the user's question using ONLY the numbered context snippets below.\n\n"
        "Tone and style:\n"
        "- Be friendly, polite, and reassuring. Open with a brief, warm acknowledgement "
        '(for example: "Happy to help with that!", "I can definitely help you sort this out.", '
        '"Sorry you\'re running into this — let\'s get it fixed."). Vary the wording naturally; '
        "do not reuse the same opener every turn.\n"
        "- Speak in first person and address the user directly. Sound like a real support teammate, "
        "not a generic bot.\n"
        "- Keep responses concise but genuinely helpful — no filler, no condescension.\n\n"
        "Solution intent (important):\n"
        "- After acknowledging, state the likely cause(s) in plain language when the context supports it.\n"
        "- Then clearly explain what the user should do or check to resolve the issue "
        '(for example: "Try clearing your browser cache, disabling extensions, and checking '
        'compatibility mode — these often resolve loading issues.", "You can check your order '
        'status here, or I can open a support ticket to track the shipment for you"). '
        "Guide them through steps kindly and clearly. Be their helper, not just a list of tasks.\n"
        "- Only suggest actions the user can actually take themselves; never claim you can access "
        "their system or perform technical actions on their behalf.\n\n"
        "Grounding and citations:\n"
        "- Cite sources inline using [n] notation matching the snippets you used.\n"
        "- If the answer cannot be found in the context, set canAnswer to false, apologise briefly, "
        "and offer to open a support ticket so a human teammate can follow up.\n"
        "- Never invent facts, policies, or steps that are not supported by the context.\n\n"
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
        raw = await llm.chat(turns, json_mode=True, reasoning=True)
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
    parsed = _try_parse_structured_json(raw)
    if not parsed:
        return raw, None, []

    answer_raw = parsed.get("answer")
    answer = answer_raw.strip() if isinstance(answer_raw, str) else ""

    ca = parsed.get("canAnswer")
    can_answer = ca if isinstance(ca, bool) else None

    ui = parsed.get("usedCitations")
    used_idx = [_coerce_citation_index(n) for n in ui] if isinstance(ui, list) else []
    used_idx = [n for n in used_idx if n is not None]
    return answer, can_answer, used_idx


def _try_parse_structured_json(raw: str) -> dict[str, object] | None:
    text = (raw or "").strip()
    if not text:
        return None

    candidates = [text]

    # Some models wrap JSON in markdown fences despite explicit JSON instructions.
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fence_match:
        fenced_json = fence_match.group(1).strip()
        if fenced_json:
            candidates.append(fenced_json)

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if 0 <= first_brace < last_brace:
        candidates.append(text[first_brace : last_brace + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _coerce_citation_index(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _resolve_can_answer(answer: str, can_answer: bool | None, *, force_false: bool = False) -> bool:
    if force_false:
        return False
    if isinstance(can_answer, bool):
        return can_answer
    return not _looks_unanswerable(answer)


def _looks_unanswerable(answer: str) -> bool:
    normalized = (answer or "").strip().lower()
    if not normalized:
        return True
    return any(pattern in normalized for pattern in _UNANSWERABLE_PATTERNS)


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
