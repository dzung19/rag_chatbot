import os
import sys
import tempfile
import sqlite3
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock qdrant_client if not present in host environment
try:
    import qdrant_client
except ImportError:
    mock_q = MagicMock()
    sys.modules["qdrant_client"] = mock_q
    sys.modules["qdrant_client.models"] = mock_q

from shared.models import ChatRequest, Skill, SkillType
from services.rag_engine.prompt_builder import (
    build_compare_context_text,
    build_rag_messages,
)
from services.rag_engine.retriever import Retriever


def test_chat_request_selected_document_ids():
    req = ChatRequest(
        query="So sánh 2 tài liệu",
        skill_id="business-compare",
        selected_document_ids=["doc-uuid-1", "doc-uuid-2"],
    )
    assert req.selected_document_ids == ["doc-uuid-1", "doc-uuid-2"]
    data = req.model_dump()
    assert data["selected_document_ids"] == ["doc-uuid-1", "doc-uuid-2"]
    print("[PASS] test_chat_request_selected_document_ids passed")


def test_build_compare_context():
    chunks = [
        {
            "id": "full_doc1",
            "text": "Điều 1: Giờ làm việc từ 8:00 đến 17:00.\nĐiều 2: Nghỉ trưa 60 phút.",
            "metadata": {"filename": "Quy_dinh_2023.pdf", "document_id": "doc1"},
            "score": 1.0,
        },
        {
            "id": "full_doc2",
            "text": "Điều 1: Giờ làm việc từ 8:30 đến 17:30.\nĐiều 2: Nghỉ trưa 45 phút.",
            "metadata": {"filename": "Quy_dinh_2024.pdf", "document_id": "doc2"},
            "score": 1.0,
        },
    ]

    context = build_compare_context_text(chunks, max_chars=4000)
    assert "=== TÀI LIỆU 1 (GỐC / THAM CHIẾU): Quy_dinh_2023.pdf ===" in context
    assert "=== TÀI LIỆU 2 (ĐỐI CHIẾU / SO SÁNH): Quy_dinh_2024.pdf ===" in context
    assert "8:00 đến 17:00" in context
    assert "8:30 đến 17:30" in context
    print("[PASS] test_build_compare_context passed")


def test_build_compare_balanced_truncation():
    # Long documents exceeding budget
    text_long_1 = "A" * 15000
    text_long_2 = "B" * 15000
    chunks = [
        {"id": "doc1", "text": text_long_1, "metadata": {"filename": "doc1.txt"}},
        {"id": "doc2", "text": text_long_2, "metadata": {"filename": "doc2.txt"}},
    ]
    context = build_compare_context_text(chunks, max_chars=2000)
    # Both should be represented and truncated
    assert "doc1.txt" in context
    assert "doc2.txt" in context
    assert "Nội dung phía sau đã được cắt bớt" in context
    # Total context length should be roughly within budget
    assert len(context) <= 2500
    print("[PASS] test_build_compare_balanced_truncation passed")


def test_build_rag_messages_with_skill():
    skill = Skill(
        id="business-compare",
        name="So sánh Nghiệp vụ",
        description="So sánh chi tiết 2 file",
        type=SkillType.MAIN,
        system_prompt_addon="Chỉ ra các điểm khác biệt chi tiết.",
        created_at="2026-10-09",
        updated_at="2026-10-09",
    )
    chunks = [
        {"id": "1", "text": "Hợp đồng cũ", "metadata": {"filename": "cu.pdf"}},
        {"id": "2", "text": "Hợp đồng mới", "metadata": {"filename": "moi.pdf"}},
    ]
    messages = build_rag_messages(
        query="Chỉ ra điểm khác nhau",
        context_chunks=chunks,
        main_skill=skill,
    )
    assert len(messages) == 2
    assert "[MAIN DIRECTIVE - So sánh Nghiệp vụ]" in messages[0]["content"]
    assert "=== TÀI LIỆU 1" in messages[1]["content"]
    assert "=== TÀI LIỆU 2" in messages[1]["content"]
    print("[PASS] test_build_rag_messages_with_skill passed")


