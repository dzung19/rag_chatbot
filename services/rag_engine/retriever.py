"""ChromaDB retriever for similarity search.

Handles query embedding and vector search with metadata filtering.
"""

from __future__ import annotations

import logging
from typing import Optional

import chromadb
import httpx

logger = logging.getLogger(__name__)


class Retriever:
    """Retrieves relevant document chunks from ChromaDB."""

    def __init__(
        self,
        chroma_host: str = "http://chromadb:8000",
        collection_name: str = "rag_documents",
        ollama_host: str = "http://ollama:11434",
        embed_model: str = "nomic-embed-text",
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.embed_model = embed_model
        self._http_client: Optional[httpx.AsyncClient] = None

        # Parse ChromaDB host
        host = chroma_host.replace("http://", "").replace("https://", "")
        if ":" in host:
            hostname, port_str = host.split(":", 1)
            port = int(port_str)
        else:
            hostname = host
            port = 8000

        self._chroma_client = chromadb.HttpClient(host=hostname, port=port)
        self._collection_name = collection_name

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120)
        return self._http_client

    async def _embed_query(self, query: str) -> list[float]:
        """Generate embedding for a query string."""
        client = await self._get_http_client()
        response = await client.post(
            f"{self.ollama_host}/api/embed",
            json={"model": self.embed_model, "input": query},
        )
        response.raise_for_status()
        data = response.json()
        return data["embeddings"][0]

    async def search(
        self,
        query: str,
        top_k: int = 5,
        where_filter: Optional[dict] = None,
    ) -> list[dict]:
        """Search for relevant chunks.

        Args:
            query: User query string.
            top_k: Number of results to return.
            where_filter: Optional ChromaDB metadata filter.

        Returns:
            List of dicts with 'text', 'metadata', and 'score' keys.
        """
        try:
            # Get query embedding
            query_embedding = await self._embed_query(query)

            # Search ChromaDB
            collection = self._chroma_client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )

            query_params = {
                "query_embeddings": [query_embedding],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
            }
            if where_filter:
                query_params["where"] = where_filter

            results = collection.query(**query_params)

            # Format results
            formatted = []
            if results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    # ChromaDB returns distances; convert cosine distance to similarity score
                    distance = results["distances"][0][i] if results["distances"] else 0
                    score = 1.0 - distance  # cosine similarity

                    formatted.append(
                        {
                            "id": doc_id,
                            "text": results["documents"][0][i] if results["documents"] else "",
                            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                            "score": round(score, 4),
                        }
                    )

            logger.debug(
                "Search for '%s': %d results (top score: %.4f)",
                query[:80],
                len(formatted),
                formatted[0]["score"] if formatted else 0,
            )
            return formatted

        except Exception as e:
            logger.error("ChromaDB search failed: %s", str(e))
            return []

    async def close(self) -> None:
        """Clean up resources."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
