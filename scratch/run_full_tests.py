"""Comprehensive unit tests for RAG performance enhancements.

Provides mock shims for container dependencies (fastapi, langchain_text_splitters, chromadb, httpx)
so test assertions run standalone on any host python.
"""

import asyncio
import os
import re
import sqlite3
import sys
from unittest.mock import MagicMock

import types

def ensure_module(name):
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        sub = ".".join(parts[:i])
        if sub not in sys.modules:
            mod = MagicMock()
            mod.__spec__ = MagicMock()
            sys.modules[sub] = mod
        if i > 1:
            parent = ".".join(parts[:i-1])
            setattr(sys.modules[parent], parts[i-1], sys.modules[sub])

for mod in [
    "langchain_text_splitters",
    "fastapi",
    "fastapi.responses",
    "fastapi.security",
    "sse_starlette",
    "sse_starlette.sse",
    "chromadb",
    "pydantic",
    "pydantic_settings",
    "httpx",
    "starlette",
    "starlette.middleware",
    "starlette.middleware.base",
    "starlette.types",
]:
    ensure_module(mod)

# Define a functional mock for RecursiveCharacterTextSplitter if missing
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:
    class MockRecursiveSplitter:
        def __init__(self, chunk_size=1000, chunk_overlap=200, **kwargs):
            self.chunk_size = chunk_size
            self.chunk_overlap = chunk_overlap

        def split_text(self, text: str) -> list[str]:
            if not text:
                return []
            chunks = []
            start = 0
            while start < len(text):
                end = min(start + self.chunk_size, len(text))
                chunks.append(text[start:end])
                if end == len(text):
                    break
                start = end - self.chunk_overlap
            return chunks

    sys.modules["langchain_text_splitters"].RecursiveCharacterTextSplitter = MockRecursiveSplitter


# Test 1: Chunking logic (Strategy 5: Structure-Aware + Contextual Headers)
def test_chunking():
    print("--- Test 1: Strategy 5 Chunking ---")
    sys.path.insert(0, os.path.abspath("services/ingestion"))
    from chunker import _is_heading, _detect_sections, chunk_text, chunk_document_pages

    # Heading detection tests
    assert _is_heading("# Giới thiệu công ty") is True
    assert _is_heading("1.1 Phạm vi áp dụng") is True
    assert _is_heading("Điều 5. Thời gian làm việc") is True
    assert _is_heading("[Slide 2]") is True
    assert _is_heading("[Sheet: Lộ trình]") is True
    assert _is_heading("QUY ĐỊNH CHUNG") is True
    assert _is_heading("Đây là một câu văn bình thường dài hơn.") is False

    doc_text = """# QUY ĐỊNH XE BUÝT BIVN
Công ty Brother bố trí xe buýt cho cán bộ công nhân viên.

## Tuyến Hải Dương
Xe chạy từ 6h00 sáng đến 18h00 chiều hàng ngày.
Điểm đón: Bệnh viện Đa khoa, Cẩm Giàng, Mao Điền.

## Tuyến Hưng Yên
Xe chạy từ 5h45 sáng. Điểm đón: Phố Nối, Như Quỳnh."""

    sections = _detect_sections(doc_text)
    print(f"Detected {len(sections)} sections:")
    for s in sections:
        print(f"  Heading: {s['heading']!r} -> Body len: {len(s['body'])}")
    assert len(sections) == 3

    # Test chunk_text with contextual headers
    chunks = chunk_text(
        text=doc_text,
        chunk_size=500,
        chunk_overlap=50,
        page_number=1,
        source_filename="01_Quy_dinh.pdf",
    )
    assert len(chunks) >= 3
    for c in chunks:
        assert "[Nguồn: 01_Quy_dinh.pdf | Trang 1" in c["text"]
        assert "chunk_index" in c
        assert "page" in c
        assert c["page"] == 1
    print(f"Generated {len(chunks)} chunks with contextual headers:")
    print(f"Sample chunk text:\n{chunks[0]['text'][:150]}...")

    # Test multi-page chunking
    pages = [
        "Trang 1: Lời mở đầu về công ty Brother.",
        "Trang 2: Chi tiết lộ trình xe buýt đưa đón công nhân viên.",
    ]
    all_chunks = chunk_document_pages(pages, source_filename="TestDoc.pdf")
    assert len(all_chunks) == 2
    assert all_chunks[0]["page"] == 1
    assert all_chunks[1]["page"] == 2
    assert all_chunks[0]["chunk_index"] == 0
    assert all_chunks[1]["chunk_index"] == 1
    print("Multi-page chunking test PASSED!")


