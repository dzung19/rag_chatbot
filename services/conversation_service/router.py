from __future__ import annotations

import os
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from .database import (
    get_database_session,
)
from .repository import (
    complete_turn,
    fail_turn,
    get_conversation,
    list_conversations,
    rename_conversation,
    soft_delete_conversation,
    start_turn,
)
from .schemas import (
    CompleteTurnRequest,
    ConversationDetail,
    ConversationSummary,
    FailTurnRequest,
    RenameConversationRequest,
    StartTurnRequest,
    StartTurnResponse,
)


DatabaseSession = Annotated[
    Session,
    Depends(get_database_session),
]


def verify_internal_service(
    x_internal_service_key:
        Annotated[
            str | None,
            Header(),
        ] = None,
) -> None:
    expected = os.getenv(
        "INTERNAL_SERVICE_KEY",
    )

    if (
        not expected
        or x_internal_service_key
        != expected
    ):
        raise HTTPException(
            status_code=
                status.HTTP_401_UNAUTHORIZED,
            detail=
                "Invalid internal credential.",
        )


InternalServiceDependency = Annotated[
    None,
    Depends(verify_internal_service),
]


public_router = APIRouter(
    prefix="/conversations",
    tags=["Conversations"],
    dependencies=[
        Depends(verify_internal_service),
    ],
)


internal_router = APIRouter(
    prefix="/internal",
    tags=["Internal"],
    dependencies=[
        Depends(verify_internal_service),
    ],
)


@public_router.get(
    "",
    response_model=
        list[ConversationSummary],
)
def read_conversations(
    session: DatabaseSession,
    x_user_id:
        Annotated[str, Header()],
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
):
    return list_conversations(
        session,
        owner_id=x_user_id,
        limit=limit,
    )


@public_router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
)
def read_conversation(
    conversation_id: str,
    session: DatabaseSession,
    x_user_id:
        Annotated[str, Header()],
):
    conversation = get_conversation(
        session,
        conversation_id,
        x_user_id,
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail=
                "Conversation not found.",
        )

    return conversation


@public_router.patch(
    "/{conversation_id}",
    response_model=ConversationSummary,
)
def update_conversation(
    conversation_id: str,
    request:
        RenameConversationRequest,
    session: DatabaseSession,
    x_user_id:
        Annotated[str, Header()],
):
    try:
        conversation = rename_conversation(
            session,
            conversation_id=
                conversation_id,
            owner_id=x_user_id,
            title=request.title,
        )

        if conversation is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Conversation "
                    "not found."
                ),
            )

        session.commit()
        session.refresh(conversation)

        return conversation

    except HTTPException:
        session.rollback()
        raise

    except Exception:
        session.rollback()
        raise


@public_router.delete(
    "/{conversation_id}",
    status_code=
        status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: str,
    session: DatabaseSession,
    x_user_id:
        Annotated[str, Header()],
):
    try:
        deleted = (
            soft_delete_conversation(
                session,
                conversation_id=
                    conversation_id,
                owner_id=x_user_id,
            )
        )

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Conversation "
                    "not found."
                ),
            )

        session.commit()

        return Response(
            status_code=
                status.HTTP_204_NO_CONTENT,
        )

    except HTTPException:
        session.rollback()
        raise

    except Exception:
        session.rollback()
        raise


@internal_router.post(
    "/turns/start",
    response_model=StartTurnResponse,
)
def create_turn(
    request: StartTurnRequest,
    session: DatabaseSession,
):
    try:
        (
            turn,
            user_message,
            assistant_message,
            duplicate,
        ) = start_turn(
            session,
            conversation_id=
                request.conversation_id,
            request_id=
                request.request_id,
            owner_id=request.owner_id,
            content=request.content,
            selected_document_ids=
                request
                .selected_document_ids,
        )

        session.commit()

        return StartTurnResponse(
            turn_id=turn.id,
            user_message_id=
                user_message.id,
            assistant_message_id=
                assistant_message.id,
            assistant_status=
                assistant_message.status,
            assistant_content=
                assistant_message.content,
            duplicate=duplicate,
        )

    except PermissionError as error:
        session.rollback()

        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        session.rollback()

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except Exception:
        session.rollback()
        raise


@internal_router.post(
    "/turns/{turn_id}/complete",
    status_code=
        status.HTTP_204_NO_CONTENT,
)
def finish_turn(
    turn_id: str,
    request: CompleteTurnRequest,
    session: DatabaseSession,
):
    try:
        complete_turn(
            session,
            turn_id=turn_id,
            assistant_message_id=
                request
                .assistant_message_id,
            owner_id=request.owner_id,
            content=request.content,
            sources=request.sources,
        )

        session.commit()

        return Response(
            status_code=
                status.HTTP_204_NO_CONTENT,
        )

    except LookupError as error:
        session.rollback()

        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except PermissionError as error:
        session.rollback()

        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    except ValueError as error:
        session.rollback()

        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    except Exception:
        session.rollback()
        raise


@internal_router.post(
    "/turns/{turn_id}/fail",
    status_code=
        status.HTTP_204_NO_CONTENT,
)
def mark_turn_failed(
    turn_id: str,
    request: FailTurnRequest,
    session: DatabaseSession,
):
    try:
        fail_turn(
            session,
            turn_id=turn_id,
            assistant_message_id=
                request
                .assistant_message_id,
            owner_id=request.owner_id,
            partial_content=
                request.partial_content,
        )

        session.commit()

        return Response(
            status_code=
                status.HTTP_204_NO_CONTENT,
        )

    except Exception:
        session.rollback()
        raise