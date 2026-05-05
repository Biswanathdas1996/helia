"""MongoDB access layer.

Mirrors the previous TypeScript `@workspace/db` lib: lazy connect, idempotent
indexes, and an atomic `next_id` counter so collections have stable numeric IDs.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None
_init_lock = asyncio.Lock()


async def _init() -> AsyncIOMotorDatabase:
    global _client, _db
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI must be set. Did you forget to provision MongoDB?")
    _client = AsyncIOMotorClient(uri)
    db = _client.get_default_database()
    await asyncio.gather(
        db.chunks.create_index([("content", "text")]),
        db.chunks.create_index([("documentId", ASCENDING)]),
        db.chunks.create_index([("chunkHash", ASCENDING)]),
        db.chunks.create_index([("tenantId", ASCENDING), ("documentId", ASCENDING)]),
        db.chunks.create_index([("tenantId", ASCENDING), ("embeddingVersion", ASCENDING)]),
        db.documents.create_index([("status", ASCENDING)]),
        db.documents.create_index([("tenantId", ASCENDING), ("status", ASCENDING)]),
        db.documents.create_index([("tenantId", ASCENDING), ("status", ASCENDING), ("contentHash", ASCENDING)]),
        db.documents.create_index([("status", ASCENDING), ("contentHash", ASCENDING)]),
        db.documents.create_index([("createdAt", DESCENDING)]),
        db.documents.create_index([("governance.sensitivity", ASCENDING)]),
        db.documents.create_index([("governance.retainUntil", ASCENDING)]),
        db.ingestion_runs.create_index([("documentId", ASCENDING), ("createdAt", DESCENDING)]),
        db.ingestion_runs.create_index([("status", ASCENDING), ("createdAt", DESCENDING)]),
        db.ingestion_runs.create_index([("tenantId", ASCENDING), ("createdAt", DESCENDING)]),
        db.conversations.create_index([("userId", ASCENDING), ("updatedAt", DESCENDING)]),
        db.messages.create_index([("conversationId", ASCENDING), ("createdAt", ASCENDING)]),
        db.messages.create_index([("createdAt", DESCENDING)]),
        db.tickets.create_index([("userId", ASCENDING), ("createdAt", DESCENDING)]),
        db.tickets.create_index([("createdAt", DESCENDING)]),
        db.audit_logs.create_index([("createdAt", DESCENDING)]),
        db.audit_logs.create_index([("actor", ASCENDING), ("createdAt", DESCENDING)]),
        db.audit_logs.create_index([("action", ASCENDING), ("createdAt", DESCENDING)]),
    )
    _db = db
    return db


async def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is not None:
        return _db
    async with _init_lock:
        if _db is None:
            await _init()
    assert _db is not None
    return _db


async def next_id(name: str) -> int:
    """Atomic auto-increment counter so each collection has stable numeric IDs."""
    db = await get_db()
    r = await db.counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,  # ReturnDocument.AFTER
    )
    # motor returns the document; with return_document=True we get the after-update doc
    return int(r["seq"])
