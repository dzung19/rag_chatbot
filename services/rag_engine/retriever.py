"""ChromaDB retriever for similarity search.

Handles query embedding and vector search with metadata filtering.
"""

from __future__ import annotations

import logging
from typing import Optional

import chromadb
import httpx

from shared.sqlite_db import get_sqlite_connection

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

    async def _keyword_search(self, query: str, top_k: int = 20) -> list[dict]:
        """Perform BM25 keyword search using SQLite FTS5."""
        try:
            conn = get_sqlite_connection()
            # FTS5 MATCH requires sanitizing query to avoid syntax errors
            # We strip out non-alphanumeric chars or just wrap in quotes
            # A simple approach: escape double quotes and wrap in quotes
            safe_query = query.replace('"', '""')
            
            cursor = conn.execute(
                "SELECT id, document_id, filename, text, bm25(document_chunks) as rank_score "
                "FROM document_chunks WHERE document_chunks MATCH ? "
                "ORDER BY rank_score ASC LIMIT ?",
                (f'"{safe_query}"', top_k)
            )
            
            results = []
            for row in cursor.fetchall():
                # bm25() returns a negative score in SQLite, more negative is better
                score = abs(row["rank_score"])
                results.append({
                    "id": row["id"],
                    "text": row["text"],
                    "metadata": {"document_id": row["document_id"], "filename": row["filename"]},
                    "score": round(score, 4),
                })
            conn.close()
            return results
        except Exception as e:
            logger.error("Keyword search failed: %s", str(e))
            return []

    async def search(
        self,
        query: str,
        top_k: int = 5,
        where_filter: Optional[dict] = None,
    ) -> list[dict]:
        """Search for relevant chunks using Hybrid Search (Vector + Keyword) and RRF."""
        try:
            # 1. Dense Search (ChromaDB Vector)
            dense_results = []
            try:
                query_embedding = await self._embed_query(query)
                collection = self._chroma_client.get_or_create_collection(
                    name=self._collection_name,
                    metadata={"hnsw:space": "cosine"},
                )

                query_params = {
                    "query_embeddings": [query_embedding],
                    "n_results": 20, # Get more for fusion
                    "include": ["documents", "metadatas", "distances"],
                }
                if where_filter:
                    query_params["where"] = where_filter

                c_results = collection.query(**query_params)

                if c_results["ids"] and c_results["ids"][0]:
                    for i, doc_id in enumerate(c_results["ids"][0]):
                        distance = c_results["distances"][0][i] if c_results["distances"] else 0
                        score = 1.0 - distance
                        dense_results.append({
                            "id": doc_id,
                            "text": c_results["documents"][0][i] if c_results["documents"] else "",
                            "metadata": c_results["metadatas"][0][i] if c_results["metadatas"] else {},
                            "score": score,
                        })
            except Exception as ce:
                logger.error("Dense search failed: %s", str(ce))

            # 2. Sparse Search (SQLite BM25 Keyword)
            sparse_results = await self._keyword_search(query, top_k=20)

            # 3. Reciprocal Rank Fusion (RRF)
            k_rrf = 60
            fused_scores = {}
            chunk_map = {}

            # Score dense
            for rank, chunk in enumerate(dense_results):
                doc_id = chunk["id"]
                chunk_map[doc_id] = chunk
                fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + 1.0 / (k_rrf + rank + 1)

            # Score sparse
            for rank, chunk in enumerate(sparse_results):
                doc_id = chunk["id"]
                if doc_id not in chunk_map:
                    chunk_map[doc_id] = chunk
                fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + 1.0 / (k_rrf + rank + 1)

            # Sort by fused score
            sorted_fused = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
            
            # 4. Format top_k results
            final_results = []
            for doc_id, rrf_score in sorted_fused[:top_k]:
                chunk = chunk_map[doc_id]
                chunk["score"] = round(rrf_score, 4) # Replace original score with RRF score
                final_results.append(chunk)

            logger.debug(
                "Hybrid search for '%s': %d results fused -> top %d",
                query[:80],
                len(sorted_fused),
                len(final_results)
            )
            return final_results

        except Exception as e:
            logger.error("Hybrid search failed: %s", str(e))
            return []

    async def close(self) -> None:
        """Clean up resources."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
