from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

log = logging.getLogger("api-server.agent_memory")

_MEM0_CLIENT: Any | None = None
_MEM0_INIT = False


def _get_client() -> Any | None:
    global _MEM0_CLIENT, _MEM0_INIT
    if _MEM0_INIT:
        return _MEM0_CLIENT

    _MEM0_INIT = True
    api_key = os.environ.get("MEM0_API_KEY")
    if not api_key:
        return None

    try:
        from mem0 import MemoryClient
    except Exception as err:
        log.warning("Mem0 SDK import failed: %s", err)
        return None

    try:
        _MEM0_CLIENT = MemoryClient(api_key=api_key)
    except Exception as err:
        log.warning("Mem0 client initialization failed: %s", err)
        _MEM0_CLIENT = None
    return _MEM0_CLIENT


def enabled() -> bool:
    return _get_client() is not None


async def search_user_memory(user_id: str, query: str, *, limit: int = 5) -> list[str]:
    client = _get_client()
    if not client or not query.strip():
        return []

    def _search() -> Any:
        try:
            return client.search(query, filters={"user_id": user_id}, limit=limit)
        except TypeError:
            return client.search(query, filters={"user_id": user_id})

    try:
        raw = await asyncio.to_thread(_search)
    except Exception as err:
        log.warning("Mem0 search failed for user %s: %s", user_id, err)
        return []

    return _extract_memory_strings(raw, limit)


async def add_exchange_memory(user_id: str, user_text: str, assistant_text: str) -> None:
    client = _get_client()
    if not client:
        return

    if not user_text.strip() or not assistant_text.strip():
        return

    messages = [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]

    def _add() -> Any:
        return client.add(messages, user_id=user_id)

    try:
        await asyncio.to_thread(_add)
    except Exception as err:
        log.warning("Mem0 add failed for user %s: %s", user_id, err)


def _extract_memory_strings(raw: Any, limit: int) -> list[str]:
    items = raw
    if isinstance(raw, dict):
        if isinstance(raw.get("results"), list):
            items = raw["results"]
        elif isinstance(raw.get("memories"), list):
            items = raw["memories"]

    if not isinstance(items, list):
        return []

    out: list[str] = []
    for item in items:
        value = _memory_text(item)
        if value and value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def _memory_text(item: Any) -> str | None:
    if isinstance(item, str):
        return item.strip() or None
    if not isinstance(item, dict):
        return None

    for key in ("memory", "text", "content", "value"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    nested = item.get("memory")
    if isinstance(nested, dict):
        for key in ("text", "content", "value"):
            v = nested.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

    return None
