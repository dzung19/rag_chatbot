"""Document Ingestion Service — Parses, chunks, embeds, and stores documents.

Supports: PDF, DOCX, PPTX, XLSX, TXT, Markdown.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
import hashlib
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from qdrant_client.models import Filter, FieldCondition, MatchValue
import hmac
from orphan_cleanup import run_orphan_cleanup
from dataclasses import asdict


from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    status,
    Header, Query
)
from fastapi.responses import JSONResponse

# Add parent directory for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.config import Settings, get_settings
from shared.logging_config import RequestLoggingMiddleware, setup_logging
from shared.models import (
    DocumentInfo,
    DocumentListResponse,
    DocumentType,
    DocumentUploadResponse,
    IngestionStatus,
    IngestionStatusEnum,
    ServiceHealth,
)
from shared.security import SecurityHeadersMiddleware
from shared.sqlite_db import (
    get_sqlite_connection,
    save_document_full_and_chunks,
    delete_document_from_sqlite,
)

from document_parser import parse_document
from chunker import chunk_text, chunk_document_pages
from embeddings import EmbeddingClient
from state_store import get_state_store

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup_logging("ingestion", os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize PostgreSQL state store tables & partial indices
    store = get_state_store()
    await asyncio.to_thread(store.init_db)

    # 2. Mark any dangling/interrupted tasks from previous crash as failed
    await asyncio.to_thread(store.clean_interrupted_tasks, grace_minutes=10)

    # 3. Backfill document metadata from Qdrant into PostgreSQL if missing
    await asyncio.to_thread(_backfill_from_qdrant)
    yield

app = FastAPI(
    title="Document Ingestion Service",
    version="0.1.0",
    docs_url="/ingest/docs",
    openapi_url="/ingest/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(RequestLoggingMiddleware, service_name="ingestion")
app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Constants & State
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS: dict[str, DocumentType] = {
    ".pdf": DocumentType.PDF,
    ".docx": DocumentType.DOCX,
    ".pptx": DocumentType.PPTX,
    ".xlsx": DocumentType.XLSX,
    ".txt": DocumentType.TXT,
    ".md": DocumentType.MD,
}

# Magic bytes for file type verification
MAGIC_BYTES: dict[str, list[bytes]] = {
    ".pdf": [b"%PDF"],
    ".docx": [b"PK\x03\x04"],  # ZIP-based Office format
    ".pptx": [b"PK\x03\x04"],
    ".xlsx": [b"PK\x03\x04"],
}

# Upload directory — outside web root
try:
    UPLOAD_DIR = Path("/app/uploaded_docs")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploaded_docs"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Embedding client singleton
_embed_client: Optional[EmbeddingClient] = None


def _get_embed_client() -> EmbeddingClient:
    global _embed_client
    if _embed_client is None:
        settings = get_settings()
        _embed_client = EmbeddingClient(
            ollama_host=settings.ollama_host,
            model=settings.ollama_embed_model,
        )
    return _embed_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_file_extension(filename: str) -> DocumentType:
    """Validate file extension against allow-list."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {ext}. Allowed: {list(ALLOWED_EXTENSIONS.keys())}",
        )
    return ALLOWED_EXTENSIONS[ext]


def _validate_magic_bytes(content: bytes, extension: str) -> bool:
    """Validate file content matches expected magic bytes."""
    if extension not in MAGIC_BYTES:
        return True  # No magic bytes check for TXT/MD
    return any(content.startswith(magic) for magic in MAGIC_BYTES[extension])


def _get_qdrant_client():
    """Get Qdrant client."""
    from qdrant_client import QdrantClient
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return client

_qdrant_collection_checked = False

