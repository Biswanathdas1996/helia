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
from fastapi.responses import JSONResponse, StreamingResponse

from app import agent_memory, cache, chat_agent, llm, metrics, query_rewrite, rate_limit
from app.audit import audit_log
from app.auth import AuthedUser, require_auth
from app.db import get_db, next_id
from app.retrieval import retrieve
from app.schemas import CreateConversationBody, SendMessageBody, TtsBody
from app.serialize import serialize_conversation, serialize_message, serialize_ticket
from app.tenant import tenant_for
from app import zoho

router = APIRouter()
log = logging.getLogger("api-server.chat")

_RETRIEVAL_CACHE_TTL = 300
# Hard ceiling on grounding/on-topic verifier wall time. The streamed answer
# is already visible to the user by the time we reach the verifier, so a
# transient slow verifier must never block the perceived response. On
# timeout we fail-open and keep the streamed answer.
_VERIFIER_TIMEOUT_S = 6.0


def _answer_uses_reasoning() -> bool:
    """Extra model reasoning adds noticeable time-to-first-token on voice/chat.

    Default off so streamed answers and non-stream replies start quickly; set
    ``HELIA_ANSWER_REASONING=1`` when quality trade-offs favour latency.
    """
    raw = (os.environ.get("HELIA_ANSWER_REASONING") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _schedule_await(awaitable):
    """Schedule concurrent work from the current event loop.

    Motor / PyMongo ``find_one`` (and some other helpers) return a bare
    ``asyncio.Future``, not a coroutine — ``asyncio.create_task`` rejects
    those on Python 3.10+. :func:`asyncio.ensure_future` accepts both.
    """
    return asyncio.ensure_future(awaitable)
_MEMORY_GRAPH_QUERY_FALLBACK = "user preferences profile support history"
_TICKET_INTENT_PATTERNS = [
    re.compile(r"\b(create|raise|open|file|submit|log|start)\s+(a\s+|an\s+|the\s+|new\s+)*(support\s+)?ticket\b", re.IGNORECASE),
    re.compile(r"\bnew\s+ticket\b", re.IGNORECASE),
    re.compile(r"\bescalate\b.*\b(ticket|human|agent|support)\b", re.IGNORECASE),
    re.compile(r"\b(want|need|like)\s+to\s+(create|open|raise|file|submit)\s+(a\s+|an\s+|the\s+|new\s+)*(support\s+)?ticket\b", re.IGNORECASE),
]
_LIST_TICKETS_INTENT_PATTERNS = [
    re.compile(r"\b(view|show|see|list|display|fetch|get|check|pull\s*up|bring\s*up)\b[^.\n]{0,40}\b(my\s+)?(support\s+|open\s+)?tickets?\b", re.IGNORECASE),
    re.compile(r"\b(my|all)\s+(support\s+|open\s+)?tickets?\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+tickets?\s+do\s+i\s+have\b", re.IGNORECASE),
    re.compile(r"\btickets?\s+(do\s+i\s+have|i\s+(have|raised|opened|filed))\b", re.IGNORECASE),
]
_TICKET_STATUS_FILTER_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
    (re.compile(r"\bin[\s_-]?progress\b", re.IGNORECASE), "in_progress"),
    (re.compile(r"\b(closed|resolved|completed)\b", re.IGNORECASE), "closed"),
    (re.compile(r"\bopen\b", re.IGNORECASE), "open"),
]
_TICKET_CONSENT_PATTERNS = [
    re.compile(r"\b(yes|yep|yeah|sure|okay|ok|please|go ahead|do it|proceed|confirm)\b", re.IGNORECASE),
    re.compile(r"\b(create|open|raise|file|submit)\s+(a\s+|an\s+|the\s+)?(support\s+)?ticket\b", re.IGNORECASE),
]
_TICKET_DECLINE_PATTERNS = [
    re.compile(r"\b(no|nah|not now|don't|do not|stop|cancel|no thanks)\b", re.IGNORECASE),
]
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

_CASUAL_GREETING_MAX_LEN = 52
_CASUAL_GREETING_MAX_WORDS = 7


def _reply_language(user_text: str | None) -> str:
    return chat_agent.detect_reply_language(user_text)


def _ticket_intent_response(user_text: str | None) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        return (
            "ज़रूर — मैं आपके लिए एक सपोर्ट टिकट खोलने में मदद कर सकता हूं ताकि हमारी मानव टीम आगे फॉलो अप कर सके। "
            "बस \"yes, create a ticket\" लिखकर जवाब दें, मैं इस जांच का सार जोड़कर टिकट बना दूंगा।"
        )
    if language == "bn":
        return (
            "অবশ্যই — আমি আপনার জন্য একটি সাপোর্ট টিকিট খুলতে সাহায্য করতে পারি যাতে মানব টিম পরে ফলো আপ করতে পারে। "
            "শুধু \"yes, create a ticket\" লিখে উত্তর দিন, আমি এই তদন্তের সারাংশ দিয়ে টিকিট তৈরি করে দেব।"
        )
    return (
        "Of course — I can help you open a support ticket so a human teammate can follow up. "
        "Reply \"yes, create a ticket\" and I will create it with a summary of this investigation."
    )


def _list_tickets_empty_reply(user_text: str | None) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        return "आपके पास अभी कोई सपोर्ट टिकट नहीं है। अगर कुछ अटका हुआ है, तो मुझे बताइए, मैं आपके लिए एक टिकट खोल सकता हूं।"
    if language == "bn":
        return "আপনার এখনো কোনো সাপোর্ট টিকিট নেই। কিছু আটকে থাকলে বলুন, আমি আপনার জন্য একটি টিকিট খুলে দিতে পারি।"
    return (
        "You don't have any support tickets yet. If something's blocking you, tell me what's going on "
        "and I can open one for you."
    )


def _ticket_offer_appendix(user_text: str | None) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        return "अगर यह अभी भी आपको रोक रहा है, तो मैं एक सपोर्ट टिकट खोल सकता हूं ताकि हमारी मानव टीम इसे संभाल सके — बस \"yes, create a ticket\" लिखकर जवाब दें।"
    if language == "bn":
        return "এতে যদি এখনো কাজ আটকে থাকে, আমি একটি সাপোর্ট টিকিট খুলে দিতে পারি যাতে মানব টিম এটা নিতে পারে — শুধু \"yes, create a ticket\" লিখে উত্তর দিন।"
    return (
        "If this is still blocking you, I can open a support ticket so a human teammate can pick it up — "
        "just reply \"yes, create a ticket\" and I'll do it."
    )


def _resolution_acknowledgement(user_text: str | None) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        return "बहुत बढ़िया — यह ठीक हो गया, यह जानकर खुशी हुई। अगर आगे कुछ और आए, तो यहीं संदेश भेजिए, मैं फिर देख लूंगा।"
    if language == "bn":
        return "দারুণ — সমস্যাটা মিটেছে জেনে ভালো লাগল। পরে আর কিছু হলে এখানেই লিখুন, আমি আবার দেখে দেব।"
    return (
        "Wonderful — really glad that sorted it. "
        "If anything else comes up, just message me here and I'll take another look."
    )


def _casual_greeting_reply(user_text: str | None) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        return "नमस्ते! मैं ठीक हूं और मदद के लिए यहां हूं। आज मैं आपकी किस बात में मदद कर सकता हूं?"
    if language == "bn":
        return "হ্যালো! আমি ভালো আছি এবং সাহায্য করতে প্রস্তুত। আজ আমি কীভাবে আপনাকে সাহায্য করতে পারি?"
    return "Hello! I'm doing well and I'm here to help. What can I assist you with today?"


