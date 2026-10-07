from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from .database import Base


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc,
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    owner_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    pinned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    archived_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    deleted_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    turns: Mapped[
        list["ChatTurn"]
    ] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    messages: Mapped[
        list["Message"]
    ] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.sequence",
    )

    __table_args__ = (
        Index(
            "ix_conversations_owner_active_updated",
            "owner_id",
            "deleted_at",
            "updated_at",
        ),
    )


class ChatTurn(Base):
    __tablename__ = "chat_turns"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    request_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )

    input_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="generating",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    completed_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    conversation: Mapped[
        Conversation
    ] = relationship(
        back_populates="turns",
    )

    messages: Mapped[
        list["Message"]
    ] = relationship(
        back_populates="turn",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "request_id",
            name=(
                "uq_chat_turns_"
                "conversation_request"
            ),
        ),
        CheckConstraint(
            (
                "status IN "
                "('generating', "
                "'completed', "
                "'failed', "
                "'cancelled')"
            ),
            name="valid_status",
        ),
        Index(
            "ix_chat_turns_conversation",
            "conversation_id",
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    turn_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "chat_turns.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    sequence: Mapped[int] = mapped_column(
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    sources_json: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    conversation: Mapped[
        Conversation
    ] = relationship(
        back_populates="messages",
    )

    turn: Mapped[
        ChatTurn
    ] = relationship(
        back_populates="messages",
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name=(
                "uq_messages_"
                "conversation_sequence"
            ),
        ),
        CheckConstraint(
            (
                "role IN "
                "('user', "
                "'assistant', "
                "'system', "
                "'tool')"
            ),
            name="valid_role",
        ),
        CheckConstraint(
            (
                "status IN "
                "('generating', "
                "'completed', "
                "'failed', "
                "'cancelled')"
            ),
            name="valid_status",
        ),
        Index(
            "ix_messages_conversation",
            "conversation_id",
        ),
        Index(
            "ix_messages_turn",
            "turn_id",
        ),
    )