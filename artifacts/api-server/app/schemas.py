"""Pydantic request bodies — minimal validation matching @workspace/api-zod."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class GovernanceInput(BaseModel):
    sensitivity: Optional[Literal["public", "internal", "confidential", "restricted"]] = None
    retentionClass: Optional[Literal["short", "standard", "long", "permanent"]] = None
    dataResidency: Optional[str] = None
    sourceSystem: Optional[str] = None
    owner: Optional[str] = None
    legalHold: Optional[bool] = None


class CreateDocumentBody(BaseModel):
    name: str = Field(min_length=1)
    sourceType: Literal["text", "pdf", "docx", "txt"]
    content: str = Field(min_length=1, max_length=20_000_000)
    tags: Optional[list[str]] = None
    governance: Optional[GovernanceInput] = None


class RejectDocumentBody(BaseModel):
    reason: Optional[str] = None


class CreateConversationBody(BaseModel):
    title: Optional[str] = None


class SendMessageBody(BaseModel):
    content: str = Field(min_length=1)
    imageDataUrl: Optional[str] = Field(default=None, max_length=20_000_000)


class TtsBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    voiceId: Optional[str] = None


class RateMessageBody(BaseModel):
    rating: Literal["up", "down"]
    comment: Optional[str] = None


class CreateTicketBody(BaseModel):
    subject: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: Literal["low", "medium", "high", "urgent"]
    relatedMessageId: Optional[int] = None


class UpdateTicketBody(BaseModel):
    status: Optional[Literal["open", "in_progress", "resolved", "closed"]] = None
    lastUpdate: Optional[str] = None
