from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import update
from .models import AdminAuditLog

from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.orm import (
    Session,
    selectinload,
)

from .models import (
    ChatTurn,
    Conversation,
    Message,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_title(content: str) -> str:
    normalized = " ".join(content.split())

    if len(normalized) <= 80:
        return normalized

    return normalized[:77].rstrip() + "..."


def build_input_hash(
    content: str,
    selected_document_ids: list[str],
) -> str:
    payload = json.dumps(
        {
            "content": content.strip(),
            "selected_document_ids":
                sorted(selected_document_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    return hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()


def list_conversations(
    session: Session,
    owner_id: str,
    limit: int,
) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(
            Conversation.owner_id == owner_id,
            Conversation.deleted_at.is_(None),
            Conversation.archived_at.is_(None),
        )
        .order_by(
            Conversation.pinned.desc(),
            Conversation.updated_at.desc(),
        )
        .limit(limit)
    )

    return list(
        session.scalars(statement).all()
    )


def get_conversation(
    session: Session,
    conversation_id: str,
    owner_id: str,
) -> Conversation | None:
    statement = (
        select(Conversation)
        .options(
            selectinload(
                Conversation.messages
            )
        )
        .where(
            Conversation.id == conversation_id,
            Conversation.owner_id == owner_id,
            Conversation.deleted_at.is_(None),
        )
    )

    return session.scalars(
        statement
    ).first()


def start_turn(
    session: Session,
    *,
    conversation_id: str,
    request_id: str,
    owner_id: str,
    content: str,
    selected_document_ids: list[str],
) -> tuple[
    ChatTurn,
    Message,
    Message,
    bool,
]:
    input_hash = build_input_hash(
        content,
        selected_document_ids,
    )

    conversation = session.get(
        Conversation,
        conversation_id,
    )

    if conversation is None:
        conversation = Conversation(
            id=conversation_id,
            owner_id=owner_id,
            title=build_title(content),
        )

        session.add(conversation)
        session.flush()

    elif (
        conversation.owner_id != owner_id
        or conversation.deleted_at is not None
    ):
        raise PermissionError(
            "Conversation is unavailable."
        )

    existing_turn = session.scalars(
        select(ChatTurn).where(
            ChatTurn.conversation_id
            == conversation_id,

            ChatTurn.request_id
            == request_id,
        )
    ).first()

    if existing_turn is not None:
        if (
            existing_turn.input_hash
            != input_hash
        ):
            raise ValueError(
                "request_id was reused "
                "with different input."
            )

        existing_messages = list(
            session.scalars(
                select(Message)
                .where(
                    Message.turn_id
                    == existing_turn.id,
                )
                .order_by(
                    Message.sequence,
                )
            ).all()
        )

        user_message = next(
            message
            for message in existing_messages
            if message.role == "user"
        )

        assistant_message = next(
            message
            for message in existing_messages
            if message.role == "assistant"
        )

        return (
            existing_turn,
            user_message,
            assistant_message,
            True,
        )

    max_sequence = session.scalar(
        select(
            func.coalesce(
                func.max(Message.sequence),
                0,
            )
        ).where(
            Message.conversation_id
            == conversation_id,
        )
    )

    turn = ChatTurn(
        id=str(uuid4()),
        conversation_id=conversation_id,
        request_id=request_id,
        input_hash=input_hash,
        status="generating",
    )

    user_message = Message(
        id=str(uuid4()),
        conversation_id=conversation_id,
        turn_id=turn.id,
        sequence=int(max_sequence) + 1,
        role="user",
        content=content,
        status="completed",
    )

    assistant_message = Message(
        id=str(uuid4()),
        conversation_id=conversation_id,
        turn_id=turn.id,
        sequence=int(max_sequence) + 2,
        role="assistant",
        content="",
        status="generating",
    )

    session.add_all(
        [
            turn,
            user_message,
            assistant_message,
        ]
    )

    conversation.updated_at = utc_now()

    session.flush()

    return (
        turn,
        user_message,
        assistant_message,
        False,
    )


def complete_turn(
    session: Session,
    *,
    turn_id: str,
    assistant_message_id: str,
    owner_id: str,
    content: str,
    sources: list[dict],
) -> None:
    turn = session.get(
        ChatTurn,
        turn_id,
    )

    assistant_message = session.get(
        Message,
        assistant_message_id,
    )

    if (
        turn is None
        or assistant_message is None
    ):
        raise LookupError(
            "Chat turn was not found."
        )

    conversation = session.get(
        Conversation,
        turn.conversation_id,
    )

    if (
        conversation is None
        or conversation.owner_id != owner_id
        or conversation.deleted_at is not None
    ):
        raise PermissionError(
            "Conversation is unavailable."
        )

    if (
        assistant_message.turn_id
        != turn.id
    ):
        raise ValueError(
            "Assistant message does not "
            "belong to the turn."
        )

    assistant_message.content = content
    assistant_message.status = "completed"

    assistant_message.sources_json = (
        json.dumps(
            sources,
            ensure_ascii=False,
        )
    )

    turn.status = "completed"
    turn.completed_at = utc_now()

    conversation.updated_at = utc_now()


def fail_turn(
    session: Session,
    *,
    turn_id: str,
    assistant_message_id: str,
    owner_id: str,
    partial_content: str,
) -> None:
    turn = session.get(
        ChatTurn,
        turn_id,
    )

    assistant_message = session.get(
        Message,
        assistant_message_id,
    )

    if turn is None:
        return

    conversation = session.get(
        Conversation,
        turn.conversation_id,
    )

    if (
        conversation is None
        or conversation.owner_id != owner_id
        or conversation.deleted_at is not None
    ):
        return

    turn.status = "failed"
    turn.completed_at = utc_now()

    if (
        assistant_message is not None
        and assistant_message.turn_id
        == turn.id
    ):
        assistant_message.content = (
            partial_content
        )

        assistant_message.status = (
            "failed"
        )

    conversation.updated_at = utc_now()


def rename_conversation(
    session: Session,
    *,
    conversation_id: str,
    owner_id: str,
    title: str,
) -> Conversation | None:
    conversation = get_conversation(
        session,
        conversation_id,
        owner_id,
    )

    if conversation is None:
        return None

    conversation.title = " ".join(
        title.split()
    )

    conversation.updated_at = utc_now()

    session.flush()

    return conversation


def soft_delete_conversation(
    session: Session,
    *,
    conversation_id: str,
    owner_id: str,
) -> bool:
    conversation = session.scalars(
        select(Conversation).where(
            Conversation.id
            == conversation_id,

            Conversation.owner_id
            == owner_id,

            Conversation.deleted_at
            .is_(None),
        )
    ).first()

    if conversation is None:
        return False

    now = utc_now()

    conversation.deleted_at = now
    conversation.updated_at = now

    generating_turns = session.scalars(
        select(ChatTurn).where(
            ChatTurn.conversation_id
            == conversation_id,

            ChatTurn.status
            == "generating",
        )
    ).all()

    for turn in generating_turns:
        turn.status = "cancelled"
        turn.completed_at = now

    generating_messages = session.scalars(
        select(Message).where(
            Message.conversation_id
            == conversation_id,

            Message.status
            == "generating",
        )
    ).all()

    for message in generating_messages:
        message.status = "cancelled"

    return True
def admin_list_deleted(
    session: Session,
    *,
    limit: int,
    offset: int,
    owner_id: str | None = None,
) -> list[dict]:
    message_count = (
        select(func.count(Message.id))
        .where(Message.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )

    statement = (
        select(
            Conversation.id,
            Conversation.title,
            Conversation.owner_id,
            Conversation.deleted_at,
            message_count.label("message_count"),
        )
        .where(Conversation.deleted_at.is_not(None))
        .order_by(
            Conversation.deleted_at.desc(),
            Conversation.id,
        )
        .limit(limit)
        .offset(offset)
    )

    if owner_id:
        statement = statement.where(
            Conversation.owner_id == owner_id
        )

    return [
        {
            **dict(row),
            "owner_name": row["owner_id"],
        }
        for row in session.execute(statement).mappings()
    ]


def admin_restore_conversation(
    session: Session,
    *,
    conversation_id: str,
    actor_id: str,
    reason: str,
) -> dict:
    row = session.execute(
        select(
            Conversation.id,
            Conversation.owner_id,
            Conversation.title,
            Conversation.deleted_at,
        ).where(Conversation.id == conversation_id)
    ).mappings().first()

    if row is None:
        raise LookupError("Conversation not found or permanently deleted.")

    if row["deleted_at"] is None:
        raise ValueError("Conversation is already active.")

    # Kiểm tra lại trạng thái ngay trong UPDATE.
    # Nếu chat bị purge hoặc restore đồng thời, không ghi audit thành công giả.
    result = session.execute(
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.deleted_at == row["deleted_at"],
        )
        .values(
            deleted_at=None,
            updated_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )

    if result.rowcount != 1:
        raise ValueError("Conversation changed. Refresh and try again.")

    session.add(
        AdminAuditLog(
            id=str(uuid4()),
            actor_id=actor_id,
            action="conversation.restore",
            target_id=conversation_id,
            target_title=row["title"],
            target_owner_id=row["owner_id"],
            reason=reason,
        )
    )

    # Flush không commit: router commit cả restore và audit cùng lúc.
    session.flush()

    return {
        "id": conversation_id,
        "owner_id": row["owner_id"],
        "restored": True,
    }


def admin_list_audit(
    session: Session,
    *,
    limit: int,
    offset: int,
) -> list[AdminAuditLog]:
    return list(
        session.scalars(
            select(AdminAuditLog)
            .order_by(
                AdminAuditLog.created_at.desc(),
                AdminAuditLog.id,
            )
            .limit(limit)
            .offset(offset)
        ).all()
    )