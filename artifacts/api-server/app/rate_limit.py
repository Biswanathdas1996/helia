"""Sliding-window rate limiter.

Uses Redis (atomic INCR + EXPIRE) when ``REDIS_URL`` is set, otherwise an
in-process dict — that's fine for a single replica and degrades to
"effectively no limit" across multiple replicas, which the deployment
docs call out.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import HTTPException, Request

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_LOCK = asyncio.Lock()


def _config(scope: str) -> tuple[int, int]:
    """Return (limit, window_seconds) for a named scope."""
    if scope == "chat":
        return int(os.environ.get("RATE_LIMIT_CHAT", "30")), 60
    if scope == "auth":
        return int(os.environ.get("RATE_LIMIT_AUTH", "10")), 60
    return int(os.environ.get("RATE_LIMIT_DEFAULT", "120")), 60


async def _redis_check(scope: str, key: str, limit: int, window: int) -> bool:
    from app.cache import _redis_client  # type: ignore[attr-defined]
    r = await _redis_client()
    if r is None:
        return False
    full_key = f"rl:{scope}:{key}"
    pipe = r.pipeline()
    pipe.incr(full_key)
    pipe.expire(full_key, window)
    count, _ = await pipe.execute()
    if int(count) > limit:
        raise HTTPException(status_code=429, detail="Too many requests")
    return True


async def enforce(request: Request, *, scope: str = "default", key: Optional[str] = None) -> None:
    limit, window = _config(scope)
    bucket_key = key or _client_key(request)

    if await _redis_check(scope, bucket_key, limit, window):
        return

    now = time.time()
    cutoff = now - window
    full_key = f"{scope}:{bucket_key}"
    async with _LOCK:
        bucket = _BUCKETS[full_key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests")
        bucket.append(now)


def _client_key(request: Request) -> str:
    # Prefer authenticated user when available, else IP.
    state_user = getattr(request.state, "user", None)
    if state_user is not None:
        uid = getattr(state_user, "userId", None)
        if uid:
            return f"u:{uid}"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return f"ip:{fwd.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"
