import logging
import sqlite3
import os
from pathlib import Path
from datetime import datetime

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
    
    _init_skills_table(conn)

def _init_skills_table(conn: sqlite3.Connection) -> None:
    """Initialize skills table and seed base skills."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('main', 'modifier')),
            category TEXT NOT NULL CHECK(category IN ('base', 'custom')),
            is_system INTEGER NOT NULL DEFAULT 0,
            system_prompt_addon TEXT NOT NULL,
            temperature_override REAL,
            top_k_override INTEGER,
            icon TEXT NOT NULL DEFAULT 'Sparkles',
            enabled_tools TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_type ON skills(type);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);")
    conn.commit()
    
    # Base skills seeding
    base_skills = [
        # MAIN SKILLS
        {
            "id": "general-assistant", "name": "General Assistant", "description": "Default balanced assistant with full grounding & source citations.",
            "type": "main", "is_system": 1, "icon": "Bot", "temp": 0.7, "top_k": 5,
            "prompt": "You are a helpful and polite company assistant. Focus on answering directly and clearly based only on the provided context."
        },
        {
            "id": "executive-summary", "name": "Executive Summarizer", "description": "Generates high-level briefing.",
            "type": "main", "is_system": 1, "icon": "FileText", "temp": 0.3, "top_k": 6,
            "prompt": "Structure your response as an executive briefing with the following sections: Executive Summary, Key Findings, Action Items, and Metrics/Deadlines. Be highly concise."
        },
        {
            "id": "policy-compliance", "name": "Policy & Compliance", "description": "Strict factual auditor.",
            "type": "main", "is_system": 1, "icon": "ShieldCheck", "temp": 0.1, "top_k": 5,
            "prompt": "Act as a strict compliance auditor. Quote section headers, highlight conditions, prerequisites, penalties, and explicit constraints. Do not make assumptions."
        },
        {
            "id": "email-memo-drafter", "name": "Email & Memo Drafter", "description": "Formats response as a ready-to-send corporate email/memo.",
            "type": "main", "is_system": 1, "icon": "Mail", "temp": 0.7, "top_k": 4,
            "prompt": "Format your response as a professional corporate email or memo. Include a Subject line, Context, Body, Next Steps, and a professional Sign-off."
        },
        {
            "id": "comparative-analyst", "name": "Comparative Analyst", "description": "Synthesizes across multiple documents using markdown tables.",
            "type": "main", "is_system": 1, "icon": "Scale", "temp": 0.2, "top_k": 8,
            "prompt": "Act as a comparative analyst. Synthesize information across multiple sources and present the core differences and similarities using Markdown tables."
        },
        {
            "id": "troubleshooter", "name": "Technical Troubleshooter", "description": "Step-by-step diagnostic workflow.",
            "type": "main", "is_system": 1, "icon": "Wrench", "temp": 0.2, "top_k": 5,
            "prompt": "Provide a step-by-step diagnostic workflow. Start with the Problem Statement, analyze the Root Cause, and then provide Actionable Resolution Steps."
        },
        # MODIFIER SKILLS
        {
            "id": "lang-vietnamese", "name": "Tiếng Việt Chuẩn", "description": "Always answer in Vietnamese.",
            "type": "modifier", "is_system": 1, "icon": "Languages", "temp": None, "top_k": None,
            "prompt": "Always formulate your answers in professional, natural Vietnamese business terminology, maintaining English technical terms in parentheses if necessary. Do NOT answer in English, even if the context is English."
        },
        {
            "id": "concise-tldr", "name": "Brief / TL;DR", "description": "Restrict output to maximum 3 concise bullet points.",
            "type": "modifier", "is_system": 1, "icon": "Zap", "temp": None, "top_k": None,
            "prompt": "Restrict your output to a maximum of 3 extremely concise bullet points. No pleasantries, no introductory phrases. Keep the entire response under 80 words."
        },
        {
            "id": "quote-grounded", "name": "Strict Verbatim Quotes", "description": "Include verbatim quotation blocks.",
            "type": "modifier", "is_system": 1, "icon": "Quote", "temp": None, "top_k": None,
            "prompt": "Include verbatim quotation blocks (`> \"quote\"`) for each key assertion alongside the document name and page number. Be very strict about only quoting."
        }
    ]
    
    now = datetime.utcnow().isoformat()
    for s in base_skills:
        conn.execute(
            """
            INSERT INTO skills (
                id, name, description, type, category, is_system, 
                system_prompt_addon, temperature_override, top_k_override, 
                icon, enabled_tools, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'base', ?, ?, ?, ?, ?, '[]', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                type=excluded.type,
                system_prompt_addon=excluded.system_prompt_addon,
                temperature_override=excluded.temperature_override,
                top_k_override=excluded.top_k_override,
                icon=excluded.icon,
                updated_at=excluded.updated_at
            WHERE is_system=1
            """,
            (
                s["id"], s["name"], s["description"], s["type"], s["is_system"], 
                s["prompt"], s["temp"], s["top_k"], s["icon"], now, now
            )
        )
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
