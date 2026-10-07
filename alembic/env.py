from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import (
    engine_from_config,
    pool,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

sys.path.insert(
    0,
    str(PROJECT_ROOT),
)


from services.conversation_service.database import (  # noqa: E402
    Base,
)

from services.conversation_service import models  # noqa: E402, F401


config = context.config


if config.config_file_name:
    fileConfig(
        config.config_file_name,
    )


DATABASE_URL = os.getenv(
    "CONVERSATION_DATABASE_URL",
    "sqlite:///D:/rag_chatbot/data/chat_history.db",
)

config.set_main_option(
    "sqlalchemy.url",
    DATABASE_URL,
)


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=(
            DATABASE_URL.startswith(
                "sqlite"
            )
        ),
        dialect_opts={
            "paramstyle": "named",
        },
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = (
        config.get_section(
            config.config_ini_section
        )
        or {}
    )

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=
                target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=(
                connection.dialect.name
                == "sqlite"
            ),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()