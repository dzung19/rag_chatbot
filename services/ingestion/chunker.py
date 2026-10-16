"""Text chunking for document ingestion.

Implements Strategy 5+ (Hybrid: Hierarchical Structure-Aware Splitting +
Table-Header Preservation + Contextual Breadcrumb Headers).
Detects multi-level document structural boundaries (chapters, articles, sections,
markdown headings, slides, sheets, tables) and prepends hierarchical breadcrumb
headers to every chunk, while keeping Markdown/pipe tables intact with repeated
column headers across sub-chunks.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# Page boundary banner emitted by pdf_parser.py (e.g. "=== TRANG 1/5 ===")
_PAGE_BANNER_RE = re.compile(r"^===\s*TRANG\s+\d+(?:\s*/\s*\d+)?\s*===$", re.IGNORECASE)

# Markdown table separator line (e.g. "|---|---|---|" or "| :--- | ---: |")
_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")

# Heading & structural boundary patterns mapped to hierarchy levels (1 = highest, 4 = lowest)
LEVEL_HEADING_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    # Level 1: Top-level chapters / parts / H1 / Roman numerals
    (1, re.compile(r"^#\s+.+")),
    (1, re.compile(r"^(?:Phần|Chương|PHẦN|CHƯƠNG)\s+[IVXLCDM\d]+\b.*", re.IGNORECASE)),
    (1, re.compile(r"^[IVXLCDM]+\.\s+\S.+")),
    # Level 2: Articles / Sections / H2 / Top-level numbered sections (1. Intro)
    (2, re.compile(r"^##\s+.+")),
    (2, re.compile(r"^(?:Mục|Điều|Phụ lục|MỤC|ĐIỀU|PHỤ LỤC)\s+[IVXLCDM\d]+\b.*", re.IGNORECASE)),
    (2, re.compile(r"^\d+\.\s+\S.+")),
    (2, re.compile(r"^\[(?:Slide|Sheet)\s*.*\]$", re.IGNORECASE)),
    # Level 3: Sub-sections / H3 / 1.1 Scope / Parser section tags
    (3, re.compile(r"^###\s+.+")),
    (3, re.compile(r"^(?:Khoản|Bảng|Hình)\s+\d+\b.*", re.IGNORECASE)),
    (3, re.compile(r"^\d+\.\d+\.?\s+\S.+")),
    (3, re.compile(r"^\[(?:Table|Notes)\s*.*\]$", re.IGNORECASE)),
    # Level 4: Deep sub-sections / H4-H6 / 1.1.1
    (4, re.compile(r"^#{4,6}\s+.+")),
    (4, re.compile(r"^\d+\.\d+\.\d+(?:\.\d+)*\.?\s+\S.+")),
]

# Legacy flat pattern list kept for backward compatibility
HEADING_PATTERNS = [pat.pattern for _, pat in LEVEL_HEADING_PATTERNS]


def _is_table_line(line: str) -> bool:
    """Return True if a line looks like a Markdown or pipe-delimited table row."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 2:
        return True
    if _MD_TABLE_SEP_RE.match(stripped):
        return True
    # Pipe-delimited table row from DOCX/PPTX parser (at least 2 pipes, not a bullet/heading)
    if stripped.count(" | ") >= 2 and not stripped.startswith(("#", "-", "*", "•")):
        return True
    return False


def _get_heading_level(line: str) -> Optional[int]:
    """Return hierarchy level (1..4) if `line` is a structural heading, else None."""
    stripped = line.strip()
    if not stripped or len(stripped) > 160:
        return None
    if _PAGE_BANNER_RE.match(stripped):
        return 0  # Special level 0: page banner marker (ignored in breadcrumb to avoid duplicating page_number)
    if _is_table_line(stripped) or " | " in stripped:
        return None

    for level, pattern in LEVEL_HEADING_PATTERNS:
        if pattern.match(stripped):
            return level

    # Short all-caps lines (common in Vietnamese policies/announcements) -> Level 2
    words = stripped.split()
    if 2 <= len(words) <= 10 and stripped.isupper() and not any(c in stripped for c in [",", ";", ".", ":"]):
        # Avoid treating container numbers or HS codes as headings
        if not re.search(r"\b(?:[A-Z]{4}\d{7}|HS\s+CODE|INVOICE\s+NO)\b", stripped):
            return 2

    return None