def test_retriever_get_full_documents_sqlite():
    # Create temp DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE VIRTUAL TABLE document_chunks USING fts5(
                id UNINDEXED,
                document_id UNINDEXED,
                filename UNINDEXED,
                page UNINDEXED,
                heading UNINDEXED,
                text
            );
        """)
        # Insert chunk 1 & 2 for doc A
        conn.execute("INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?)",
                     ("A_1", "docA", "fileA.pdf", 1, "Sec 1", "Nội dung phần 1 của file A."))
        conn.execute("INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?)",
                     ("A_2", "docA", "fileA.pdf", 2, "Sec 2", "Nội dung phần 2 của file A."))
        # Insert chunk 1 for doc B
        conn.execute("INSERT INTO document_chunks VALUES (?, ?, ?, ?, ?, ?)",
                     ("B_1", "docB", "fileB.pdf", 1, "Intro", "Nội dung hoàn chỉnh của file B."))
        conn.commit()
        conn.close()

        # Monkey patch get_sqlite_connection
        import services.rag_engine.retriever as ret_mod
        orig_get_conn = ret_mod.get_sqlite_connection

        def mock_conn():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        ret_mod.get_sqlite_connection = mock_conn

        retriever = Retriever()
        docs = retriever.get_full_documents(["docA", "docB"])

        assert len(docs) == 2
        assert docs[0]["metadata"]["filename"] == "fileA.pdf"
        assert "Nội dung phần 1 của file A.\n\nNội dung phần 2 của file A." == docs[0]["text"]
        assert docs[1]["metadata"]["filename"] == "fileB.pdf"
        assert "Nội dung hoàn chỉnh của file B." == docs[1]["text"]

        ret_mod.get_sqlite_connection = orig_get_conn
        print("[PASS] test_retriever_get_full_documents_sqlite passed")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_retriever_get_full_documents_prioritizes_full_table():
    from shared.sqlite_db import save_document_full_and_chunks, get_sqlite_connection
    import services.rag_engine.retriever as ret_mod

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        os.environ["SQLITE_DB_PATH"] = db_path

        # Save via helper
        save_document_full_and_chunks(
            document_id="doc1",
            filename="hop_dong_A.pdf",
            full_text="Toàn văn điều khoản hợp đồng A.",
            page_count=2,
            file_size_bytes=1024,
            file_type="pdf",
            chunks=[{"chunk_index": 0, "page": 1, "heading": "Mục 1", "text": "Chunk 1 của A"}],
        )
        save_document_full_and_chunks(
            document_id="doc2",
            filename="hop_dong_B.pdf",
            full_text="Toàn văn điều khoản hợp đồng B.",
            page_count=3,
            file_size_bytes=2048,
            file_type="pdf",
            chunks=[{"chunk_index": 0, "page": 1, "heading": "Mục 1", "text": "Chunk 1 của B"}],
        )

        retriever = Retriever()
        docs = retriever.get_full_documents(["doc1", "doc2"])

        assert len(docs) == 2
        # Clean full text is retrieved instead of chunk text
        assert docs[0]["text"] == "Toàn văn điều khoản hợp đồng A."
        assert docs[0]["metadata"]["filename"] == "hop_dong_A.pdf"
        assert docs[1]["text"] == "Toàn văn điều khoản hợp đồng B."
        assert docs[1]["metadata"]["filename"] == "hop_dong_B.pdf"

        print("[PASS] test_retriever_get_full_documents_prioritizes_full_table passed")
    finally:
        os.environ.pop("SQLITE_DB_PATH", None)
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    test_chat_request_selected_document_ids()
    test_build_compare_context()
    test_build_compare_balanced_truncation()
    test_build_rag_messages_with_skill()
    test_retriever_get_full_documents_sqlite()
    test_retriever_get_full_documents_prioritizes_full_table()
    print("\nALL BUSINESS COMPARE TESTS PASSED SUCCESSFULLY!")
