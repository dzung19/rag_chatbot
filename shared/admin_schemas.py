from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RestoreConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("Reason must contain at least 5 characters.")
        return value


class AdminDeletedConversation(BaseModel):
    id: str
    title: str
    owner_id: str

    # Chưa có directory người dùng: dùng owner_id làm nhãn hiển thị.
    owner_name: str

    deleted_at: datetime
    message_count: int


class RestoreConversationResponse(BaseModel):
    id: str
    owner_id: str
    restored: bool


class AdminAuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str
    action: str
    target_id: str
    target_title: str
    target_owner_id: str
    reason: str
    created_at: datetime