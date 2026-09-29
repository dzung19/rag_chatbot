"""Text chunking for document ingestion.

Uses langchain-text-splitters for recursive character splitting with
configurable chunk size and overlap.
"""

from __future__ import annotations

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

<<<<<<< HEAD
# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200

# Parent blocks are what gets sent to the LLM; children are what gets embedded.
DEFAULT_PARENT_SIZE = 2400
DEFAULT_PARENT_OVERLAP = 100

# Sections shorter than this are merged into the following section instead of
# becoming a near-useless standalone chunk (e.g. a lone heading line).
MIN_SECTION_CHARS = 120

_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

# Heading / structural boundary patterns, checked line by line.
_HEADING_PATTERNS = [
    re.compile(r"^#{1,6}\s+\S"),                       # markdown: ## Title
    re.compile(r"^\[(Slide|Sheet|Table|Notes)[^\]]*\]"),  # parser markers
    re.compile(r"^\d+(\.\d+)*[\.\)]?\s+\S.{2,}$"),     # 1. / 1.2.3 Title
    re.compile(r"^[A-Z][A-Z0-9 \-/&]{5,}$"),           # ALL CAPS HEADING
    re.compile(r"^(Chapter|Section|Phần|Chương|Mục)\s+[\dIVX]+", re.IGNORECASE),
]


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    return any(p.match(stripped) for p in _HEADING_PATTERNS)


def _clean_heading(line: str) -> str:
    """Normalise a heading line into a short breadcrumb label."""
    return re.sub(r"^[#\s\d\.\)\[\]]+", "", line.strip()).strip(" ]:-") or line.strip()


# ---------------------------------------------------------------------------
# Structure-aware segmentation
# ---------------------------------------------------------------------------


def split_into_sections(text: str) -> list[dict]:
    """Split raw text into structural sections.

    Returns:
        List of dicts with 'title' (may be empty) and 'text'.
    """
    lines = text.splitlines()
    sections: list[dict] = []
    current_title = ""
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append({"title": current_title, "text": body})

    for line in lines:
        if _is_heading(line):
            flush()
            buffer = [line]
            current_title = _clean_heading(line)
        else:
            buffer.append(line)
    flush()

    if not sections:
        return [{"title": "", "text": text.strip()}] if text.strip() else []

    # Merge only heading-only stubs forward; do not absorb short value rows.
    merged: list[dict] = []
    for section in sections:
        if (merged and merged[-1]["text"].strip() == merged[-1]["title"].strip()
                and len(merged[-1]["text"]) < MIN_SECTION_CHARS):
            prev = merged.pop()
            merged.append({"title": prev["title"], "text": prev["text"] + "\n" + section["text"]})
        else:
            merged.append(section)
    return merged


_KEY_VALUE = re.compile(r"^.{2,120}?\s*[:：]\s*\S.*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

def _atomic_kind(line: str) -> str:
    line = line.strip()
    if not line or _TABLE_SEPARATOR.fullmatch(line):
        return ""
    cells = [c.strip() for c in line.strip("|").split("|")]
    if "|" in line and len(cells) >= 2 and cells[0] and cells[1]:
        return "table_row"
    if _is_heading(line):
        return ""
    return "key_value" if _KEY_VALUE.fullmatch(line) else ""

def _segments(text: str) -> list[tuple[str, str]]:
    result = []
    prose = []
    for line in text.splitlines():
        kind = _atomic_kind(line)
        if kind:
            if prose and "\n".join(prose).strip():
                result.append(("prose", "\n".join(prose).strip()))
            prose = []
            result.append((kind, line.strip()))
        else:
            prose.append(line)
    if prose and "\n".join(prose).strip():
        result.append(("prose", "\n".join(prose).strip()))
    return result

# ---------------------------------------------------------------------------
# Core chunking
# ---------------------------------------------------------------------------


def _splitter(size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        length_function=len,
        separators=_SEPARATORS,
        is_separator_regex=False,
    )


def _build_context_header(source: str, section_title: str, page: int) -> str:
    parts = [p for p in (source, section_title) if p]
    breadcrumb = " > ".join(parts)
    if page:
        breadcrumb = f"{breadcrumb} (p.{page})" if breadcrumb else f"p.{page}"
    return f"[{breadcrumb}]\n" if breadcrumb else ""


def chunk_pages(
    pages: list[str],
    *,
    source: str = "",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    parent_size: int = DEFAULT_PARENT_SIZE,
    parent_overlap: int = DEFAULT_PARENT_OVERLAP,
    use_parent_child: bool = True,
    add_context_header: bool = True,
) -> list[dict]:
    """Chunk a document that is already split page-by-page.

    Args:
        pages: Text of each page / slide / sheet, in order.
        source: Original filename, used in the contextual header.
        chunk_size: Max characters per child chunk (the embedded unit).
        chunk_overlap: Overlap between child chunks.
        parent_size: Max characters per parent block (the retrieved context).
        parent_overlap: Overlap between parent blocks.
        use_parent_child: If False, children are emitted without parent text.
        add_context_header: Prepend a 'file > section (p.N)' breadcrumb to the
            text that gets embedded.

    Returns:
        List of dicts with keys:
            text          - raw chunk text (no header)
            embed_text    - text to send to the embedding model
            chunk_index   - global running index
            page          - 1-based page / slide / sheet number
            section       - section title, '' when unknown
            parent_index  - index of the owning parent block
            parent_text   - full parent block text ('' if disabled)
    """
    child_splitter = _splitter(chunk_size, chunk_overlap)
    parent_splitter = _splitter(parent_size, parent_overlap)

    results: list[dict] = []
    chunk_index = 0
    parent_index = 0

    for page_no, page_text in enumerate(pages, start=1):
        if not page_text or not page_text.strip():
            continue

        for section in split_into_sections(page_text):
            title = section["title"]
            for kind, segment in _segments(section["text"]):
                parents = [segment] if kind != "prose" else parent_splitter.split_text(segment)
                for parent in parents:
                    parent = parent.strip()
                    if not parent:
                        continue
                    children = ([parent] if kind != "prose" or len(parent) <= chunk_size
                                else child_splitter.split_text(parent))
                    for child in children:
                        child = child.strip()
                        if not child:
                            continue
                        header = _build_context_header(source, title, page_no) if add_context_header else ""
                        results.append({"text": child, "embed_text": f"{header}{child}",
                                        "chunk_index": chunk_index, "page": page_no,
                                        "section": title, "parent_index": parent_index,
                                        "parent_text": parent if use_parent_child else "",
                                        "record_type": kind})
                        chunk_index += 1
                    parent_index += 1
    logger.debug(
        "Chunked %d page(s) into %d child chunks across %d parent block(s) "
        "(child=%d/%d, parent=%d/%d)",
        len(pages),
        len(results),
        parent_index,
        chunk_size,
        chunk_overlap,
        parent_size,
        parent_overlap,
    )
    return results

=======
>>>>>>> parent of 41b1889 (update)

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
