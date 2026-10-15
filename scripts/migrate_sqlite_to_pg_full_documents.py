"""Migration script: Copy full_documents from SQLite to PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_pg_full_documents.py [--sqlite-path PATH] [--pg-url URL]
"""

from __future__ import annotations

import argparse
import logging
import os
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


def migrate_full_documents(sqlite_path: str, pg_url: str) -> None:
    if create_engine is None:
        logger.error(
            "SQLAlchemy is required to run migrations.\n"
            "Please run 'pip install sqlalchemy psycopg[binary]' or run inside docker:\n"
            "  docker compose exec ingestion python scripts/migrate_sqlite_to_pg_full_documents.py"
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

    # 1. Ensure table exists in PostgreSQL
    with pg_engine.begin() as pg_conn:
        pg_conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS full_documents (
                    document_id VARCHAR(36) PRIMARY KEY,
                    filename VARCHAR(512) NOT NULL,
                    full_text TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 1,
                    file_size_bytes BIGINT NOT NULL DEFAULT 0,
                    file_type VARCHAR(32) NOT NULL DEFAULT 'txt',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_full_docs_id ON full_documents(document_id);
                """
            )
        )

        try:
            cur = sqlite_conn.execute("SELECT * FROM full_documents")
            rows = cur.fetchall()
            logger.info("Found %d full_documents in SQLite.", len(rows))
            migrated = 0
            for r in rows:
                d = dict(r)
                pg_conn.execute(
                    text(
                        """
                        INSERT INTO full_documents (
                            document_id, filename, full_text, page_count, file_size_bytes, file_type, created_at
                        ) VALUES (
                            :document_id, :filename, :full_text, :page_count, :file_size_bytes, :file_type, :created_at
                        )
                        ON CONFLICT (document_id) DO UPDATE SET
                            filename = EXCLUDED.filename,
                            full_text = EXCLUDED.full_text,
                            page_count = EXCLUDED.page_count,
                            file_size_bytes = EXCLUDED.file_size_bytes,
                            file_type = EXCLUDED.file_type
                        """
                    ),
                    {
                        "document_id": d["document_id"],
                        "filename": d["filename"],
                        "full_text": d["full_text"],
                        "page_count": d.get("page_count", 1),
                        "file_size_bytes": d.get("file_size_bytes", 0),
                        "file_type": d.get("file_type", "txt"),
                        "created_at": d.get("created_at") or datetime.now(timezone.utc),
                    },
                )
                migrated += 1
            logger.info("Successfully migrated %d full_documents to PostgreSQL.", migrated)
        except sqlite3.OperationalError as e:
            logger.info("No full_documents table in SQLite: %s", str(e))

    sqlite_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate full_documents from SQLite to PostgreSQL")
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
    migrate_full_documents(args.sqlite_path, args.pg_url)