def _is_heading(line: str) -> bool:
    """Determine whether a line is a structural heading."""
    lvl = _get_heading_level(line)
    return lvl is not None and lvl > 0


def _clean_heading_text(raw_heading: str) -> str:
    """Strip leading markdown '#' symbols and redundant brackets for clean breadcrumbs."""
    s = raw_heading.strip()
    s = re.sub(r"^#{1,6}\s+", "", s).strip()
    if s.startswith("[") and s.endswith("]") and len(s) > 2:
        s = s[1:-1].strip()
    return s


def _detect_sections(text: str) -> list[dict]:
    """Split text into structural sections while maintaining a multi-level heading breadcrumb."""
    lines = text.split("\n")
    sections: list[dict] = []
    heading_stack: dict[int, str] = {}
    current_lines: list[str] = []

    def current_breadcrumb() -> Optional[str]:
        if not heading_stack:
            return None
        ordered = [heading_stack[k] for k in sorted(heading_stack.keys()) if heading_stack[k]]
        return " › ".join(ordered) if ordered else None

    def flush_section() -> None:
        nonlocal current_lines
        if current_lines:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append({"heading": current_breadcrumb(), "body": body})
        current_lines = []

    for line in lines:
        lvl = _get_heading_level(line)
        if lvl == 0:
            # Page banner ("=== TRANG X/Y ==="): keep as a lightweight marker or skip without resetting stack
            continue
        if lvl is not None and lvl > 0:
            flush_section()
            cleaned_h = _clean_heading_text(line)
            # Clear deeper or equal levels in the breadcrumb stack
            for existing_lvl in list(heading_stack.keys()):
                if existing_lvl >= lvl:
                    del heading_stack[existing_lvl]
            heading_stack[lvl] = cleaned_h
        else:
            current_lines.append(line)

    flush_section()
    return sections if sections else [{"heading": None, "body": text.strip()}]


# ---------------------------------------------------------------------------
# Table-Aware Section Splitting
# ---------------------------------------------------------------------------


def _segment_body_into_prose_and_tables(body: str) -> list[tuple[str, list[str]]]:
    """Partition section body lines into alternating ('prose', lines) and ('table', lines) blocks."""
    lines = body.split("\n")
    blocks: list[tuple[str, list[str]]] = []
    cur_type: Optional[str] = None
    cur_lines: list[str] = []

    for line in lines:
        line_type = "table" if _is_table_line(line) else "prose"
        if cur_type is None:
            cur_type = line_type
            cur_lines = [line]
        elif line_type == cur_type:
            cur_lines.append(line)
        else:
            # A table must have at least 2 lines; otherwise treat single pipe line as prose
            if cur_type == "table" and len(cur_lines) < 2:
                if blocks and blocks[-1][0] == "prose":
                    blocks[-1][1].extend(cur_lines)
                else:
                    blocks.append(("prose", cur_lines))
            else:
                blocks.append((cur_type, cur_lines))
            cur_type = line_type
            cur_lines = [line]

    if cur_lines:
        if cur_type == "table" and len(cur_lines) < 2:
            if blocks and blocks[-1][0] == "prose":
                blocks[-1][1].extend(cur_lines)
            else:
                blocks.append(("prose", cur_lines))
        else:
            blocks.append((cur_type or "prose", cur_lines))

    return blocks


