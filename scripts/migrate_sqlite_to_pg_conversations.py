"""Migration script: Copy conversation history from SQLite to PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_pg_conversations.py [--sqlite-path PATH] [--pg-url URL]
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
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


def migrate_conversations(sqlite_path: str, pg_url: str) -> None:
    if create_engine is None:
        logger.error(
            "SQLAlchemy is required to run migrations.\n"
            "Please run 'pip install sqlalchemy psycopg[binary]' or run inside docker:\n"
            "  docker compose exec conversation-service python scripts/migrate_sqlite_to_pg_conversations.py"
        )
        sys.exit(1)

    if not os.path.exists(sqlite_path):
        logger.info("SQLite file '%s' not found. Nothing to migrate.", sqlite_path)
        return

    logger.info("Connecting to SQLite database: %s", sqlite_path)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    logger.info("Connecting to PostgreSQL database: %s", pg_url.split("@")[-1])
    pg_engine = create_engine(pg_url)

    with pg_engine.begin() as pg_conn:
        # 1. Migrate conversations
        try:
            cur = sqlite_conn.execute("SELECT * FROM conversations")
            conv_rows = cur.fetchall()
            for r in conv_rows:
                d = dict(r)
                pg_conn.execute(
                    text(
                        """
                        INSERT INTO conversations (
                            id, owner_id, title, pinned, archived_at, deleted_at, created_at, updated_at
                        ) VALUES (
                            :id, :owner_id, :title, :pinned, :archived_at, :deleted_at, :created_at, :updated_at
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    d,
                )
            logger.info("Migrated %d conversations.", len(conv_rows))
        except sqlite3.OperationalError as e:
            logger.info("No conversations table in SQLite: %s", str(e))

        # 2. Migrate chat_turns
        try:
            cur = sqlite_conn.execute("SELECT * FROM chat_turns")
            turn_rows = cur.fetchall()
            for r in turn_rows:
                d = dict(r)
                pg_conn.execute(
                    text(
                        """
                        INSERT INTO chat_turns (
                            id, conversation_id, request_id, input_hash, status, created_at, completed_at
                        ) VALUES (
                            :id, :conversation_id, :request_id, :input_hash, :status, :created_at, :completed_at
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    d,
                )
            logger.info("Migrated %d chat_turns.", len(turn_rows))
        except sqlite3.OperationalError as e:
            logger.info("No chat_turns table in SQLite: %s", str(e))

        # 3. Migrate messages
        try:
            cur = sqlite_conn.execute("SELECT * FROM messages")
            msg_rows = cur.fetchall()
            for r in msg_rows:
                d = dict(r)
                pg_conn.execute(
                    text(
                        """
                        INSERT INTO messages (
                            id, conversation_id, turn_id, sequence, role, content, status, sources_json, created_at, updated_at
                        ) VALUES (
                            :id, :conversation_id, :turn_id, :sequence, :role, :content, :status, :sources_json, :created_at, :updated_at
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    d,
                )
            logger.info("Migrated %d messages.", len(msg_rows))
        except sqlite3.OperationalError as e:
            logger.info("No messages table in SQLite: %s", str(e))

        # 4. Migrate admin_audit_logs
        try:
            cur = sqlite_conn.execute("SELECT * FROM admin_audit_logs")
            log_rows = cur.fetchall()
            for r in log_rows:
                d = dict(r)
                pg_conn.execute(
                    text(
                        """
                        INSERT INTO admin_audit_logs (
                            id, actor_id, action, target_id, target_title, target_owner_id, reason, created_at
                        ) VALUES (
                            :id, :actor_id, :action, :target_id, :target_title, :target_owner_id, :reason, :created_at
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    d,
                )
            logger.info("Migrated %d admin_audit_logs.", len(log_rows))
        except sqlite3.OperationalError as e:
            logger.info("No admin_audit_logs table in SQLite: %s", str(e))

    sqlite_conn.close()
    logger.info("Conversation history migration completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate chat history from SQLite to PostgreSQL")
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("SQLITE_CHAT_HISTORY_PATH", "shared_data/chat_history.db"),
        help="Path to SQLite chat_history.db",
    )
    parser.add_argument(
        "--pg-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://rag_user:rag_password@localhost:5432/rag_chatbot",
        ),
        help="Target PostgreSQL connection URL",
    )
    args = parser.parse_args()
    migrate_conversations(args.sqlite_path, args.pg_url)
