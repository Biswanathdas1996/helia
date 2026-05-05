"""Zoho Desk API client.

Uses the OAuth2 refresh-token grant. When credentials are absent the
module is a no-op — ``create_ticket`` returns ``None`` and tickets stay
local-only. ``get_ticket_status`` similarly returns ``None``.

Required env vars:

- ``ZOHO_DESK_DOMAIN``        — e.g. ``desk.zoho.com`` or ``desk.zoho.eu``
- ``ZOHO_ORG_ID``             — Desk organisation ID
- ``ZOHO_REFRESH_TOKEN``      — long-lived refresh token
- ``ZOHO_CLIENT_ID``          — OAuth client id
- ``ZOHO_CLIENT_SECRET``      — OAuth client secret
- ``ZOHO_DEPARTMENT_ID``      — optional default department for new tickets
- ``ZOHO_ACCOUNTS_DOMAIN``    — defaults to ``accounts.zoho.com``
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

log = logging.getLogger("api-server.zoho")

_token: dict[str, Any] = {"value": None, "exp": 0.0}
_PRIORITY_MAP = {"low": "Low", "medium": "Medium", "high": "High", "urgent": "Urgent"}


def is_configured() -> bool:
    return all(
        os.environ.get(k)
        for k in (
            "ZOHO_DESK_DOMAIN",
            "ZOHO_ORG_ID",
            "ZOHO_REFRESH_TOKEN",
            "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET",
        )
    )


async def _access_token() -> str:
    if _token["value"] and _token["exp"] > time.time() + 30:
        return _token["value"]  # type: ignore[return-value]
    accounts = os.environ.get("ZOHO_ACCOUNTS_DOMAIN", "accounts.zoho.com")
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"https://{accounts}/oauth/v2/token",
            params={
                "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
                "client_id": os.environ["ZOHO_CLIENT_ID"],
                "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
                "grant_type": "refresh_token",
            },
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Zoho token refresh failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    _token["value"] = data["access_token"]
    _token["exp"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]  # type: ignore[return-value]


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    domain = os.environ["ZOHO_DESK_DOMAIN"]
    org_id = os.environ["ZOHO_ORG_ID"]
    token = await _access_token()
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "orgId": org_id,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.request(method, f"https://{domain}/api/v1{path}", headers=headers, **kwargs)


async def create_ticket(
    *,
    subject: str,
    description: str,
    priority: str,
    requester_email: Optional[str],
    requester_name: Optional[str],
) -> Optional[dict[str, Any]]:
    if not is_configured():
        return None
    body: dict[str, Any] = {
        "subject": subject,
        "description": description,
        "priority": _PRIORITY_MAP.get(priority, "Medium"),
        "channel": "Web",
        "contact": {
            "lastName": (requester_name or requester_email or "Helia user").strip() or "Helia user",
            "email": requester_email,
        },
    }
    dept = os.environ.get("ZOHO_DEPARTMENT_ID")
    if dept:
        body["departmentId"] = dept
    try:
        r = await _request("POST", "/tickets", json=body)
    except Exception as err:
        log.warning("zoho create_ticket failed: %s", err)
        return None
    if r.status_code >= 400:
        log.warning("zoho create_ticket %s: %s", r.status_code, r.text[:300])
        return None
    return r.json()


async def get_ticket(ticket_id: str) -> Optional[dict[str, Any]]:
    if not is_configured() or not ticket_id:
        return None
    try:
        r = await _request("GET", f"/tickets/{ticket_id}")
    except Exception as err:
        log.warning("zoho get_ticket failed: %s", err)
        return None
    if r.status_code >= 400:
        return None
    return r.json()
