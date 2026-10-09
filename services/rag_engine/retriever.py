"""ChromaDB retriever for similarity search.

Handles query preprocessing, parallel hybrid retrieval (vector dense + SQLite FTS5 BM25 sparse),
and Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams
import httpx

from shared.sqlite_db import get_sqlite_connection

logger = logging.getLogger(__name__)


class Retriever:
    """Retrieves relevant document chunks from ChromaDB and SQLite FTS5."""

    def __init__(
        self,
        qdrant_url: str = "http://127.0.0.1:6333",
        qdrant_api_key: Optional[str] = None,
        collection_name: str = "rag_documents",
        ollama_host: str = "http://ollama:11434",
        embed_model: str = "nomic-embed-text",
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.embed_model = embed_model
        self._http_client: Optional[httpx.AsyncClient] = None

        self._qdrant_client = AsyncQdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        self._collection_name = collection_name
        self._collection_checked = False

    async def _ensure_collection(self):
        """Ensure Qdrant collection exists."""
        if not self._collection_checked:
            if not await self._qdrant_client.collection_exists(self._collection_name):
                await self._qdrant_client.create_collection(
                    collection_name=self._collection_name,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
                )
            self._collection_checked = True

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=15.0),
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        return self._http_client

    def _preprocess_query(self, query: str) -> str:
        """Normalize query whitespace and trim trailing punctuation."""
        if not query:
            return ""
        q = re.sub(r"\s+", " ", query).strip()
        q = q.rstrip("?!.;")
        return q.strip()

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

    async def _dense_search(
        self,
        query: str,
        top_k: int = 20,
        where_filter: Optional[dict] = None,
    ) -> list[dict]:
        """Perform dense vector similarity search in Qdrant."""
        try:
            query_embedding = await self._embed_query(query)
            await self._ensure_collection()
            
            # Note: where_filter translation from chroma to qdrant might be needed
            # For now, passing without filter mapping
            
            q_results = await self._qdrant_client.search(
                collection_name=self._collection_name,
                query_vector=query_embedding,
                limit=top_k,
                with_payload=True
            )
            
            dense_results = []
            for point in q_results:
                dense_results.append({
                    "id": str(point.id),
                    "text": point.payload.get("text", "") if point.payload else "",
                    "metadata": point.payload or {},
                    "score": point.score,
                })
                
            return dense_results
        except Exception as e:
            logger.error("Dense search failed: %s", str(e))
            return []

    async def _keyword_search(self, query: str, top_k: int = 20) -> list[dict]:
        """Perform BM25 keyword search using SQLite FTS5 with word-level OR matching."""
        try:
            conn = get_sqlite_connection()
            # Tokenize into clean alphanumeric words
            clean_words = re.findall(r"[\w]+", query)
            if not clean_words:
                conn.close()
                return []

            safe_terms = [w.replace('"', '""') for w in clean_words if len(w) > 1]
            if not safe_terms:
                safe_terms = [w.replace('"', '""') for w in clean_words]

            clean_phrase = " ".join(safe_terms)
            if len(safe_terms) > 1:
                or_terms = " OR ".join(f'"{t}"' for t in safe_terms)
                fts_match = f'"{clean_phrase}" OR ({or_terms})'
            else:
                fts_match = f'"{safe_terms[0]}"'

            cursor = conn.execute(
                "SELECT id, document_id, filename, page, heading, text, bm25(document_chunks) as rank_score "
                "FROM document_chunks WHERE document_chunks MATCH ? "
                "ORDER BY rank_score ASC LIMIT ?",
                (fts_match, top_k),
            )

            results = []
            for row in cursor.fetchall():
                score = abs(row["rank_score"])
                page_val = None
                heading_val = ""
                try:
                    page_val = row["page"]
                    heading_val = row["heading"] or ""
                except (IndexError, KeyError):
                    pass

                results.append(
                    {
                        "id": row["id"],
                        "text": row["text"],
                        "metadata": {
                            "document_id": row["document_id"],
                            "filename": row["filename"],
                            "page": page_val,
                            "heading": heading_val,
                        },
                        "score": round(score, 4),
                    }
                )
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
        """Search for relevant chunks using parallel Hybrid Search (Vector + BM25) and RRF."""
        try:
            clean_query = self._preprocess_query(query)
            if not clean_query:
                return []

            # 1 & 2. Run Dense & Sparse searches in parallel
            dense_task = self._dense_search(clean_query, top_k=20, where_filter=where_filter)
            sparse_task = self._keyword_search(clean_query, top_k=20)
            res_dense, res_sparse = await asyncio.gather(
                dense_task, sparse_task, return_exceptions=True
            )

            dense_results = res_dense if isinstance(res_dense, list) else []
            sparse_results = res_sparse if isinstance(res_sparse, list) else []
            if isinstance(res_dense, Exception):
                logger.error("Dense search exception: %s", str(res_dense))
            if isinstance(res_sparse, Exception):
                logger.error("Sparse search exception: %s", str(res_sparse))

            # 3. Reciprocal Rank Fusion (RRF)
            k_rrf = 60
            fused_scores: dict[str, float] = {}
            chunk_map: dict[str, dict] = {}

            # Score dense results
            for rank, chunk in enumerate(dense_results):
                doc_id = chunk["id"]
                chunk_map[doc_id] = chunk
                fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + 1.0 / (k_rrf + rank + 1)

            # Score sparse results
            for rank, chunk in enumerate(sparse_results):
                doc_id = chunk["id"]
                if doc_id not in chunk_map:
                    chunk_map[doc_id] = chunk
                else:
                    # Enrich metadata from sparse if dense had missing fields (e.g. heading/page)
                    for k, v in chunk.get("metadata", {}).items():
                        if v and not chunk_map[doc_id].get("metadata", {}).get(k):
                            chunk_map[doc_id].setdefault("metadata", {})[k] = v
                fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + 1.0 / (k_rrf + rank + 1)

            # Sort by fused score descending
            sorted_fused = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)

            # 4. Format top_k results
            final_results = []
            for doc_id, rrf_score in sorted_fused[:top_k]:
                chunk = chunk_map[doc_id]
                chunk["score"] = round(rrf_score, 4)
                final_results.append(chunk)

            logger.debug(
                "Hybrid search for '%s': dense=%d, sparse=%d -> %d fused -> top %d",
                clean_query[:80],
                len(dense_results),
                len(sparse_results),
                len(sorted_fused),
                len(final_results),
            )
            return final_results

        except Exception as e:
            logger.error("Hybrid search failed: %s", str(e))
            return []

    def get_full_documents(self, document_ids: list[str]) -> list[dict]:
        """Fetch full concatenated text for specified document IDs from SQLite FTS5 table.

        Args:
            document_ids: List of document IDs (UUIDs) to retrieve.

        Returns:
            List of dicts formatted like retriever chunks, where each item represents
            a full document with concatenated text from all its chunks in original order.
        """
        if not document_ids:
            return []

        try:
            conn = get_sqlite_connection()
            placeholders = ",".join("?" for _ in document_ids)
            query = f"""
                SELECT rowid, id, document_id, filename, page, heading, text
                FROM document_chunks
                WHERE document_id IN ({placeholders})
                ORDER BY document_id, rowid ASC
            """
            cursor = conn.execute(query, tuple(document_ids))
            rows = cursor.fetchall()
            conn.close()

            # Group rows by document_id in the exact order requested
            docs_by_id: dict[str, list] = {doc_id: [] for doc_id in document_ids}
            for row in rows:
                doc_id = row["document_id"]
                if doc_id in docs_by_id:
                    docs_by_id[doc_id].append(row)

            full_docs = []
            for doc_id in document_ids:
                chunk_rows = docs_by_id.get(doc_id, [])
                if not chunk_rows:
                    continue

                first_row = chunk_rows[0]
                filename = first_row["filename"] or f"document_{doc_id}"
                full_text = "\n\n".join(r["text"].strip() for r in chunk_rows if r["text"])
                total_pages = max((r["page"] for r in chunk_rows if r["page"] is not None), default=None)

                full_docs.append({
                    "id": f"full_{doc_id}",
                    "text": full_text,
                    "metadata": {
                        "document_id": doc_id,
                        "filename": filename,
                        "page": total_pages,
                        "heading": "Toàn văn tài liệu",
                        "chunk_index": 0,
                    },
                    "score": 1.0,
                })

            return full_docs
        except Exception as e:
            logger.error("Failed to fetch full documents from SQLite: %s", str(e), exc_info=True)
            return []

    async def close(self) -> None:
        """Clean up resources."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
