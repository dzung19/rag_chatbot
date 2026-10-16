"""Hybrid Retriever for Qdrant (Dense Vector) + PostgreSQL/SQLite FTS (Sparse BM25).

Handles query preprocessing, Vietnamese conversational stopword pruning for keyword search,
parallel hybrid retrieval (vector dense + PostgreSQL GIN tsvector / SQLite FTS5 BM25),
metadata filtering (`selected_document_ids`), Reciprocal Rank Fusion (RRF),
and Neighboring Chunk Expansion (`chunk_index + 1`) for unbroken tables/sections.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from typing import Optional

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    VectorParams,
)

from shared.sqlite_db import get_sqlite_connection

logger = logging.getLogger(__name__)

_CHUNK_ID_PATTERN = re.compile(r"_chunk_(\d+)$")
_CONTEXT_HEADER_PREFIX_RE = re.compile(r"^\[(?:Nguồn:|[^\]]*›)[^\]]*\]\s*\n?", re.MULTILINE)

# Common Vietnamese conversational filler words that hurt strict AND keyword matching
_VI_QUERY_STOPWORDS = {
    "cho",
    "tôi",
    "toi",
    "mình",
    "minh",
    "em",
    "anh",
    "chị",
    "chi",
    "hỏi",
    "hoi",
    "xin",
    "biết",
    "biet",
    "về",
    "ve",
    "là",
    "la",
    "gì",
    "gi",
    "ở",
    "o",
    "đâu",
    "dau",
    "như",
    "nhu",
    "thế",
    "the",
    "nào",
    "nao",
    "sao",
    "vậy",
    "vay",
    "nhé",
    "nhe",
    "nhỉ",
    "nhi",
    "ạ",
    "a",
    "ơi",
    "oi",
    "please",
    "tell",
    "me",
    "what",
    "where",
    "how",
}


class Retriever:
    """Retrieves relevant document chunks from Qdrant, PostgreSQL FTS, and SQLite FTS5."""

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

    @staticmethod
    def _extract_keyword_tokens(query: str) -> list[str]:
        """Extract clean keyword tokens from a user query, pruning conversational fillers."""
        raw_tokens = re.findall(r"[\w]+", query.lower())
        if not raw_tokens:
            return []
        meaningful = [t for t in raw_tokens if len(t) > 1 or t.isdigit()]
        if not meaningful:
            meaningful = raw_tokens
        filtered = [t for t in meaningful if t not in _VI_QUERY_STOPWORDS]
        return filtered if filtered else meaningful

    @staticmethod
    def _extract_filter_doc_ids(where_filter: Optional[dict]) -> list[str]:
        """Extract a normalized list of document_ids from `where_filter` if present."""
        if not where_filter or not isinstance(where_filter, dict):
            return []
        doc_ids: list[str] = []
        if "document_ids" in where_filter and isinstance(where_filter["document_ids"], list):
            doc_ids.extend(str(x).strip() for x in where_filter["document_ids"] if x)
        elif "document_id" in where_filter and where_filter["document_id"]:
            val = where_filter["document_id"]
            if isinstance(val, list):
                doc_ids.extend(str(x).strip() for x in val if x)
            else:
                doc_ids.append(str(val).strip())
        return list(dict.fromkeys(d for d in doc_ids if d))

    @classmethod
    def _build_qdrant_filter(cls, where_filter: Optional[dict]) -> Optional[Filter]:
        """Convert dictionary `where_filter` into Qdrant `Filter` object."""
        doc_ids = cls._extract_filter_doc_ids(where_filter)
        if not doc_ids:
            return None
        if len(doc_ids) == 1:
            return Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=doc_ids[0]))]
            )
        return Filter(
            must=[FieldCondition(key="document_id", match=MatchAny(any=doc_ids))]
        )

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
            qdrant_filter = self._build_qdrant_filter(where_filter)

            if hasattr(self._qdrant_client, "query_points"):
                q_response = await self._qdrant_client.query_points(
                    collection_name=self._collection_name,
                    query=query_embedding,
                    query_filter=qdrant_filter,
                    limit=top_k,
                    with_payload=True,
                )
                q_results = q_response.points
            else:
                q_results = await self._qdrant_client.search(
                    collection_name=self._collection_name,
                    query_vector=query_embedding,
                    query_filter=qdrant_filter,
                    limit=top_k,
                    with_payload=True,
                )

            dense_results = []
            for point in q_results:
                payload = point.payload or {}
                document_id = payload.get("document_id", "")
                chunk_index = payload.get("chunk_index")

                # Match SQLite / PostgreSQL chunk ID format so RRF merges identical chunks
                if document_id and chunk_index is not None:
                    chunk_id = f"{document_id}_chunk_{chunk_index}"
                else:
                    chunk_id = str(point.id)

                dense_results.append(
                    {
                        "id": chunk_id,
                        "text": payload.get("text", ""),
                        "metadata": payload,
                        "score": point.score,
                    }
                )

            return dense_results
        except Exception as e:
            logger.error("Dense search failed: %s", str(e))
            return []

    def _chunk_index_from_id(self, chunk_id: str) -> int | None:
        """Extract integer chunk_index from '<document_id>_chunk_<n>'."""
        match = _CHUNK_ID_PATTERN.search(chunk_id or "")
        return int(match.group(1)) if match else None

    async def _keyword_search(
        self,
        query: str,
        top_k: int = 20,
        where_filter: Optional[dict] = None,
    ) -> list[dict]:
        """Perform keyword full-text search in PostgreSQL (GIN tsvector) with SQLite FTS5 fallback."""
        tokens = self._extract_keyword_tokens(query)
        if not tokens:
            return []

        doc_ids = self._extract_filter_doc_ids(where_filter)

        # 1. Try PostgreSQL Full-Text Search (unaccent + GIN tsvector) with OR + AND phrase boost
        try:
            from shared.db import get_db_engine, is_postgres_available

            if is_postgres_available():
                from sqlalchemy import text

                # Sanitize alphanumeric tokens for tsquery
                pg_tokens = [re.sub(r"[^\w]", "", t) for t in tokens]
                pg_tokens = [t for t in pg_tokens if t]
                if pg_tokens:
                    or_tsq = " | ".join(pg_tokens)
                    clean_q = " ".join(pg_tokens)
                    engine = get_db_engine()
                    with engine.connect() as pg_conn:
                        if doc_ids:
                            stmt = text(
                                """
                                SELECT id, document_id, filename, page, heading, text,
                                       (
                                           ts_rank_cd(tsv, to_tsquery('simple', unaccent(:or_q)))
                                           + CASE
                                               WHEN tsv @@ plainto_tsquery('simple', unaccent(:clean_q))
                                               THEN ts_rank_cd(tsv, plainto_tsquery('simple', unaccent(:clean_q))) * 2.0
                                               ELSE 0.0
                                             END
                                       ) AS rank_score
                                FROM document_chunks
                                WHERE document_id = ANY(:doc_ids)
                                  AND tsv @@ to_tsquery('simple', unaccent(:or_q))
                                ORDER BY rank_score DESC
                                LIMIT :limit
                                """
                            )
                            params = {
                                "or_q": or_tsq,
                                "clean_q": clean_q,
                                "doc_ids": doc_ids,
                                "limit": top_k,
                            }
                        else:
                            stmt = text(
                                """
                                SELECT id, document_id, filename, page, heading, text,
                                       (
                                           ts_rank_cd(tsv, to_tsquery('simple', unaccent(:or_q)))
                                           + CASE
                                               WHEN tsv @@ plainto_tsquery('simple', unaccent(:clean_q))
                                               THEN ts_rank_cd(tsv, plainto_tsquery('simple', unaccent(:clean_q))) * 2.0
                                               ELSE 0.0
                                             END
                                       ) AS rank_score
                                FROM document_chunks
                                WHERE tsv @@ to_tsquery('simple', unaccent(:or_q))
                                ORDER BY rank_score DESC
                                LIMIT :limit
                                """
                            )
                            params = {
                                "or_q": or_tsq,
                                "clean_q": clean_q,
                                "limit": top_k,
                            }

                        pg_results = pg_conn.execute(stmt, params).mappings().fetchall()
                        if pg_results:
                            results = []
                            for row in pg_results:
                                score = float(row["rank_score"] or 0.0)
                                results.append(
                                    {
                                        "id": row["id"],
                                        "text": row["text"],
                                        "metadata": {
                                            "document_id": row["document_id"],
                                            "filename": row["filename"],
                                            "page": row["page"],
                                            "heading": row["heading"] or "",
                                            "chunk_index": self._chunk_index_from_id(row["id"]),
                                        },
                                        "score": round(score, 4),
                                    }
                                )
                            return results
        except Exception as e:
            logger.debug("PostgreSQL keyword search notice: %s", str(e))

        # 2. Fallback to SQLite FTS5
        try:
            conn = get_sqlite_connection()
            safe_terms = [w.replace('"', '""') for w in tokens if w]
            if not safe_terms:
                conn.close()
                return []

            clean_phrase = " ".join(safe_terms)
            if len(safe_terms) > 1:
                or_terms = " OR ".join(f'"{t}"' for t in safe_terms)
                fts_match = f'"{clean_phrase}" OR ({or_terms})'
            else:
                fts_match = f'"{safe_terms[0]}"'

            if doc_ids:
                placeholders = ",".join("?" for _ in doc_ids)
                sql = (
                    f"SELECT id, document_id, filename, page, heading, text, bm25(document_chunks) as rank_score "
                    f"FROM document_chunks WHERE document_chunks MATCH ? AND document_id IN ({placeholders}) "
                    f"ORDER BY rank_score ASC LIMIT ?"
                )
                sql_params = (fts_match, *doc_ids, top_k)
            else:
                sql = (
                    "SELECT id, document_id, filename, page, heading, text, bm25(document_chunks) as rank_score "
                    "FROM document_chunks WHERE document_chunks MATCH ? "
                    "ORDER BY rank_score ASC LIMIT ?"
                )
                sql_params = (fts_match, top_k)

            cursor = conn.execute(sql, sql_params)

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
                            "chunk_index": self._chunk_index_from_id(row["id"]),
                        },
                        "score": round(score, 4),
                    }
                )
            conn.close()
            return results
        except Exception as e:
            logger.error("Keyword search failed: %s", str(e))
            return []

    def _fetch_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, dict]:
        """Fetch specific chunks by their IDs from PostgreSQL or SQLite for neighbor expansion."""
        if not chunk_ids:
            return {}

        found: dict[str, dict] = {}

        # 1. Try PostgreSQL first
        try:
            from shared.db import get_db_engine, is_postgres_available

            if is_postgres_available():
                from sqlalchemy import text

                engine = get_db_engine()
                with engine.connect() as pg_conn:
                    stmt = text(
                        "SELECT id, document_id, filename, page, heading, text "
                        "FROM document_chunks WHERE id = ANY(:ids)"
                    )
                    rows = pg_conn.execute(stmt, {"ids": chunk_ids}).mappings().fetchall()
                    for r in rows:
                        found[r["id"]] = dict(r)
        except Exception as e:
            logger.debug("PostgreSQL neighbor chunk lookup notice: %s", str(e))

        missing = [cid for cid in chunk_ids if cid not in found]
        if missing:
            try:
                conn = get_sqlite_connection()
                placeholders = ",".join("?" for _ in missing)
                cur = conn.execute(
                    f"SELECT id, document_id, filename, page, heading, text "
                    f"FROM document_chunks WHERE id IN ({placeholders})",
                    tuple(missing),
                )
                for r in cur.fetchall():
                    found[r["id"]] = dict(r)
                conn.close()
            except Exception as e:
                logger.debug("SQLite neighbor chunk lookup notice: %s", str(e))

        return found

    def _expand_neighbor_chunks(
        self,
        top_chunks: list[dict],
        max_expand_count: int = 2,
        max_combined_chars: int = 2600,
    ) -> list[dict]:
        """Expand the highest-ranked chunks with their immediate next chunk (chunk_index + 1)
        when they belong to the same document/section and were not already retrieved.
        """
        if not top_chunks:
            return top_chunks

        existing_ids = {c.get("id") for c in top_chunks}
        candidate_map: dict[int, str] = {}

        for idx, chunk in enumerate(top_chunks[:max_expand_count]):
            meta = chunk.get("metadata") or {}
            # Do not expand Excel summary chunks
            if meta.get("is_summary"):
                continue
            doc_id = meta.get("document_id")
            c_idx = meta.get("chunk_index")
            if c_idx is None:
                c_idx = self._chunk_index_from_id(chunk.get("id", ""))
            if doc_id and c_idx is not None:
                next_id = f"{doc_id}_chunk_{c_idx + 1}"
                if next_id not in existing_ids:
                    candidate_map[idx] = next_id

        if not candidate_map:
            return top_chunks

        fetched = self._fetch_chunks_by_ids(list(candidate_map.values()))
        if not fetched:
            return top_chunks

        for idx, next_id in candidate_map.items():
            neighbor = fetched.get(next_id)
            if not neighbor:
                continue
            parent = top_chunks[idx]
            parent_meta = parent.get("metadata") or {}
            parent_page = parent_meta.get("page")
            neighbor_page = neighbor.get("page")

            # Expand only if on the same page or adjacent page with matching/continuation content
            if parent_page and neighbor_page and abs(int(neighbor_page) - int(parent_page)) > 1:
                continue

            neighbor_body = _CONTEXT_HEADER_PREFIX_RE.sub("", neighbor.get("text", "")).strip()
            if not neighbor_body:
                continue

            cur_text = parent.get("text", "")
            if len(cur_text) + len(neighbor_body) <= max_combined_chars:
                parent["text"] = f"{cur_text}\n{neighbor_body}"

        return top_chunks

    async def search(
        self,
        query: str,
        top_k: int = 5,
        where_filter: Optional[dict] = None,
    ) -> list[dict]:
        """Search for relevant chunks using parallel Hybrid Search (Vector + BM25), RRF, and Neighbor Expansion."""
        try:
            clean_query = self._preprocess_query(query)
            if not clean_query:
                return []

            # 1 & 2. Run Dense & Sparse searches in parallel
            dense_task = self._dense_search(clean_query, top_k=20, where_filter=where_filter)
            sparse_task = self._keyword_search(clean_query, top_k=20, where_filter=where_filter)
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

            # 5. Neighboring chunk expansion for top results
            if final_results:
                final_results = await asyncio.to_thread(
                    self._expand_neighbor_chunks, final_results
                )

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
        """Fetch full concatenated text for specified document IDs.

        Prioritizes the `full_documents` table (clean, continuous raw text).
        Falls back to concatenating chunks from `document_chunks` if full_documents is absent.

        Args:
            document_ids: List of document IDs (UUIDs) to retrieve.

        Returns:
            List of dicts formatted like retriever chunks, where each item represents
            a full document in the requested order.
        """
        if not document_ids:
            return []

        try:
            full_rows = {}

            # 1. Check PostgreSQL full_documents table first if available
            try:
                from shared.db import get_db_engine, is_postgres_available

                if is_postgres_available():
                    from sqlalchemy import text

                    engine = get_db_engine()
                    with engine.connect() as pg_conn:
                        stmt = text(
                            "SELECT document_id, filename, full_text, page_count "
                            "FROM full_documents WHERE document_id = ANY(:ids)"
                        )
                        pg_rows = pg_conn.execute(stmt, {"ids": document_ids}).mappings().fetchall()
                        for r in pg_rows:
                            full_rows[r["document_id"]] = dict(r)
            except Exception as e:
                logger.debug("PostgreSQL full_documents lookup failed: %s", str(e))

            # 2. Check SQLite for any documents not found in PostgreSQL (backward compatibility)
            missing_ids = [doc_id for doc_id in document_ids if doc_id not in full_rows]
            chunk_docs_by_id: dict[str, list] = {}

            if missing_ids:
                try:
                    conn = get_sqlite_connection()
                    placeholders = ",".join("?" for _ in missing_ids)
                    try:
                        full_cur = conn.execute(
                            f"""
                            SELECT document_id, filename, full_text, page_count
                            FROM full_documents
                            WHERE document_id IN ({placeholders})
                            """,
                            tuple(missing_ids),
                        )
                        for r in full_cur.fetchall():
                            full_rows[r["document_id"]] = dict(r)
                    except sqlite3.OperationalError:
                        pass

                    # Check document_chunks table for any documents still not found
                    still_missing = [doc_id for doc_id in missing_ids if doc_id not in full_rows]
                    if still_missing:
                        chunk_docs_by_id = {doc_id: [] for doc_id in still_missing}
                        missing_placeholders = ",".join("?" for _ in still_missing)
                        chunk_query = f"""
                            SELECT rowid, id, document_id, filename, page, heading, text
                            FROM document_chunks
                            WHERE document_id IN ({missing_placeholders})
                            ORDER BY document_id, rowid ASC
                        """
                        chunk_cursor = conn.execute(chunk_query, tuple(still_missing))
                        for row in chunk_cursor.fetchall():
                            d_id = row["document_id"]
                            if d_id in chunk_docs_by_id:
                                chunk_docs_by_id[d_id].append(row)

                    conn.close()
                except Exception as e:
                    logger.warning("SQLite full_documents fallback failed: %s", str(e))

            full_docs = []
            for doc_id in document_ids:
                if doc_id in full_rows:
                    row = full_rows[doc_id]
                    full_docs.append(
                        {
                            "id": f"full_{doc_id}",
                            "text": row["full_text"],
                            "metadata": {
                                "document_id": doc_id,
                                "filename": row["filename"],
                                "page": row["page_count"],
                                "heading": "Toàn văn tài liệu",
                                "chunk_index": 0,
                            },
                            "score": 1.0,
                        }
                    )
                elif chunk_docs_by_id.get(doc_id):
                    chunk_rows = chunk_docs_by_id[doc_id]
                    first_row = chunk_rows[0]
                    filename = first_row["filename"] or f"document_{doc_id}"
                    full_text = "\n\n".join(r["text"].strip() for r in chunk_rows if r["text"])
                    total_pages = max(
                        (r["page"] for r in chunk_rows if r["page"] is not None), default=None
                    )
                    full_docs.append(
                        {
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
                        }
                    )

            return full_docs
        except Exception as e:
            logger.error("Failed to fetch full documents: %s", str(e), exc_info=True)
            return []

    async def close(self) -> None:
        """Clean up resources."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
