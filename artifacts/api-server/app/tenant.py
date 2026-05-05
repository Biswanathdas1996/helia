"""Tenant resolution and governance metadata.

Multi-tenant model:
  - Every document and chunk carries a ``tenantId``. All reads, writes, dedup,
    and retrieval are scoped by it.
  - ``tenantId`` is derived from the authed user. By default we use the email
    domain (``acme.com`` → ``acme``); a user document may override via
    ``user.tenantId``. Single-tenant deployments fall back to ``DEFAULT_TENANT``
    from env (default ``"default"``).

Governance fields live under ``document.governance`` and are validated /
defaulted here so the rest of the code never has to reason about missing keys.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from app.auth import AuthedUser

Sensitivity = Literal["public", "internal", "confidential", "restricted"]
RetentionClass = Literal["short", "standard", "long", "permanent"]

_RETENTION_DAYS: dict[str, Optional[int]] = {
    "short": 30,
    "standard": 365,
    "long": 365 * 7,
    "permanent": None,
}


def default_tenant() -> str:
    return os.environ.get("DEFAULT_TENANT", "default") or "default"


def tenant_for(user: AuthedUser, *, override: Optional[dict] = None) -> str:
    """Resolve the tenant id for a request.

    Order:
      1. Explicit override (e.g. user document field) when provided.
      2. Email domain prefix (`alice@acme.com` → `acme`).
      3. ``DEFAULT_TENANT`` env.
    """
    if override and isinstance(override.get("tenantId"), str) and override["tenantId"]:
        return override["tenantId"]
    email = (user.email or "").strip().lower()
    if "@" in email:
        domain = email.split("@", 1)[1]
        if domain:
            return domain.split(".", 1)[0] or default_tenant()
    return default_tenant()


def normalize_governance(
    raw: Optional[dict],
    *,
    owner: Optional[str],
    created_at: Optional[datetime] = None,
) -> dict:
    """Validate / default the governance block stored on each document.

    Unknown keys are dropped so we never leak arbitrary client data into the
    DB. Missing fields get safe defaults.
    """
    raw = raw or {}

    sensitivity = raw.get("sensitivity")
    if sensitivity not in ("public", "internal", "confidential", "restricted"):
        sensitivity = "internal"

    retention = raw.get("retentionClass")
    if retention not in _RETENTION_DAYS:
        retention = "standard"

    residency = raw.get("dataResidency")
    if not isinstance(residency, str) or not residency:
        residency = os.environ.get("DEFAULT_DATA_RESIDENCY", "global") or "global"

    source_system = raw.get("sourceSystem")
    if not isinstance(source_system, str) or not source_system:
        source_system = "manual"

    governance_owner = raw.get("owner")
    if not isinstance(governance_owner, str) or not governance_owner:
        governance_owner = owner or "unknown"

    legal_hold = bool(raw.get("legalHold", False))

    days = _RETENTION_DAYS.get(retention)
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    retain_until: Optional[datetime] = None
    if days is not None:
        retain_until = created_at + timedelta(days=days)

    return {
        "sensitivity": sensitivity,
        "retentionClass": retention,
        "retainUntil": retain_until,
        "dataResidency": residency,
        "sourceSystem": source_system,
        "owner": governance_owner,
        "legalHold": legal_hold,
    }


def serialize_governance(g: Optional[dict]) -> dict:
    """Project governance block for API responses (datetime → ISO string)."""
    g = g or {}
    retain_until = g.get("retainUntil")
    if isinstance(retain_until, datetime):
        retain_until = retain_until.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "sensitivity": g.get("sensitivity") or "internal",
        "retentionClass": g.get("retentionClass") or "standard",
        "retainUntil": retain_until,
        "dataResidency": g.get("dataResidency") or "global",
        "sourceSystem": g.get("sourceSystem") or "manual",
        "owner": g.get("owner") or "unknown",
        "legalHold": bool(g.get("legalHold", False)),
    }
