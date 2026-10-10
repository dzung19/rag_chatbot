"""Document Ingestion Service — Parses, chunks, embeds, and stores documents.

Supports: PDF, DOCX, PPTX, XLSX, TXT, Markdown.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from qdrant_client.models import Filter, FieldCondition, MatchValue

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    status,
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
from shared.sqlite_db import get_sqlite_connection

from document_parser import parse_document
from chunker import chunk_text, chunk_document_pages
from embeddings import EmbeddingClient

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup_logging("ingestion", os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reconstruct document metadata cache from Qdrant on startup
    _reconstruct_documents_from_qdrant()
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

# In-memory document metadata store (replace with DB in production)
_documents: dict[str, DocumentInfo] = {}
_ingestion_tasks: dict[str, IngestionStatus] = {}

# Embedding client singleton
_embed_client: Optional[EmbeddingClient] = None
_hash_index: dict[str, str] = {}


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



# ---------------------------------------------------------------------------
# Background Ingestion Task
# ---------------------------------------------------------------------------

async def _process_document(
    document_id: str,
    file_path: Path,
    original_filename: str,
    doc_type: DocumentType,
    content_hash: str,
) -> None:
    """Background task: parse, chunk, embed, and store a document."""
    task_status = _ingestion_tasks.get(document_id)
    if task_status:
        task_status.status = IngestionStatusEnum.PROCESSING

    try:
        # 1. Parse document (yields per page/slide/sheet)
        logger.info("Parsing document %s (%s)", document_id, original_filename)
        text_pages = list(parse_document(str(file_path), doc_type.value))

        if not text_pages:
            raise ValueError("No text extracted from document.")

        # 2. Chunk text per-page using Strategy 5 (structure-aware + contextual headers)
        settings = get_settings()
        chunks = chunk_document_pages(
            pages=text_pages,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            source_filename=original_filename,
        )
        logger.info(
            "Document %s (%d pages) split into %d chunks",
            document_id,
            len(text_pages),
            len(chunks),
        )

        if task_status:
            task_status.total_chunks = len(chunks)

        # 3. Generate embeddings
        embed_client = _get_embed_client()
        chunk_texts = [c["text"] for c in chunks]
        embeddings = await embed_client.embed_batch(chunk_texts)
        logger.info("Generated %d embeddings for document %s", len(embeddings), document_id)

        # 4. Store in Qdrant with enriched metadata & batching
        client = _get_qdrant_client()
        _ensure_collection()
        ingested_at = datetime.now(timezone.utc).isoformat()
        
        from qdrant_client.models import PointStruct
        
        points = []
        for i, c in enumerate(chunks):
            points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}_chunk_{c['chunk_index']}")),
                    vector=embeddings[i],
                    payload={
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
                        "content_hash": content_hash
                    }
                )
            )

        # Batch upsert to Qdrant
        qdrant_batch_size = 100
        for i in range(0, len(points), qdrant_batch_size):
            b_slice = slice(i, i + qdrant_batch_size)
            client.upsert(
                collection_name=settings.qdrant_collection,
                points=points[b_slice]
            )

        # 4.5. Store in SQLite FTS5 for Hybrid Search
        try:
            sqlite_conn = get_sqlite_connection()
            # Delete old chunks if re-ingesting
            sqlite_conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
            
            sqlite_data = [
                (
                    f"{document_id}_chunk_{c['chunk_index']}", 
                    document_id, 
                    original_filename, 
                    c.get("page", 0),
                    c.get("heading", ""),
                    c["text"]
                )
                for c in chunks
            ]
            sqlite_conn.executemany(
                "INSERT INTO document_chunks (id, document_id, filename, page, heading, text) VALUES (?, ?, ?, ?, ?, ?)",
                sqlite_data
            )
            sqlite_conn.commit()
            sqlite_conn.close()
        except Exception as sqlite_err:
            logger.error("Failed to insert into SQLite FTS5: %s", str(sqlite_err))
            # Continue even if SQLite fails so ChromaDB ingestion completes

        # 5. Update status
        if task_status:
            task_status.status = IngestionStatusEnum.COMPLETED
            task_status.chunks_processed = len(chunks)

        if document_id in _documents:
            _documents[document_id].status = IngestionStatusEnum.COMPLETED
            _documents[document_id].chunk_count = len(chunks)
            _documents[document_id].updated_at = datetime.now(timezone.utc)

        logger.info(
            "Document %s ingested successfully: %d chunks stored",
            document_id,
            len(chunks),
        )

    except Exception as e:
        logger.error("Failed to ingest document %s: %s", document_id, str(e))
        if _hash_index.get(content_hash) == document_id:
            _hash_index.pop(content_hash, None)
        if task_status:
            task_status.status = IngestionStatusEnum.FAILED
            task_status.error_message = str(e)
        if document_id in _documents:
            _documents[document_id].status = IngestionStatusEnum.FAILED


def _reconstruct_documents_from_qdrant() -> None:
    """Reconstruct in-memory document metadata from Qdrant on startup."""
    try:
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

        # Reconstruct DocumentInfo for each unique document
        for doc_id, filename in doc_groups.items():
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

            if content_hash:
                if content_hash in _hash_index:
                    logger.warning(
                        "Duplicate document detected: %s ('%s') duplicates %s. "
                        "Consider deleting it.",
                        doc_id,
                        filename,
                        _hash_index[content_hash],
                    )
                else:
                    _hash_index[content_hash] = doc_id
            size_bytes = 0
            created_at = datetime.now(timezone.utc)
            if file_path.exists():
                size_bytes = file_path.stat().st_size
                created_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                
            # Note: Count might be hard to calculate precisely without a DB-side aggregation
            # Let's approximate or leave at 0 if exact count is not necessary.
            # To be accurate we could issue a count query per doc_id.
            # But since we just need basic info, let's just create it with chunk_count=0
            # or do a count request
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
            
            _documents[doc_id] = DocumentInfo(
                document_id=doc_id,
                filename=filename,
                file_type=doc_type,
                file_size_bytes=size_bytes,
                chunk_count=chunk_count,
                status=IngestionStatusEnum.COMPLETED,
                created_at=created_at,
            )
            # Reconstruct the task tracking status as completed
            _ingestion_tasks[doc_id] = IngestionStatus(
                document_id=doc_id,
                status=IngestionStatusEnum.COMPLETED,
                chunks_processed=chunk_count,
                total_chunks=chunk_count,
            )
        if doc_groups:
            logger.info("Successfully reconstructed metadata for %d documents from Qdrant.", len(doc_groups))
    except Exception as e:
        logger.error("Failed to reconstruct document metadata from Qdrant: %s", str(e))


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
    existing_id = _hash_index.get(content_hash)
    if existing_id:
        existing = _documents.get(existing_id)
        if existing and existing.status != IngestionStatusEnum.FAILED:
            logger.info(
                "Duplicate upload '%s' -> reuse document %s",
                file.filename,
                existing_id,
            )
            return DocumentUploadResponse(
                document_id=existing_id,
                filename=existing.filename,
                status=existing.status,
            )
        # Bản cũ lỗi hoặc không còn: cho phép upload lại
        _hash_index.pop(content_hash, None)

    # 4. Save with UUID filename (original name stored in metadata only)
    document_id = str(uuid.uuid4())
    safe_filename = f"{document_id}{ext}"
    file_path = UPLOAD_DIR / safe_filename

    with open(file_path, "wb") as f:
        f.write(content)

    logger.info(
        "File uploaded: %s → %s (%d bytes)",
        file.filename,
        safe_filename,
        len(content),
    )

    # 5. Create metadata record
    doc_info = DocumentInfo(
        document_id=document_id,
        filename=file.filename,
        file_type=doc_type,
        file_size_bytes=len(content),
        chunk_count=0,
        status=IngestionStatusEnum.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    _documents[document_id] = doc_info
    _hash_index[content_hash] = document_id
    # 6. Create ingestion task tracker
    _ingestion_tasks[document_id] = IngestionStatus(
        document_id=document_id,
        status=IngestionStatusEnum.PENDING,
    )

    # 7. Queue background processing
    background_tasks.add_task(
        _process_document, document_id, file_path, file.filename, doc_type, content_hash
    )

    return DocumentUploadResponse(
        document_id=document_id,
        filename=file.filename,
        status=IngestionStatusEnum.PENDING,
    )


@app.get("/ingest/documents", response_model=DocumentListResponse)
async def list_documents():
    """List all indexed documents."""
    docs = list(_documents.values())
    return DocumentListResponse(documents=docs, total=len(docs))


@app.get("/ingest/documents/{document_id}/status", response_model=IngestionStatus)
async def get_ingestion_status(document_id: str):
    """Get the ingestion status for a document."""
    if document_id not in _ingestion_tasks:
        raise HTTPException(status_code=404, detail="Document not found.")
    return _ingestion_tasks[document_id]


@app.delete("/ingest/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document and its embeddings from the vector store."""
    if document_id not in _documents:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Remove from Qdrant
    try:
        client = _get_qdrant_client()
        settings = get_settings()
        
        client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id)
                    )
                ]
            )
        )
        logger.info("Deleted chunks for document %s from Qdrant", document_id)
    except Exception as e:
        logger.error("Failed to delete from Qdrant: %s", str(e))

    # Remove from SQLite FTS5
    try:
        sqlite_conn = get_sqlite_connection()
        sqlite_conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        sqlite_conn.commit()
        sqlite_conn.close()
    except Exception as e:
        logger.error("Failed to delete from SQLite FTS5: %s", str(e))

    # Remove uploaded file
    doc_info = _documents[document_id]
    ext = f".{doc_info.file_type.value}"
    file_path = UPLOAD_DIR / f"{document_id}{ext}"
    if file_path.exists():
        file_path.unlink()

    # Remove metadata
    for h, d in list(_hash_index.items()):
        if d == document_id:
            del _hash_index[h]
    _documents.pop(document_id, None)
    _ingestion_tasks.pop(document_id, None)

    return JSONResponse(content={"message": "Document deleted successfully."})


@app.get("/ingest/health", response_model=ServiceHealth)
async def health_check():
    """Health check endpoint."""
    qdrant_ok = False
    try:
        client = _get_qdrant_client()
        client.get_collections()
        qdrant_ok = True
    except Exception:
        pass

    return ServiceHealth(
        service="ingestion",
        status="healthy" if qdrant_ok else "degraded",
        version="0.1.0",
        details=f"Qdrant: {'connected' if qdrant_ok else 'unavailable'}",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