def _compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def _ensure_collection():
    """Ensure Qdrant collection and payload indexes exist."""
    global _qdrant_collection_checked
    if not _qdrant_collection_checked:
        client = _get_qdrant_client()
        settings = get_settings()
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        if not client.collection_exists(settings.qdrant_collection):
            client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )

        for field in ("document_id", "content_hash"):
            try:
                client.create_payload_index(
                    collection_name=settings.qdrant_collection,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass  # Index đã tồn tại

        _qdrant_collection_checked = True

class DocumentCancelledError(Exception):
    """Document bị xóa trong lúc đang ingest."""


async def _ensure_not_cancelled(document_id: str) -> None:
    is_cancelled = await asyncio.to_thread(get_state_store().is_cancelled, document_id)
    if is_cancelled:
        raise DocumentCancelledError(document_id)

# ---------------------------------------------------------------------------
# Background Ingestion Task
# ---------------------------------------------------------------------------

async def _process_document(
    document_id: str,
    file_path: Path,
    original_filename: str,
    doc_type: DocumentType,
    content_hash: str,
    chunks: Optional[list[dict]] = None,
    text_pages: Optional[list[str]] = None,
    sqlite_saved: bool = False,
) -> None:
    """Background task: parse, chunk, embed, and store a document."""
    store = get_state_store()

    try:
        settings = get_settings()

        # 1 & 2. Parse and chunk only if not already provided
        if chunks is None or text_pages is None:
            logger.info("Parsing document %s (%s)", document_id, original_filename)
            if doc_type == DocumentType.XLSX:
                from excel_parser import parse_and_chunk_excel
                text_pages, chunks = await asyncio.to_thread(
                    parse_and_chunk_excel,
                    file_path=str(file_path),
                    source_filename=original_filename,
                    chunk_chars=settings.excel_chunk_chars,
                    max_rows_per_sheet=settings.excel_max_rows_per_sheet,
                    max_cols=settings.excel_max_cols,
                    full_index_rows=settings.excel_full_index_rows,
                    max_chunks_per_file=settings.excel_max_chunks_per_file,
                    table_gap_rows=settings.excel_table_gap_rows,
                    sparse_threshold=settings.excel_sparse_threshold,
                )
            else:
                def _parse_and_chunk_sync():
                    pages = list(parse_document(str(file_path), doc_type.value))
                    if not pages:
                        raise ValueError("No text extracted from document.")
                    ch = chunk_document_pages(
                        pages=pages,
                        chunk_size=settings.chunk_size,
                        chunk_overlap=settings.chunk_overlap,
                        source_filename=original_filename,
                    )
                    return pages, ch

                text_pages, chunks = await asyncio.to_thread(_parse_and_chunk_sync)
            logger.info(
                "Document %s (%d pages) split into %d chunks",
                document_id,
                len(text_pages),
                len(chunks),
            )

        await asyncio.to_thread(
            store.update_progress,
            document_id,
            chunks_processed=0,
            total_chunks=len(chunks),
        )

        # 3. Generate embeddings with batching and progress tracking
        embed_client = _get_embed_client()
        chunk_texts = [c["text"] for c in chunks]

        async def update_progress(done: int, total: int):
            await asyncio.to_thread(
                store.update_progress,
                document_id,
                chunks_processed=done,
                total_chunks=total,
            )

        embeddings = await embed_client.embed_batch(
            chunk_texts,
            batch_size=settings.embed_batch_size,
            on_progress=update_progress,
        )
        logger.info("Generated %d embeddings for document %s", len(embeddings), document_id)

        # 4. Store in Qdrant with enriched metadata & batching
        client = _get_qdrant_client()
        _ensure_collection()
        ingested_at = datetime.now(timezone.utc).isoformat()

        from qdrant_client.models import PointStruct

        points = []
        for i, c in enumerate(chunks):
            payload = {
                "document_id": document_id,
                "filename": original_filename,
                "source": original_filename,
                "chunk_index": c["chunk_index"],
                "total_chunks": len(chunks),
                "page": c.get("page", 0),
                "heading": c.get("heading", ""),
                "file_type": doc_type.value,
                "ingested_at": ingested_at,
                "text": chunk_texts[i],
                "content_hash": content_hash,
            }
            # Carry over custom metadata (sheet, row_start, row_end, etc.)
            if "metadata" in c and isinstance(c["metadata"], dict):
                for meta_k, meta_v in c["metadata"].items():
                    if meta_k not in payload:
                        payload[meta_k] = meta_v

            points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}_chunk_{c['chunk_index']}")),
                    vector=embeddings[i],
                    payload=payload,
                )
            )

        await _ensure_not_cancelled(document_id)

        # Batch upsert to Qdrant
        qdrant_batch_size = 100
        for i in range(0, len(points), qdrant_batch_size):
            b_slice = slice(i, i + qdrant_batch_size)
            await asyncio.to_thread(
                client.upsert,
                collection_name=settings.qdrant_collection, 
                points=points[b_slice], 
                wait = True
            )

        # 4.5. Ensure SQLite and PostgreSQL have both full_documents and document_chunks
        if not sqlite_saved:
            full_text = "\n\n".join(p.strip() for p in text_pages if p and p.strip())
            await asyncio.to_thread(
                store.save_full_document,
                document_id=document_id,
                filename=original_filename,
                full_text=full_text,
                page_count=len(text_pages),
                file_size_bytes=file_path.stat().st_size if file_path.exists() else 0,
                file_type=doc_type.value,
            )
            await asyncio.to_thread(
                save_document_full_and_chunks,
                document_id=document_id,
                filename=original_filename,
                full_text=full_text,
                page_count=len(text_pages),
                file_size_bytes=file_path.stat().st_size if file_path.exists() else 0,
                file_type=doc_type.value,
                chunks=chunks,
            )
        else:
            logger.info("Storage already populated at upload, skipping. document_id=%s",
                document_id,)

        await _ensure_not_cancelled(document_id)

        # 5. Update status
        await asyncio.to_thread(store.mark_completed, document_id, len(chunks))

        logger.info(
            "Document %s ingested successfully: %d chunks stored",
            document_id,
            len(chunks),
        )

    except DocumentCancelledError:
        logger.info("Ingestion cancelled because document was deleted: %s", document_id)
        # Dọn những gì đã ghi sau khi endpoint delete chạy
        try:
            await asyncio.to_thread(
                _get_qdrant_client().delete,
                collection_name=get_settings().qdrant_collection,
                points_selector=Filter(must=[
                    FieldCondition(key="document_id", match=MatchValue(value=document_id))
                ]),
                wait=True,
            )
            await asyncio.to_thread(delete_document_from_sqlite, document_id)
        except Exception:
            logger.exception("Cleanup after cancel failed: %s", document_id)
        # Không gán FAILED, không thêm lại vào store vì row đã bị xóa

    except Exception as e:
        logger.error("Failed to ingest document %s: %s", document_id, str(e))

        try:
            await asyncio.to_thread(
                _get_qdrant_client().delete,
                collection_name=get_settings().qdrant_collection,
                points_selector=Filter(must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id),
                    )
                ]),
                wait=True,
        )
        except Exception:
            logger.exception("Failed to clean Qdrant for document %s", document_id)

        try:
            await asyncio.to_thread(delete_document_from_sqlite, document_id)
        except Exception:
            logger.exception(
                "Failed to clean SQLite for document %s", document_id
            )

        await asyncio.to_thread(store.mark_failed, document_id, str(e))


