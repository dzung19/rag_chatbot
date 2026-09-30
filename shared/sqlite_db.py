import logging
import sqlite3
import os
from pathlib import Path

from shared.config import get_settings

logger = logging.getLogger(__name__)


def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize or upgrade FTS5 table with Vietnamese-friendly unicode61 tokenizer."""
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='document_chunks'")
    row = cur.fetchone()
    if row and row[0]:
        sql = row[0].lower()
        # Upgrade if legacy porter tokenizer was used
        if "porter" in sql:
            logger.warning(
                "FTS5 table uses legacy 'porter' tokenizer. Upgrading to 'unicode61 remove_diacritics 2'..."
            )
            conn.execute("DROP TABLE IF EXISTS document_chunks")
            conn.commit()

    # Create virtual table with unicode61 tokenizer
    # remove_diacritics 2 enables accent-insensitive matching (e.g., 'nghi phep' matches 'nghỉ phép')
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks USING fts5(
            id UNINDEXED,
            document_id UNINDEXED,
            filename UNINDEXED,
            page UNINDEXED,
            heading UNINDEXED,
            text,
            tokenize='unicode61 remove_diacritics 2'
        );
    """)
    conn.commit()


def get_sqlite_connection() -> sqlite3.Connection:
    """Get a connection to the SQLite database, initializing it if necessary."""
    settings = get_settings()
    db_path = Path(settings.sqlite_db_path)
    
    # Ensure directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Connect to SQLite
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Enable WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode=WAL")
    
    _init_db(conn)
    
    return conn
