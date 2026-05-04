"""PwC AI gateway client — port of src/lib/pwc-ai.ts."""
from __future__ import annotations

import os
from typing import Any, Literal, TypedDict

import httpx

BASE_URL = "https://genai-sharedservice-americas.pwc.com"
MODEL = "vertex_ai.gemini-2.5-flash-image"


class ChatTurn(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


def _credentials() -> tuple[str, str]:
    api_key = os.environ.get("PWC_AI_API_KEY")
    token = os.environ.get("PWC_TOKEN")
    if not api_key or not token:
        raise RuntimeError("PWC_AI_API_KEY or PWC_TOKEN not configured")
    return api_key, token


async def chat(messages: list[ChatTurn], *, json_mode: bool = False) -> str:
    api_key, token = _credentials()
    body: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.2,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "x-api-key": token,
            },
            json=body,
        )
    if res.status_code >= 400:
        raise RuntimeError(f"PwC AI gateway error {res.status_code}: {res.text[:300]}")
    data = res.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


async def extract_text_from_base64(mime_type: str, base64_data: str, filename: str) -> str:
    api_key, token = _credentials()
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
        "model": MODEL,
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
        res = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "x-api-key": token,
            },
            json=body,
        )
    if res.status_code >= 400:
        raise RuntimeError(f"PwC AI extraction error {res.status_code}: {res.text[:300]}")
    data = res.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
