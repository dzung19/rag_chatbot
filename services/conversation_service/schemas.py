from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class ConversationSummary(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: str
    title: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


class StoredMessage(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: str
    conversation_id: str
    turn_id: str
    sequence: int
    role: str
    content: str
    status: str
    sources_json: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(
    ConversationSummary
):
    messages: list[StoredMessage]


class RenameConversationRequest(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=200,
    )


class StartTurnRequest(BaseModel):
    conversation_id: str
    request_id: str
    owner_id: str

    content: str = Field(
        min_length=1,
        max_length=10_000,
    )

    selected_document_ids: list[str] = Field(
        default_factory=list,
    )


class StartTurnResponse(BaseModel):
    turn_id: str
    user_message_id: str
    assistant_message_id: str
    assistant_status: str
    assistant_content: str
    duplicate: bool


class CompleteTurnRequest(BaseModel):
    owner_id: str
    assistant_message_id: str
    content: str
    sources: list[dict] = Field(
        default_factory=list,
    )


class FailTurnRequest(BaseModel):
    owner_id: str
    assistant_message_id: str
    partial_content: str = ""