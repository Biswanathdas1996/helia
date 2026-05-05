"""Lightweight cache abstraction.

If ``REDIS_URL`` is set, uses Redis. Otherwise falls back to a per-process
in-memory dict (useful for development and single-replica deployments).

Values are JSON-encoded; callers should serialize complex types themselves
when they need exact round-tripping.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional

_LOCAL: dict[str, tuple[float, str]] = {}
_LOCAL_LOCK = asyncio.Lock()
_REDIS = None
_REDIS_INIT = False


async def _redis_client():
    global _REDIS, _REDIS_INIT
    if _REDIS_INIT:
        return _REDIS
    _REDIS_INIT = True
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis.asyncio as redis  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        client = redis.from_url(url, encoding="utf-8", decode_responses=True)
        await client.ping()
        _REDIS = client
        return _REDIS
    except Exception:
        _REDIS = None
        return None


async def get(key: str) -> Optional[Any]:
    r = await _redis_client()
    if r is not None:
        raw = await r.get(key)
        return json.loads(raw) if raw else None

    async with _LOCAL_LOCK:
        entry = _LOCAL.get(key)
        if not entry:
            return None
        expires, raw = entry
        if expires and expires < time.time():
            _LOCAL.pop(key, None)
            return None
        return json.loads(raw)


async def set(key: str, value: Any, *, ttl_seconds: int = 300) -> None:
    payload = json.dumps(value)
    r = await _redis_client()
    if r is not None:
        await r.set(key, payload, ex=ttl_seconds)
        return
    async with _LOCAL_LOCK:
        _LOCAL[key] = (time.time() + ttl_seconds if ttl_seconds else 0.0, payload)


async def delete(key: str) -> None:
    r = await _redis_client()
    if r is not None:
        await r.delete(key)
        return
    async with _LOCAL_LOCK:
        _LOCAL.pop(key, None)
