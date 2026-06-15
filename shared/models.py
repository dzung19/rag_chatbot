"""Shared Pydantic models for RAG Chatbot microservices."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class IngestionStatusEnum(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    TXT = "txt"
    MD = "md"


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """User chat query."""
    query: str = Field(..., min_length=1, max_length=5000, description="User question")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of context chunks to retrieve")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="LLM temperature")
    stream: bool = Field(default=True, description="Whether to stream the response via SSE")


class SourceDocument(BaseModel):
    """A source document chunk returned alongside the answer."""
    document_id: str
    filename: str
    chunk_index: int
    content: str
    score: float
    page: Optional[int] = None


class ChatResponse(BaseModel):
    """Non-streaming chat response."""
    answer: str
    sources: list[SourceDocument] = []
    query: str
    model: str
    processing_time_ms: float


# ---------------------------------------------------------------------------
# Document Management
# ---------------------------------------------------------------------------

class DocumentUploadResponse(BaseModel):
    """Response after uploading a document for ingestion."""
    document_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    status: IngestionStatusEnum = IngestionStatusEnum.PENDING
    message: str = "Document received and queued for processing."


class DocumentInfo(BaseModel):
    """Metadata about an indexed document."""
    document_id: str
    filename: str
    file_type: DocumentType
    file_size_bytes: int
    chunk_count: int
    status: IngestionStatusEnum
    created_at: datetime
    updated_at: Optional[datetime] = None


class DocumentListResponse(BaseModel):
    """List of indexed documents."""
    documents: list[DocumentInfo] = []
    total: int = 0


class IngestionStatus(BaseModel):
    """Status of a document ingestion task."""
    document_id: str
    status: IngestionStatusEnum
    chunks_processed: int = 0
    total_chunks: int = 0
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class ServiceHealth(BaseModel):
    """Health status of a single service."""
    service: str
    status: str  # "healthy" or "unhealthy"
    version: str = "0.1.0"
    details: Optional[str] = None


class HealthResponse(BaseModel):
    """Aggregated health status."""
    status: str  # "healthy" or "degraded"
    services: list[ServiceHealth] = []
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

class LogQueryRequest(BaseModel):
    """Query parameters for log search."""
    service: Optional[str] = Field(None, description="Filter by service name")
    level: Optional[str] = Field(None, description="Filter by log level (INFO, WARNING, ERROR)")
    search: Optional[str] = Field(None, max_length=500, description="Text search in log messages")
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=1000)


class LogEntry(BaseModel):
    """A single log entry."""
    timestamp: datetime
    service: str
    level: str
    message: str
    request_id: Optional[str] = None
    extra: Optional[dict] = None


class LogQueryResponse(BaseModel):
    """Response for a log query."""
    logs: list[LogEntry] = []
    total: int = 0
