"""Clerk-based auth dependencies for FastAPI.

Mirrors the previous Express middleware:
  - `require_auth` validates the Clerk session (Bearer token or session cookie),
    fetches the user, computes their role, and attaches an `AuthedUser` to the
    request for downstream handlers.
  - `require_admin` ensures the resolved user has the admin role.

Roles:
  - The `ADMIN_EMAILS` env var (comma-separated) is the allow-list.
  - If `ADMIN_EMAILS` is empty, the very first user (oldest by created_at)
    becomes the admin (bootstrap behaviour).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from clerk_backend_api import Clerk
from clerk_backend_api.security import (
    AuthenticateRequestOptions,
    authenticate_request,
)
from clerk_backend_api.models import GetUserListRequest
import httpx
from fastapi import Depends, HTTPException, Request, status

log = logging.getLogger("api-server.auth")


@dataclass
class AuthedUser:
    userId: str
    email: Optional[str]
    firstName: Optional[str]
    lastName: Optional[str]
    imageUrl: Optional[str]
    role: str  # "admin" | "user"


def _admin_emails() -> list[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


_clerk: Optional[Clerk] = None


def _get_clerk() -> Clerk:
    global _clerk
    if _clerk is None:
        secret = os.environ.get("CLERK_SECRET_KEY")
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Clerk is not configured on the server.",
            )
        _clerk = Clerk(bearer_auth=secret)
    return _clerk


def _to_httpx_request(req: Request) -> httpx.Request:
    """Build an httpx.Request mirror of the FastAPI request for Clerk's helpers."""
    headers = {k: v for k, v in req.headers.items()}
    # Use the original URL so Clerk can read host/path; body is irrelevant for auth.
    return httpx.Request(req.method, str(req.url), headers=headers)


async def require_auth(request: Request) -> AuthedUser:
    if hasattr(request.state, "user") and request.state.user is not None:
        return request.state.user  # type: ignore[no-any-return]

    secret = os.environ.get("CLERK_SECRET_KEY")
    if not secret:
        raise HTTPException(status_code=503, detail="Auth not configured")

    try:
        state = authenticate_request(
            _to_httpx_request(request),
            AuthenticateRequestOptions(secret_key=secret),
        )
    except Exception as err:  # pragma: no cover - defensive
        log.exception("authenticate_request failed: %s", err)
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not state.is_signed_in or not state.payload:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = state.payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    clerk = _get_clerk()
    try:
        user = clerk.users.get(user_id=user_id)
    except Exception as err:
        log.exception("clerk.users.get failed: %s", err)
        raise HTTPException(status_code=401, detail="Unauthorized")

    primary_email_id = getattr(user, "primary_email_address_id", None)
    email: Optional[str] = None
    email_addresses = getattr(user, "email_addresses", None) or []
    if primary_email_id:
        for ea in email_addresses:
            if getattr(ea, "id", None) == primary_email_id:
                email = getattr(ea, "email_address", None)
                break
    if email is None and email_addresses:
        email = getattr(email_addresses[0], "email_address", None)

    role = "user"
    allow_list = _admin_emails()
    if email and email.lower() in allow_list:
        role = "admin"
    elif not allow_list:
        # Bootstrap: first registered user becomes admin.
        try:
            first = clerk.users.list(
                request=GetUserListRequest(limit=1, order_by="+created_at")
            )
            first_id = None
            if first and len(first) > 0:
                first_id = getattr(first[0], "id", None)
            if first_id == user_id:
                role = "admin"
        except Exception:  # pragma: no cover - non-fatal
            pass

    authed = AuthedUser(
        userId=user_id,
        email=email,
        firstName=getattr(user, "first_name", None),
        lastName=getattr(user, "last_name", None),
        imageUrl=getattr(user, "image_url", None),
        role=role,
    )
    request.state.user = authed
    return authed


async def require_admin(user: AuthedUser = Depends(require_auth)) -> AuthedUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