def _ungrounded_fallback(user_text: str | None) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        return (
            "माफ कीजिए — मुझे हमारी नॉलेज बेस में इसके लिए सत्यापित स्टेप्स नहीं मिले, इसलिए मैं मेनू या सेटिंग्स के बारे में अनुमान नहीं लगाना चाहता। "
            "अगर आप चाहें, तो मैं एक सपोर्ट टिकट खोल सकता हूं ताकि हमारी मानव टीम इसे आगे संभाल सके — बस \"yes, create a ticket\" लिखकर जवाब दें।"
        )
    if language == "bn":
        return (
            "দুঃখিত — আমাদের নলেজ বেসে এর জন্য যাচাই করা ধাপ পাইনি, তাই মেনু বা সেটিংস নিয়ে আন্দাজ করতে চাই না। "
            "চাইলে আমি একটি সাপোর্ট টিকিট খুলে দিতে পারি যাতে মানব টিম এটা এগিয়ে নিতে পারে — শুধু \"yes, create a ticket\" লিখে উত্তর দিন।"
        )
    return (
        "Sorry — I couldn't find verified steps for this in our knowledge base, so I don't want to"
        " guess at the exact menus or settings. If you'd like, I can open a support ticket so a human"
        " teammate can pick this up — just reply \"yes, create a ticket\" and I'll do it."
    )


def _model_error_reply(user_text: str | None, *, include_ticket_offer: bool = False) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        if include_ticket_offer:
            return "मुझे अभी मॉडल तक पहुंचने में दिक्कत हो रही है। कृपया थोड़ी देर में फिर कोशिश करें, या चाहें तो एक सपोर्ट टिकट खोल दें।"
        return "मुझे अभी मॉडल तक पहुंचने में दिक्कत हो रही है। कृपया थोड़ी देर में फिर कोशिश करें।"
    if language == "bn":
        if include_ticket_offer:
            return "এই মুহূর্তে মডেলে পৌঁছাতে সমস্যা হচ্ছে। একটু পরে আবার চেষ্টা করুন, বা চাইলে একটি সাপোর্ট টিকিট খুলতে পারেন।"
        return "এই মুহূর্তে মডেলে পৌঁছাতে সমস্যা হচ্ছে। একটু পরে আবার চেষ্টা করুন।"
    if include_ticket_offer:
        return "I'm having trouble reaching the model right now. Please try again, or open a support ticket."
    return "I'm having trouble reaching the model right now. Please try again in a moment."


def _generation_empty_reply(user_text: str | None) -> str:
    language = _reply_language(user_text)
    if language == "hi":
        return "मैं इस समय जवाब तैयार नहीं कर सका।"
    if language == "bn":
        return "আমি এই মুহূর্তে একটি উত্তর তৈরি করতে পারিনি।"
    return "I couldn't generate a response."
