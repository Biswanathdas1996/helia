"""Pydantic request bodies — minimal validation matching @workspace/api-zod."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CreateDocumentBody(BaseModel):
    name: str = Field(min_length=1)
    sourceType: Literal["text", "pdf", "docx", "txt"]
    content: str = Field(min_length=1)
    tags: Optional[list[str]] = None


class RejectDocumentBody(BaseModel):
    reason: Optional[str] = None


class CreateConversationBody(BaseModel):
    title: Optional[str] = None


class SendMessageBody(BaseModel):
    content: str = Field(min_length=1)


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
