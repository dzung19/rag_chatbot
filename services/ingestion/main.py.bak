"""Document Ingestion Service — Parses, chunks, embeds, and stores documents.

Supports: PDF, DOCX, PPTX, XLSX, TXT, Markdown.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
from chunker import chunk_text
from embeddings import EmbeddingClient

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup_logging("ingestion", os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reconstruct document metadata cache from ChromaDB on startup
    _reconstruct_documents_from_chroma()
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


def _get_chroma_client():
    """Get ChromaDB client."""
    import chromadb

    settings = get_settings()
    # Parse host and port from URL
    host = settings.chroma_host.replace("http://", "").replace("https://", "")
    if ":" in host:
        hostname, port_str = host.split(":", 1)
        port = int(port_str)
    else:
        hostname = host
        port = 8000

    client = chromadb.HttpClient(host=hostname, port=port)
    return client


def _get_collection():
    """Get or create the ChromaDB collection."""
    client = _get_chroma_client()
    settings = get_settings()
    return client.get_or_create_collection(
        name=settings.chroma_collection,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Background Ingestion Task
# ---------------------------------------------------------------------------

async def _process_document(
    document_id: str,
    file_path: Path,
    original_filename: str,
    doc_type: DocumentType,
) -> None:
    """Background task: parse, chunk, embed, and store a document."""
    task_status = _ingestion_tasks.get(document_id)
    if task_status:
        task_status.status = IngestionStatusEnum.PROCESSING

    try:
        # 1. Parse document
        logger.info("Parsing document %s (%s)", document_id, original_filename)
        text_pages = list(parse_document(str(file_path), doc_type.value))

        if not text_pages:
            raise ValueError("No text extracted from document.")

        full_text = "\n\n".join(text_pages)

        # 2. Chunk text
        settings = get_settings()
        chunks = chunk_text(
            full_text,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        logger.info("Document %s split into %d chunks", document_id, len(chunks))

        if task_status:
            task_status.total_chunks = len(chunks)

        # 3. Generate embeddings
        embed_client = _get_embed_client()
        chunk_texts = [c["text"] for c in chunks]
        embeddings = await embed_client.embed_batch(chunk_texts)
        logger.info("Generated %d embeddings for document %s", len(embeddings), document_id)

        # 4. Store in ChromaDB
        collection = _get_collection()
        ids = [f"{document_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "document_id": document_id,
                "filename": original_filename,
                "chunk_index": c["chunk_index"],
                "page": c.get("page", 0),
                "source": original_filename,
            }
            for c in chunks
        ]

        # Batch upsert (ChromaDB handles batching internally)
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunk_texts,
            metadatas=metadatas,
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
                    c["text"]
                )
                for c in chunks
            ]
            sqlite_conn.executemany(
                "INSERT INTO document_chunks (id, document_id, filename, text) VALUES (?, ?, ?, ?)",
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
        if task_status:
            task_status.status = IngestionStatusEnum.FAILED
            task_status.error_message = str(e)
        if document_id in _documents:
            _documents[document_id].status = IngestionStatusEnum.FAILED


def _reconstruct_documents_from_chroma() -> None:
    """Reconstruct in-memory document metadata from ChromaDB on startup."""
    try:
        collection = _get_collection()
        # Query ChromaDB for all document metadatas
        results = collection.get(include=["metadatas"])
        metadatas = results.get("metadatas", [])
        
        # Group by document_id to find unique documents
        doc_groups = {}
        for meta in metadatas:
            if not meta:
                continue
            doc_id = meta.get("document_id")
            filename = meta.get("filename")
            if doc_id and filename:
                doc_groups[doc_id] = filename
                
        # Reconstruct DocumentInfo for each unique document
        for doc_id, filename in doc_groups.items():
            ext = Path(filename).suffix.lower()
            file_path = UPLOAD_DIR / f"{doc_id}{ext}"
            size_bytes = 0
            created_at = datetime.now(timezone.utc)
            if file_path.exists():
                size_bytes = file_path.stat().st_size
                created_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                
            # Count the number of chunks for this document in the database
            chunk_count = sum(1 for m in metadatas if m and m.get("document_id") == doc_id)
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
            logger.info("Successfully reconstructed metadata for %d documents from ChromaDB.", len(doc_groups))
    except Exception as e:
        logger.error("Failed to reconstruct document metadata from ChromaDB: %s", str(e))


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

    # 6. Create ingestion task tracker
    _ingestion_tasks[document_id] = IngestionStatus(
        document_id=document_id,
        status=IngestionStatusEnum.PENDING,
    )

    # 7. Queue background processing
    background_tasks.add_task(
        _process_document, document_id, file_path, file.filename, doc_type
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

    # Remove from ChromaDB
    try:
        collection = _get_collection()
        # Get all chunk IDs for this document
        results = collection.get(
            where={"document_id": document_id},
            include=[],
        )
        if results["ids"]:
            collection.delete(ids=results["ids"])
            logger.info(
                "Deleted %d chunks for document %s", len(results["ids"]), document_id
            )
    except Exception as e:
        logger.error("Failed to delete from ChromaDB: %s", str(e))

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
    _documents.pop(document_id, None)
    _ingestion_tasks.pop(document_id, None)

    return JSONResponse(content={"message": "Document deleted successfully."})


@app.get("/ingest/health", response_model=ServiceHealth)
async def health_check():
    """Health check endpoint."""
    chroma_ok = False
    try:
        client = _get_chroma_client()
        client.heartbeat()
        chroma_ok = True
    except Exception:
        pass

    return ServiceHealth(
        service="ingestion",
        status="healthy" if chroma_ok else "degraded",
        version="0.1.0",
        details=f"ChromaDB: {'connected' if chroma_ok else 'unavailable'}",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
