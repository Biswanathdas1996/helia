"""Zoho Desk API client.

Uses the OAuth2 refresh-token grant. When credentials are absent the
module is a no-op — ``create_ticket`` returns ``None`` and tickets stay
local-only. ``get_ticket_status`` similarly returns ``None``.

Required env vars (REST OAuth path):

- ``ZOHO_BASE_URL``           — preferred; Desk REST prefix e.g. ``https://desk.zoho.com/api/v1``
- ``ZOHO_DESK_DOMAIN``        — legacy alternative: hostname only, e.g. ``desk.zoho.com`` or ``desk.zoho.eu``
- ``ZOHO_ORG_ID``             — Desk organisation ID
- ``ZOHO_REFRESH_TOKEN``      — long-lived refresh token
- ``ZOHO_CLIENT_ID``          — OAuth client id
- ``ZOHO_CLIENT_SECRET``      — OAuth client secret
- ``ZOHO_DEPARTMENT_ID``      — optional default department for new tickets
- ``ZOHO_CONTACT_ID``         — optional Desk contact id (MCP ticket body)
- ``ZOHO_ACCOUNTS_DOMAIN``    — defaults to ``accounts.zoho.com``
- ``ZOHO_DEFAULT_REQUESTER_EMAIL`` — used when the signed-in user has no email or Desk rejects it (must be allowed by your org)

Optional **MCP** (Zoho Desk tools over HTTP; used by ``create_desk_ticket`` and ``get_ticket`` when REST is unavailable):

- ``MCP_ENABLED``             — ``true`` with ``MCP_ENDPOINT_URL``
- ``MCP_ENDPOINT_URL``        — MCP Streamable HTTP URL
- ``MCP_AUTH_TOKEN``          — optional Bearer token
- ``ZOHO_ORG_ID``             — optional for MCP transport alone; required for Zoho MCP tools using ``orgId``
- ``FORCE_MCP_TICKET_CREATION`` — if ``true``, do not fall back to REST when MCP is enabled but fails
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


def _looks_like_email(value: str) -> bool:
    """Minimal syntax check — Desk applies org-specific rules (domains, allow-lists)."""
    v = value.strip()
    if len(v) < 3 or v.count("@") != 1 or " " in v:
        return False
    local, _, domain = v.partition("@")
    if not local or not domain or "." not in domain:
        return False
    host_labels = [p for p in domain.split(".") if p]
    return len(host_labels) >= 2 and all(p.strip() == p for p in host_labels)


def desk_requester_email(requester_email: Optional[str]) -> str:
    """Email sent to Desk as ``contact.email`` — never empty (Desk rejects blank values)."""
    candidate = (requester_email or "").strip()
    if _looks_like_email(candidate):
        return candidate
    fb = (os.environ.get("ZOHO_DEFAULT_REQUESTER_EMAIL") or "").strip()
    if _looks_like_email(fb):
        log.debug("Zoho Desk: using ZOHO_DEFAULT_REQUESTER_EMAIL for ticket requester")
        return fb
    log.warning(
        "Zoho Desk: requester email missing or invalid (%r); set ZOHO_DEFAULT_REQUESTER_EMAIL "
        "to an address allowed by your Desk organisation",
        candidate[:120] if candidate else "(empty)",
    )
    return "noreply@example.com"


def _desk_api_base() -> Optional[str]:
    """REST API prefix: ``ZOHO_BASE_URL`` if set, else ``https://{ZOHO_DESK_DOMAIN}/api/v1``."""
    raw = (os.environ.get("ZOHO_BASE_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    domain = (os.environ.get("ZOHO_DESK_DOMAIN") or "").strip()
    if not domain:
        return None
    domain = domain.removeprefix("https://").removeprefix("http://").split("/")[0].strip()
    if not domain:
        return None
    return f"https://{domain}/api/v1"


def is_configured() -> bool:
    if _desk_api_base() is None:
        return False
    return all(
        os.environ.get(k)
        for k in (
            "ZOHO_ORG_ID",
            "ZOHO_REFRESH_TOKEN",
            "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET",
        )
    )


def mcp_transport_configured() -> bool:
    """Remote MCP HTTP transport is enabled (Desk ticket tools may still require ``ZOHO_ORG_ID``)."""
    try:
        from app.mcp_client import MCPHttpClient
    except ImportError:
        return False
    return MCPHttpClient.enabled()


def mcp_desk_configured() -> bool:
    """MCP HTTP is on and ``ZOHO_ORG_ID`` is set (required for Zoho Desk MCP ``orgId`` params)."""
    return mcp_transport_configured() and bool((os.environ.get("ZOHO_ORG_ID") or "").strip())


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
    base = _desk_api_base()
    if not base:
        raise RuntimeError("Zoho Desk API base URL not configured (ZOHO_BASE_URL or ZOHO_DESK_DOMAIN)")
    org_id = os.environ["ZOHO_ORG_ID"]
    token = await _access_token()
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "orgId": org_id,
        "Content-Type": "application/json",
    }
    url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.request(method, url, headers=headers, **kwargs)


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
    eff_email = desk_requester_email(requester_email)
    body: dict[str, Any] = {
        "subject": subject,
        "description": description,
        "priority": _PRIORITY_MAP.get(priority, "Medium"),
        "channel": "Web",
        "contact": {
            "lastName": (requester_name or eff_email or "Helia user").strip() or "Helia user",
            "email": eff_email,
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


def _looks_like_desk_ticket(obj: Any) -> bool:
    return isinstance(obj, dict) and (
        "status" in obj or "ticketNumber" in obj or ("id" in obj and "subject" in obj)
    )


def _coerce_desk_ticket_from_mcp(raw: Any) -> Optional[dict[str, Any]]:
    """Normalize MCP JSON into a Desk-like dict (``status``, ``statusType``, etc.)."""
    if not isinstance(raw, dict) or raw.get("error"):
        return None

    candidates: list[dict[str, Any]] = []
    for key in ("ticket", "data", "body", "ticketInfo", "result"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    if _looks_like_desk_ticket(raw):
        candidates.insert(0, raw)

    for c in candidates:
        if _looks_like_desk_ticket(c):
            return c
    return None


async def get_ticket(ticket_id: str) -> Optional[dict[str, Any]]:
    """Fetch a Desk ticket by id: REST when OAuth is configured, else MCP."""
    if not ticket_id:
        return None
    tid = str(ticket_id).strip()
    if not tid:
        return None

    if is_configured():
        try:
            r = await _request("GET", f"/tickets/{tid}")
        except Exception as err:
            log.warning("zoho get_ticket failed: %s", err)
            return None
        if r.status_code >= 400:
            return None
        return r.json()

    if mcp_transport_configured():
        from app.mcp_client import get_mcp_client

        try:
            raw = await get_mcp_client().get_ticket(tid)
        except Exception as err:
            log.warning("zoho MCP get_ticket failed: %s", err)
            return None
        return _coerce_desk_ticket_from_mcp(raw)

    return None


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _extract_mcp_ticket_id(raw: Any) -> Optional[str]:
    if not isinstance(raw, dict) or raw.get("error") or raw.get("errorCode"):
        return None

    def pick_id(d: dict[str, Any]) -> Optional[str]:
        for key in ("id", "ticketId", "ticket_id"):
            tid = d.get(key)
            if tid is not None and str(tid).strip():
                return str(tid).strip()
        return None

    tid = pick_id(raw)
    if tid:
        return tid

    for key in ("ticket", "data", "result", "body", "ticketInfo", "response"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            tid = pick_id(nested)
            if tid:
                return tid
    return None


async def create_desk_ticket(
    *,
    subject: str,
    description: str,
    priority: str,
    requester_email: Optional[str],
    requester_name: Optional[str],
    phone: str = "",
    category: str = "General",
) -> Optional[dict[str, Any]]:
    """Create a Zoho Desk ticket: MCP when configured, otherwise direct REST API.

    If ``FORCE_MCP_TICKET_CREATION`` is true and MCP is configured, REST is not used
    when MCP does not return a ticket id (avoids duplicate tickets when MCP is the
    required path).
    """
    force_mcp = _env_truthy("FORCE_MCP_TICKET_CREATION")
    email = desk_requester_email(requester_email)
    name = (requester_name or requester_email or email or "Helia user").strip() or "Helia user"

    if mcp_transport_configured():
        from app.mcp_client import get_mcp_client

        raw: Optional[dict[str, Any]] = None
        try:
            raw = await get_mcp_client().create_ticket(
                subject=subject,
                description=description,
                email=email,
                contact_name=name,
                phone=phone,
                priority=priority,
                category=category,
            )
        except Exception as err:
            log.warning("zoho MCP create_ticket failed: %s", err)

        tid = _extract_mcp_ticket_id(raw)
        if tid:
            return {"id": tid}

        if isinstance(raw, dict) and raw.get("errorCode"):
            errs = raw.get("errors")
            log.warning(
                "Zoho MCP rejected ticket: %s — %s%s",
                raw.get("errorCode"),
                raw.get("message"),
                f"; detail={errs!r}" if errs else "",
            )

        if raw and raw.get("accepted"):
            log.info("Zoho MCP ticket accepted with no ticket id in response")

        if force_mcp:
            keys_preview = list(raw.keys())[:16] if isinstance(raw, dict) else type(raw).__name__
            log.warning(
                "Zoho MCP did not return a ticket id (response preview keys=%s); "
                "FORCE_MCP_TICKET_CREATION is set, skipping REST",
                keys_preview,
            )
            return None

    if is_configured():
        return await create_ticket(
            subject=subject,
            description=description,
            priority=priority,
            requester_email=requester_email,
            requester_name=requester_name,
        )
    return None
