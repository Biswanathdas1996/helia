"""Custom JWT-based auth for FastAPI.

Flow:
  - POST /api/auth/register  → create user in MongoDB, return JWT cookie
  - POST /api/auth/login     → verify password, return JWT cookie
  - POST /api/auth/logout    → clear cookie
  - GET  /api/me             → decode cookie, return AuthedUser

Roles:
    - Role is stored on each user document ("admin" | "user").
    - Legacy users without a role are bootstrapped: first user is admin, others are user.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Response, status

_JWT_ALGORITHM = "HS256"
_COOKIE_NAME = "helia_session"
_TOKEN_TTL_DAYS = 30


def _jwt_secret() -> str:
    s = os.environ.get("SESSION_SECRET", "")
    if not s:
        raise HTTPException(status_code=503, detail="Auth not configured (SESSION_SECRET missing)")
    return s


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=_TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_token(token: str) -> str:
    """Return user_id (sub) or raise HTTPException."""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Unauthorized")


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("NODE_ENV") == "production",
        max_age=_TOKEN_TTL_DAYS * 86400,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(_COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# AuthedUser dataclass
# ---------------------------------------------------------------------------

@dataclass
class AuthedUser:
    userId: str
    email: Optional[str]
    firstName: Optional[str]
    lastName: Optional[str]
    imageUrl: Optional[str]
    role: str  # "admin" | "user"


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def require_auth(
    request: Request,
    helia_session: Optional[str] = Cookie(default=None, alias=_COOKIE_NAME),
) -> AuthedUser:
    if hasattr(request.state, "user") and request.state.user is not None:
        return request.state.user  # type: ignore[no-any-return]

    if not helia_session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = decode_token(helia_session)

    from app.db import get_db
    db = await get_db()
    user = await db.users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email = user.get("email", "")
    role = user.get("role")
    if role not in {"admin", "user"}:
        # Bootstrap legacy users that predate the explicit role field.
        role = "user"
        try:
            first = await db.users.find_one({}, sort=[("createdAt", 1)])
            if first and first["_id"] == user_id:
                role = "admin"
        except Exception:
            pass

        await db.users.update_one(
            {"_id": user_id},
            {"$set": {"role": role, "updatedAt": datetime.now(timezone.utc)}},
        )

    authed = AuthedUser(
        userId=user_id,
        email=email,
        firstName=user.get("firstName"),
        lastName=user.get("lastName"),
        imageUrl=user.get("imageUrl"),
        role=role,
    )
    request.state.user = authed
    return authed


async def require_admin(user: AuthedUser = Depends(require_auth)) -> AuthedUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
