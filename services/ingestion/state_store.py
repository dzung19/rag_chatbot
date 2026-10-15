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
    ForeignKey,
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


class FullDocument(Base):
    """Stores full continuous text of documents for business comparison."""

    __tablename__ = "full_documents"

    document_id = Column(String(36), primary_key=True)
    filename = Column(String(512), nullable=False)
    full_text = Column(Text, nullable=False)
    page_count = Column(Integer, nullable=False, default=1)
    file_size_bytes = Column(BigInteger, nullable=False, default=0)
    file_type = Column(String(32), nullable=False, default="txt")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_full_documents_created_at", "created_at"),
    )


class DocumentChunk(Base):
    """Stores chunked text representations and metadata matching ERD."""

    __tablename__ = "document_chunks"

    id = Column(String(64), primary_key=True)
    document_id = Column(
        String(36),
        ForeignKey("ingestion_documents.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    filename = Column(String(512), nullable=False)
    chunk_index = Column(Integer, nullable=False, default=0)
    page = Column(Integer, nullable=False, default=0)
    heading = Column(Text, nullable=False, default="")
    text = Column(Text, nullable=False)
    vector_id = Column(String(64), nullable=True)
    embedding_model = Column(String(64), nullable=False, default="nomic-embed-text")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("uq_document_chunks_doc_chunk", "document_id", "chunk_index", unique=True),
        Index("ix_document_chunks_document_id", "document_id"),
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
        """Create tables, extensions, and indices if they do not exist."""
        try:
            if not self.is_sqlite:
                with self.engine.begin() as conn:
                    try:
                        conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent;"))
                    except Exception as e:
                        logger.debug("unaccent extension creation notice: %s", str(e))

            Base.metadata.create_all(self.engine)

            if not self.is_sqlite:
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_documents_hash_active
                            ON ingestion_documents (content_hash)
                            WHERE status <> 'failed';
                            """
                        )
                    )
                    # Add tsvector generated column and GIN index if not present
                    try:
                        conn.execute(
                            text(
                                """
                                DO $$
                                BEGIN
                                    IF NOT EXISTS (
                                        SELECT 1 FROM information_schema.columns 
                                        WHERE table_name='document_chunks' AND column_name='tsv'
                                    ) THEN
                                        ALTER TABLE document_chunks 
                                        ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', unaccent(text))) STORED;
                                        CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING GIN(tsv);
                                    END IF;
                                END $$;
                                """
                            )
                        )
                    except Exception as e:
                        logger.debug("TSV column creation notice: %s", str(e))

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

    def save_full_document(
        self,
        document_id: str,
        filename: str,
        full_text: str,
        page_count: int,
        file_size_bytes: int,
        file_type: str,
    ) -> None:
        """Save or replace full document text in PostgreSQL."""
        with self.SessionLocal() as session:
            existing = session.get(FullDocument, document_id)
            now = datetime.now(timezone.utc)
            if existing:
                existing.filename = filename
                existing.full_text = full_text
                existing.page_count = page_count
                existing.file_size_bytes = file_size_bytes
                existing.file_type = file_type
                existing.created_at = now
            else:
                doc = FullDocument(
                    document_id=document_id,
                    filename=filename,
                    full_text=full_text,
                    page_count=page_count,
                    file_size_bytes=file_size_bytes,
                    file_type=file_type,
                    created_at=now,
                )
                session.add(doc)
            session.commit()

    def save_document_chunks(
        self,
        document_id: str,
        filename: str,
        chunks: list[dict],
        embedding_model: str = "nomic-embed-text",
    ) -> None:
        """Save chunked representations to PostgreSQL."""
        if not chunks:
            return
        with self.SessionLocal() as session:
            session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            now = datetime.now(timezone.utc)
            chunk_objs = []
            for c in chunks:
                chunk_id = f"{document_id}_chunk_{c.get('chunk_index', 0)}"
                chunk_objs.append(
                    DocumentChunk(
                        id=chunk_id,
                        document_id=document_id,
                        filename=filename,
                        chunk_index=c.get("chunk_index", 0),
                        page=c.get("page", 0),
                        heading=c.get("heading", ""),
                        text=c["text"],
                        vector_id=chunk_id,
                        embedding_model=embedding_model,
                        created_at=now,
                    )
                )
            session.add_all(chunk_objs)
            session.commit()

    def get_full_documents(self, document_ids: list[str]) -> list[dict]:
        """Fetch full documents by IDs."""
        if not document_ids:
            return []
        with self.SessionLocal() as session:
            stmt = select(FullDocument).where(FullDocument.document_id.in_(document_ids))
            rows = session.scalars(stmt).all()
            return [
                {
                    "document_id": r.document_id,
                    "filename": r.filename,
                    "full_text": r.full_text,
                    "page_count": r.page_count,
                    "file_size_bytes": r.file_size_bytes,
                    "file_type": r.file_type,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    def has_full_document(self, document_id: str) -> bool:
        """Check if full document exists in PostgreSQL."""
        with self.SessionLocal() as session:
            stmt = select(FullDocument.document_id).where(FullDocument.document_id == document_id)
            return session.scalar(stmt) is not None

    def delete_full_document(self, document_id: str) -> bool:
        """Delete full document from PostgreSQL."""
        with self.SessionLocal() as session:
            doc = session.get(FullDocument, document_id)
            if doc:
                session.delete(doc)
                session.commit()
                return True
            return False

    def delete_document(self, document_id: str) -> Optional[DocumentInfo]:
        """Delete document from state store and return deleted record."""
        with self.SessionLocal() as session:
            doc = session.get(IngestionDocument, document_id)
            if not doc:
                return None
            doc_info = _row_to_doc_info(doc)
            session.delete(doc)
            full_doc = session.get(FullDocument, document_id)
            if full_doc:
                session.delete(full_doc)
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
