"""Text chunking for document ingestion.

Implements Strategy 5 (Hybrid: Structure-Aware Splitting + Contextual Headers).
Detects document structural boundaries (headings, articles, sections, slides, sheets)
and prepends contextual metadata headers to every chunk to improve dense and sparse retrieval.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# Heading & structural boundary patterns (supports Markdown, Vietnamese documents, slides, sheets)
HEADING_PATTERNS = [
    r"^#{1,6}\s+.+",                                      # Markdown headings (# Heading)
    r"^\d+(\.\d+)*\.?\s+\S.+",                           # Numbered sections (1. Intro, 1.1 Scope, etc.)
    r"^[IVXLCDM]+\.\s+.+",                               # Roman numerals (I. General, II. Terms)
    r"^(?:Phần|Chương|Mục|Điều|Khoản|Bảng|Hình|Phụ lục)\s+\d+.*", # Vietnamese legal & document markers
    r"^\[(?:Slide|Sheet|Table|Notes)\s*.*\]",            # Parser structural tags
]


def _is_heading(line: str) -> bool:
    """Determine whether a line is a structural heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 150:
        return False
    # Check regex patterns
    if any(re.match(pattern, stripped, re.IGNORECASE) for pattern in HEADING_PATTERNS):
        return True
    # Check for short all-caps lines (common in Vietnamese policies/announcements)
    words = stripped.split()
    if 2 <= len(words) <= 10 and stripped.isupper() and not any(c in stripped for c in [",", ";", "."]):
        return True
    return False


def _detect_sections(text: str) -> list[dict]:
    """Split text into structural sections based on detected headings."""
    lines = text.split("\n")
    sections: list[dict] = []
    current_heading: Optional[str] = None
    current_lines: list[str] = []

    for line in lines:
        if _is_heading(line):
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append({"heading": current_heading, "body": body})
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    body = "\n".join(current_lines).strip()
    if body:
        sections.append({"heading": current_heading, "body": body})

    return sections if sections else [{"heading": None, "body": text}]


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    page_number: int = 0,
    source_filename: str = "",
    start_chunk_index: int = 0,
) -> list[dict]:
    """Split text into structure-aware chunks with contextual headers.

    Args:
        text: Page or section text content.
        chunk_size: Maximum characters per chunk (content portion).
        chunk_overlap: Character overlap between consecutive sub-chunks.
        page_number: Document page, slide, or sheet number (1-based if known).
        source_filename: Original document filename for context attribution.
        start_chunk_index: Starting index for global chunk numbering.

    Returns:
        List of dicts with 'text', 'raw_text', 'chunk_index', 'page', 'heading',
        'source', and 'context_header'.
    """
    if not text or not text.strip():
        return []

    sections = _detect_sections(text)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", ", ", " ", ""],
        is_separator_regex=False,
    )

    result = []
    chunk_idx = start_chunk_index

    for section in sections:
        heading = section["heading"]
        body = section["body"]

        # Build contextual header prefix
        header_parts = []
        if source_filename:
            header_parts.append(f"Nguồn: {source_filename}")
        if page_number > 0:
            header_parts.append(f"Trang {page_number}")
        if heading:
            header_parts.append(f"Mục: {heading}")

        context_header = f"[{' | '.join(header_parts)}]\n" if header_parts else ""

        if len(body) <= chunk_size:
            result.append(
                {
                    "text": (context_header + body).strip(),
                    "raw_text": body.strip(),
                    "chunk_index": chunk_idx,
                    "page": page_number,
                    "heading": heading or "",
                    "source": source_filename,
                    "context_header": context_header.strip(),
                }
            )
            chunk_idx += 1
        else:
            sub_chunks = splitter.split_text(body)
            for sub_text in sub_chunks:
                if sub_text.strip():
                    result.append(
                        {
                            "text": (context_header + sub_text).strip(),
                            "raw_text": sub_text.strip(),
                            "chunk_index": chunk_idx,
                            "page": page_number,
                            "heading": heading or "",
                            "source": source_filename,
                            "context_header": context_header.strip(),
                        }
                    )
                    chunk_idx += 1

    logger.debug(
        "Chunked text into %d chunks (sections=%d, page=%d, size=%d, overlap=%d)",
        len(result),
        len(sections),
        page_number,
        chunk_size,
        chunk_overlap,
    )
    return result


def chunk_document_pages(
    pages: list[str],
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    source_filename: str = "",
) -> list[dict]:
    """Chunk all pages of a parsed document while maintaining accurate page numbers.

    Args:
        pages: List of page/slide/sheet text strings from parser.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Overlap between sub-chunks.
        source_filename: Original filename for context headers.

    Returns:
        List of all chunk dicts across pages with sequential chunk_index.
    """
    all_chunks: list[dict] = []
    current_index = 0

    for page_num, page_text in enumerate(pages, start=1):
        if not page_text or not page_text.strip():
            continue
        page_chunks = chunk_text(
            text=page_text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            page_number=page_num,
            source_filename=source_filename,
            start_chunk_index=current_index,
        )
        all_chunks.extend(page_chunks)
        current_index += len(page_chunks)

    logger.info(
        "Document '%s' (%d pages) chunked into %d total chunks",
        source_filename,
        len(pages),
        len(all_chunks),
    )
    return all_chunks
