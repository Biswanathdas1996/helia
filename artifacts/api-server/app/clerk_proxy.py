"""Clerk Frontend API proxy (production only).

Mirrors the Express version: forwards requests under `/api/__clerk/*` to
`https://frontend-api.clerk.dev/*`, rewriting host headers and adding the
required Clerk-Proxy-Url + Clerk-Secret-Key headers.

Only active when NODE_ENV=production AND CLERK_SECRET_KEY is set.
"""
from __future__ import annotations

import os
from typing import Iterable

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

CLERK_FAPI = "https://frontend-api.clerk.dev"
CLERK_PROXY_PATH = "/api/__clerk"

router = APIRouter()

# Hop-by-hop headers we should not forward.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _enabled() -> bool:
    return (
        os.environ.get("NODE_ENV") == "production"
        and bool(os.environ.get("CLERK_SECRET_KEY"))
    )


def _client_host(req: Request) -> str:
    forwarded = req.headers.get("x-forwarded-host")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return req.headers.get("host", "")


def _filter_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in items:
        if k.lower() in _HOP_BY_HOP:
            continue
        out[k] = v
    return out


@router.api_route(
    CLERK_PROXY_PATH + "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def clerk_proxy(path: str, request: Request) -> Response:
    if not _enabled():
        raise HTTPException(status_code=404)

    secret = os.environ["CLERK_SECRET_KEY"]
    proto = request.headers.get("x-forwarded-proto", "https")
    host = _client_host(request)
    proxy_url = f"{proto}://{host}{CLERK_PROXY_PATH}"

    target = f"{CLERK_FAPI}/{path}"
    qs = request.url.query
    if qs:
        target = f"{target}?{qs}"

    headers = _filter_headers(request.headers.items())
    headers["Clerk-Proxy-Url"] = proxy_url
    headers["Clerk-Secret-Key"] = secret

    xff = request.headers.get("x-forwarded-for")
    client_ip = (xff or "").split(",")[0].strip() or (request.client.host if request.client else "")
    if client_ip:
        headers["X-Forwarded-For"] = client_ip

    body = await request.body()
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        upstream = await client.request(
            request.method,
            target,
            headers=headers,
            content=body,
        )

    response_headers = _filter_headers(upstream.headers.items())
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
