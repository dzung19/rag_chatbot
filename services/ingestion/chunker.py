"""Text chunking for document ingestion.

Uses langchain-text-splitters for recursive character splitting with
configurable chunk size and overlap.
"""

from __future__ import annotations

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[dict]:
    """Split text into overlapping chunks.

    Args:
        text: Full document text.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Overlap between consecutive chunks.

    Returns:
        List of dicts with 'text', 'chunk_index', and 'page' keys.
    """
    if not text or not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
        is_separator_regex=False,
    )

    chunks = splitter.split_text(text)

    result = []
    for i, chunk_text_content in enumerate(chunks):
        if chunk_text_content.strip():
            result.append(
                {
                    "text": chunk_text_content.strip(),
                    "chunk_index": i,
                    "page": 0,  # Page tracking handled at parser level
                }
            )

    logger.debug(
        "Chunked text into %d chunks (size=%d, overlap=%d)",
        len(result),
        chunk_size,
        chunk_overlap,
    )
    return result
