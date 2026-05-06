from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from app import agent_memory, cache, chat_agent, llm, metrics, rate_limit
from app.audit import audit_log
from app.auth import AuthedUser, require_auth
from app.db import get_db, next_id
from app.retrieval import retrieve
from app.schemas import CreateConversationBody, SendMessageBody
from app.serialize import serialize_conversation, serialize_message
from app.tenant import tenant_for
from app import zoho

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
    "Reply \"yes, create a ticket\" and I will create it with a summary of this investigation, "
    "or tap the **Create Ticket** button below."
)
_TICKET_CONSENT_PATTERNS = [
    re.compile(r"\b(yes|yep|yeah|sure|okay|ok|please|go ahead|do it|proceed|confirm)\b", re.IGNORECASE),
    re.compile(r"\b(create|open|raise|file|submit)\s+(a\s+|an\s+|the\s+)?(support\s+)?ticket\b", re.IGNORECASE),
]
_TICKET_DECLINE_PATTERNS = [
    re.compile(r"\b(no|nah|not now|don't|do not|stop|cancel|no thanks)\b", re.IGNORECASE),
]
_TICKET_OFFER_APPENDIX = (
    "If this is still blocking you, I can open a support ticket so a human teammate can pick it up — "
    "just reply \"yes, create a ticket\" and I'll do it."
)
_UNANSWERABLE_PATTERNS = [
    "do not contain information",
    "cannot answer",
    "can't answer",
    "unable to answer",
    "not enough information",
    "don't have enough information",
    "couldn't find a confident answer",
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
_STAGE1_MARKERS = ("what's likely happening:", "what’s likely happening:")
_STAGE2_MARKERS = ("try this now:",)
_STAGE3_MARKERS = ("what to tell me next:",)
_AFFIRMATIVE_REPLY_PATTERNS = [
    re.compile(r"\b(yes|yep|yeah|sure|ok|okay|please|continue|go ahead|do it|help me)\b", re.IGNORECASE),
    re.compile(r"\b(next step|what should i do|how do i fix|show me)\b", re.IGNORECASE),
]
_TROUBLESHOOTING_HINT_PATTERN = re.compile(
    r"\b(can't|cannot|unable|not able|not working|issue|error|blocked|failed|problem|submit|access)\b",
    re.IGNORECASE,
)
_UNRESOLVED_SIGNAL_PATTERNS = [
    re.compile(r"\b(didn'?t|did not|doesn'?t|does not)\s+work\b", re.IGNORECASE),
    re.compile(r"\bstill\s+(not\s+working|failing|broken|stuck|blocked|the\s+same|same)\b", re.IGNORECASE),
    re.compile(r"\bsame\s+(error|issue|problem|message|behavior|behaviour)\b", re.IGNORECASE),
    re.compile(r"\bno\s+(luck|change|effect|difference)\b", re.IGNORECASE),
    re.compile(r"\b(already\s+tried|tried\s+(that|it|this))\b.*\b(no|not|still|again)\b", re.IGNORECASE),
    re.compile(r"\b(not|isn'?t|hasn'?t|haven'?t)\s+(resolved|fixed|working|helped)\b", re.IGNORECASE),
    re.compile(r"\bthat\s+didn'?t\s+help\b", re.IGNORECASE),
]
_RESOLVED_SIGNAL_PATTERNS = [
    re.compile(r"\b(it|that|this)\s+(worked|works|did\s+the\s+trick|fixed\s+it|sorted\s+it|solved\s+it)\b", re.IGNORECASE),
    re.compile(r"\b(working|works)\s+(now|fine\s+now|as\s+expected)\b", re.IGNORECASE),
    re.compile(r"\b(fixed|resolved|sorted|solved)\s+(it|the\s+(issue|problem|error|bug))\b", re.IGNORECASE),
    re.compile(r"\ball\s+(good|set|fixed|sorted)\s+now\b", re.IGNORECASE),
    re.compile(r"\bno\s+more\s+(echo|noise|errors?|issues?|problems?|lag|crashes?|glitches?)\b", re.IGNORECASE),
    re.compile(
        r"\b(echo|noise|error|issue|problem|lag|crash|glitch|sound|audio|feedback)\s+"
        r"(stopped|is\s+gone|has\s+stopped|went\s+away|disappeared|cleared\s+up)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(issue|problem|error|bug)\s+(is\s+)?(resolved|fixed|gone)\b", re.IGNORECASE),
    re.compile(r"\b(thanks|thank\s+you)[^.!?\n]{0,40}?\b(worked|fixed|sorted|did\s+it|did\s+the\s+trick)\b", re.IGNORECASE),
    re.compile(r"\bproblem\s+solved\b", re.IGNORECASE),
]
_RESOLUTION_PRIOR_KINDS = {"clarification_question", "answer", "ticket_offer"}
_RESOLUTION_ACKNOWLEDGEMENT = (
    "Wonderful — really glad that sorted it. "
    "If anything else comes up, just message me here and I'll take another look."
)


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _contains_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_affirmative_reply(text: str) -> bool:
    return any(pattern.search(text) for pattern in _AFFIRMATIVE_REPLY_PATTERNS)


def _infer_required_troubleshooting_stage(
    recent_messages: list[dict[str, object]],
    current_user_content: str,
) -> str | None:
    previous_assistant_text = ""
    for message in reversed(recent_messages):
        if message.get("role") == "assistant":
            content = message.get("content")
            previous_assistant_text = _normalize_text(content if isinstance(content, str) else "")
            break

    user_text = _normalize_text(current_user_content)

    if _contains_any_marker(previous_assistant_text, _STAGE3_MARKERS):
        return "Stage 4"
    if _contains_any_marker(previous_assistant_text, _STAGE2_MARKERS):
        return "Stage 3"
    if _contains_any_marker(previous_assistant_text, _STAGE1_MARKERS):
        if _is_affirmative_reply(user_text):
            return "Stage 2"
        return "Stage 1"

    if _TROUBLESHOOTING_HINT_PATTERN.search(user_text):
        return "Stage 1"
    return None


def _parse_id(raw: str) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid id")
    if n <= 0:
        raise HTTPException(status_code=400, detail="Invalid id")
    return n


# ---------------------------------------------------------------------------
# Image-to-text for chat context
# ---------------------------------------------------------------------------

_ALLOWED_IMAGE_MIME = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"})
_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/chat/image-describe")
async def describe_chat_image(
    file: UploadFile = File(...),
    user: AuthedUser = Depends(require_auth),
) -> dict[str, object]:
    """Accept a multipart image file and return a plain-text description.

    The description is intended to be prepended to the user's chat message so
    the agent can reason about the image without requiring multimodal support in
    the downstream pipeline.
    """
    import base64 as _base64

    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in _ALLOWED_IMAGE_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type '{content_type}'. Allowed: PNG, JPEG, WEBP, GIF.",
        )

    raw = await file.read()
    if len(raw) > _IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB limit.")

    b64 = _base64.b64encode(raw).decode()
    try:
        description = await llm.describe_image_for_chat(content_type, b64)
    except Exception as exc:
        log.exception("Image description failed")
        raise HTTPException(
            status_code=502,
            detail=f"Image description service unavailable: {exc}",
        ) from exc

    if not description:
        raise HTTPException(status_code=502, detail="Image description service returned empty content.")

    return {"description": description}


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
        "agentState": chat_agent.default_state(),
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
    user_msg = await _persist_user_message(
        db, cid, body.content, image_data_url=body.imageDataUrl
    )
    await _bump_conversation_title(db, c, body.content)
    agent_state = chat_agent.normalize_state(c.get("agentState"))

    if await _should_create_ticket_from_consent(db, cid, body.content):
        ticket = await _create_ticket_from_conversation(
            db,
            cid,
            user=user,
            agent_state=agent_state,
            trigger_user_message=body.content,
            related_message_id=user_msg["_id"],
        )
        await _persist_agent_state(
            db,
            cid,
            {
                **agent_state,
                "lastAction": "create_ticket",
                "lastQuestion": None,
                "resolutionSummary": None,
            },
        )
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=_ticket_created_reply(ticket),
            citations=[],
            can_answer=False,
            started=started,
            rewritten_query=None,
            intent="ticket_created",
            kind="ticket_created",
            ticket_id=ticket["_id"],
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    if await _should_acknowledge_resolution(db, cid, body.content):
        await _persist_agent_state(db, cid, _resolution_state(agent_state, body.content))
        assistant_msg = await _persist_resolution_acknowledgement(db, cid, started)
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    if _detect_ticket_intent(body.content):
        await _persist_agent_state(
            db,
            cid,
            {
                **agent_state,
                "lastAction": "offer_ticket",
                "lastQuestion": None,
            },
        )
        assistant_msg = await _persist_ticket_intent_reply(db, cid, started)
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    planner_state = await _planner_state_with_unresolved_signal(
        db, cid, agent_state, body.content
    )
    prepared = await _prepare_agent_turn(
        db,
        cid,
        body.content,
        user_id=user.userId,
        tenant_id=tenant_for(user),
        agent_state=agent_state,
        planner_state=planner_state,
    )

    decision = prepared["decision"]
    retrieval = prepared["retrieval"]
    planner_citations = prepared["selected_citations"]

    if decision.action == "ask_clarifying_question":
        next_state = chat_agent.apply_decision_to_state(agent_state, decision)
        await _persist_agent_state(db, cid, next_state)
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=decision.reply,
            citations=planner_citations,
            can_answer=None,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent="clarification_question",
            kind="clarification_question",
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    if decision.action == "offer_ticket":
        next_state = chat_agent.apply_decision_to_state(agent_state, decision)
        await _persist_agent_state(db, cid, next_state)
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=decision.reply,
            citations=planner_citations,
            can_answer=False,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent="investigation_ticket_offer",
            kind="ticket_offer",
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    if decision.action == "create_ticket":
        ticket = await _create_ticket_from_conversation(
            db,
            cid,
            user=user,
            agent_state=agent_state,
            trigger_user_message=body.content,
            related_message_id=user_msg["_id"],
        )
        await _persist_agent_state(
            db,
            cid,
            {
                **agent_state,
                "lastAction": "create_ticket",
                "lastQuestion": None,
                "resolutionSummary": None,
            },
        )
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=_ticket_created_reply(ticket),
            citations=[],
            can_answer=False,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent="ticket_created",
            kind="ticket_created",
            ticket_id=ticket["_id"],
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    answer_state = chat_agent.apply_decision_to_state(agent_state, decision)
    _, turns = await _build_chat_payload(
        db,
        cid,
        body.content,
        retrieval.context_block(),
        prepared["memory_snippets"],
        agent_state=answer_state,
    )

    answer, can_answer, used_idx = await _generate_answer(turns)
    filtered = _select_citations(retrieval.citations(), used_idx or decision.used_citations)
    can_answer = _resolve_can_answer(answer, can_answer)

    answer, can_answer, replaced_by_verifier = await _enforce_grounded_answer(
        answer=answer,
        citations=filtered,
        can_answer=can_answer,
    )

    message_kind = "answer"
    response_intent = retrieval.intent
    if not can_answer:
        if not replaced_by_verifier:
            answer = _append_ticket_offer(answer)
        message_kind = "ticket_offer"
        response_intent = "investigation_ticket_offer"

    final_state = chat_agent.apply_decision_to_state(
        agent_state,
        decision,
        final_answer=answer if can_answer else None,
    )
    if message_kind == "ticket_offer":
        final_state["lastAction"] = "offer_ticket"
        final_state["lastQuestion"] = None
    await _persist_agent_state(db, cid, final_state)

    assistant_msg = await _persist_assistant_message(
        db,
        cid,
        content=answer,
        citations=filtered,
        can_answer=can_answer,
        started=started,
        rewritten_query=retrieval.rewritten_query,
        intent=response_intent,
        kind=message_kind,
    )
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
        user_msg = await _persist_user_message(
            db, cid, body.content, image_data_url=body.imageDataUrl
        )
        yield _sse("process", {"name": "Saving user prompt", "status": "completed"})

        yield _sse("process", {"name": "Updating conversation metadata", "status": "started"})
        await _bump_conversation_title(db, c, body.content)
        yield _sse("process", {"name": "Updating conversation metadata", "status": "completed"})

        yield _sse("user", serialize_message(user_msg))

        agent_state = chat_agent.normalize_state(c.get("agentState"))

        yield _sse("process", {"name": "Checking ticket creation consent", "status": "started"})
        if await _should_create_ticket_from_consent(db, cid, body.content):
            yield _sse("process", {"name": "Checking ticket creation consent", "status": "completed"})
            yield _sse("process", {"name": "Creating support ticket", "status": "started"})
            ticket = await _create_ticket_from_conversation(
                db,
                cid,
                user=user,
                agent_state=agent_state,
                trigger_user_message=body.content,
                related_message_id=user_msg["_id"],
            )
            await _persist_agent_state(
                db,
                cid,
                {
                    **agent_state,
                    "lastAction": "create_ticket",
                    "lastQuestion": None,
                    "resolutionSummary": None,
                },
            )
            ticket_reply = _ticket_created_reply(ticket)
            yield _sse("citations", [])
            for chunk in ticket_reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_assistant_message(
                db,
                cid,
                content=ticket_reply,
                citations=[],
                can_answer=False,
                started=started,
                rewritten_query=None,
                intent="ticket_created",
                kind="ticket_created",
                ticket_id=ticket["_id"],
            )
            yield _sse("process", {"name": "Creating support ticket", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return
        yield _sse("process", {"name": "Checking ticket creation consent", "status": "completed"})

        yield _sse("process", {"name": "Checking resolution signal", "status": "started"})
        if await _should_acknowledge_resolution(db, cid, body.content):
            yield _sse("process", {"name": "Checking resolution signal", "status": "completed"})
            yield _sse("process", {"name": "Acknowledging resolution", "status": "started"})
            await _persist_agent_state(db, cid, _resolution_state(agent_state, body.content))
            yield _sse("citations", [])
            for chunk in _RESOLUTION_ACKNOWLEDGEMENT.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_resolution_acknowledgement(db, cid, started)
            yield _sse("process", {"name": "Acknowledging resolution", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return
        yield _sse("process", {"name": "Checking resolution signal", "status": "completed"})

        yield _sse("process", {"name": "Checking ticket escalation intent", "status": "started"})
        if _detect_ticket_intent(body.content):
            yield _sse("process", {"name": "Checking ticket escalation intent", "status": "completed"})
            yield _sse("process", {"name": "Preparing escalation guidance", "status": "started"})
            await _persist_agent_state(
                db,
                cid,
                {
                    **agent_state,
                    "lastAction": "offer_ticket",
                    "lastQuestion": None,
                },
            )
            yield _sse("citations", [])
            for chunk in _TICKET_INTENT_RESPONSE.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_ticket_intent_reply(db, cid, started)
            yield _sse("process", {"name": "Preparing escalation guidance", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return
        yield _sse("process", {"name": "Checking ticket escalation intent", "status": "completed"})

        yield _sse("process", {"name": "Reviewing investigation memory", "status": "started"})
        planner_state = await _planner_state_with_unresolved_signal(
            db, cid, agent_state, body.content
        )
        yield _sse("process", {"name": "Enhancing user query", "status": "started"})
        enhanced_query, enhanced_intent, enhanced_keywords, enhanced_subqueries = await _enhance_query(
            db,
            cid,
            body.content,
            agent_state=planner_state,
        )
        yield _sse("process", {"name": "Enhancing user query", "status": "completed"})

        yield _sse("process", {"name": "Retrieving relevant knowledge", "status": "started"})
        retrieval = await _cached_retrieve(
            db,
            body.content,
            tenant_id=tenant_for(user),
            rewritten=enhanced_query,
            intent=enhanced_intent,
            keywords=enhanced_keywords,
            subqueries=enhanced_subqueries,
        )
        yield _sse("process", {"name": "Retrieving relevant knowledge", "status": "completed"})

        yield _sse("process", {"name": "Loading user memory", "status": "started"})
        memory_snippets = await agent_memory.search_user_memory(user.userId, body.content)
        yield _sse("process", {"name": "Loading user memory", "status": "completed"})
        yield _sse("process", {"name": "Reviewing investigation memory", "status": "completed"})

        yield _sse("process", {"name": "Planning next best action", "status": "started"})
        recent_messages = await _recent_messages(db, cid)
        decision = await chat_agent.decide_next_action(
            recent_messages=recent_messages,
            current_user_message=body.content,
            retrieval_context=retrieval.context_block(),
            citations=retrieval.citations(),
            memory_snippets=memory_snippets,
            agent_state=planner_state,
        )
        yield _sse("process", {"name": "Planning next best action", "status": "completed"})

        planner_citations = _select_citations(retrieval.citations(), decision.used_citations)
        yield _sse("citations", planner_citations)

        yield _sse("process", {"name": "Updating investigation memory", "status": "started"})
        if decision.action != "answer":
            next_state = chat_agent.apply_decision_to_state(agent_state, decision)
            await _persist_agent_state(db, cid, next_state)
        yield _sse("process", {"name": "Updating investigation memory", "status": "completed"})

        if decision.action == "ask_clarifying_question":
            yield _sse("process", {"name": "Asking a clarifying question", "status": "started"})
            for chunk in decision.reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_assistant_message(
                db,
                cid,
                content=decision.reply,
                citations=planner_citations,
                can_answer=None,
                started=started,
                rewritten_query=retrieval.rewritten_query,
                intent="clarification_question",
                kind="clarification_question",
            )
            yield _sse("process", {"name": "Asking a clarifying question", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        if decision.action == "offer_ticket":
            yield _sse("process", {"name": "Preparing escalation guidance", "status": "started"})
            for chunk in decision.reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_assistant_message(
                db,
                cid,
                content=decision.reply,
                citations=planner_citations,
                can_answer=False,
                started=started,
                rewritten_query=retrieval.rewritten_query,
                intent="investigation_ticket_offer",
                kind="ticket_offer",
            )
            yield _sse("process", {"name": "Preparing escalation guidance", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        if decision.action == "create_ticket":
            yield _sse("process", {"name": "Creating support ticket", "status": "started"})
            ticket = await _create_ticket_from_conversation(
                db,
                cid,
                user=user,
                agent_state=agent_state,
                trigger_user_message=body.content,
                related_message_id=user_msg["_id"],
            )
            await _persist_agent_state(
                db,
                cid,
                {
                    **agent_state,
                    "lastAction": "create_ticket",
                    "lastQuestion": None,
                    "resolutionSummary": None,
                },
            )
            ticket_reply = _ticket_created_reply(ticket)
            for chunk in ticket_reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_assistant_message(
                db,
                cid,
                content=ticket_reply,
                citations=[],
                can_answer=False,
                started=started,
                rewritten_query=retrieval.rewritten_query,
                intent="ticket_created",
                kind="ticket_created",
                ticket_id=ticket["_id"],
            )
            yield _sse("process", {"name": "Creating support ticket", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        answer_state = chat_agent.apply_decision_to_state(agent_state, decision)

        yield _sse("process", {"name": "Composing grounded answer", "status": "started"})
        _, turns = await _build_chat_payload(
            db,
            cid,
            body.content,
            retrieval.context_block(),
            memory_snippets,
            agent_state=answer_state,
        )
        yield _sse("process", {"name": "Composing grounded answer", "status": "completed"})

        accum: list[str] = []
        llm_failed = False
        yield _sse("process", {"name": "Generating grounded answer", "status": "started"})
        try:
            async for delta in llm.chat_stream(turns, reasoning=True):
                accum.append(delta)
        except Exception as err:
            log.exception("stream LLM failed: %s", err)
            accum = ["I'm having trouble reaching the model right now. Please try again, or open a support ticket."]
            llm_failed = True
            yield _sse("process", {"name": "Generating grounded answer", "status": "error"})
        else:
            yield _sse("process", {"name": "Generating grounded answer", "status": "completed"})

        full = "".join(accum).strip()
        answer, can_answer, used_idx = _try_parse_structured(full)
        if not answer:
            answer = full or "I couldn't generate a response."
            can_answer = can_answer if can_answer is not None else True
        can_answer = _resolve_can_answer(answer, can_answer, force_false=llm_failed)

        filtered = _select_citations(retrieval.citations(), used_idx or decision.used_citations)

        yield _sse("process", {"name": "Verifying grounding", "status": "started"})
        answer, can_answer, replaced_by_verifier = await _enforce_grounded_answer(
            answer=answer,
            citations=filtered,
            can_answer=can_answer,
        )
        yield _sse("process", {"name": "Verifying grounding", "status": "completed"})

        message_kind = "answer"
        response_intent = retrieval.intent
        if replaced_by_verifier:
            message_kind = "ticket_offer"
            response_intent = "investigation_ticket_offer"
        elif not can_answer:
            answer = _append_ticket_offer(answer)
            message_kind = "ticket_offer"
            response_intent = "investigation_ticket_offer"

        for chunk in answer.split(" "):
            if chunk:
                yield _sse("token", {"delta": chunk + " "})

        yield _sse("process", {"name": "Saving assistant response", "status": "started"})
        final_state = chat_agent.apply_decision_to_state(
            agent_state,
            decision,
            final_answer=answer if can_answer else None,
        )
        if message_kind == "ticket_offer":
            final_state["lastAction"] = "offer_ticket"
            final_state["lastQuestion"] = None
        await _persist_agent_state(db, cid, final_state)
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=answer,
            citations=filtered,
            can_answer=can_answer,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent=response_intent,
            kind=message_kind,
        )
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


def _detect_unresolved_signal(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _UNRESOLVED_SIGNAL_PATTERNS)


def _detect_resolved_signal(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if any(p.search(text) for p in _UNRESOLVED_SIGNAL_PATTERNS):
        return False
    return any(p.search(text) for p in _RESOLVED_SIGNAL_PATTERNS)


async def _should_acknowledge_resolution(db, cid: int, user_text: str) -> bool:
    if not _detect_resolved_signal(user_text):
        return False
    last_assistant = await db.messages.find_one(
        {"conversationId": cid, "role": "assistant"},
        sort=[("createdAt", -1)],
    )
    if not last_assistant:
        return False
    return str(last_assistant.get("kind") or "").strip() in _RESOLUTION_PRIOR_KINDS


def _resolution_state(agent_state: dict[str, object], user_text: str) -> dict[str, object]:
    return {
        **agent_state,
        "lastAction": "answer",
        "lastQuestion": None,
        "lastUnresolvedSignal": None,
        "resolutionSummary": _clip_line(user_text, limit=240),
    }


async def _persist_resolution_acknowledgement(db, cid: int, started: float) -> dict:
    return await _persist_assistant_message(
        db,
        cid,
        content=_RESOLUTION_ACKNOWLEDGEMENT,
        citations=[],
        can_answer=True,
        started=started,
        rewritten_query=None,
        intent="resolution_acknowledgement",
        kind="resolution_acknowledgement",
    )


async def _planner_state_with_unresolved_signal(
    db,
    cid: int,
    agent_state: dict[str, object],
    user_text: str,
) -> dict[str, object]:
    if not _detect_unresolved_signal(user_text):
        return agent_state
    last_assistant = await db.messages.find_one(
        {"conversationId": cid, "role": "assistant"},
        sort=[("createdAt", -1)],
    )
    if not last_assistant:
        return agent_state
    if str(last_assistant.get("kind") or "").strip() != "answer":
        return agent_state
    signal = _clip_line(user_text, limit=160) or "user reported the previous fix did not work"
    return {**agent_state, "lastUnresolvedSignal": signal}


async def _persist_ticket_intent_reply(db, cid: int, started: float) -> dict:
    return await _persist_assistant_message(
        db,
        cid,
        content=_TICKET_INTENT_RESPONSE,
        citations=[],
        can_answer=False,
        started=started,
        rewritten_query=None,
        intent="ticket_request",
        kind="ticket_offer",
    )


def _has_ticket_consent(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if any(pattern.search(cleaned) for pattern in _TICKET_DECLINE_PATTERNS):
        return False
    return any(pattern.search(cleaned) for pattern in _TICKET_CONSENT_PATTERNS)


async def _should_create_ticket_from_consent(db, cid: int, user_text: str) -> bool:
    if not _has_ticket_consent(user_text):
        return False
    last_assistant = await db.messages.find_one(
        {"conversationId": cid, "role": "assistant"},
        sort=[("createdAt", -1)],
    )
    if not last_assistant:
        return False
    return str(last_assistant.get("kind") or "").strip() == "ticket_offer"


def _normalize_string_items(raw: object, *, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split()).strip()
        if not value or value in out:
            continue
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _clip_line(text: str, *, limit: int = 240) -> str:
    compact = " ".join((text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


def _ticket_subject(summary: str, latest_user_message: str) -> str:
    preferred = _clip_line(summary, limit=140)
    if preferred:
        return preferred
    fallback = _clip_line(latest_user_message, limit=140)
    if fallback:
        return fallback
    return "Unresolved support issue from chat"


def _ticket_priority(*, issue_text: str) -> str:
    normalized = (issue_text or "").lower()
    if any(term in normalized for term in ("outage", "production down", "service down", "system down", "sev1")):
        return "urgent"
    if any(term in normalized for term in ("blocked", "cannot", "can't", "failed", "failure", "error", "access denied")):
        return "high"
    return "medium"


_UNGROUNDED_FALLBACK = (
    "Sorry — I couldn't find verified steps for this in our knowledge base, so I don't want to"
    " guess at the exact menus or settings. If you'd like, I can open a support ticket so a human"
    " teammate can pick this up — just reply \"yes, create a ticket\" and I'll do it."
)


async def _enforce_grounded_answer(
    *,
    answer: str,
    citations: list[dict[str, object]],
    can_answer: bool,
) -> tuple[str, bool, bool]:
    """Verify every action / UI claim is supported by the citations.

    Returns ``(answer, can_answer, replaced)``. When the verifier rejects the
    answer, the answer text is replaced with a ticket-offer fallback and
    ``can_answer`` is forced to False. ``replaced`` indicates whether the
    answer was rewritten so callers can adjust streamed output / message kind.
    """
    if not can_answer:
        return answer, can_answer, False
    if not (answer or "").strip():
        return answer, can_answer, False
    if not chat_agent.has_action_claims(answer) and citations:
        # No action verbs and we have citations — verifier still useful for cause claims
        pass
    grounded, unsupported = await chat_agent.verify_answer_grounding(
        answer=answer,
        citations=citations,
    )
    if grounded:
        return answer, can_answer, False
    log.info(
        "answer rejected by grounding verifier; unsupported=%s",
        unsupported,
    )
    rewritten = await chat_agent.rewrite_to_ground(
        answer=answer,
        citations=citations,
        unsupported=unsupported,
    )
    if rewritten:
        regrounded, still_unsupported = await chat_agent.verify_answer_grounding(
            answer=rewritten,
            citations=citations,
        )
        if regrounded:
            log.info("answer rewritten to remove unsupported phrases and re-verified")
            return rewritten, True, False
        log.info(
            "rewrite still rejected by grounding verifier; unsupported=%s",
            still_unsupported,
        )
    return _UNGROUNDED_FALLBACK, False, True


def _append_ticket_offer(answer: str) -> str:
    base = (answer or "").strip() or "I could not find a confident fix from the verified context yet."
    lowered = base.lower()
    if "yes, create a ticket" in lowered or "create a support ticket" in lowered:
        return base
    return f"{base}\n\n{_TICKET_OFFER_APPENDIX}"


def _ticket_created_reply(ticket: dict[str, object]) -> str:
    ticket_id = ticket.get("_id")
    subject = str(ticket.get("subject") or "your issue")
    status = str(ticket.get("status") or "open")
    return (
        f"I created support ticket #{ticket_id} for \"{subject}\". "
        f"It is currently {status}. A human teammate can now follow up with the investigation summary."
    )


def _ticket_description(
    *,
    conversation_id: int,
    issue_summary: str,
    user_points: list[str],
    known_facts: list[str],
    missing_facts: list[str],
) -> str:
    lines: list[str] = [
        "Issue Summary:",
        issue_summary or "The issue remains unresolved after automated troubleshooting.",
        "",
        "What the user reported:",
    ]
    if user_points:
        lines.extend(f"- {point}" for point in user_points[:4])
    else:
        lines.append("- No additional user details captured.")

    lines.extend(["", "Helia investigation summary:"])
    if known_facts:
        lines.extend(f"- Confirmed: {fact}" for fact in known_facts[:6])
    else:
        lines.append("- Confirmed: Unable to confirm durable facts yet.")

    if missing_facts:
        lines.append("- Remaining unknowns:")
        lines.extend(f"  - {fact}" for fact in missing_facts[:3])

    lines.extend(["", f"Conversation ID: {conversation_id}"])
    return "\n".join(lines).strip()


async def _create_ticket_from_conversation(
    db,
    cid: int,
    *,
    user: AuthedUser,
    agent_state: dict[str, object],
    trigger_user_message: str,
    related_message_id: int | None,
) -> dict[str, object]:
    recent = await _recent_messages(db, cid, limit=12)
    user_points = [
        _clip_line(str(m.get("content") or ""), limit=260)
        for m in recent
        if str(m.get("role") or "") == "user" and str(m.get("content") or "").strip()
    ]

    issue_summary = _clip_line(str(agent_state.get("summary") or ""), limit=220)
    if not issue_summary:
        issue_summary = _clip_line(trigger_user_message, limit=220)

    known_facts = _normalize_string_items(agent_state.get("knownFacts"), limit=6)
    missing_facts = _normalize_string_items(agent_state.get("missingFacts"), limit=3)

    subject = _ticket_subject(issue_summary, trigger_user_message)
    description = _ticket_description(
        conversation_id=cid,
        issue_summary=issue_summary,
        user_points=user_points,
        known_facts=known_facts,
        missing_facts=missing_facts,
    )
    priority = _ticket_priority(issue_text=f"{issue_summary}\n{trigger_user_message}\n{' '.join(known_facts)}")

    now = datetime.now(timezone.utc)
    external_id = f"HEL-{random.randint(10000, 99999)}"
    if zoho.is_configured():
        try:
            resp = await zoho.create_ticket(
                subject=subject,
                description=description,
                priority=priority,
                requester_email=user.email,
                requester_name=" ".join(filter(None, [user.firstName, user.lastName])).strip() or user.email,
            )
            if resp and resp.get("id"):
                external_id = f"zoho:{resp['id']}"
        except Exception as err:
            log.warning("chat escalation create_ticket failed on zoho, keeping local id: %s", err)

    ticket = {
        "_id": await next_id("tickets"),
        "userId": user.userId,
        "subject": subject,
        "description": description,
        "priority": priority,
        "status": "open",
        "externalId": external_id,
        "relatedMessageId": related_message_id,
        "lastUpdate": "Ticket opened from chat escalation",
        "createdAt": now,
        "updatedAt": now,
    }
    await db.tickets.insert_one(ticket)
    await audit_log(
        action="ticket.create",
        actor=user.email or user.userId,
        target=external_id,
        meta={
            "priority": priority,
            "subject": subject,
            "source": "chat_agent",
            "conversationId": cid,
        },
    )
    return ticket


async def _enhance_query(
    db,
    cid: int,
    current: str,
    *,
    agent_state: dict[str, object] | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """Resolve follow-up references and produce a retrieval-friendly rewrite.

    Returns ``(rewritten, intent, keywords, subqueries)``. Keywords and
    subqueries are used to widen the retrieval pool with multi-query hybrid
    search so the agent gets richer grounded context.
    """
    if os.environ.get("DISABLE_QUERY_REWRITE", "").lower() in {"1", "true", "yes"}:
        return current, "general", [], []

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
    investigation_block = chat_agent.state_context_block(agent_state or {})

    sys_prompt = (
        "You enhance customer support queries for a knowledge-base retrieval step. "
        "Given the recent conversation, the investigation memory, and the user's latest message,"
        " produce a search plan that maximises grounded recall over the knowledge base.\n\n"
        "CRITICAL — the rewritten query MUST be a self-contained, consolidated question that"
        " carries forward every key term established earlier in this conversation. The latest user"
        " message is almost never enough on its own: it is usually a short reply to a clarifying"
        " question (e.g. 'yes', 'all', 'web browser', 'access denied', a number, a category). Treat"
        " those replies as new facts to MERGE with the original topic, not as a new query.\n\n"
        "To build the rewritten query, walk the conversation and the investigation memory and"
        " collect: the original product / system the user asked about (e.g. Jira, Zoom, Outlook),"
        " the core symptom or task (e.g. access denied, cannot join, missing button), every error"
        " message or code mentioned, the scope/qualifier the user has confirmed (e.g. all projects"
        " vs one project, web vs desktop, free vs paid), and any environment detail (browser, OS,"
        " device). Then fuse them into ONE natural-language question.\n\n"
        "Output four fields:\n"
        "- rewritten: a single self-contained query, 6 to 40 words, that a search engine could"
        " answer cold without seeing this conversation. It MUST mention the product/system, the"
        " symptom, and every clarifying fact the user has already confirmed. Expand obvious"
        " acronyms. Strip greetings and pleasantries. Preserve proper nouns, error codes, product"
        " names, and exact UI strings verbatim. NEVER return just the latest user message verbatim"
        " when prior turns established a topic — always merge.\n"
        "- intent: one of how_to | troubleshooting | billing | account | policy | general.\n"
        "- keywords: 4 to 10 short search terms or phrases that capture the most important concepts"
        " in the user's situation. Include product names, feature names, error codes, action verbs"
        " (the symptom or task), and the technical noun the user is struggling with. Add 1 to 2"
        " useful synonyms or alternate spellings only when they would help BM25 retrieval. Do NOT"
        " invent product UI elements that the user did not mention.\n"
        "- subqueries: 1 to 3 alternative phrasings of the same consolidated question, each under"
        " 25 words, that target a different angle (for example: cause-oriented vs fix-oriented vs"
        " symptom-oriented, or different vocabulary the knowledge base might use). Each must also"
        " be self-contained and stay faithful to the user's actual problem; do not drift to a"
        " different topic.\n\n"
        "Worked example. Conversation so far: user said 'I have access issue on Jira', then"
        " confirmed 'all' projects, then 'Access denied', then 'web browser'. Latest message:"
        " 'web browser'. WRONG rewrite: 'web browser'. CORRECT rewrite: 'Jira access denied error"
        " for all projects when accessing via web browser — how to restore access'.\n\n"
        'Reply as JSON: {"rewritten": "<query>", "intent": "<intent>", '
        '"keywords": ["<term>", ...], "subqueries": ["<phrasing>", ...]}'
    )
    user_prompt = (
        f"Investigation memory:\n{investigation_block}\n\n"
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
            max_tokens=400,
        )
        obj = json.loads(raw)
        rewritten = (obj.get("rewritten") or "").strip() or current
        intent = (obj.get("intent") or "general").strip() or "general"
        keywords = _normalize_string_items(obj.get("keywords"), limit=10)
        subqueries = _normalize_string_items(obj.get("subqueries"), limit=3)
        return rewritten, intent, keywords, subqueries
    except Exception as err:
        log.debug("query enhancement failed, using raw query: %s", err)
        return current, "general", [], []


async def _persist_user_message(
    db, cid: int, content: str, *, image_data_url: str | None = None
) -> dict:
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
        "imageDataUrl": image_data_url,
        "createdAt": datetime.now(timezone.utc),
    }
    await db.messages.insert_one(msg)
    return msg


async def _persist_assistant_message(
    db,
    cid: int,
    *,
    content: str,
    citations: list[dict[str, object]],
    can_answer: bool | None,
    started: float,
    rewritten_query: str | None,
    intent: str | None,
    kind: str,
    ticket_id: int | None = None,
) -> dict:
    assistant_msg = {
        "_id": await next_id("messages"),
        "conversationId": cid,
        "role": "assistant",
        "content": content,
        "citations": citations,
        "canAnswer": can_answer,
        "latencyMs": int((time.time() - started) * 1000),
        "rating": None,
        "feedbackComment": None,
        "rewrittenQuery": rewritten_query,
        "intent": intent,
        "kind": kind,
        "ticketId": ticket_id,
        "createdAt": datetime.now(timezone.utc),
    }
    await db.messages.insert_one(assistant_msg)
    return assistant_msg


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


async def _persist_agent_state(db, cid: int, state: dict[str, object]) -> None:
    await db.conversations.update_one(
        {"_id": cid},
        {"$set": {"agentState": state, "updatedAt": datetime.now(timezone.utc)}},
    )


async def _recent_messages(db, cid: int, *, limit: int = 8) -> list[dict[str, object]]:
    recent = (
        await db.messages.find(
            {"conversationId": cid},
            {"role": 1, "content": 1, "createdAt": 1},
        )
        .sort("createdAt", -1)
        .to_list(length=limit)
    )
    recent.reverse()
    return recent


def _select_citations(citations: list[dict[str, object]], used_idx: list[int]) -> list[dict[str, object]]:
    if not citations:
        return []
    if not used_idx:
        return citations
    filtered = [citations[n - 1] for n in used_idx if 1 <= n <= len(citations)]
    return filtered or citations


async def _prepare_agent_turn(
    db,
    cid: int,
    content: str,
    *,
    user_id: str,
    tenant_id: str | None,
    agent_state: dict[str, object],
    planner_state: dict[str, object] | None = None,
) -> dict[str, object]:
    state_for_planner = planner_state or agent_state
    enhanced_query, enhanced_intent, enhanced_keywords, enhanced_subqueries = await _enhance_query(
        db,
        cid,
        content,
        agent_state=state_for_planner,
    )
    retrieval = await _cached_retrieve(
        db,
        content,
        tenant_id=tenant_id,
        rewritten=enhanced_query,
        intent=enhanced_intent,
        keywords=enhanced_keywords,
        subqueries=enhanced_subqueries,
    )
    memory_snippets = await agent_memory.search_user_memory(user_id, content)
    recent_messages = await _recent_messages(db, cid)
    decision = await chat_agent.decide_next_action(
        recent_messages=recent_messages,
        current_user_message=content,
        retrieval_context=retrieval.context_block(),
        citations=retrieval.citations(),
        memory_snippets=memory_snippets,
        agent_state=state_for_planner,
    )
    return {
        "decision": decision,
        "retrieval": retrieval,
        "memory_snippets": memory_snippets,
        "selected_citations": _select_citations(retrieval.citations(), decision.used_citations),
    }


async def _cached_retrieve(
    db,
    query: str,
    *,
    tenant_id: str | None = None,
    rewritten: str | None = None,
    intent: str | None = None,
    keywords: list[str] | None = None,
    subqueries: list[str] | None = None,
):
    cache_key_query = rewritten or query
    keyword_seed = "|".join(sorted((k or "").strip().lower() for k in (keywords or []) if k))
    subquery_seed = "|".join(sorted((s or "").strip().lower() for s in (subqueries or []) if s))
    cache_seed = f"{tenant_id or '_'}::{cache_key_query}::{keyword_seed}::{subquery_seed}"
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
        db,
        query,
        tenant_id=tenant_id,
        pre_rewritten=rewritten,
        pre_intent=intent,
        pre_keywords=keywords,
        pre_subqueries=subqueries,
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
    *,
    agent_state: dict[str, object] | None = None,
):
    memory_block = ""
    if memory_snippets:
        memory_lines = "\n".join(f"- {m}" for m in memory_snippets[:5])
        memory_block = (
            "\n\nKnown user memory (preferences and profile facts):\n"
            f"{memory_lines}\n"
            "Use this only when relevant to the current request."
        )

    investigation_block = ""
    if agent_state:
        investigation_block = (
            "\n\nCurrent investigation memory:\n"
            f"{chat_agent.state_context_block(agent_state)}"
        )

    recent = (
        await db.messages.find({"conversationId": cid})
        .sort("createdAt", 1)
        .to_list(length=None)
    )
    recent_slice = recent[-6:]
    required_stage = _infer_required_troubleshooting_stage(recent_slice, current_user_content)
    stage_requirement_block = ""
    if required_stage:
        stage_requirement_block = (
            f"- Current required troubleshooting stage for this turn: {required_stage}.\n"
            "- You must follow the required stage exactly for troubleshooting-oriented requests.\n"
            "- If required stage is Stage 1, output only diagnosis plus consent check for next steps.\n"
            "- If required stage is Stage 2, output only 'Try this now:' with one concrete action.\n"
            "- If required stage is Stage 3, output only 'What to tell me next:' asking for the result.\n"
            "- If required stage is Stage 4, output the fuller grounded resolution or fallback path.\n"
        )

    sys_prompt = (
        "You are Helia, a warm and capable AI customer support teammate. "
        "Answer the user's question using ONLY the numbered context snippets below.\n\n"
        "This is the final answer step for the current turn. The investigation has already happened. "
        "Use the accumulated investigation memory and the verified context to give the strongest useful"
        " answer you can now. Do not restart broad discovery in this step, but you may end with one"
        " brief, targeted follow-up question when it helps the user confirm the next action.\n\n"
        "Strict grounding rule (most important):\n"
        "- Every concrete action step you ask the user to take — every 'click X', 'open Y', 'go to Z',"
        " 'select W', 'run N', 'enable M', 'check P', every menu name, button label, settings path,"
        " configuration value, command, URL, or product UI element — MUST appear by name in one of"
        " the numbered context snippets. Cite the snippet inline as [n] at the action.\n"
        "- The same rule applies to the probable cause / 'What's likely happening' diagnosis: state"
        " a cause ONLY if the snippets explicitly support it. Do not infer causes from general"
        " product knowledge.\n"
        "- Do not invent product UI steps, menu paths, icons, arrows, settings names, or screen"
        " labels from general knowledge, even if you are confident they exist in the real product."
        " If the snippet does not name the exact UI element or step verbatim, you do not know it"
        " for this product and you must not write it.\n"
        "- Forbidden examples (when no snippet contains these strings): writing 'click the ^ arrow',"
        " 'select Audio Settings', 'go to Settings > Audio', 'open the Zoom client', 'click the"
        " gear icon'. If a snippet does not contain that label, you cannot use it. Generic phrasing"
        " like 'check your audio settings' without naming a specific UI control is also forbidden"
        " unless a snippet says it.\n"
        "- If the snippets do not contain a concrete grounded action or cause for the user's specific"
        " situation, do NOT improvise. Instead set canAnswer to false, briefly say what specific"
        " detail or document is missing, and either ask one narrow question whose answer would"
        " unlock a grounded step from the snippets, or wait for the user to provide more detail."
        " Never fabricate a fix or a cause.\n"
        "- If the user describes a product or scenario the snippets do not cover at all, say so plainly"
        " and set canAnswer to false. Do not pivot to generic advice from world knowledge.\n"
        "- Self-check before sending: for every action verb (click, open, select, navigate, go to,"
        " enable, run, etc.) and every product-specific noun in your answer, confirm the named"
        " thing appears in a cited snippet. If any does not, rewrite the answer to remove it or"
        " set canAnswer to false.\n"
        "- Verbatim quoting rule (critical): when the snippet names a specific UI element, menu,"
        " button, setting, path, command, or error code, copy the exact wording from the snippet —"
        " do not rephrase, title-case, expand, or shorten it. If the snippet says 'audio"
        " preferences', write 'audio preferences', not 'Audio Settings'. If the snippet says"
        " 'Speaker' dropdown, do not call it a 'speaker selector'. Wrap the copied label in single"
        " quotes so the user sees it exactly as the KB names it. A downstream verifier rejects"
        " paraphrased UI labels even when the underlying thing is the same, so faithful copying is"
        " mandatory, not stylistic.\n"
        "- Narrative-snippet exception: some snippets are narrative recollections rather than"
        " labeled procedures (for example 'I adjusted the audio device selection and the echo"
        " stopped'). In that case there is no UI label to quote, and you SHOULD still recommend the"
        " same action in plain language — for example 'Try changing your audio device [n]' — and"
        " set canAnswer to true. Stay faithful to what the snippet actually describes (same"
        " feature area, same action). Do NOT invent specific UI controls the snippet does not"
        " name; just recommend the action itself with a [n] citation.\n\n"
        "Tone and style:\n"
        "- Be friendly, polite, and reassuring. Open with a brief, warm acknowledgement "
        '(for example: "Happy to help with that!", "I can definitely help you sort this out.", '
        '"Sorry you\'re running into this — let\'s get it fixed."). Vary the wording naturally; '
        "do not reuse the same opener every turn.\n"
        "- Show human emotional intelligence. Use acknowledgement, empathy, sympathy, appreciation,"
        " gentle joy, or sorrow only when they fit the situation.\n"
        "- If the user is blocked, worried, or frustrated, explicitly acknowledge that and respond with"
        " empathy or sympathy in a natural way.\n"
        "- If the user has already provided useful context or followed prior steps, briefly appreciate"
        " that effort before moving into the solution.\n"
        "- Use joy or upbeat warmth only when the situation is genuinely positive, such as confirming"
        " something worked or sharing good news. Do not sound cheerful about a failure or access issue.\n"
        "- If the situation involves loss, failure, denial, or inconvenience, a light note of sorrow or"
        " concern is appropriate, but keep it concise and professional.\n"
        "- Speak in first person and address the user directly. Sound like a real support teammate, "
        "not a generic bot.\n\n"
        "Length and format (strict):\n"
        "- Keep every reply short and conversational. Stages 1, 2, and 3 must each fit in 2 to 3"
        " short sentences. Stage 4 may run a little longer (up to 5 short sentences) but never"
        " becomes a long article.\n"
        "- Do not use bullet lists in Stage 1, Stage 2, or Stage 3. Use plain conversational sentences.\n"
        "- In Stage 4, use bullets only if listing 2 or 3 short concrete steps. Never produce long"
        " bullet dumps or multi-paragraph walls of text.\n"
        "- Aim for a real back-and-forth chat where the user is invited to participate each turn,"
        " not a one-shot help-article dump.\n\n"
        "Solution intent (important):\n"
        "- Use the whole conversation plus investigation memory, not just the latest user message,"
        " to decide what the user has already tried and what the most useful next action is.\n"
        "- After acknowledging, state the likely cause(s) in plain language when the context supports it.\n"
        "- Do not dump the entire resolution immediately when a staged troubleshooting reply would be"
        " more useful. Use strict step-by-step turn gating for blocked users.\n"
        "- When troubleshooting, follow this exact sequence across separate assistant turns, not in one"
        " combined reply:\n"
        "  Stage 1: Give only a short 'What's likely happening:' diagnosis and then ask if the user"
        " wants help with next steps. Stop there.\n"
        "  Stage 2: Only after the user confirms (for example: yes, sure, continue), give only"
        " 'Try this now:' with one concrete action. Stop there.\n"
        "  Stage 3: In the next assistant turn, ask only 'What to tell me next:' focused on the result"
        " of that action.\n"
        "  Stage 4: Only after the user shares that result, provide the fuller resolution or fallback"
        " path grounded in context.\n"
        f"{stage_requirement_block}"
        "- Output EXACTLY ONE stage per assistant turn. Never combine 'What's likely happening:' with"
        " 'Try this now:' in the same message. Never combine 'Try this now:' with 'What to tell me"
        " next:' in the same message. Each label belongs to its own separate turn.\n"
        "- After outputting 'Try this now:', stop. Do not also write 'What to tell me next:' in that"
        " same turn — that line is for the following assistant turn only.\n"
        "- The follow-up question must be specific and actionable, not generic. Ask about the result"
        " of the step you just gave or the single most decisive remaining detail.\n"
        "- Once the user reaches Stage 4, clearly explain what they should do or check to resolve"
        " the issue. Guide them through steps kindly and clearly. Be their helper, not just a list"
        " of tasks.\n"
        "- Use the investigation memory to tailor the answer to the facts already gathered in prior turns.\n"
        "- Prefer a concrete likely fix path over a vague diagnosis summary.\n"
        "- Only suggest actions the user can actually take themselves; never claim you can access "
        "their system or perform technical actions on their behalf.\n\n"
        "Grounding and citations:\n"
        "- Cite sources inline using [n] notation matching the snippets you used. Every action step"
        " and every product-specific term needs a [n] citation.\n"
        "- If the answer cannot be found in the context, set canAnswer to false, apologise briefly,"
        " and say exactly what detail or document is still missing. Do not invent a 'best guess' next"
        " step from general knowledge. If a narrow clarifying question would unlock a grounded step"
        " from the snippets, ask that question instead.\n"
        "- Do not mention support tickets or escalation unless the user explicitly asks for that.\n"
        "- Never invent facts, policies, UI elements, menu paths, settings names, or steps that are"
        " not supported by the context.\n\n"
        'Respond as JSON with this exact shape:\n'
        '{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }\n\n'
        f"{memory_block}"
        f"{investigation_block}"
        f"Context:\n{context}"
    )

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
            "Please try again in a moment.",
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