def _chunk_single_table(
    table_lines: list[str],
    chunk_size: int,
    intro_line: str = "",
) -> list[str]:
    """Split a Markdown or pipe table into chunks while repeating the table header on every chunk."""
    if not table_lines:
        return []

    full_table = "\n".join(table_lines).strip()
    if intro_line:
        candidate = f"{intro_line}\n{full_table}"
        if len(candidate) <= chunk_size:
            return [candidate]
    elif len(full_table) <= chunk_size:
        return [full_table]

    # Identify header rows (1 row for simple pipe tables, 2 rows if row 2 is |---|---|)
    if len(table_lines) >= 2 and _MD_TABLE_SEP_RE.match(table_lines[1].strip()):
        header_lines = table_lines[:2]
        data_rows = table_lines[2:]
    else:
        header_lines = table_lines[:1]
        data_rows = table_lines[1:]

    if not data_rows:
        return [full_table]

    header_str = "\n".join(header_lines)
    if intro_line and len(intro_line) < 160:
        header_str = f"{intro_line}\n{header_str}"

    overhead = len(header_str) + 1
    row_budget = max(200, chunk_size - overhead)

    table_chunks: list[str] = []
    cur_rows: list[str] = []
    cur_len = 0

    for row in data_rows:
        row_len = len(row) + 1
        if cur_rows and (cur_len + row_len > row_budget):
            table_chunks.append(f"{header_str}\n" + "\n".join(cur_rows))
            # Keep 1 row overlap for continuity when table has many rows
            overlap_row = cur_rows[-1]
            if len(overlap_row) + 1 + row_len <= row_budget and len(cur_rows) >= 2:
                cur_rows = [overlap_row, row]
                cur_len = len(overlap_row) + 1 + row_len
            else:
                cur_rows = [row]
                cur_len = row_len
        else:
            cur_rows.append(row)
            cur_len += row_len

    if cur_rows:
        table_chunks.append(f"{header_str}\n" + "\n".join(cur_rows))

    return table_chunks


def _split_section_body(
    body: str,
    chunk_size: int,
    chunk_overlap: int,
    splitter: RecursiveCharacterTextSplitter,
) -> list[str]:
    """Split a section body into sub-chunks, preserving table headers and row integrity."""
    if len(body) <= chunk_size:
        return [body]

    blocks = _segment_body_into_prose_and_tables(body)
    # Fast path if no tables exist in this section
    if all(b_type == "prose" for b_type, _ in blocks):
        return [c.strip() for c in splitter.split_text(body) if c and c.strip()]

    sub_chunks: list[str] = []
    pending_prose: list[str] = []

    def flush_prose() -> None:
        nonlocal pending_prose
        if not pending_prose:
            return
        prose_text = "\n".join(pending_prose).strip()
        pending_prose = []
        if not prose_text:
            return
        if len(prose_text) <= chunk_size:
            sub_chunks.append(prose_text)
        else:
            sub_chunks.extend(c.strip() for c in splitter.split_text(prose_text) if c and c.strip())

    for b_type, b_lines in blocks:
        if b_type == "prose":
            pending_prose.extend(b_lines)
        else:
            # Table block: check if trailing short prose line is a caption/intro for this table
            intro_line = ""
            if pending_prose:
                non_empty_prose = [ln for ln in pending_prose if ln.strip()]
                if len(non_empty_prose) == 1 and len(non_empty_prose[0].strip()) <= 140:
                    intro_line = non_empty_prose[0].strip()
                    pending_prose = []
                else:
                    flush_prose()

            t_chunks = _chunk_single_table(b_lines, chunk_size=chunk_size, intro_line=intro_line)
            # If the table is small and can merge with the previous prose chunk, merge them
            if (
                len(t_chunks) == 1
                and sub_chunks
                and len(sub_chunks[-1]) + 2 + len(t_chunks[0]) <= chunk_size
            ):
                sub_chunks[-1] = f"{sub_chunks[-1]}\n\n{t_chunks[0]}"
            else:
                sub_chunks.extend(t_chunks)

    flush_prose()
    return [c for c in sub_chunks if c and c.strip()]


# ---------------------------------------------------------------------------
# Public Chunking Functions
# ---------------------------------------------------------------------------


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    page_number: int = 0,
    source_filename: str = "",
    start_chunk_index: int = 0,
) -> list[dict]:
    """Split text into structure-aware chunks with hierarchical contextual headers.

    Args:
        text: Page or section text content.
        chunk_size: Maximum characters per chunk (content portion).
        chunk_overlap: Character overlap between consecutive prose sub-chunks.
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
        separators=["\n\n", "\n", ". ", "; ", ", ", " ", ""],
        is_separator_regex=False,
    )

    result = []
    chunk_idx = start_chunk_index

    for section in sections:
        heading = section["heading"]
        body = section["body"]

        # Build contextual header prefix with full breadcrumb
        header_parts = []
        if source_filename:
            header_parts.append(f"Nguồn: {source_filename}")
        if page_number > 0:
            header_parts.append(f"Trang {page_number}")
        if heading:
            header_parts.append(f"Mục: {heading}")

        context_header = f"[{' | '.join(header_parts)}]\n" if header_parts else ""

        sub_chunks = _split_section_body(
            body=body,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            splitter=splitter,
        )

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
