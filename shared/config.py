"""Centralized configuration for RAG Chatbot services.

Uses Pydantic BaseSettings to load from environment variables.
Secrets follow the multi-tiered fallback pattern:
  env var → file on disk → ephemeral random value + severe warning.
"""

from __future__ import annotations

import logging
import os
import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field

logger = logging.getLogger(__name__)


def _resolve_secret(env_key: str, file_path: str | None = None) -> str:
    """Resolve a secret using multi-tiered fallback.

    Resolution order:
    1. Environment variable
    2. File on disk (optional)
    3. Ephemeral random value + severe warning (not suitable for production
       or horizontally scaled deployments)
    """
    value = os.environ.get(env_key)
    if value:
        return value

    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                value = f.read().strip()
            if value:
                return value
        except OSError:
            pass

    ephemeral = secrets.token_hex(32)
    logger.warning(
        "Secret '%s' not found in environment or file. "
        "Generating EPHEMERAL value. This instance is ISOLATED — "
        "not suitable for production or horizontal scaling.",
        env_key,
    )
    return ephemeral


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Authentication ---
    api_key: str = Field(
        default_factory=lambda: _resolve_secret("API_KEY", "api_key.txt"),
        description="API key for authenticating requests",
    )

    # --- LLM ---
    ollama_host: str = Field(default="http://ollama:11434")
    ollama_model: str = Field(default="gemma4:e2b")
    ollama_embed_model: str = Field(default="nomic-embed-text")
    ollama_timeout: int = Field(default=300, description="LLM request timeout in seconds")

    # --- ChromaDB ---
    chroma_host: str = Field(default="http://localhost:8003")
    chroma_collection: str = Field(default="rag_documents")

    # --- Document Processing ---
    chunk_size: int = Field(default=1000, ge=100, le=5000)
    chunk_overlap: int = Field(default=200, ge=0, le=1000)
    max_upload_size_mb: int = Field(default=50, ge=1, le=200)
    sqlite_db_path: str = Field(default="/app/shared_data/rag_hybrid.db")

    # --- Downstream Service URLs ---
    ingestion_service_url: str = Field(default="http://localhost:8001")
    rag_engine_service_url: str = Field(default="http://rag-engine:8002")
    onedrive_connector_url: str = Field(default="http://onedrive-connector:8004")
    gateway_url: str = Field(default="http://gateway:8000")

    # --- CORS ---
    cors_origins: str = Field(default="http://localhost:3000,http://127.0.0.1:3000")

    # --- Logging ---
    log_level: str = Field(default="INFO")
    loki_url: str = Field(default="http://loki:3100")

    # --- OneDrive/SharePoint (Phase 2) ---
    azure_tenant_id: str | None = Field(default=None)
    azure_client_id: str | None = Field(default=None)
    azure_client_secret: str | None = Field(default=None)
    sharepoint_domain: str | None = Field(default=None)
    sharepoint_site_path: str | None = Field(default=None)
    sharepoint_drive_name: str | None = Field(default=None)
    
    # --- Local Sync (Phase 2 Fallback) ---
    sync_source_type: str = Field(default="azure")
    local_sync_path: str | None = Field(default=None)

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def local_sync_paths_list(self) -> list[str]:
        if not self.local_sync_path:
            return []
        return [p.strip() for p in self.local_sync_path.split(",") if p.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
