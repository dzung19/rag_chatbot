"""ChromaDB retriever for similarity search.

Handles query preprocessing, parallel hybrid retrieval (vector dense + SQLite FTS5 BM25 sparse),
and Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import chromadb
import httpx

from shared.sqlite_db import get_sqlite_connection

logger = logging.getLogger(__name__)


class Retriever:
    """Retrieves relevant document chunks from ChromaDB and SQLite FTS5."""

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
        self._collection = None

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

    def _get_collection(self):
        """Get cached ChromaDB collection to avoid recreation per query."""
        if self._collection is None:
            self._collection = self._chroma_client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

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
        """Perform dense vector similarity search in ChromaDB."""
        try:
            query_embedding = await self._embed_query(query)
            collection = self._get_collection()

            query_params: dict = {
                "query_embeddings": [query_embedding],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
            }
            if where_filter:
                query_params["where"] = where_filter

            c_results = collection.query(**query_params)
            dense_results = []

            if c_results["ids"] and c_results["ids"][0]:
                for i, doc_id in enumerate(c_results["ids"][0]):
                    distance = (
                        c_results["distances"][0][i] if c_results["distances"] else 0.0
                    )
                    score = 1.0 - distance
                    dense_results.append(
                        {
                            "id": doc_id,
                            "text": (
                                c_results["documents"][0][i]
                                if c_results["documents"]
                                else ""
                            ),
                            "metadata": (
                                c_results["metadatas"][0][i]
                                if c_results["metadatas"]
                                else {}
                            ),
                            "score": score,
                        }
                    )
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

    async def close(self) -> None:
        """Clean up resources."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
