"""Embedding client for Ollama.

Generates text embeddings using the nomic-embed-text model via Ollama's
/api/embed endpoint. Supports batch embedding.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Async client for generating embeddings via Ollama."""

    def __init__(
        self,
        ollama_host: str = "http://ollama:11434",
        model: str = "nomic-embed-text",
        timeout: int = 120,
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector as list of floats.
        """
        client = await self._get_client()
        response = await client.post(
            f"{self.ollama_host}/api/embed",
            json={"model": self.model, "input": text},
        )
        response.raise_for_status()
        data = response.json()
        return data["embeddings"][0]

    async def embed_batch(
        self, texts: list[str], batch_size: int = 32
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Ollama's /api/embed supports batch input. We process in sub-batches
        to avoid memory issues with very large document sets.

        Args:
            texts: List of texts to embed.
            batch_size: Number of texts per API call.

        Returns:
            List of embedding vectors.
        """
        all_embeddings: list[list[float]] = []
        client = await self._get_client()

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            logger.debug(
                "Embedding batch %d-%d of %d", i, i + len(batch), len(texts)
            )

            response = await client.post(
                f"{self.ollama_host}/api/embed",
                json={"model": self.model, "input": batch},
            )
            response.raise_for_status()
            data = response.json()
            all_embeddings.extend(data["embeddings"])

        return all_embeddings

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
