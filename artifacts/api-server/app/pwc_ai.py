"""PwC AI gateway client.

Exposes chat completions and embeddings via the PwC GenAI shared service.
The gateway is OpenAI-compatible: ``/chat/completions`` and ``/embeddings``
both accept the standard request bodies and return the standard response
shapes.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal, TypedDict

import httpx

BASE_URL = "https://genai-sharedservice-americas.pwc.com"
CHAT_MODEL = "azure.grok-4-fast-reasoning"
EMBEDDING_MODEL = "vertex_ai.gemini-embedding"
log = logging.getLogger("api-server.pwc_ai")


def _default_reasoning_effort() -> str:
    raw = (os.environ.get("PWC_REASONING_EFFORT") or "medium").strip().lower()
    return raw if raw in {"low", "medium", "high"} else "medium"


class ChatTurn(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


def _credentials() -> tuple[str, str]:
    api_key = os.environ.get("PWC_AI_API_KEY")
    token = os.environ.get("PWC_TOKEN")
    if not api_key or not token:
        raise RuntimeError("PWC_AI_API_KEY or PWC_TOKEN not configured")
    return api_key, token


def _headers() -> dict[str, str]:
    api_key, token = _credentials()
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "x-api-key": token,
    }


def _chat_model() -> str:
    return os.environ.get("PWC_CHAT_MODEL", CHAT_MODEL)


def _embedding_model() -> str:
    return os.environ.get("PWC_EMBEDDING_MODEL", EMBEDDING_MODEL)


async def chat(
    messages: list[ChatTurn],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    reasoning: bool = False,
) -> str:
    body: dict[str, Any] = {
        "model": _chat_model(),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if reasoning:
        body["reasoning_effort"] = _default_reasoning_effort()

    async with httpx.AsyncClient(timeout=60.0) as client:
        res, body = await _post_chat_with_json_fallback(client, body, json_mode=json_mode)

        if res.status_code >= 400 and (
            _is_unsupported_model_error(res.status_code, res.text)
            or _is_model_access_error(res.status_code, res.text)
        ):
            current = str(body.get("model") or "")
            allowed = _extract_allowed_models(res.text)
            for fallback_model in _chat_model_fallbacks(current=current, allowed=allowed):
                retry_body = {**body, "model": fallback_model}
                log.warning(
                    "Configured chat model '%s' rejected; retrying with fallback model '%s'",
                    current,
                    fallback_model,
                )
                retry_res, retry_body = await _post_chat_with_json_fallback(
                    client,
                    retry_body,
                    json_mode=json_mode,
                )
                if retry_res.status_code < 400:
                    res = retry_res
                    body = retry_body
                    break
                res = retry_res

    if res.status_code >= 400:
        raise RuntimeError(f"PwC AI gateway error {res.status_code}: {res.text[:300]}")
    data = res.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


async def _post_chat_with_json_fallback(
    client: httpx.AsyncClient,
    body: dict[str, Any],
    *,
    json_mode: bool,
) -> tuple[httpx.Response, dict[str, Any]]:
    res = await client.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=body)
    if res.status_code >= 400 and "reasoning_effort" in body:
        detail = res.text[:500]
        if _should_retry_without_reasoning(res.status_code, detail):
            log.warning("reasoning_effort rejected by gateway/model; retrying without it")
            body = {k: v for k, v in body.items() if k != "reasoning_effort"}
            res = await client.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=body)
    if json_mode and res.status_code >= 400:
        detail = res.text[:500]
        # Some deployments may reject response_format even when the request is
        # otherwise valid. Retry once without response_format.
        model = str(body.get("model") or "")
        if _should_retry_without_response_format(res.status_code, detail) or model.endswith("-image"):
            log.warning(
                "JSON response_format rejected by gateway/model; retrying without response_format"
            )
            body = {k: v for k, v in body.items() if k != "response_format"}
            res = await client.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=body)
    return res, body


def _should_retry_without_reasoning(status_code: int, detail: str) -> bool:
    if status_code not in {400, 404, 415, 422}:
        return False
    text = (detail or "").lower()
    return "reasoning_effort" in text and any(
        m in text for m in ("unsupported", "not supported", "unknown", "invalid", "unrecognized")
    )


def _chat_model_fallbacks(*, current: str, allowed: list[str] | None = None) -> list[str]:
    out: list[str] = []
    configured = os.environ.get("PWC_CHAT_MODEL_FALLBACKS", "")
    if configured:
        for raw in configured.split(","):
            model = raw.strip()
            if model and model != current and model not in out:
                out.append(model)

    defaults = [
        "vertex_ai.gemini-2.5-flash-image",
        "vertex_ai.gemini-2.5-pro",
    ]
    for model in defaults:
        if model != current and model not in out:
            out.append(model)

    if allowed:
        allowed_set = set(allowed)
        out = [m for m in out if m in allowed_set]
    return out


def _extract_allowed_models(detail: str) -> list[str]:
    text = detail or ""
    match = re.search(r"models\s*=\s*\[(.*?)\]", text)
    if not match:
        return []
    chunk = match.group(1)
    out: list[str] = []
    for raw in chunk.split(","):
        model = raw.strip().strip("'\"")
        if model and model not in out:
            out.append(model)
    return out


def _is_unsupported_model_error(status_code: int, detail: str) -> bool:
    if status_code not in {400, 404, 422}:
        return False
    text = (detail or "").lower()
    return (
        "not supported by this model" in text
        or "received model group" in text
        or "model_not_found" in text
        or "unknown model" in text
        or "invalid model" in text
    )


def _is_model_access_error(status_code: int, detail: str) -> bool:
    if status_code != 401:
        return False
    text = (detail or "").lower()
    return "key not allowed to access model" in text


def _should_retry_without_response_format(status_code: int, detail: str) -> bool:
    if status_code not in {400, 404, 415, 422}:
        return False
    text = (detail or "").lower()
    response_format_markers = (
        "response_format",
        "json_object",
        "json schema",
        "json mode",
    )
    incompatibility_markers = (
        "unsupported",
        "not supported",
        "not allow",
        "invalid",
        "unknown",
        "unrecognized",
        "does not support",
    )
    return any(m in text for m in response_format_markers) and any(
        m in text for m in incompatibility_markers
    )


async def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    body = {"model": _embedding_model(), "input": texts}
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(f"{BASE_URL}/embeddings", headers=_headers(), json=body)
    if res.status_code >= 400:
        raise RuntimeError(f"PwC embedding error {res.status_code}: {res.text[:300]}")
    data = res.json()
    items = data.get("data") or []
    return [d.get("embedding") or [] for d in items]


async def extract_text_from_base64(mime_type: str, base64_data: str, filename: str) -> str:
    is_image = mime_type.startswith("image/")

    if is_image:
        user_content: Any = [
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_data}"}},
            {
                "type": "text",
                "text": (
                    "Extract ALL text content from this image verbatim. Include every word, "
                    "number, heading, label, caption, and table cell. Output plain text only — "
                    "no markdown, no commentary."
                ),
            },
        ]
    else:
        user_content = [
            {
                "type": "text",
                "text": (
                    f'The following is a base64-encoded {mime_type} file named "{filename}".\n\n'
                    "Extract ALL text content from it verbatim. Preserve headings, bullet "
                    "points, table structure (as plain text), and paragraph breaks. Output "
                    f"plain text only — no markdown, no commentary.\n\nBase64 content:\n{base64_data}"
                ),
            }
        ]

    body = {
        "model": _chat_model(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise document text extractor. Extract text verbatim. "
                    "Never summarise or omit content."
                ),
            },
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 8192,
        "temperature": 0,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        res = await client.post(f"{BASE_URL}/chat/completions", headers=_headers(), json=body)
    if res.status_code >= 400:
        raise RuntimeError(f"PwC AI extraction error {res.status_code}: {res.text[:300]}")
    data = res.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
