"""Persistent document and ingestion state store backed by PostgreSQL.

Replaces volatile in-memory dicts (_documents, _ingestion_tasks, _hash_index)
to enable multi-worker scaling, atomic deduplication, and crash resilience.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    select,
    update,
    delete,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Add parent directory for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.config import get_settings
from shared.models import (
    DocumentInfo,
    DocumentType,
    IngestionStatus,
    IngestionStatusEnum,
)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class IngestionDocument(Base):
    """Stores document metadata, processing status, and content hash."""

    __tablename__ = "ingestion_documents"

    document_id = Column(String(36), primary_key=True)
    filename = Column(String(512), nullable=False)
    file_type = Column(String(32), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False, default=0)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    chunk_count = Column(Integer, nullable=False, default=0)
    total_chunks = Column(Integer, nullable=False, default=0)
    chunks_processed = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_ingestion_documents_hash_active",
            "content_hash",
            unique=True,
            postgresql_where=(text("status <> 'failed'")),
        ),
        Index("ix_ingestion_documents_status", "status"),
        Index("ix_ingestion_documents_created_at", "created_at"),
    )


def _row_to_doc_info(row: IngestionDocument) -> DocumentInfo:
    file_type_val = row.file_type.lower()
    allowed_types = {e.value: e for e in DocumentType}
    doc_type = allowed_types.get(file_type_val, DocumentType.TXT)
    return DocumentInfo(
        document_id=row.document_id,
        filename=row.filename,
        file_type=doc_type,
        file_size_bytes=row.file_size_bytes,
        chunk_count=row.chunk_count,
        status=IngestionStatusEnum(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_status(row: IngestionDocument) -> IngestionStatus:
    return IngestionStatus(
        document_id=row.document_id,
        status=IngestionStatusEnum(row.status),
        chunks_processed=row.chunks_processed,
        total_chunks=row.total_chunks,
        error_message=row.error_message,
    )


class StateStore:
    """Manages persistent ingestion document state in PostgreSQL."""

    def __init__(self, database_url: Optional[str] = None):
        settings = get_settings()
        self.db_url = database_url or settings.database_url or os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://rag_user:rag_password@postgres:5432/rag_chatbot"
        )
        self.is_sqlite = self.db_url.startswith("sqlite")

        engine_kwargs = {"pool_pre_ping": True}
        if not self.is_sqlite:
            engine_kwargs.update({
                "pool_size": 10,
                "max_overflow": 20,
                "pool_recycle": 1800,
            })
        else:
            engine_kwargs["connect_args"] = {"check_same_thread": False}

        self.engine = create_engine(self.db_url, **engine_kwargs)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    def init_db(self) -> None:
        """Create tables and partial unique index if they do not exist."""
        try:
            Base.metadata.create_all(self.engine)
            if not self.is_sqlite:
                with self.engine.connect() as conn:
                    conn.execute(
                        text(
                            """
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_documents_hash_active
                            ON ingestion_documents (content_hash)
                            WHERE status <> 'failed';
                            """
                        )
                    )
                    conn.commit()
            logger.info("Initialized ingestion state store on database: %s", self.db_url.split("@")[-1])
        except Exception as e:
            logger.error("Failed to initialize ingestion state store DB: %s", str(e), exc_info=True)
            raise

    def claim_document(
        self,
        document_id: str,
        filename: str,
        doc_type: str,
        file_size_bytes: int,
        content_hash: str,
    ) -> tuple[DocumentInfo, bool]:
        """Atomically claim a document for ingestion.

        Uses PostgreSQL partial unique index on content_hash (where status <> 'failed').
        Returns:
            (doc_info, is_new):
            - is_new=True: Document successfully claimed for processing.
            - is_new=False: Duplicate active document already exists, returns existing record.
        """
        now = datetime.now(timezone.utc)

        with self.SessionLocal() as session:
            if not self.is_sqlite:
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = (
                    pg_insert(IngestionDocument)
                    .values(
                        document_id=document_id,
                        filename=filename,
                        file_type=doc_type,
                        file_size_bytes=file_size_bytes,
                        content_hash=content_hash,
                        status=IngestionStatusEnum.PROCESSING.value,
                        chunk_count=0,
                        total_chunks=0,
                        chunks_processed=0,
                        error_message=None,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["content_hash"],
                        index_where=(text("status <> 'failed'")),
                    )
                    .returning(IngestionDocument)
                )
                res = session.execute(stmt).scalar_one_or_none()
                session.commit()

                if res is not None:
                    return _row_to_doc_info(res), True

                # Duplicate detected: fetch existing document with this content_hash
                existing = session.execute(
                    select(IngestionDocument)
                    .where(
                        IngestionDocument.content_hash == content_hash,
                        IngestionDocument.status != IngestionStatusEnum.FAILED.value,
                    )
                    .order_by(IngestionDocument.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()

                if existing:
                    return _row_to_doc_info(existing), False

                # Fallback if somehow not found, query by document_id
                doc = session.get(IngestionDocument, document_id)
                if doc:
                    return _row_to_doc_info(doc), True
                raise RuntimeError("Failed to claim document or find existing duplicate.")
            else:
                # SQLite fallback
                existing = session.execute(
                    select(IngestionDocument)
                    .where(
                        IngestionDocument.content_hash == content_hash,
                        IngestionDocument.status != IngestionStatusEnum.FAILED.value,
                    )
                ).scalar_one_or_none()
                if existing:
                    return _row_to_doc_info(existing), False

                new_doc = IngestionDocument(
                    document_id=document_id,
                    filename=filename,
                    file_type=doc_type,
                    file_size_bytes=file_size_bytes,
                    content_hash=content_hash,
                    status=IngestionStatusEnum.PROCESSING.value,
                    chunk_count=0,
                    total_chunks=0,
                    chunks_processed=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_doc)
                session.commit()
                return _row_to_doc_info(new_doc), True

    def get_document(self, document_id: str) -> Optional[DocumentInfo]:
        """Fetch document metadata by document_id."""
        with self.SessionLocal() as session:
            doc = session.get(IngestionDocument, document_id)
            if doc:
                return _row_to_doc_info(doc)
            return None

    def get_document_by_hash(self, content_hash: str) -> Optional[DocumentInfo]:
        """Fetch document metadata by content_hash (non-failed only)."""
        with self.SessionLocal() as session:
            stmt = (
                select(IngestionDocument)
                .where(
                    IngestionDocument.content_hash == content_hash,
                    IngestionDocument.status != IngestionStatusEnum.FAILED.value,
                )
                .order_by(IngestionDocument.created_at.desc())
                .limit(1)
            )
            doc = session.execute(stmt).scalar_one_or_none()
            if doc:
                return _row_to_doc_info(doc)
            return None

    def list_documents(self) -> list[DocumentInfo]:
        """List all indexed documents ordered by creation time descending."""
        with self.SessionLocal() as session:
            stmt = select(IngestionDocument).order_by(IngestionDocument.created_at.desc())
            rows = session.execute(stmt).scalars().all()
            return [_row_to_doc_info(r) for r in rows]

    def get_task_status(self, document_id: str) -> Optional[IngestionStatus]:
        """Get ingestion status and progress for a document."""
        with self.SessionLocal() as session:
            doc = session.get(IngestionDocument, document_id)
            if doc:
                return _row_to_status(doc)
            return None

    def update_progress(
        self,
        document_id: str,
        chunks_processed: int,
        total_chunks: Optional[int] = None,
    ) -> None:
        """Update chunks processed and optionally total chunks."""
        with self.SessionLocal() as session:
            vals = {
                "chunks_processed": chunks_processed,
                "updated_at": datetime.now(timezone.utc),
            }
            if total_chunks is not None:
                vals["total_chunks"] = total_chunks

            stmt = (
                update(IngestionDocument)
                .where(IngestionDocument.document_id == document_id)
                .values(**vals)
            )
            session.execute(stmt)
            session.commit()

    def mark_completed(self, document_id: str, chunk_count: int) -> None:
        """Mark document ingestion as completed."""
        with self.SessionLocal() as session:
            stmt = (
                update(IngestionDocument)
                .where(IngestionDocument.document_id == document_id)
                .values(
                    status=IngestionStatusEnum.COMPLETED.value,
                    chunk_count=chunk_count,
                    chunks_processed=chunk_count,
                    total_chunks=chunk_count,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.execute(stmt)
            session.commit()

    def mark_failed(self, document_id: str, error_message: str) -> None:
        """Mark document ingestion as failed."""
        with self.SessionLocal() as session:
            stmt = (
                update(IngestionDocument)
                .where(IngestionDocument.document_id == document_id)
                .values(
                    status=IngestionStatusEnum.FAILED.value,
                    error_message=error_message,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.execute(stmt)
            session.commit()

    def delete_document(self, document_id: str) -> Optional[DocumentInfo]:
        """Delete document from state store and return deleted record."""
        with self.SessionLocal() as session:
            doc = session.get(IngestionDocument, document_id)
            if not doc:
                return None
            doc_info = _row_to_doc_info(doc)
            session.delete(doc)
            session.commit()
            return doc_info

    def is_cancelled(self, document_id: str) -> bool:
        """Check if document has been deleted/cancelled by user."""
        with self.SessionLocal() as session:
            doc = session.get(IngestionDocument, document_id)
            return doc is None

    def clean_interrupted_tasks(self, grace_minutes: int = 10) -> int:
        """Mark pending/processing tasks as failed if interrupted by restart."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)
        with self.SessionLocal() as session:
            stmt = (
                update(IngestionDocument)
                .where(
                    IngestionDocument.status.in_([
                        IngestionStatusEnum.PENDING.value,
                        IngestionStatusEnum.PROCESSING.value,
                    ]),
                    IngestionDocument.updated_at < cutoff,
                )
                .values(
                    status=IngestionStatusEnum.FAILED.value,
                    error_message="Interrupted by service restart",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            res = session.execute(stmt)
            session.commit()
            affected = res.rowcount
            if affected > 0:
                logger.warning("Marked %d stale/interrupted ingestion tasks as FAILED", affected)
            return affected

    def upsert_reconstructed_document(
        self,
        document_id: str,
        filename: str,
        file_type: str,
        file_size_bytes: int,
        content_hash: str,
        chunk_count: int,
        created_at: datetime,
    ) -> None:
        """Insert reconstructed document from Qdrant if not already present."""
        with self.SessionLocal() as session:
            existing = session.get(IngestionDocument, document_id)
            if existing:
                return
            doc = IngestionDocument(
                document_id=document_id,
                filename=filename,
                file_type=file_type,
                file_size_bytes=file_size_bytes,
                content_hash=content_hash,
                status=IngestionStatusEnum.COMPLETED.value,
                chunk_count=chunk_count,
                total_chunks=chunk_count,
                chunks_processed=chunk_count,
                created_at=created_at,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(doc)
            try:
                session.commit()
            except Exception:
                session.rollback()


# Global state store singleton
_state_store: Optional[StateStore] = None


def get_state_store() -> StateStore:
    """Get or initialize the StateStore singleton."""
    global _state_store
    if _state_store is None:
        _state_store = StateStore()
    return _state_store