def _backfill_from_qdrant() -> None:
    """Backfill document metadata from Qdrant into PostgreSQL if missing."""
    try:
        store = get_state_store()
        existing_docs = {d.document_id for d in store.list_documents()}

        client = _get_qdrant_client()
        settings = get_settings()
        _ensure_collection()

        # Qdrant scroll API to fetch all payload points
        offset = None
        doc_groups = {}
        doc_hashes: dict[str, str] = {}

        while True:
            points, offset = client.scroll(
                collection_name=settings.qdrant_collection,
                limit=1000,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )

            for point in points:
                meta = point.payload
                if not meta or "document_id" not in meta:
                    continue
                doc_id = meta["document_id"]
                filename = meta.get("filename")
                if doc_id and filename:
                    doc_groups[doc_id] = filename
                    if meta.get("content_hash"):
                        doc_hashes[doc_id] = meta["content_hash"]

            if offset is None:
                break

        backfilled_count = 0
        for doc_id, filename in doc_groups.items():
            if doc_id in existing_docs:
                continue

            ext = Path(filename).suffix.lower()
            file_path = UPLOAD_DIR / f"{doc_id}{ext}"
            doc_filter = Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=doc_id))]
            )

            # Backfill hash cho tài liệu cũ chưa có
            content_hash = doc_hashes.get(doc_id)
            if not content_hash and file_path.exists():
                content_hash = _compute_hash(file_path.read_bytes())
                client.set_payload(
                    collection_name=settings.qdrant_collection,
                    payload={"content_hash": content_hash},
                    points=doc_filter,
                )

            size_bytes = 0
            created_at = datetime.now(timezone.utc)
            if file_path.exists():
                size_bytes = file_path.stat().st_size
                created_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)

            count_result = client.count(
                collection_name=settings.qdrant_collection,
                count_filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=doc_id)
                        )
                    ]
                )
            )
            chunk_count = count_result.count
            doc_type = ALLOWED_EXTENSIONS.get(ext, DocumentType.TXT)

            store.upsert_reconstructed_document(
                document_id=doc_id,
                filename=filename,
                file_type=doc_type.value,
                file_size_bytes=size_bytes,
                content_hash=content_hash or f"legacy_{doc_id}",
                chunk_count=chunk_count,
                created_at=created_at,
            )
            backfilled_count += 1

        if backfilled_count > 0:
            logger.info("Successfully backfilled metadata for %d documents from Qdrant into PostgreSQL.", backfilled_count)
    except Exception as e:
        logger.error("Failed to backfill document metadata from Qdrant: %s", str(e))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/ingest/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
):
    """Upload a document for ingestion into the RAG pipeline."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    # 1. Validate extension
    doc_type = _validate_file_extension(file.filename)

    # 2. Read content with size limit
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size: {settings.max_upload_size_mb}MB.",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    # 3. Validate magic bytes
    ext = Path(file.filename).suffix.lower()
    if not _validate_magic_bytes(content, ext):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match its extension. Possible file type mismatch.",
        )
    
    content_hash = _compute_hash(content)
    store = get_state_store()

    # Check for existing document in PostgreSQL
    existing = await asyncio.to_thread(store.get_document_by_hash, content_hash)
    if existing and existing.status != IngestionStatusEnum.FAILED:
        in_storage = await asyncio.to_thread(store.has_full_document, existing.document_id)
        if not in_storage:
            def _check_sqlite_exists(d_id: str) -> bool:
                conn = get_sqlite_connection()
                try:
                    cur = conn.execute("SELECT 1 FROM full_documents WHERE document_id = ?", (d_id,))
                    return cur.fetchone() is not None
                finally:
                    conn.close()

            in_storage = await asyncio.to_thread(_check_sqlite_exists, existing.document_id)

        if in_storage:
            logger.info(
                "Duplicate upload '%s' -> reuse document %s (verified in storage)",
                file.filename,
                existing.document_id,
            )
            return DocumentUploadResponse(
                document_id=existing.document_id,
                filename=existing.filename,
                status=existing.status,
            )

    # 4. Save with UUID filename (original name stored in metadata only)
    document_id = str(uuid.uuid4())
    safe_filename = f"{document_id}{ext}"
    file_path = UPLOAD_DIR / safe_filename

    await asyncio.to_thread(file_path.write_bytes, content)

    # 4.1 Parse text and commit to PostgreSQL & SQLite immediately (instant availability)
    try:
        if doc_type == DocumentType.XLSX:
            from excel_parser import parse_and_chunk_excel

            text_pages, chunks = await asyncio.to_thread(
                parse_and_chunk_excel,
                file_path=str(file_path),
                source_filename=file.filename,
                chunk_chars=settings.excel_chunk_chars,
                max_rows_per_sheet=settings.excel_max_rows_per_sheet,
                max_cols=settings.excel_max_cols,
                full_index_rows=settings.excel_full_index_rows,
                max_chunks_per_file=settings.excel_max_chunks_per_file,
                table_gap_rows=settings.excel_table_gap_rows,
                sparse_threshold=settings.excel_sparse_threshold,
            )
        else:
            def _parse_and_chunk():
                pages = list(parse_document(str(file_path), doc_type.value))
                if not pages:
                    raise ValueError("No text extracted from document.")
                ch = chunk_document_pages(
                    pages=pages,
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                    source_filename=file.filename,
                )
                return pages, ch

            text_pages, chunks = await asyncio.to_thread(_parse_and_chunk)

        if not text_pages:
            raise ValueError("No text extracted from document.")

        full_text = "\n\n".join(p.strip() for p in text_pages if p and p.strip())
        # Save to PostgreSQL full_documents
        await asyncio.to_thread(
            store.save_full_document,
            document_id=document_id,
            filename=file.filename,
            full_text=full_text,
            page_count=len(text_pages),
            file_size_bytes=len(content),
            file_type=doc_type.value,
        )
        # Also save to SQLite (for backward compatibility during migration)
        await asyncio.to_thread(
            save_document_full_and_chunks,
            document_id=document_id,
            filename=file.filename,
            full_text=full_text,
            page_count=len(text_pages),
            file_size_bytes=len(content),
            file_type=doc_type.value,
            chunks=chunks,
        )
    except Exception as e:
        logger.error("Failed to extract and store document text for %s: %s", document_id, str(e), exc_info=True)
        await asyncio.to_thread(file_path.unlink, missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot extract text from file: {str(e)}",
        )

    logger.info(
        "File uploaded and text saved to SQLite: %s → %s (%d bytes, %d pages, %d chunks)",
        file.filename,
        safe_filename,
        len(content),
        len(text_pages),
        len(chunks),
    )

    # 5. Atomically claim document in PostgreSQL
    doc_info, is_new = await asyncio.to_thread(
        store.claim_document,
        document_id=document_id,
        filename=file.filename,
        doc_type=doc_type.value,
        file_size_bytes=len(content),
        content_hash=content_hash,
    )
    if not is_new:
        logger.info("Concurrent duplicate upload detected for %s -> reuse %s", file.filename, doc_info.document_id)
        return DocumentUploadResponse(
            document_id=doc_info.document_id,
            filename=doc_info.filename,
            status=doc_info.status,
        )

    await asyncio.to_thread(
        store.update_progress,
        document_id,
        chunks_processed=0,
        total_chunks=len(chunks),
    )

    # 6. Queue background processing (embeddings and Qdrant vector indexing)
    background_tasks.add_task(
        _process_document,
        document_id,
        file_path,
        file.filename,
        doc_type,
        content_hash,
        chunks=chunks,
        text_pages=text_pages,
        sqlite_saved=True
    )

    return DocumentUploadResponse(
        document_id=document_id,
        filename=file.filename,
        status=IngestionStatusEnum.PROCESSING,
    )


@app.get("/ingest/documents", response_model=DocumentListResponse)
async def list_documents():
    """List all indexed documents."""
    docs = await asyncio.to_thread(get_state_store().list_documents)
    return DocumentListResponse(documents=docs, total=len(docs))


@app.get("/ingest/documents/{document_id}/status", response_model=IngestionStatus)
async def get_ingestion_status(document_id: str):
    """Get the ingestion status for a document."""
    task = await asyncio.to_thread(get_state_store().get_task_status, document_id)
    if not task:
        raise HTTPException(status_code=404, detail="Document not found.")
    return task


@app.delete("/ingest/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document and its embeddings from the vector store."""
    store = get_state_store()
    doc_info = await asyncio.to_thread(store.delete_document, document_id)
    if not doc_info:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Remove from Qdrant
    try:
        client = _get_qdrant_client()
        settings = get_settings()

        await asyncio.to_thread(
            client.delete,
            collection_name=settings.qdrant_collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id)
                    )
                ]
            ),
        )
        logger.info("Deleted chunks for document %s from Qdrant", document_id)
    except Exception as e:
        logger.error("Failed to delete from Qdrant: %s", str(e))

    # Remove from SQLite (full_documents and document_chunks)
    try:
        await asyncio.to_thread(delete_document_from_sqlite, document_id)
    except Exception as e:
        logger.error("Failed to delete from SQLite: %s", str(e))

    # Remove uploaded file
    ext = f".{doc_info.file_type.value}"
    file_path = UPLOAD_DIR / f"{document_id}{ext}"
    if file_path.exists():
        await asyncio.to_thread(file_path.unlink, missing_ok=True)

    return JSONResponse(content={"message": "Document deleted successfully."})

