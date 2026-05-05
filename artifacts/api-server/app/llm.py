"""LLM + embedding client.

Single provider: PwC GenAI gateway.
- Chat:       ``vertex_ai.gemini-2.5-flash-image-image``
- Embeddings: ``vertex_ai.gemini-embedding``

The gateway is OpenAI-compatible at the wire level. Streaming uses standard
SSE (``data: {...}`` chunks); when the gateway has not enabled streaming for
the deployment, ``chat_stream`` falls back to a single full-response chunk.
"""
from __future__ import annotations

import json as _json
import os
from typing import Any, AsyncIterator, Iterable, Literal, TypedDict

import httpx

from app import pwc_ai


class ChatTurn(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


def chat_model() -> str:
    return os.environ.get("PWC_CHAT_MODEL", pwc_ai.CHAT_MODEL)


def embedding_model() -> str:
    return os.environ.get("PWC_EMBEDDING_MODEL", pwc_ai.EMBEDDING_MODEL)


def embedding_dim() -> int:
    raw = os.environ.get("EMBEDDING_DIM")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    # Keep this in sync with your Atlas vector index dimension.
    return 768


def embeddings_available() -> bool:
    return bool(os.environ.get("PWC_AI_API_KEY") and os.environ.get("PWC_TOKEN"))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

async def chat(
    messages: list[ChatTurn],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> str:
    return await pwc_ai.chat(
        messages, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens
    )


async def chat_stream(
    messages: list[ChatTurn],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> AsyncIterator[str]:
    """Stream content deltas from the PwC gateway.

    If the gateway returns a non-streaming response (e.g., streaming is not
    enabled for the deployment), the full message is yielded as a single chunk.
    """
    api_key = os.environ.get("PWC_AI_API_KEY")
    token = os.environ.get("PWC_TOKEN")
    if not api_key or not token:
        raise RuntimeError("PWC_AI_API_KEY or PWC_TOKEN not configured")

    body: dict[str, Any] = {
        "model": chat_model(),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "x-api-key": token,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST", f"{pwc_ai.BASE_URL}/chat/completions", headers=headers, json=body
        ) as r:
            if r.status_code >= 400:
                detail = (await r.aread())[:300]
                raise RuntimeError(f"PwC stream error {r.status_code}: {detail!r}")

            content_type = r.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                # Gateway returned a buffered JSON response — emit the full content as one chunk.
                raw = await r.aread()
                try:
                    data = _json.loads(raw)
                    full = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                except Exception:
                    full = raw.decode("utf-8", errors="replace")
                if full:
                    yield full
                return

            async for line in r.aiter_lines():
                async for delta in _parse_sse_chunk(line):
                    yield delta


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

async def embed(texts: Iterable[str]) -> list[list[float]]:
    items = [t for t in texts]
    if not items:
        return []
    return await pwc_ai.embed(items)


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

async def _parse_sse_chunk(line: str) -> AsyncIterator[str]:
    if not line or not line.startswith("data:"):
        return
    payload = line[5:].strip()
    if payload == "[DONE]" or not payload:
        return
    try:
        obj = _json.loads(payload)
    except Exception:
        return
    for ch in obj.get("choices") or []:
        delta = (ch.get("delta") or {}).get("content")
        if delta:
            yield delta
