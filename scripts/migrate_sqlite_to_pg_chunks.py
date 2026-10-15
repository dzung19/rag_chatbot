"""Migration script: Copy document_chunks from SQLite FTS5 to PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_pg_chunks.py [--sqlite-path PATH] [--pg-url URL]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from sqlalchemy import create_engine, text
except ImportError:
    create_engine = None  # type: ignore
    text = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def migrate_chunks(sqlite_path: str, pg_url: str) -> None:
    if create_engine is None:
        logger.error(
            "SQLAlchemy is required to run migrations.\n"
            "Please run 'pip install sqlalchemy psycopg[binary]' or run inside docker:\n"
            "  docker compose exec ingestion python scripts/migrate_sqlite_to_pg_chunks.py"
        )
        sys.exit(1)

    if not os.path.exists(sqlite_path):
        logger.info("SQLite file '%s' not found. Nothing to migrate.", sqlite_path)
        return

    logger.info("Connecting to SQLite database: %s", sqlite_path)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    logger.info("Connecting to PostgreSQL: %s", pg_url.split("@")[-1])
    pg_engine = create_engine(pg_url)

    # 1. Ensure table, unaccent extension, and GIN tsvector index exist in PostgreSQL
    with pg_engine.begin() as pg_conn:
        try:
            pg_conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent;"))
        except Exception as e:
            logger.warning("Could not enable unaccent extension: %s", str(e))

        pg_conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id VARCHAR(64) PRIMARY KEY,
                    document_id VARCHAR(36) NOT NULL,
                    filename VARCHAR(512) NOT NULL,
                    chunk_index INTEGER NOT NULL DEFAULT 0,
                    page INTEGER NOT NULL DEFAULT 0,
                    heading TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    vector_id VARCHAR(64),
                    embedding_model VARCHAR(64) DEFAULT 'nomic-embed-text',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_document_chunks_doc_chunk UNIQUE (document_id, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id);
                """
            )
        )

        try:
            pg_conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name='document_chunks' AND column_name='tsv'
                        ) THEN
                            ALTER TABLE document_chunks 
                            ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', unaccent(text))) STORED;
                            CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING GIN(tsv);
                        END IF;
                    END $$;
                    """
                )
            )
        except Exception as e:
            logger.warning("TSV column creation: %s", str(e))

        # 2. Read from SQLite document_chunks
        try:
            cur = sqlite_conn.execute("SELECT id, document_id, filename, page, heading, text FROM document_chunks")
            rows = cur.fetchall()
            logger.info("Found %d chunks in SQLite.", len(rows))
            migrated = 0
            now = datetime.now(timezone.utc)

            for r in rows:
                d = dict(r)
                chunk_id = str(d["id"])
                # Extract chunk index from id (e.g., "{doc_id}_chunk_{index}")
                match = re.search(r"_chunk_(\d+)$", chunk_id)
                chunk_index = int(match.group(1)) if match else 0

                pg_conn.execute(
                    text(
                        """
                        INSERT INTO document_chunks (
                            id, document_id, filename, chunk_index, page, heading, text, vector_id, embedding_model, created_at
                        ) VALUES (
                            :id, :document_id, :filename, :chunk_index, :page, :heading, :text, :vector_id, 'nomic-embed-text', :created_at
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            filename = EXCLUDED.filename,
                            chunk_index = EXCLUDED.chunk_index,
                            page = EXCLUDED.page,
                            heading = EXCLUDED.heading,
                            text = EXCLUDED.text
                        """
                    ),
                    {
                        "id": chunk_id,
                        "document_id": str(d["document_id"]),
                        "filename": str(d.get("filename") or ""),
                        "chunk_index": chunk_index,
                        "page": int(d.get("page") or 0),
                        "heading": str(d.get("heading") or ""),
                        "text": str(d.get("text") or ""),
                        "vector_id": chunk_id,
                        "created_at": now,
                    },
                )
                migrated += 1
            logger.info("Successfully migrated %d chunks to PostgreSQL.", migrated)
        except sqlite3.OperationalError as e:
            logger.info("No document_chunks table in SQLite: %s", str(e))

    sqlite_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate document_chunks from SQLite FTS5 to PostgreSQL")
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("SQLITE_DB_PATH", "shared_data/shared_db.db"),
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--pg-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://rag_user:rag_password@localhost:5432/rag_chatbot",
        ),
        help="PostgreSQL connection URL",
    )
    args = parser.parse_args()
    migrate_chunks(args.sqlite_path, args.pg_url)