# Test 2: SQLite FTS5 BM25 with Vietnamese unicode61 tokenizer
def test_sqlite_fts5():
    print("\n--- Test 2: SQLite FTS5 with unicode61 remove_diacritics 2 ---")
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE VIRTUAL TABLE document_chunks USING fts5(
            id UNINDEXED,
            document_id UNINDEXED,
            filename UNINDEXED,
            page UNINDEXED,
            heading UNINDEXED,
            text,
            tokenize='unicode61 remove_diacritics 2'
        );
    """)

    sample_chunks = [
        ("c1", "doc1", "01_Lo_trinh_xe_buyt.pdf", 1, "Tuyến HD-01", "Lộ trình xe buýt BIVN đón tại Bệnh viện Đa khoa Hải Dương lúc 6h15"),
        ("c2", "doc2", "02_Noi_quy_lao_dong.pdf", 3, "Điều 10", "Quy định về thời gian làm việc, nghỉ phép năm và chế độ thai sản"),
        ("c3", "doc1", "01_Lo_trinh_xe_buyt.pdf", 2, "Tuyến HY-02", "Xe đưa đón công nhân viên tại trạm Phố Nối Hưng Yên"),
    ]

    conn.executemany(
        "INSERT INTO document_chunks (id, document_id, filename, page, heading, text) VALUES (?, ?, ?, ?, ?, ?)",
        sample_chunks
    )

    def search_fts(query):
        clean_words = re.findall(r"[\w]+", query)
        safe_terms = [w.replace('"', '""') for w in clean_words if len(w) > 1]
        clean_phrase = " ".join(safe_terms)
        if len(safe_terms) > 1:
            or_terms = " OR ".join(f'"{t}"' for t in safe_terms)
            fts_match = f'"{clean_phrase}" OR ({or_terms})'
        else:
            fts_match = f'"{safe_terms[0]}"'

        cur = conn.execute(
            "SELECT id, filename, page, heading, text, bm25(document_chunks) as rank_score "
            "FROM document_chunks WHERE document_chunks MATCH ? ORDER BY rank_score ASC",
            (fts_match,)
        )
        return cur.fetchall()

    # Query with accents
    res1 = search_fts("xe buýt bệnh viện đa khoa")
    assert len(res1) >= 1
    assert res1[0][0] == "c1"
    print(f"Accent search 'xe buýt bệnh viện đa khoa' -> matched chunk {res1[0][0]} (Page {res1[0][2]})")

    # Query WITHOUT accents
    res2 = search_fts("xe buyt benh vien da khoa")
    assert len(res2) >= 1
    assert res2[0][0] == "c1"
    print(f"Unaccented search 'xe buyt benh vien da khoa' -> matched chunk {res2[0][0]} successfully!")

    # Multi-term non-contiguous query
    res3 = search_fts("quy định thai sản")
    assert len(res3) >= 1
    assert res3[0][0] == "c2"
    print(f"Non-contiguous query 'quy định thai sản' -> matched chunk {res3[0][0]} successfully!")
    print("SQLite FTS5 test PASSED!")


# Test 3: Prompt Builder & Context Budgeting
def test_prompt_builder():
    print("\n--- Test 3: Prompt Builder & Context Budgeting ---")
    sys.path.insert(0, os.path.abspath("services/rag_engine"))
    from prompt_builder import build_rag_messages, build_rag_prompt, build_context_text

    chunks = [
        {
            "id": "c1",
            "text": "Nội dung chi tiết về xe buýt tuyến HD-01 đón công nhân viên.",
            "metadata": {"filename": "01_Lo_trinh.pdf", "page": 2, "heading": "HD-01"},
            "score": 0.92,
        },
        {
            "id": "c2",
            "text": "Thông tin giờ xuất bến và danh sách điểm dừng.",
            "metadata": {"filename": "01_Lo_trinh.pdf", "page": 3, "heading": "Giờ chạy"},
            "score": 0.85,
        }
    ]

    messages = build_rag_messages(
        query="Xe buýt HD-01 đón ở đâu?",
        context_chunks=chunks,
    )
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "BIVN" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "=== CONTEXT DOCUMENTS ===" in messages[1]["content"]
    assert "01_Lo_trinh.pdf" in messages[1]["content"]
    assert "Page: 2" in messages[1]["content"]
    assert "HD-01" in messages[1]["content"]
    assert "<user_query>Xe buýt HD-01 đón ở đâu?</user_query>" in messages[1]["content"]
    print("Message building with system/user separation PASSED!")

    # Test truncation budget
    long_chunks = [
        {"id": f"c_{i}", "text": "A" * 1000, "metadata": {"filename": f"doc_{i}.pdf"}, "score": 0.8}
        for i in range(10)
    ]
    budgeted_text = build_context_text(long_chunks, max_chars=2500)
    assert len(budgeted_text) <= 3000
    print(f"Context budgeting test: requested max 2500 chars -> resulting len: {len(budgeted_text)}")
    print("Prompt Builder test PASSED!")


# Test 4: LaTeX Arrow Cleaning (Sync and Streaming)
def test_latex_arrow_cleaner():
    print("\n--- Test 4: LaTeX Arrow Cleaning ---")
    sys.path.insert(0, os.path.abspath("services/rag_engine"))
    from main import _clean_latex_arrows, _clean_stream_tokens

    raw_text = r"Điểm A $\rightarrow$ Điểm B \rightarrow Điểm C $\leftarrow$ Điểm D --> Điểm E -> Điểm F"
    cleaned = _clean_latex_arrows(raw_text)
    assert "$\rightarrow$" not in cleaned
    assert r"\rightarrow" not in cleaned
    assert "-->" not in cleaned
    assert "->" not in cleaned
    print("Sync cleaner output:", cleaned)

    async def run_streaming_test():
        async def mock_stream():
            tokens = ["Điểm ", "1 ", "$\\right", "arrow$", " Điểm ", "2 ", "\\rightarrow", " Điểm 3"]
            for t in tokens:
                yield t

        collected = []
        async for piece in _clean_stream_tokens(mock_stream()):
            collected.append(piece)
        full_stream = "".join(collected)
        assert full_stream == "Điểm 1 → Điểm 2 → Điểm 3", f"Unexpected: {full_stream}"
        print("Streaming cleaner output:", full_stream)

    asyncio.run(run_streaming_test())
    print("LaTeX Arrow cleaner test PASSED!")


if __name__ == "__main__":
    test_chunking()
    test_sqlite_fts5()
    test_prompt_builder()
    test_latex_arrow_cleaner()
    print("\n==============================================")
    print("ALL RAG PERFORMANCE UNIT TESTS PASSED (4/4)!")
    print("==============================================")
