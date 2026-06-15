import logging
import sqlite3
import os
from pathlib import Path

from shared.config import get_settings

logger = logging.getLogger(__name__)

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
    
    # Initialize the FTS5 virtual table if it doesn't exist
    # tokenize='porter' is good for English stemming, unicode61 handles basic punctuation well.
    # We use porter to get basic stemming, though for Vietnamese it behaves like unicode61.
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks USING fts5(
            id UNINDEXED,
            document_id UNINDEXED,
            filename UNINDEXED,
            text,
            tokenize='porter'
        );
    """)
    conn.commit()
    
    return conn
