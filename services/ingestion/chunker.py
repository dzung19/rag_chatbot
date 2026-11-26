"""Text chunking for document ingestion.

Implements Strategy 6 (Legal & Policy Structure-Aware Splitting + Cross-Page Streaming +
Clause-Boundary & Lead-in Preservation + Table-Header Preservation + Contextual Breadcrumbs).
Detects 5-level legal/policy hierarchy (Phần › Chương › Mục/Phụ lục › Tiểu mục › Điều),
keeps numbered clauses (1., 2., a), b)) intact inside their parent Điều across page breaks,
and prepends hierarchical breadcrumb headers to every chunk.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# Page boundary banner emitted by pdf_parser.py (e.g. "=== TRANG 1/5 ===")
_PAGE_BANNER_RE = re.compile(r"^===\s*TRANG\s+\d+(?:\s*/\s*\d+)?\s*===$", re.IGNORECASE)

# Hidden structured JSON block emitted by pdf_parser.py for B/L comparison
_STRUCTURED_JSON_BLOCK_RE = re.compile(
    r"\n*### \[STRUCTURED_SHIPPING_DATA_JSON\]\s*\n.*?\n### \[/STRUCTURED_SHIPPING_DATA_JSON\]\s*",
    re.DOTALL,
)

# Markdown table separator line (e.g. "|---|---|---|" or "| :--- | ---: |")
_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$")

# Legal clause boundary markers inside an Article (Điều)
_CLAUSE_START_RE = re.compile(
    r"^(?:Khoản\s+\d+\b|\d+(?:\.\d+)*\.\s+\S|[a-zđ]\)\s+\S|[-•]\s+\S)",
    re.IGNORECASE,
)

# 5-level structural hierarchy for Vietnamese Legal / Corporate Policy / Markdown docs:
# Level 1: Phần / # H1
# Level 2: Chương / Roman numeral (I., II.) / ## H2
# Level 3: Mục / Phụ lục / [Slide/Sheet] / ### H3
# Level 4: Tiểu mục / #### H4-H6 / [Table/Notes]
# Level 5: Điều (e.g. Điều 12. ...)
LEVEL_HEADING_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    # Level 1: Parts / H1
    (1, re.compile(r"^#\s+.+")),
    (1, re.compile(r"^(?:Phần|PHẦN)\s+[IVXLCDM\d]+\b.*")),
    # Level 2: Chapters / Roman numerals / H2
    (2, re.compile(r"^##\s+.+")),
    (2, re.compile(r"^(?:Chương|CHƯƠNG)\s+[IVXLCDM\d]+\b.*")),
    (2, re.compile(r"^[IVXLCDM]+\.\s+\S.+")),
    # Level 3: Sections / Appendices / Slides / Sheets / H3
    (3, re.compile(r"^###\s+.+")),
    (3, re.compile(r"^(?:Mục|Phụ lục|MỤC|PHỤ LỤC)\s+[IVXLCDM\dA-Z]+\b.*")),
    (3, re.compile(r"^\[(?:Slide|Sheet)\s*.*\]$", re.IGNORECASE)),
    # Level 4: Sub-sections / H4-H6 / Tables
    (4, re.compile(r"^#{4,6}\s+.+")),
    (4, re.compile(r"^(?:Tiểu\s+mục|TIỂU\s+MỤC|Bảng|Hình)\s+[IVXLCDM\d]+\b.*")),
    (4, re.compile(r"^\[(?:Table|Notes)\s*.*\]$", re.IGNORECASE)),
    # Level 5: Legal Articles (Điều X)
    (5, re.compile(r"^(?:Điều|ĐIỀU)\s+\d+[a-zA-Z]?(?:[.:]\s*\S.*|\s+[^\s].*|\s*$)")),
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


def _get_heading_level(line: str, *, in_article: bool = False) -> Optional[int]:
    """Return hierarchy level (1..5) if `line` is a structural heading, else None."""
    stripped = line.strip()
    if not stripped or len(stripped) > 160:
        return None
    if _PAGE_BANNER_RE.match(stripped):
        return 0  # Special level 0: page banner marker
    if _is_table_line(stripped) or " | " in stripped:
        return None

    for level, pattern in LEVEL_HEADING_PATTERNS:
        if pattern.match(stripped):
            return level

    # Numbered ALL-CAPS section title (e.g. "1. QUY ĐỊNH CHUNG") only outside an Article
    if not in_article and re.match(r"^\d+(?:\.\d+)*\.?\s+[A-ZÀ-Ỹ0-9\s\-/()]{3,80}$", stripped):
        if stripped.isupper() and not stripped.endswith("."):
            return 3 if "." in stripped.split()[0].rstrip(".") else 2

    # Short all-caps lines (common in Vietnamese policies/announcements) -> Level 2 (or 4 inside article? keep outside)
    words = stripped.split()
    if not in_article and 2 <= len(words) <= 10 and stripped.isupper() and not any(c in stripped for c in [",", ";", ".", ":"]):
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


def _detect_sections_from_line_stream(line_pages: list[tuple[str, int]]) -> list[dict]:
    """Split a stream of (line, page_number) pairs into structural sections across pages."""
    sections: list[dict] = []
    heading_stack: dict[int, str] = {}
    current_lines: list[tuple[str, int]] = []
    pending_empty_heading: Optional[tuple[str, Optional[str], int]] = None

    def current_breadcrumb() -> Optional[str]:
        if not heading_stack:
            return None
        ordered = [heading_stack[k] for k in sorted(heading_stack.keys()) if heading_stack[k]]
        return " › ".join(ordered) if ordered else None

    def flush_section() -> None:
        nonlocal current_lines, pending_empty_heading
        if current_lines:
            body = "\n".join(ln for ln, _ in current_lines).strip()
            if body:
                pages_in_sec = [p for _, p in current_lines if p > 0]
                start_page = pages_in_sec[0] if pages_in_sec else 0
                sections.append(
                    {
                        "heading": current_breadcrumb(),
                        "body": body,
                        "page": start_page,
                        "line_pages": list(current_lines),
                    }
                )
            pending_empty_heading = None
        current_lines = []

    for line, page_num in line_pages:
        in_article = 5 in heading_stack
        lvl = _get_heading_level(line, in_article=in_article)
        if lvl == 0:
            continue
        if lvl is not None and lvl > 0:
            if current_lines:
                flush_section()
            elif pending_empty_heading is not None:
                prev_raw, prev_bc, prev_page = pending_empty_heading
                # Preserve standalone heading that had no body before a sibling/higher heading
                prev_lvl = _get_heading_level(prev_raw, in_article=False) or 5
                if lvl <= prev_lvl:
                    sections.append(
                        {
                            "heading": prev_bc,
                            "body": prev_raw,
                            "page": prev_page,
                            "line_pages": [(prev_raw, prev_page)],
                        }
                    )

            cleaned_h = _clean_heading_text(line)
            for existing_lvl in list(heading_stack.keys()):
                if existing_lvl >= lvl:
                    del heading_stack[existing_lvl]
            heading_stack[lvl] = cleaned_h
            pending_empty_heading = (line.strip(), current_breadcrumb(), page_num)
        else:
            if line.strip() or current_lines:
                pending_empty_heading = None
                current_lines.append((line, page_num))

    flush_section()
    if pending_empty_heading is not None:
        prev_raw, prev_bc, prev_page = pending_empty_heading
        sections.append(
            {
                "heading": prev_bc,
                "body": prev_raw,
                "page": prev_page,
                "line_pages": [(prev_raw, prev_page)],
            }
        )

    if not sections:
        full_body = "\n".join(ln for ln, _ in line_pages).strip()
        first_p = next((p for _, p in line_pages if p > 0), 0)
        return [{"heading": None, "body": full_body, "page": first_p, "line_pages": line_pages}] if full_body else []
    return sections


def _detect_sections(text: str) -> list[dict]:
    """Split text into structural sections while maintaining a multi-level heading breadcrumb."""
    line_pages = [(ln, 0) for ln in text.split("\n")]
    return _detect_sections_from_line_stream(line_pages)


# ---------------------------------------------------------------------------
# Table-Aware & Clause-Aware Section Splitting
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


def _split_prose_with_clauses(
    prose_text: str,
    chunk_size: int,
    splitter: RecursiveCharacterTextSplitter,
) -> list[str]:
    """Split long legal/policy prose along clause boundaries (1., 2., a), b)) and preserve lead-in context."""
    if len(prose_text) <= chunk_size:
        return [prose_text]

    lines = prose_text.split("\n")
    clause_blocks: list[str] = []
    cur_block: list[str] = []

    for ln in lines:
        if cur_block and _CLAUSE_START_RE.match(ln.strip()):
            clause_blocks.append("\n".join(cur_block).strip())
            cur_block = [ln]
        else:
            cur_block.append(ln)
    if cur_block:
        clause_blocks.append("\n".join(cur_block).strip())
    clause_blocks = [b for b in clause_blocks if b]

    # If no clause structure found, fall back to recursive splitter
    if len(clause_blocks) <= 1:
        return [c.strip() for c in splitter.split_text(prose_text) if c and c.strip()]

    # Detect short introductory lead-in before the first numbered clause
    lead_in = ""
    if not _CLAUSE_START_RE.match(clause_blocks[0].lstrip()) and len(clause_blocks[0]) <= 250:
        lead_in = clause_blocks[0]

    chunks: list[str] = []
    cur_parts: list[str] = []
    cur_len = 0

    def flush_parts() -> None:
        nonlocal cur_parts, cur_len
        if not cur_parts:
            return
        text_out = "\n".join(cur_parts).strip()
        if chunks and lead_in and not text_out.startswith(lead_in):
            text_out = f"[Dẫn nhập: {lead_in}]\n{text_out}"
        chunks.append(text_out)
        cur_parts = []
        cur_len = 0

    lead_overhead = (len(lead_in) + 16) if lead_in else 0
    for blk in clause_blocks:
        if len(blk) > chunk_size:
            flush_parts()
            sub_pieces = [c.strip() for c in splitter.split_text(blk) if c and c.strip()]
            for sp in sub_pieces:
                if chunks and lead_in and not sp.startswith(lead_in):
                    chunks.append(f"[Dẫn nhập: {lead_in}]\n{sp}")
                else:
                    chunks.append(sp)
            continue

        effective_limit = chunk_size - (lead_overhead if chunks else 0)
        blk_len = len(blk) + 1
        if cur_parts and (cur_len + blk_len > max(250, effective_limit)):
            flush_parts()
        cur_parts.append(blk)
        cur_len += blk_len

    flush_parts()
    return [c for c in chunks if c and c.strip()]


def _split_section_body(
    body: str,
    chunk_size: int,
    chunk_overlap: int,
    splitter: RecursiveCharacterTextSplitter,
) -> list[str]:
    """Split a section body into sub-chunks, preserving clause boundaries, table headers, and row integrity."""
    if len(body) <= chunk_size:
        return [body]

    blocks = _segment_body_into_prose_and_tables(body)
    if all(b_type == "prose" for b_type, _ in blocks):
        return _split_prose_with_clauses(body, chunk_size=chunk_size, splitter=splitter)

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
        sub_chunks.extend(_split_prose_with_clauses(prose_text, chunk_size=chunk_size, splitter=splitter))

    for b_type, b_lines in blocks:
        if b_type == "prose":
            pending_prose.extend(b_lines)
        else:
            intro_line = ""
            if pending_prose:
                non_empty_prose = [ln for ln in pending_prose if ln.strip()]
                if len(non_empty_prose) == 1 and len(non_empty_prose[0].strip()) <= 140:
                    intro_line = non_empty_prose[0].strip()
                    pending_prose = []
                else:
                    flush_prose()

            t_chunks = _chunk_single_table(b_lines, chunk_size=chunk_size, intro_line=intro_line)
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


def _build_chunks_from_sections(
    sections: list[dict],
    chunk_size: int,
    chunk_overlap: int,
    source_filename: str,
    start_chunk_index: int,
    default_page: int = 0,
) -> list[dict]:
    """Convert detected structural sections into contextualized chunks."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", "; ", ", ", " ", ""],
        is_separator_regex=False,
    )

    result: list[dict] = []
    chunk_idx = start_chunk_index

    for section in sections:
        heading = section["heading"]
        body = section["body"]
        sec_page = section.get("page") or default_page

        header_parts = []
        if source_filename:
            header_parts.append(f"Nguồn: {source_filename}")
        if sec_page > 0:
            header_parts.append(f"Trang {sec_page}")
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
                        "page": sec_page,
                        "heading": heading or "",
                        "source": source_filename,
                        "context_header": context_header.strip(),
                    }
                )
                chunk_idx += 1

    return result


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    page_number: int = 0,
    source_filename: str = "",
    start_chunk_index: int = 0,
) -> list[dict]:
    """Split text into structure-aware chunks with hierarchical contextual headers."""
    if not text or not text.strip():
        return []

    clean_text = _STRUCTURED_JSON_BLOCK_RE.sub("\n", text).strip()
    if not clean_text:
        return []

    line_pages = [(ln, page_number) for ln in clean_text.split("\n")]
    sections = _detect_sections_from_line_stream(line_pages)
    result = _build_chunks_from_sections(
        sections=sections,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        source_filename=source_filename,
        start_chunk_index=start_chunk_index,
        default_page=page_number,
    )

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
    """Chunk all pages of a parsed document as a continuous cross-page stream.

    Maintains hierarchical heading breadcrumbs and keeps articles/clauses unbroken
    across page breaks while preserving accurate starting page numbers on every chunk.
    """
    line_pages: list[tuple[str, int]] = []
    for page_num, page_text in enumerate(pages, start=1):
        if not page_text or not page_text.strip():
            continue
        cleaned_page = _STRUCTURED_JSON_BLOCK_RE.sub("\n", page_text).strip()
        if not cleaned_page:
            continue
        for ln in cleaned_page.split("\n"):
            line_pages.append((ln, page_num))

    if not line_pages:
        return []

    sections = _detect_sections_from_line_stream(line_pages)
    all_chunks = _build_chunks_from_sections(
        sections=sections,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        source_filename=source_filename,
        start_chunk_index=0,
    )

    logger.info(
        "Document '%s' (%d pages) chunked into %d total chunks (cross-page sections=%d)",
        source_filename,
        len(pages),
        len(all_chunks),
        len(sections),
    )
    return all_chunks
