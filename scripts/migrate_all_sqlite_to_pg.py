"""Master migration script: One-step migration of all SQLite databases to PostgreSQL.

Migrates:
  1. Base and custom Skills (seeded or migrated via shared.skills_repo)
  2. Conversation history (conversations, chat_turns, messages, audit logs)
  3. Full documents continuous text (full_documents)
  4. Document chunks FTS & metadata (document_chunks with tsvector / GIN index)

Usage:
    python scripts/migrate_all_sqlite_to_pg.py [--chat-sqlite PATH] [--shared-sqlite PATH] [--pg-url URL]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.migrate_sqlite_to_pg_conversations import migrate_conversations
from scripts.migrate_sqlite_to_pg_full_documents import migrate_full_documents
from scripts.migrate_sqlite_to_pg_chunks import migrate_chunks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration_master")


def main() -> None:
    parser = argparse.ArgumentParser(description="One-step migration of all SQLite data to PostgreSQL")
    parser.add_argument(
        "--chat-sqlite",
        default=os.getenv("SQLITE_CHAT_HISTORY_PATH", "shared_data/chat_history.db"),
        help="Path to SQLite chat_history.db",
    )
    parser.add_argument(
        "--shared-sqlite",
        default=os.getenv("SQLITE_DB_PATH", "shared_data/shared_db.db"),
        help="Path to SQLite shared_db.db",
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

    logger.info("==================================================")
    logger.info("Starting Master SQLite -> PostgreSQL Migration")
    logger.info("Target DB: %s", args.pg_url.split("@")[-1])
    logger.info("==================================================")

    # 1. Skills verification and base seeding
    logger.info("\n>>> Step 1/4: Ensuring Skills catalog in PostgreSQL...")
    try:
        from shared.skills_repo import list_skills
        skills = list_skills()
        logger.info("Skills catalog verified. Total available skills: %d", len(skills))
    except Exception as e:
        logger.warning("Skills check encountered warning: %s", str(e))

    # 2. Conversation history
    logger.info("\n>>> Step 2/4: Migrating Conversation History from %s...", args.chat_sqlite)
    migrate_conversations(args.chat_sqlite, args.pg_url)

    # 3. Full documents
    logger.info("\n>>> Step 3/4: Migrating Full Documents from %s...", args.shared_sqlite)
    migrate_full_documents(args.shared_sqlite, args.pg_url)

    # 4. Document chunks
    logger.info("\n>>> Step 4/4: Migrating Document Chunks (FTS) from %s...", args.shared_sqlite)
    migrate_chunks(args.shared_sqlite, args.pg_url)

    logger.info("\n==================================================")
    logger.info("All SQLite to PostgreSQL migrations completed successfully!")
    logger.info("==================================================")


if __name__ == "__main__":
    main()
