from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import (
    Engine,
    MetaData,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    sessionmaker,
)


DATABASE_URL = os.getenv(
    "CONVERSATION_DATABASE_URL",
    "sqlite:///D:/rag_chatbot/data/chat_history.db",
)


# Đặt tên constraint rõ ràng giúp Alembic tạo
# migration ổn định trên nhiều loại database.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": (
        "fk_%(table_name)s_"
        "%(column_0_name)s_"
        "%(referred_table_name)s"
    ),
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention=NAMING_CONVENTION,
    )


def create_database_engine() -> Engine:
    is_sqlite = DATABASE_URL.startswith(
        "sqlite"
    )

    engine_options: dict = {
        "pool_pre_ping": True,
    }

    if is_sqlite:
        engine_options["connect_args"] = {
            "check_same_thread": False,
        }

    return create_engine(
        DATABASE_URL,
        **engine_options,
    )


engine = create_database_engine()


# Các PRAGMA chỉ được áp dụng cho SQLite.
# PostgreSQL sẽ không chạy phần này.
if engine.dialect.name == "sqlite":

    @event.listens_for(
        engine,
        "connect",
    )
    def configure_sqlite_connection(
        dbapi_connection,
        connection_record,
    ) -> None:
        del connection_record

        cursor = dbapi_connection.cursor()

        cursor.execute(
            "PRAGMA foreign_keys = ON"
        )

        cursor.execute(
            "PRAGMA journal_mode = WAL"
        )

        cursor.execute(
            "PRAGMA busy_timeout = 5000"
        )

        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


def get_database_session() -> Generator[
    Session,
    None,
    None,
]:
    session = SessionLocal()

    try:
        yield session
    finally:
        session.close()