_cleanup_lock = asyncio.Lock()


def _is_processing(doc_id: str) -> bool:
    task = get_state_store().get_task_status(doc_id)
    return task is not None and task.status in (
        IngestionStatusEnum.PENDING,
        IngestionStatusEnum.PROCESSING,
    )

@app.post("/ingest/internal/cleanup")
async def cleanup_orphans(
    dry_run: bool = True,
    grace_hours: int = Query(default=24, ge=6, le=720),
    max_delete: int = Query(default=50, ge=1, le=1000),
    x_internal_service_key: str = Header(default=""),
):
    """Dọn dữ liệu mồ côi giữa Qdrant, SQLite và uploaded_docs."""
    settings = get_settings()
    expected_key = settings.internal_service_key or ""

    if not expected_key or not hmac.compare_digest(
        x_internal_service_key, expected_key
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    if _cleanup_lock.locked():
        raise HTTPException(status_code=409, detail="Cleanup is already running.")

    async with _cleanup_lock:
        report = await asyncio.to_thread(
            run_orphan_cleanup,
            qdrant_client=_get_qdrant_client(),
            collection=settings.qdrant_collection,
            upload_dir=UPLOAD_DIR,
            is_processing=_is_processing,
            dry_run=dry_run,
            grace_hours=grace_hours,
            max_delete=max_delete,
        )

    # Đồng bộ lại trạng thái trong PostgreSQL.
    store = get_state_store()
    for doc_id in report.deleted_ids:
        await asyncio.to_thread(store.delete_document, doc_id)

    return asdict(report)

@app.get("/ingest/health", response_model=ServiceHealth)
async def health_check():
    """Health check endpoint checking Qdrant and PostgreSQL."""
    from sqlalchemy import text

    def _check_qdrant() -> bool:
        try:
            client = _get_qdrant_client()
            client.get_collections()
            return True
        except Exception:
            return False

    def _check_db() -> bool:
        try:
            with get_state_store().engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    qdrant_ok, db_ok = await asyncio.gather(
        asyncio.to_thread(_check_qdrant),
        asyncio.to_thread(_check_db),
    )

    all_ok = qdrant_ok and db_ok
    details = f"Qdrant: {'connected' if qdrant_ok else 'unavailable'} | DB: {'connected' if db_ok else 'unavailable'}"

    return ServiceHealth(
        service="ingestion",
        status="healthy" if all_ok else "degraded",
        version="0.1.0",
        details=details,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