# Normalized (lowercase, punctuation stripped, no apostrophes) phrases only — keeps
# real questions like "Hello, I need VPN access" on the full retrieval path.
_CASUAL_GREETING_PHRASES: frozenset[str] = frozenset(
    {
        "hi",
        "hey",
        "hello",
        "yo",
        "howdy",
        "sup",
        "hiya",
        "greetings",
        "morning",
        "mornin",
        "afternoon",
        "evening",
        "hi there",
        "hey there",
        "hello there",
        "hi everyone",
        "hey everyone",
        "hello everyone",
        "good morning",
        "good afternoon",
        "good evening",
        "good day",
        "how are you",
        "howre you",
        "how is it going",
        "hows it going",
        "whats up",
        "what is up",
        "how do you do",
        "how are you doing",
        "how have you been",
    }
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


# ---------------------------------------------------------------------------
# Voice chat — ElevenLabs Text-to-Speech
# ---------------------------------------------------------------------------

_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# Default to "Monika Sogam" — an Indian English female voice from the
# ElevenLabs voice library (warm, natural, professional). If your account
# can't use library voices directly (free plan), add her to your VoiceLab and
# override this with ELEVENLABS_VOICE_ID in .env.
_ELEVENLABS_DEFAULT_VOICE_ID = "1qZOLVpd1TVic43MSkFY"
# Premade voices that work on free-tier accounts; used only when the
# configured/library voice is unavailable for the current ElevenLabs plan.
_ELEVENLABS_SERVER_FALLBACK_VOICE_IDS = (
    "hpp4J3VqNfWAUOO0d1Us",  # Bella
    "cgSgspJ2msm6clMCkdW9",  # Jessica
    "EXAVITQu4vr4xnSDxMaL",  # Sarah
)
# Multilingual v2 reproduces non-American accents (incl. Indian English) far
# better than the turbo models, which is critical for natural pronunciation.
_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
_ELEVENLABS_LANGUAGE_CODE = "en"


def _strip_text_for_tts(text: str) -> str:
    """Remove markdown / citation markers so TTS reads natural prose."""
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    # Strip fenced code blocks
    cleaned = re.sub(r"```[\s\S]*?```", " ", cleaned)
    # Strip inline code
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    # Bold/italic markers
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*(.+?)\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"_(.+?)_", r"\1", cleaned)
    # Citation refs like [1] or [12]
    cleaned = re.sub(r"\[\d+\]", "", cleaned)
    # Markdown links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    # Bullet / heading markers at line start
    cleaned = re.sub(r"^[ \t]*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^[ \t]*#+\s+", "", cleaned, flags=re.MULTILINE)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _browser_tts_fallback(text: str, reason: str) -> JSONResponse:
    """Return a JSON payload telling the browser to use its built-in
    SpeechSynthesis API so the user still hears the response when ElevenLabs
    is unavailable (e.g. missing key, free-plan voice restriction).
    """
    return JSONResponse(
        status_code=200,
        content={"useBrowserTts": True, "text": text, "reason": reason},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/chat/tts")
async def chat_text_to_speech(
    body: TtsBody,
    user: AuthedUser = Depends(require_auth),
) -> Response:
    """Synthesize the given text into MP3 audio using ElevenLabs.

    The endpoint keeps the ElevenLabs API key on the server. The browser
    normally receives an ``audio/mpeg`` payload, but when ElevenLabs is not
    configured or rejects the request (for example, a free-plan account
    cannot use library voices), we return a small JSON object so the
    browser can fall back to its built-in ``speechSynthesis`` API and the
    user still hears the assistant's reply.
    """
    import os as _os

    spoken = _strip_text_for_tts(body.text)
    if not spoken:
        raise HTTPException(status_code=400, detail="Nothing to speak")

    api_key = (_os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        log.warning(
            "ELEVENLABS_API_KEY is not set; falling back to browser SpeechSynthesis."
        )
        return _browser_tts_fallback(spoken, reason="missing_api_key")

    voice_id = (body.voiceId or _os.environ.get("ELEVENLABS_VOICE_ID") or _ELEVENLABS_DEFAULT_VOICE_ID).strip()
    model_id = (_os.environ.get("ELEVENLABS_MODEL_ID") or _ELEVENLABS_MODEL_ID).strip()
    language_code = (_os.environ.get("ELEVENLABS_LANGUAGE_CODE") or _ELEVENLABS_LANGUAGE_CODE).strip()

    url = _ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    # Voice settings tuned for a natural, polite Indian English woman:
    #   stability  ~0.55  -> consistent tone without going monotone/robotic
    #   similarity ~0.85  -> strongly preserve the chosen voice's accent
    #   style      ~0.35  -> allow gentle, human expressiveness/warmth
    #   speaker_boost      -> richer, more present timbre
    payload: dict[str, object] = {
        "text": spoken,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.55,
            "similarity_boost": 0.85,
            "style": 0.35,
            "use_speaker_boost": True,
        },
    }
    if language_code:
        # Hint multilingual v2 to read English with the voice's native accent
        # rather than auto-detecting; helps lock in Indian English pronunciation.
        payload["language_code"] = language_code
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }

    try:
        import httpx as _httpx

        async with _httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200 and resp.status_code in {401, 402, 403, 404}:
                for fallback_voice_id in _ELEVENLABS_SERVER_FALLBACK_VOICE_IDS:
                    if fallback_voice_id == voice_id:
                        continue
                    fallback_url = _ELEVENLABS_TTS_URL.format(voice_id=fallback_voice_id)
                    fallback_resp = await client.post(fallback_url, headers=headers, json=payload)
                    if fallback_resp.status_code == 200:
                        log.warning(
                            "Configured ElevenLabs voice %s unavailable (%s); using server-side fallback voice %s.",
                            voice_id,
                            resp.status_code,
                            fallback_voice_id,
                        )
                        resp = fallback_resp
                        break
    except Exception as exc:
        log.warning("TTS request failed (%s); falling back to browser SpeechSynthesis.", exc)
        return _browser_tts_fallback(spoken, reason="request_failed")

    if resp.status_code != 200:
        log.warning(
            "ElevenLabs TTS failed: %s %s — falling back to browser SpeechSynthesis.",
            resp.status_code,
            resp.text[:500],
        )
        reason = "elevenlabs_error"
        if resp.status_code == 402:
            reason = "payment_required"
        elif resp.status_code in (401, 403):
            reason = "unauthorized"
        return _browser_tts_fallback(spoken, reason=reason)

    return Response(
        content=resp.content,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


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

    # Parallel setup: persist the new message, update the conversation
    # title, load the most recent assistant message (used by three intent
    # short-circuits below) all in one round trip.
    persist_user_task = asyncio.create_task(
        _persist_user_message(db, cid, body.content, image_data_url=body.imageDataUrl)
    )
    bump_title_task = asyncio.create_task(_bump_conversation_title(db, c, body.content))
    last_assistant_task = _schedule_await(
        db.messages.find_one(
            {"conversationId": cid, "role": "assistant"},
            sort=[("createdAt", -1)],
        )
    )

    user_msg = await persist_user_task
    last_assistant: dict[str, object] | None
    last_assistant, _ = await asyncio.gather(last_assistant_task, bump_title_task)

    agent_state = chat_agent.normalize_state(c.get("agentState"))
    planner_state = _planner_state_with_unresolved_signal_sync(
        agent_state, body.content, last_assistant
    )

    if _should_create_ticket_from_consent_sync(body.content, last_assistant):
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
            content=_ticket_created_reply(ticket, body.content),
            citations=[],
            can_answer=False,
            started=started,
            rewritten_query=None,
            intent="ticket_created",
            kind="ticket_created",
            ticket_id=ticket["_id"],
            user_id=user.userId,
            user_text=body.content,
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    if _should_acknowledge_resolution_sync(body.content, last_assistant):
        await _persist_agent_state(db, cid, _resolution_state(agent_state, body.content))
        assistant_msg = await _persist_resolution_acknowledgement(
            db, cid, started, user_id=user.userId, user_text=body.content
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    if _detect_list_tickets_intent(body.content):
        status_filter = _extract_ticket_status_filter(body.content)
        tickets = await _fetch_user_tickets(db, user.userId, status=status_filter)
        assistant_msg = await _persist_tickets_list_reply(
            db, cid, started, tickets, status=status_filter,
            user_id=user.userId, user_text=body.content,
        )
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
        assistant_msg = await _persist_ticket_intent_reply(
            db, cid, started, user_id=user.userId, user_text=body.content
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    if _is_casual_greeting_only(body.content) and not (body.imageDataUrl or "").strip():
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=_casual_greeting_reply(body.content),
            citations=[],
            can_answer=True,
            started=started,
            rewritten_query=None,
            intent="casual_greeting",
            kind="casual_greeting",
            user_id=user.userId,
            user_text=body.content,
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    # Run query rewrite + durable user memory search concurrently with the
    # retrieval that depends on the rewrite. ``_prepare_agent_turn`` keeps
    # the planner LLM call after retrieval (it needs the citations), but
    # internally also parallelises the rewrite + memory search.
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
    # Full retrieval set (same as LLM context); _select_citations is only for verifier/used-[n] logic.
    context_citations = retrieval.citations()

    if decision.action == "ask_clarifying_question":
        next_state = chat_agent.apply_decision_to_state(agent_state, decision)
        await _persist_agent_state(db, cid, next_state)
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=decision.reply,
            citations=context_citations,
            can_answer=None,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent="clarification_question",
            kind="clarification_question",
            user_id=user.userId,
            user_text=body.content,
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
            citations=context_citations,
            can_answer=False,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent="investigation_ticket_offer",
            kind="ticket_offer",
            user_id=user.userId,
            user_text=body.content,
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
            content=_ticket_created_reply(ticket, body.content),
            citations=[],
            can_answer=False,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent="ticket_created",
            kind="ticket_created",
            ticket_id=ticket["_id"],
            user_id=user.userId,
            user_text=body.content,
        )
        return {
            "userMessage": serialize_message(user_msg),
            "assistantMessage": serialize_message(assistant_msg),
        }

    answer_state = chat_agent.apply_decision_to_state(agent_state, decision)
    _, turns, required_stage = await _build_chat_payload(
        db,
        cid,
        body.content,
        retrieval.context_block(),
        prepared["memory_snippets"],
        agent_state=answer_state,
        rewritten_query=retrieval.rewritten_query,
        recent_messages=prepared["recent_messages"],
    )

    answer, can_answer, used_idx = await _generate_answer(turns, user_text=body.content)
    filtered = _select_citations(retrieval.citations(), used_idx or decision.used_citations)
    can_answer = _resolve_can_answer(answer, can_answer)

    answer, can_answer, replaced_by_verifier = await _enforce_grounded_answer_safe(
        answer=answer,
        citations=filtered,
        can_answer=can_answer,
        rewritten_query=retrieval.rewritten_query,
        check_topic=(required_stage == "Stage 4"),
        user_text=body.content,
    )

    message_kind = "answer"
    response_intent = retrieval.intent
    if not can_answer:
        if not replaced_by_verifier:
            answer = _append_ticket_offer(answer, body.content)
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

    final_verdict = message_kind == "answer" and required_stage == "Stage 4"
    assistant_msg = await _persist_assistant_message(
        db,
        cid,
        content=answer,
        citations=context_citations,
        can_answer=can_answer,
        started=started,
        rewritten_query=retrieval.rewritten_query,
        intent=response_intent,
        kind=message_kind,
        final_verdict=final_verdict,
        user_id=user.userId,
        user_text=body.content,
    )

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

        # ------------------------------------------------------------------
        # Phase 1 — Setup work (no LLM, all parallel).
        #
        # We can persist the user prompt, refresh the conversation metadata,
        # load the last assistant message (for the three intent classifiers
        # below), and read the recent message window all at the same time.
        # ------------------------------------------------------------------
        yield _sse("process", {"name": "Saving user prompt", "status": "started"})
        persist_user_task = asyncio.create_task(
            _persist_user_message(db, cid, body.content, image_data_url=body.imageDataUrl)
        )
        bump_title_task = asyncio.create_task(_bump_conversation_title(db, c, body.content))
        last_assistant_task = _schedule_await(
            db.messages.find_one(
                {"conversationId": cid, "role": "assistant"},
                sort=[("createdAt", -1)],
            )
        )
        recent_messages_task = asyncio.create_task(_recent_messages(db, cid))

        user_msg = await persist_user_task
        yield _sse("process", {"name": "Saving user prompt", "status": "completed"})
        yield _sse("user", serialize_message(user_msg))
        yield _sse("process", {"name": "Updating conversation metadata", "status": "started"})

        last_assistant: dict[str, object] | None
        last_assistant, _ = await asyncio.gather(last_assistant_task, bump_title_task)
        yield _sse("process", {"name": "Updating conversation metadata", "status": "completed"})

        agent_state = chat_agent.normalize_state(c.get("agentState"))

        # ------------------------------------------------------------------
        # Phase 2 — Deterministic short-circuits using the preloaded
        # last_assistant doc. None of these need the LLM at all, so they
        # produce a response in tens of milliseconds.
        # ------------------------------------------------------------------
        if _should_create_ticket_from_consent_sync(body.content, last_assistant):
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
            ticket_reply = _ticket_created_reply(ticket, body.content)
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
                user_id=user.userId,
                user_text=body.content,
            )
            yield _sse("process", {"name": "Creating support ticket", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        if _should_acknowledge_resolution_sync(body.content, last_assistant):
            yield _sse("process", {"name": "Acknowledging resolution", "status": "started"})
            await _persist_agent_state(db, cid, _resolution_state(agent_state, body.content))
            yield _sse("citations", [])
            resolution_reply = _resolution_acknowledgement(body.content)
            for chunk in resolution_reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_resolution_acknowledgement(
                db, cid, started, user_id=user.userId, user_text=body.content
            )
            yield _sse("process", {"name": "Acknowledging resolution", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        if _detect_list_tickets_intent(body.content):
            yield _sse("process", {"name": "Fetching your tickets", "status": "started"})
            status_filter = _extract_ticket_status_filter(body.content)
            tickets = await _fetch_user_tickets(db, user.userId, status=status_filter)
            tickets_reply = _format_tickets_reply(tickets, status=status_filter, user_text=body.content)
            yield _sse("citations", [])
            for chunk in tickets_reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_tickets_list_reply(
                db, cid, started, tickets, status=status_filter,
                user_id=user.userId, user_text=body.content,
            )
            yield _sse("process", {"name": "Fetching your tickets", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        if _detect_ticket_intent(body.content):
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
            ticket_intent_reply = _ticket_intent_response(body.content)
            for chunk in ticket_intent_reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_ticket_intent_reply(
                db, cid, started, user_id=user.userId, user_text=body.content
            )
            yield _sse("process", {"name": "Preparing escalation guidance", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        if _is_casual_greeting_only(body.content) and not (body.imageDataUrl or "").strip():
            yield _sse("process", {"name": "Replying", "status": "started"})
            yield _sse("citations", [])
            greeting_reply = _casual_greeting_reply(body.content)
            for chunk in greeting_reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_assistant_message(
                db,
                cid,
                content=greeting_reply,
                citations=[],
                can_answer=True,
                started=started,
                rewritten_query=None,
                intent="casual_greeting",
                kind="casual_greeting",
                user_id=user.userId,
                user_text=body.content,
            )
            yield _sse("process", {"name": "Replying", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        # ------------------------------------------------------------------
        # Phase 3 — Pre-LLM grounding context.
        #
        # query rewrite + durable user memory search + recent messages are
        # mutually independent. We dispatch them concurrently to collapse
        # ~3 LLM/embedding round trips into ~1 wall-clock round trip.
        # ------------------------------------------------------------------
        planner_state = _planner_state_with_unresolved_signal_sync(
            agent_state, body.content, last_assistant
        )

        yield _sse("process", {"name": "Enhancing user query", "status": "started"})
        yield _sse("process", {"name": "Loading user memory", "status": "started"})
        enhance_task = asyncio.create_task(
            _enhance_query(db, cid, body.content, agent_state=planner_state)
        )
        memory_task = asyncio.create_task(
            agent_memory.search_user_memory(user.userId, body.content)
        )

        (
            (enhanced_query, enhanced_intent, enhanced_keywords, enhanced_subqueries),
            memory_snippets,
            recent_messages,
        ) = await asyncio.gather(enhance_task, memory_task, recent_messages_task)
        yield _sse("process", {"name": "Enhancing user query", "status": "completed"})
        yield _sse("process", {"name": "Loading user memory", "status": "completed"})

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

        # ------------------------------------------------------------------
        # Phase 4 — Planner LLM call (now without reasoning) and citations.
        # ------------------------------------------------------------------
        yield _sse("process", {"name": "Planning next best action", "status": "started"})
        decision = await chat_agent.decide_next_action(
            recent_messages=recent_messages,
            current_user_message=body.content,
            retrieval_context=retrieval.context_block(),
            citations=retrieval.citations(),
            memory_snippets=memory_snippets,
            agent_state=planner_state,
        )
        yield _sse("process", {"name": "Planning next best action", "status": "completed"})

        # Same chunk list as retrieval.context_block() / planner input — show
        # all in UI, not usedCitations-only.
        context_citations = retrieval.citations()
        yield _sse("citations", context_citations)

        if decision.action != "answer":
            next_state = chat_agent.apply_decision_to_state(agent_state, decision)
            await _persist_agent_state(db, cid, next_state)

        if decision.action == "ask_clarifying_question":
            yield _sse("process", {"name": "Asking a clarifying question", "status": "started"})
            for chunk in decision.reply.split(" "):
                yield _sse("token", {"delta": chunk + " "})
            assistant_msg = await _persist_assistant_message(
                db,
                cid,
                content=decision.reply,
                citations=context_citations,
                can_answer=None,
                started=started,
                rewritten_query=retrieval.rewritten_query,
                intent="clarification_question",
                kind="clarification_question",
                user_id=user.userId,
                user_text=body.content,
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
                citations=context_citations,
                can_answer=False,
                started=started,
                rewritten_query=retrieval.rewritten_query,
                intent="investigation_ticket_offer",
                kind="ticket_offer",
                user_id=user.userId,
                user_text=body.content,
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
            ticket_reply = _ticket_created_reply(ticket, body.content)
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
                user_id=user.userId,
                user_text=body.content,
            )
            yield _sse("process", {"name": "Creating support ticket", "status": "completed"})
            yield _sse("process", {"name": "Completed", "status": "completed"})
            yield _sse("done", serialize_message(assistant_msg))
            return

        # ------------------------------------------------------------------
        # Phase 5 — Main grounded answer.
        #
        # We stream the LLM deltas directly to the client via an incremental
        # JSON extractor so the user sees prose forming in real time instead
        # of waiting for the verifier to finish. The full raw output is also
        # accumulated so we can still pull canAnswer / usedCitations out of
        # the JSON envelope.
        # ------------------------------------------------------------------
        answer_state = chat_agent.apply_decision_to_state(agent_state, decision)

        _, turns, required_stage = await _build_chat_payload(
            db,
            cid,
            body.content,
            retrieval.context_block(),
            memory_snippets,
            agent_state=answer_state,
            rewritten_query=retrieval.rewritten_query,
            recent_messages=recent_messages,
        )

        accum: list[str] = []
        streamer = _JsonAnswerStreamer()
        llm_failed = False
        emitted_any = False
        yield _sse("process", {"name": "Generating grounded answer", "status": "started"})
        try:
            async for delta in llm.chat_stream(turns, reasoning=_answer_uses_reasoning()):
                accum.append(delta)
                cleaned = streamer.feed(delta)
                if cleaned:
                    emitted_any = True
                    yield _sse("token", {"delta": cleaned})
        except Exception as err:
            log.exception("stream LLM failed: %s", err)
            llm_failed = True
            fallback = _model_error_reply(body.content, include_ticket_offer=True)
            fallback_json = json.dumps(
                {"answer": fallback, "canAnswer": False, "usedCitations": []},
                ensure_ascii=False,
            )
            accum = [fallback_json]
            yield _sse("process", {"name": "Generating grounded answer", "status": "error"})
            yield _sse("replace", {"content": fallback})
        else:
            yield _sse("process", {"name": "Generating grounded answer", "status": "completed"})

        full = "".join(accum).strip()
        answer, can_answer, used_idx = _try_parse_structured(full)
        if not answer:
            answer = full or _generation_empty_reply(body.content)
            can_answer = can_answer if can_answer is not None else True
            # Model didn't produce JSON; surface the raw text to the client.
            tail = streamer.finalize(full)
            if tail and not emitted_any:
                yield _sse("replace", {"content": answer})
            elif tail:
                yield _sse("token", {"delta": tail})
        can_answer = _resolve_can_answer(answer, can_answer, force_false=llm_failed)

        filtered = _select_citations(retrieval.citations(), used_idx or decision.used_citations)

        # ------------------------------------------------------------------
        # Phase 6 — Grounding / on-topic verification.
        #
        # The streamed answer is already on screen. The verifier runs with a
        # 6-second budget and fails open on timeout. On-topic is only checked
        # for Stage 4 (final verdict) replies where drift to a different
        # product actually matters; Stage 1/2/3 replies are short diagnostic
        # turns where on-topic risk is negligible and the extra LLM call is
        # not worth the latency.
        # ------------------------------------------------------------------
        yield _sse("process", {"name": "Verifying grounding", "status": "started"})
        verified_answer, can_answer, replaced_by_verifier = await _enforce_grounded_answer_safe(
            answer=answer,
            citations=filtered,
            can_answer=can_answer,
            rewritten_query=retrieval.rewritten_query,
            check_topic=(required_stage == "Stage 4"),
            user_text=body.content,
        )
        yield _sse("process", {"name": "Verifying grounding", "status": "completed"})

        message_kind = "answer"
        response_intent = retrieval.intent
        if replaced_by_verifier:
            message_kind = "ticket_offer"
            response_intent = "investigation_ticket_offer"
            answer = verified_answer
            # Tell the UI to swap the streamed bubble content with the
            # verifier-approved replacement.
            yield _sse("replace", {"content": answer})
        elif not can_answer:
            updated = _append_ticket_offer(verified_answer, body.content)
            if updated != verified_answer:
                # We only need to send the appended tail, not the whole
                # message, because the streamed prose is already shown.
                tail = updated[len(verified_answer):]
                if tail:
                    yield _sse("token", {"delta": tail})
            answer = updated
            message_kind = "ticket_offer"
            response_intent = "investigation_ticket_offer"
        else:
            answer = verified_answer

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
        final_verdict = message_kind == "answer" and required_stage == "Stage 4"
        assistant_msg = await _persist_assistant_message(
            db,
            cid,
            content=answer,
            citations=context_citations,
            can_answer=can_answer,
            started=started,
            rewritten_query=retrieval.rewritten_query,
            intent=response_intent,
            kind=message_kind,
            final_verdict=final_verdict,
            user_id=user.userId,
            user_text=body.content,
        )
        yield _sse("process", {"name": "Saving assistant response", "status": "completed"})
        yield _sse("process", {"name": "Completed", "status": "completed"})
        yield _sse("done", serialize_message(assistant_msg))

    return StreamingResponse(events(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def _fire_and_forget(coro) -> None:
    """Run ``coro`` as a background task and swallow exceptions.

    Used for non-critical post-response work (durable memory writes,
    audit-style updates) that must NEVER block the SSE ``done`` event.
    Exceptions are logged so silent failures still surface in monitoring.
    """
    async def _runner() -> None:
        try:
            await coro
        except Exception as err:  # noqa: BLE001 — log and swallow
            log.warning("background task failed: %s", err)

    try:
        asyncio.create_task(_runner())
    except RuntimeError:
        # No running loop (extremely rare in our flow); drop the coroutine.
        coro.close()


class _JsonAnswerStreamer:
    """Incrementally extract the value of the top-level ``"answer"`` string
    field from a streamed JSON LLM response.

    Used so we can yield SSE ``token`` events to the client as the model is
    still generating, while preserving the JSON envelope (``canAnswer``,
    ``usedCitations``) needed by the grounded answer pipeline.

    Behaviour:
    - Feeds raw model deltas in via :meth:`feed` and returns the cleaned
      prose chunk that should be emitted to the client right now.
    - Properly handles JSON escapes (``\\n``, ``\\t``, ``\\"``, ``\\\\``,
      ``\\uXXXX``) including escapes that straddle SSE chunks.
    - Stops emitting once the closing quote of the answer string is seen.
    - Falls back gracefully when the model does not actually emit a JSON
      object with an ``answer`` key (treats the raw text as the answer so
      the user still sees something).
    """

    __slots__ = ("_raw", "_cursor", "_opened", "_closed", "_fallback_emitted")

    def __init__(self) -> None:
        self._raw = ""
        self._cursor = 0
        self._opened = False
        self._closed = False
        self._fallback_emitted = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def feed(self, delta: str) -> str:
        if self._closed or not delta:
            return ""
        self._raw += delta

        if not self._opened:
            idx = self._find_answer_value_start()
            if idx is None:
                # Don't accumulate an unbounded prefix while we wait for the
                # ``"answer":"`` opening. Keep a small tail so a key split
                # across chunk boundaries is still detectable.
                if len(self._raw) > 1024:
                    self._raw = self._raw[-256:]
                return ""
            self._opened = True
            self._cursor = idx + 1  # absolute index just past the opening quote

        return self._emit_pending()

    def finalize(self, raw_full: str) -> str:
        """Emit any text the streamer missed because the model didn't follow
        the JSON shape. Returns text that has NOT been previously emitted.
        """
        if self._opened:
            return ""
        # No ``"answer"`` field was ever found in the raw output. Surface the
        # remaining text so the user still sees something.
        stripped = (raw_full or "").strip()
        if not stripped or self._fallback_emitted >= len(stripped):
            return ""
        tail = stripped[self._fallback_emitted :]
        self._fallback_emitted = len(stripped)
        self._closed = True
        return tail

    def _find_answer_value_start(self) -> int | None:
        key_pos = self._raw.find('"answer"')
        if key_pos < 0:
            return None
        colon_pos = self._raw.find(":", key_pos + len('"answer"'))
        if colon_pos < 0:
            return None
        i = colon_pos + 1
        while i < len(self._raw) and self._raw[i] in " \t\n\r":
            i += 1
        if i >= len(self._raw):
            return None
        if self._raw[i] != '"':
            # Value is null / false / number — there's no string to stream.
            self._closed = True
            return None
        return i

    def _emit_pending(self) -> str:
        out: list[str] = []
        i = self._cursor
        raw = self._raw
        n = len(raw)
        while i < n:
            ch = raw[i]
            if ch == '"':
                self._closed = True
                break
            if ch == "\\":
                if i + 1 >= n:
                    break  # Wait for more input to complete the escape.
                nxt = raw[i + 1]
                if nxt == "u":
                    if i + 6 > n:
                        break  # Wait for the 4 hex digits.
                    try:
                        out.append(chr(int(raw[i + 2 : i + 6], 16)))
                    except ValueError:
                        out.append("?")
                    i += 6
                    continue
                if nxt == "n":
                    out.append("\n")
                elif nxt == "t":
                    out.append("\t")
                elif nxt == "r":
                    out.append("\r")
                elif nxt in ('"', "\\", "/"):
                    out.append(nxt)
                else:
                    out.append(nxt)
                i += 2
                continue
            out.append(ch)
            i += 1
        self._cursor = i
        return "".join(out)


def _detect_ticket_intent(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _TICKET_INTENT_PATTERNS)


def _is_casual_greeting_only(content: str) -> bool:
    """True for short openers (hi, hello, how are you) with no real request.

    Skips query rewrite, memory search, and RAG retrieval — answers with a brief acknowledgement.
    """
    raw = (content or "").strip()
    if not raw or len(raw) > _CASUAL_GREETING_MAX_LEN:
        return False
    if any(ch.isdigit() for ch in raw):
        return False
    low = raw.lower()
    low = low.replace("\u2018", "'").replace("\u2019", "'")
    low = re.sub(r"[^\w\s']+", " ", low)
    low = re.sub(r"'", "", low)
    low = re.sub(r"\s+", " ", low).strip()
    if not low:
        return False
    words = low.split()
    if not words or len(words) > _CASUAL_GREETING_MAX_WORDS:
        return False
    return low in _CASUAL_GREETING_PHRASES


def _detect_list_tickets_intent(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if any(p.search(text) for p in _TICKET_INTENT_PATTERNS):
        return False
    return any(p.search(text) for p in _LIST_TICKETS_INTENT_PATTERNS)


def _extract_ticket_status_filter(content: str) -> str | None:
    text = (content or "").strip()
    if not text:
        return None
    for pattern, status in _TICKET_STATUS_FILTER_PATTERNS:
        if pattern.search(text):
            return status
    return None


async def _fetch_user_tickets(
    db,
    user_id: str,
    *,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    query: dict[str, object] = {"userId": user_id}
    if status:
        query["status"] = status
    rows = (
        await db.tickets.find(query)
        .sort("createdAt", -1)
        .to_list(length=limit)
    )
    return [serialize_ticket(r) for r in rows]


def _format_tickets_reply(
    tickets: list[dict[str, object]],
    *,
    status: str | None = None,
    user_text: str | None = None,
) -> str:
    language = _reply_language(user_text)
    status_label = status.replace("_", " ") if status else None
    if not tickets:
        if status_label:
            if language == "hi":
                return f"आपके पास अभी {status_label} सपोर्ट टिकट नहीं हैं। अगर आप चाहें, तो मैं किसी दूसरे स्टेटस के टिकट भी दिखा सकता हूं।"
            if language == "bn":
                return f"আপনার এখন {status_label} সাপোর্ট টিকিট নেই। চাইলে অন্য স্ট্যাটাসের টিকিটও দেখাতে পারি।"
            return (
                f"You don't have any {status_label} support tickets right now. "
                "Let me know if you'd like to see tickets in another status."
            )
        return _list_tickets_empty_reply(user_text)
    header = (
        f"यह रहे आपके {status_label} सपोर्ट टिकट ({len(tickets)} कुल):"
        if language == "hi" and status_label
        else f"এখানে আপনার {status_label} সাপোর্ট টিকিট ({len(tickets)}টি মোট):"
        if language == "bn" and status_label
        else f"Here are your {status_label} support tickets ({len(tickets)} total):"
        if status_label
        else f"यह रहे आपके सपोर्ट टिकट ({len(tickets)} कुल):"
        if language == "hi"
        else f"এখানে আপনার সাপোর্ট টিকিট ({len(tickets)}টি মোট):"
        if language == "bn"
        else f"Here are your support tickets ({len(tickets)} total):"
    )
    lines: list[str] = [header, ""]
    for t in tickets:
        ticket_id = t.get("id")
        untitled = "Untitled"
        if language == "hi":
            untitled = "शीर्षक नहीं"
        elif language == "bn":
            untitled = "শিরোনাম নেই"
        subject = str(t.get("subject") or untitled).strip() or untitled
        status = str(t.get("status") or "open").strip() or "open"
        priority = str(t.get("priority") or "").strip()
        meta = status if not priority else f"{status} · {priority} priority"
        lines.append(f"- **#{ticket_id} — {subject}** — {meta}")
        last_update = str(t.get("lastUpdate") or "").strip()
        if last_update:
            if language == "hi":
                lines.append(f"  आखिरी अपडेट: {last_update}")
            elif language == "bn":
                lines.append(f"  সর্বশেষ আপডেট: {last_update}")
            else:
                lines.append(f"  Last update: {last_update}")
    lines.append("")
    if language == "hi":
        lines.append("अगर आप चाहें, तो इनमें से किसी भी टिकट को विस्तार से देख सकते हैं।")
    elif language == "bn":
        lines.append("চাইলে এগুলোর যেকোনো একটিতে আরও গভীরে যেতে পারি।")
    else:
        lines.append("Let me know if you'd like to dig into any of these.")
    return "\n".join(lines)


async def _persist_tickets_list_reply(
    db,
    cid: int,
    started: float,
    tickets: list[dict[str, object]],
    *,
    status: str | None = None,
    user_id: str | None = None,
    user_text: str | None = None,
) -> dict:
    return await _persist_assistant_message(
        db,
        cid,
        content=_format_tickets_reply(tickets, status=status, user_text=user_text),
        citations=[],
        can_answer=True,
        started=started,
        rewritten_query=None,
        intent="list_tickets",
        kind="tickets_list",
        user_id=user_id,
        user_text=user_text,
    )


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


def _should_acknowledge_resolution_sync(
    user_text: str, last_assistant: dict[str, object] | None
) -> bool:
    if not _detect_resolved_signal(user_text):
        return False
    if not last_assistant:
        return False
    return str(last_assistant.get("kind") or "").strip() in _RESOLUTION_PRIOR_KINDS


async def _should_acknowledge_resolution(db, cid: int, user_text: str) -> bool:
    if not _detect_resolved_signal(user_text):
        return False
    last_assistant = await db.messages.find_one(
        {"conversationId": cid, "role": "assistant"},
        sort=[("createdAt", -1)],
    )
    return _should_acknowledge_resolution_sync(user_text, last_assistant)


def _resolution_state(agent_state: dict[str, object], user_text: str) -> dict[str, object]:
    return {
        **agent_state,
        "lastAction": "answer",
        "lastQuestion": None,
        "lastUnresolvedSignal": None,
        "resolutionSummary": _clip_line(user_text, limit=240),
    }


async def _persist_resolution_acknowledgement(
    db,
    cid: int,
    started: float,
    *,
    user_id: str | None = None,
    user_text: str | None = None,
) -> dict:
    return await _persist_assistant_message(
        db,
        cid,
        content=_resolution_acknowledgement(user_text),
        citations=[],
        can_answer=True,
        started=started,
        rewritten_query=None,
        intent="resolution_acknowledgement",
        kind="resolution_acknowledgement",
        user_id=user_id,
        user_text=user_text,
    )


_NOT_LOADED = object()  # Sentinel: caller did not pre-load ``last_assistant``.


def _planner_state_with_unresolved_signal_sync(
    agent_state: dict[str, object],
    user_text: str,
    last_assistant: dict[str, object] | None,
) -> dict[str, object]:
    """Pure-Python version that operates on a preloaded last-assistant doc."""
    if not _detect_unresolved_signal(user_text):
        return agent_state
    if not last_assistant:
        return agent_state
    if str(last_assistant.get("kind") or "").strip() != "answer":
        return agent_state
    signal = _clip_line(user_text, limit=160) or "user reported the previous fix did not work"
    return {**agent_state, "lastUnresolvedSignal": signal}


async def _planner_state_with_unresolved_signal(
    db,
    cid: int,
    agent_state: dict[str, object],
    user_text: str,
    *,
    last_assistant: object = _NOT_LOADED,
) -> dict[str, object]:
    if not _detect_unresolved_signal(user_text):
        return agent_state
    if last_assistant is _NOT_LOADED:
        last_assistant = await db.messages.find_one(
            {"conversationId": cid, "role": "assistant"},
            sort=[("createdAt", -1)],
        )
    return _planner_state_with_unresolved_signal_sync(
        agent_state, user_text, last_assistant  # type: ignore[arg-type]
    )


async def _persist_ticket_intent_reply(
    db,
    cid: int,
    started: float,
    *,
    user_id: str | None = None,
    user_text: str | None = None,
) -> dict:
    return await _persist_assistant_message(
        db,
        cid,
        content=_ticket_intent_response(user_text),
        citations=[],
        can_answer=False,
        started=started,
        rewritten_query=None,
        intent="ticket_request",
        kind="ticket_offer",
        user_id=user_id,
        user_text=user_text,
    )


def _has_ticket_consent(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if any(pattern.search(cleaned) for pattern in _TICKET_DECLINE_PATTERNS):
        return False
    return any(pattern.search(cleaned) for pattern in _TICKET_CONSENT_PATTERNS)


def _should_create_ticket_from_consent_sync(
    user_text: str, last_assistant: dict[str, object] | None
) -> bool:
    if not _has_ticket_consent(user_text):
        return False
    if not last_assistant:
        return False
    kind = str(last_assistant.get("kind") or "").strip()
    if kind == "ticket_offer":
        return True
    if kind == "answer" and last_assistant.get("finalVerdict") is True:
        return True
    return False


async def _should_create_ticket_from_consent(db, cid: int, user_text: str) -> bool:
    if not _has_ticket_consent(user_text):
        return False
    last_assistant = await db.messages.find_one(
        {"conversationId": cid, "role": "assistant"},
        sort=[("createdAt", -1)],
    )
    return _should_create_ticket_from_consent_sync(user_text, last_assistant)


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


async def _enforce_grounded_answer(
    *,
    answer: str,
    citations: list[dict[str, object]],
    can_answer: bool,
    rewritten_query: str | None = None,
    check_topic: bool = True,
    user_text: str | None = None,
) -> tuple[str, bool, bool]:
    """Verify every action / UI claim is supported by the citations and that
    the answer stays on the topic of the consolidated user query.

    Returns ``(answer, can_answer, replaced)``. When either verifier rejects
    the answer and a repair pass cannot salvage it, the answer text is
    replaced with a ticket-offer fallback and ``can_answer`` is forced to
    False. ``replaced`` indicates whether the answer was rewritten so callers
    can adjust streamed output / message kind.

    The grounding and on-topic verifiers are dispatched concurrently to
    halve verifier wall-time. The whole flow is wrapped in a global timeout
    by the caller — when invoked from the streaming endpoint, the streamed
    answer is already visible to the user so timing out and accepting the
    streamed text is a safe fail-open.

    ``check_topic`` lets callers skip the on-topic verifier when it adds no
    safety value — short Stage 1/2/3 diagnostic turns rarely drift to a
    different product, and Stage 4 is where drift actually matters.
    """
    if not can_answer:
        return answer, can_answer, False
    if not (answer or "").strip():
        return answer, can_answer, False

    has_topic_check = bool(check_topic and (rewritten_query or "").strip())

    # Run the two verifiers in parallel — both are read-only on the answer,
    # and the on-topic verdict on the original answer is still valid even
    # if grounding later rewrites it (we re-verify in that branch).
    grounding_task = asyncio.create_task(
        chat_agent.verify_answer_grounding(answer=answer, citations=citations)
    )
    topic_task: asyncio.Task[tuple[bool, list[str]]] | None = None
    if has_topic_check:
        topic_task = asyncio.create_task(
            chat_agent.verify_answer_on_topic(
                user_query=rewritten_query or "",
                answer=answer,
                citations=citations,
            )
        )

    grounded, unsupported = await grounding_task

    if not grounded:
        log.info(
            "answer rejected by grounding verifier; unsupported=%s",
            unsupported,
        )
        if topic_task is not None:
            topic_task.cancel()
        rewritten = await chat_agent.rewrite_to_ground(
            answer=answer,
            citations=citations,
            unsupported=unsupported,
        )
        if not rewritten:
            return _ungrounded_fallback(user_text), False, True
        regrounded, still_unsupported = await chat_agent.verify_answer_grounding(
            answer=rewritten,
            citations=citations,
        )
        if not regrounded:
            log.info(
                "rewrite still rejected by grounding verifier; unsupported=%s",
                still_unsupported,
            )
            return _ungrounded_fallback(user_text), False, True
        log.info("answer rewritten to remove unsupported phrases and re-verified")
        answer = rewritten
        # The original on-topic check was on the now-rewritten answer's
        # predecessor — re-run it if topic checking is requested.
        if has_topic_check:
            topic_task = asyncio.create_task(
                chat_agent.verify_answer_on_topic(
                    user_query=rewritten_query or "",
                    answer=answer,
                    citations=citations,
                )
            )

    if topic_task is None:
        return answer, True, False

    try:
        on_topic, off_topic = await topic_task
    except asyncio.CancelledError:
        on_topic, off_topic = True, []

    if on_topic:
        return answer, True, False

    log.info("answer rejected by on-topic verifier; offTopic=%s", off_topic)
    retopic = await chat_agent.rewrite_to_topic(
        user_query=rewritten_query or "",
        answer=answer,
        citations=citations,
        off_topic=off_topic,
    )
    if not retopic:
        return _ungrounded_fallback(user_text), False, True

    # Re-verify both grounding and on-topic on the topic-repaired answer in
    # parallel so we don't pay two sequential LLM round-trips.
    regrounded_task = asyncio.create_task(
        chat_agent.verify_answer_grounding(answer=retopic, citations=citations)
    )
    retopic_task = asyncio.create_task(
        chat_agent.verify_answer_on_topic(
            user_query=rewritten_query or "",
            answer=retopic,
            citations=citations,
        )
    )
    (regrounded, _), (still_on_topic, still_off_topic) = await asyncio.gather(
        regrounded_task, retopic_task
    )
    if regrounded and still_on_topic:
        log.info("answer rewritten to stay on topic and re-verified")
        return retopic, True, False
    log.info("topic rewrite still rejected; offTopic=%s", still_off_topic)
    return _ungrounded_fallback(user_text), False, True


async def _enforce_grounded_answer_safe(
    *,
    answer: str,
    citations: list[dict[str, object]],
    can_answer: bool,
    rewritten_query: str | None,
    check_topic: bool,
    user_text: str | None = None,
) -> tuple[str, bool, bool]:
    """Wrap :func:`_enforce_grounded_answer` with a global wall-time budget.

    On timeout we keep the streamed answer (fail-open) because the user has
    already seen it and a hung verifier must never gate response delivery.
    """
    try:
        return await asyncio.wait_for(
            _enforce_grounded_answer(
                answer=answer,
                citations=citations,
                can_answer=can_answer,
                rewritten_query=rewritten_query,
                check_topic=check_topic,
                user_text=user_text,
            ),
            timeout=_VERIFIER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning(
            "verifier exceeded %.1fs budget; accepting streamed answer",
            _VERIFIER_TIMEOUT_S,
        )
        return answer, can_answer, False


def _append_ticket_offer(answer: str, user_text: str | None = None) -> str:
    base = (answer or "").strip() or "I could not find a confident fix from the verified context yet."
    lowered = base.lower()
    if "yes, create a ticket" in lowered or "create a support ticket" in lowered:
        return base
    return f"{base}\n\n{_ticket_offer_appendix(user_text)}"


def _ticket_created_reply(ticket: dict[str, object], user_text: str | None = None) -> str:
    ticket_id = ticket.get("_id")
    subject = str(ticket.get("subject") or "your issue")
    status = str(ticket.get("status") or "open")
    language = _reply_language(user_text)
    if language == "hi":
        return (
            f"मैंने \"{subject}\" के लिए सपोर्ट टिकट #{ticket_id} बना दिया है। "
            f"यह अभी {status} स्थिति में है। अब हमारी मानव टीम जांच के सार के साथ आगे फॉलो अप कर सकती है।"
        )
    if language == "bn":
        return (
            f"আমি \"{subject}\" এর জন্য সাপোর্ট টিকিট #{ticket_id} তৈরি করেছি। "
            f"এটি এখন {status} অবস্থায় আছে। এখন মানব টিম তদন্তের সারাংশ নিয়ে ফলো আপ করতে পারবে।"
        )
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
    try:
        resp = await zoho.create_desk_ticket(
            subject=subject,
            description=description,
            priority=priority,
            requester_email=user.email,
            requester_name=" ".join(filter(None, [user.firstName, user.lastName])).strip() or user.email,
            category="Chat",
        )
        if resp and resp.get("id"):
            external_id = f"zoho:{resp['id']}"
            log.info(
                "chat agent ticket synced to Zoho Desk: local will use external_id=%s",
                external_id,
            )
    except Exception as err:
        log.warning("chat escalation create_desk_ticket failed on zoho, keeping local id: %s", err)

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
    """Resolve follow-up references and produce a retrieval-friendly rewrite."""
    recent = (
        await db.messages.find(
            {"conversationId": cid}, {"role": 1, "content": 1, "createdAt": 1}
        )
        .sort("createdAt", -1)
        .to_list(length=12)
    )
    recent.reverse()
    return await query_rewrite.enhance_query(
        current,
        recent_messages=recent,
        agent_state=agent_state,
    )


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
    final_verdict: bool = False,
    user_id: str | None = None,
    user_text: str | None = None,
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
    if final_verdict:
        assistant_msg["finalVerdict"] = True
    await db.messages.insert_one(assistant_msg)
    if user_id and user_text and content:
        # Mem0 writes can take 1–3s; they must not gate the SSE ``done``
        # event. Fire-and-forget so the user sees the response immediately.
        _fire_and_forget(agent_memory.add_exchange_memory(user_id, user_text, content))
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
    pre_enhanced: tuple[str, str, list[str], list[str]] | None = None,
) -> dict[str, object]:
    state_for_planner = planner_state or agent_state

    # Fire the durable memory lookup and the recent-messages read alongside
    # query enhancement. Query rewrite needs the planner state but not the
    # other two — those have no dependency on the rewrite. We chain them so
    # the wall-clock latency is roughly ``max(rewrite, memory, recent)``
    # instead of the sum.
    memory_task = asyncio.create_task(agent_memory.search_user_memory(user_id, content))
    recent_task = asyncio.create_task(_recent_messages(db, cid))

    if pre_enhanced is not None:
        enhanced_query, enhanced_intent, enhanced_keywords, enhanced_subqueries = pre_enhanced
    else:
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
    memory_snippets, recent_messages = await asyncio.gather(memory_task, recent_task)

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
        "recent_messages": recent_messages,
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
    rewritten_query: str | None = None,
    recent_messages: list[dict[str, object]] | None = None,
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

    # Stage detection only needs the last ~8 turns. Re-use the slice we already
    # loaded for the planner when the caller supplies it to avoid a second DB round trip.
    if recent_messages is not None:
        recent_slice = recent_messages
    else:
        recent_desc = (
            await db.messages.find(
                {"conversationId": cid},
                {"role": 1, "content": 1, "createdAt": 1},
            )
            .sort("createdAt", -1)
            .to_list(length=8)
        )
        recent_slice = list(reversed(recent_desc))
    answer_query = (rewritten_query or "").strip() or current_user_content
    latest_user_message_block = (
        "\n\nLatest user message for language matching:\n"
        f"{current_user_content.strip() or '(empty latest user message)'}\n"
    )
    required_stage = _infer_required_troubleshooting_stage(recent_slice, current_user_content)
    is_final_verdict_stage = required_stage == "Stage 4"
    stage_requirement_block = ""
    if required_stage:
        stage_requirement_block = (
            f"- Current required troubleshooting stage for this turn: {required_stage}.\n"
            "- You must follow the required stage exactly for troubleshooting-oriented requests.\n"
            "- If required stage is Stage 1, output only diagnosis plus consent check for next steps.\n"
            "- If required stage is Stage 2, output only 'Try this now:' with one concrete action.\n"
            "- If required stage is Stage 3, output only 'What to tell me next:' asking for the result.\n"
            "- If required stage is Stage 4, output the complete grounded resolution or honest fallback."
            " This is the final verdict: do NOT ask any question, do NOT use question marks, and end with"
            " a brief warm closing sentence as a statement only.\n"
        )

    answer_step_closing = (
        "This is the final answer step for the current turn. The investigation has already happened. "
        "Use the accumulated investigation memory and the verified context to give the strongest useful"
        " answer you can now. Do not restart broad discovery in this step, but you may end with one"
        " brief, targeted follow-up question when it helps the user confirm the next action.\n\n"
        if not is_final_verdict_stage
        else (
            "This is the final answer step for the current turn. The investigation has already happened. "
            "Use the accumulated investigation memory and the verified context to give the strongest useful"
            " answer you can now. For Stage 4 (final verdict), do NOT ask any follow-up question and do NOT"
            " use question marks — deliver complete guidance and close warmly in statements only.\n\n"
        )
    )

    ungrounded_clarify_rule = (
        " situation, do NOT improvise. Instead set canAnswer to false, briefly say what specific"
        " detail or document is missing, and either ask one narrow question whose answer would"
        " unlock a grounded step from the snippets, or wait for the user to provide more detail."
        " Never fabricate a fix or a cause.\n"
        if not is_final_verdict_stage
        else (
            " situation, do NOT improvise. Instead set canAnswer to false, briefly say what specific"
            " detail or document is missing, and do not ask clarifying questions. Never fabricate a fix or a cause.\n"
        )
    )

    participation_line = (
        "- Aim for a real back-and-forth chat where the user is invited to participate each turn,"
        " not a one-shot help-article dump.\n\n"
        if not is_final_verdict_stage
        else "- Stage 4 is the closing turn: be complete and decisive; the app will collect feedback separately.\n\n"
    )

    followup_detail_rules = (
        "- The follow-up question must be specific and actionable, not generic. Ask about the result"
        " of the step you just gave or the single most decisive remaining detail.\n"
        "- Once the user reaches Stage 4, clearly explain what they should do or check to resolve"
        " the issue. Guide them through steps kindly and clearly. Be their helper, not just a list"
        " of tasks.\n"
        if not is_final_verdict_stage
        else (
            "- Stage 4 only: present your fullest useful resolution without asking the user for anything.\n"
        )
    )

    kb_missing_followup = (
        " If a narrow clarifying question would unlock a grounded step"
        " from the snippets, ask that question instead.\n"
        if not is_final_verdict_stage
        else " Do not ask clarifying questions in Stage 4.\n"
    )

    sys_prompt = (
        "You are Helia, a warm and capable AI customer support teammate. "
        "Answer the user's question using ONLY the numbered context snippets below.\n\n"
        f"{answer_step_closing}"
        "IMPORTANT — answer the consolidated user query exactly:\n"
        "- The user message you receive in this turn is a SELF-CONTAINED, REWRITTEN question that"
        "  already merges the original product/system, the symptom, and every clarifying fact the"
        "  user has confirmed earlier in this conversation. Treat it as the authoritative statement"
        "  of what the user is asking about right now.\n"
        "- Stay strictly on the product, feature, and scope named in that consolidated query. Do NOT"
        "  introduce a different product or feature, even if a snippet mentions it. For example, if"
        "  the consolidated query is about Zoom, do not mention Bluetooth or any other unrelated"
        "  feature, even if a retrieved snippet talks about it.\n"
        "- If the cited snippets do not actually cover the product or scope in the consolidated"
        "  query, set canAnswer to false rather than answering about a different topic.\n\n"
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
        f"{ungrounded_clarify_rule}"
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
        "- Match the user's latest language. If they write in English, reply in English; if they"
        " switch to Hindi, Bengali, or another language, switch with them unless they explicitly ask"
        " for a different language.\n"
        "- Speak in first person and address the user directly. Sound like a real support teammate, "
        "not a generic bot.\n\n"
        "Length and format (strict):\n"
        "- Keep every reply short and conversational. Stages 1, 2, and 3 must each fit in 2 to 3"
        " short sentences. Stage 4 may run a little longer (up to 5 short sentences) but never"
        " becomes a long article.\n"
        "- Do not use bullet lists in Stage 1, Stage 2, or Stage 3. Use plain conversational sentences.\n"
        "- In Stage 4, use bullets only if listing 2 or 3 short concrete steps. Never produce long"
        " bullet dumps or multi-paragraph walls of text.\n"
        f"{participation_line}"
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
        f"{followup_detail_rules}"
        "- Use the investigation memory to tailor the answer to the facts already gathered in prior turns.\n"
        "- Prefer a concrete likely fix path over a vague diagnosis summary.\n"
        "- Only suggest actions the user can actually take themselves; never claim you can access "
        "their system or perform technical actions on their behalf.\n\n"
        "Grounding and citations:\n"
        "- Cite sources inline using [n] notation matching the snippets you used. Every action step"
        " and every product-specific term needs a [n] citation.\n"
        "- If the answer cannot be found in the context, set canAnswer to false, apologise briefly,"
        " and say exactly what detail or document is still missing. Do not invent a 'best guess' next"
        f" step from general knowledge.{kb_missing_followup}"
        "- Do not mention support tickets or escalation unless the user explicitly asks for that.\n"
        "- Never invent facts, policies, UI elements, menu paths, settings names, or steps that are"
        " not supported by the context.\n\n"
        f"{latest_user_message_block}"
        'Respond as JSON with this exact shape:\n'
        '{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }\n\n'
        f"{memory_block}"
        f"{investigation_block}"
        f"Context:\n{context}"
    )

    turns: list[llm.ChatTurn] = [{"role": "system", "content": sys_prompt}]
    turns.append({"role": "user", "content": answer_query})
    return sys_prompt, turns, required_stage


async def _generate_answer(
    turns: list[llm.ChatTurn],
    *,
    user_text: str | None = None,
) -> tuple[str, bool | None, list[int]]:
    try:
        raw = await llm.chat(turns, json_mode=True, reasoning=_answer_uses_reasoning())
        metrics.LLM_CALLS.labels(provider="auto", kind="chat", outcome="ok").inc()
    except Exception as err:
        metrics.LLM_CALLS.labels(provider="auto", kind="chat", outcome="error").inc()
        log.exception("LLM call failed: %s", err)
        return (
            _model_error_reply(user_text),
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
