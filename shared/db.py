"""Shared database connection manager for PostgreSQL.

Provides engine and session factories with connection pooling and health checking.
Gracefully handles missing optional dependencies in non-database environments.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

try:
    from sqlalchemy import create_engine, Engine, text
    from sqlalchemy.orm import sessionmaker, Session
    HAS_SQLALCHEMY = True
except ImportError:
    create_engine = None  # type: ignore
    Engine = Any  # type: ignore
    text = None  # type: ignore
    sessionmaker = None  # type: ignore
    Session = Any  # type: ignore
    HAS_SQLALCHEMY = False

from shared.config import get_settings

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_session_factory: Optional[Any] = None


def get_database_url() -> str:
    """Get PostgreSQL connection URL from settings or environment."""
    settings = get_settings()
    url = settings.database_url or os.getenv("DATABASE_URL")
    if not url:
        user = os.getenv("POSTGRES_USER", "rag_user")
        pwd = os.getenv("POSTGRES_PASSWORD", "rag_password")
        host = os.getenv("POSTGRES_HOST", "postgres")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "rag_chatbot")
        url = f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"
    return url


def get_db_engine() -> Engine:
    """Get or create singleton SQLAlchemy Engine for PostgreSQL."""
    if not HAS_SQLALCHEMY:
        raise RuntimeError("SQLAlchemy is not installed in the current environment.")

    global _engine
    if _engine is None:
        db_url = get_database_url()
        _engine = create_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    return _engine


def get_db_session() -> Session:
    """Create a new SQLAlchemy session."""
    if not HAS_SQLALCHEMY:
        raise RuntimeError("SQLAlchemy is not installed in the current environment.")

    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_db_engine(), expire_on_commit=False)
    return _session_factory()


def is_postgres_available() -> bool:
    """Check if PostgreSQL is reachable."""
    if not HAS_SQLALCHEMY:
        return False

    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.debug("PostgreSQL connection check failed: %s", str(e))
        return False
