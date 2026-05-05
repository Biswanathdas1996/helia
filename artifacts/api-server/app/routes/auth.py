"""Custom register / login / logout endpoints."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import rate_limit
from app.audit import audit_log
from app.auth import (
    AuthedUser,
    clear_auth_cookie,
    create_token,
    hash_password,
    require_auth,
    set_auth_cookie,
    verify_password,
)
from app.db import get_db, next_id

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MIN_PASSWORD_LEN = 8


class RegisterBody(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=_MIN_PASSWORD_LEN)
    firstName: Optional[str] = None
    lastName: Optional[str] = None


class LoginBody(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return email


@router.post("/auth/register", status_code=201)
async def register(body: RegisterBody, request: Request, response: Response) -> dict[str, object]:
    await rate_limit.enforce(request, scope="auth")
    email = _validate_email(body.email)

    db = await get_db()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    is_first_user = (await db.users.count_documents({}, limit=1)) == 0

    now = datetime.now(timezone.utc)
    user_id = str(await next_id("users"))
    user = {
        "_id": user_id,
        "email": email,
        "firstName": (body.firstName or "").strip() or None,
        "lastName": (body.lastName or "").strip() or None,
        "imageUrl": None,
        "passwordHash": hash_password(body.password),
        "role": "admin" if is_first_user else "user",
        "createdAt": now,
        "updatedAt": now,
    }
    await db.users.insert_one(user)

    token = create_token(user_id)
    set_auth_cookie(response, token)
    await audit_log(action="auth.register", actor=email, target=user_id)

    return {
        "userId": user_id,
        "email": email,
        "firstName": user["firstName"],
        "lastName": user["lastName"],
        "imageUrl": None,
    }


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, object]:
    await rate_limit.enforce(request, scope="auth")
    email = _validate_email(body.email)

    db = await get_db()
    user = await db.users.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(body.password, user.get("passwordHash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user["_id"])
    set_auth_cookie(response, token)
    await audit_log(action="auth.login", actor=email, target=user["_id"])

    return {
        "userId": user["_id"],
        "email": user.get("email"),
        "firstName": user.get("firstName"),
        "lastName": user.get("lastName"),
        "imageUrl": user.get("imageUrl"),
    }


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    clear_auth_cookie(response)
    return {"status": "ok"}
