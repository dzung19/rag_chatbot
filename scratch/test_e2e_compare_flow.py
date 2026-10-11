import os
import sys
import tempfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock qdrant if needed
try:
    import qdrant_client
except ImportError:
    mock_q = MagicMock()
    sys.modules["qdrant_client"] = mock_q
    sys.modules["qdrant_client.models"] = mock_q

from shared.sqlite_db import save_document_full_and_chunks, get_sqlite_connection
from shared.models import Skill, SkillType
from services.rag_engine.retriever import Retriever
from services.rag_engine.prompt_builder import build_rag_messages


def test_full_e2e_compare_pipeline():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        os.environ["SQLITE_DB_PATH"] = db_path

        doc1_id = "uuid-contract-2023"
        doc1_name = "Hop_dong_2023.pdf"
        doc1_text = (
            "ĐIỀU 1: PHẠM VI DỊCH VỤ\n"
            "Bên B cung cấp dịch vụ bảo trì định kỳ 12 tháng.\n\n"
            "ĐIỀU 2: PHÍ DỊCH VỤ VÀ THANH TOÁN\n"
            "Tổng chi phí dịch vụ là 50.000.000 VNĐ, thanh toán 100% khi ký kết.\n\n"
            "ĐIỀU 3: BẢO HÀNH\n"
            "Thời hạn bảo hành linh kiện là 6 tháng kể từ ngày bàn giao."
        )

        doc2_id = "uuid-contract-2024"
        doc2_name = "Hop_dong_2024.pdf"
        doc2_text = (
            "ĐIỀU 1: PHẠM VI DỊCH VỤ\n"
            "Bên B cung cấp dịch vụ bảo trì định kỳ 24 tháng kèm hỗ trợ khẩn cấp 24/7.\n\n"
            "ĐIỀU 2: PHÍ DỊCH VỤ VÀ THANH TOÁN\n"
            "Tổng chi phí dịch vụ là 85.000.000 VNĐ, thanh toán chia 2 đợt (50% ký kết, 50% sau 12 tháng).\n\n"
            "ĐIỀU 3: BẢO HÀNH\n"
            "Thời hạn bảo hành linh kiện là 12 tháng kể từ ngày bàn giao."
        )

        # 1. Simulate Ingestion saving both documents
        save_document_full_and_chunks(
            document_id=doc1_id,
            filename=doc1_name,
            full_text=doc1_text,
            page_count=1,
            file_size_bytes=len(doc1_text),
            file_type="pdf",
            chunks=[{"chunk_index": 0, "page": 1, "heading": "Hợp đồng 2023", "text": doc1_text}],
        )

        save_document_full_and_chunks(
            document_id=doc2_id,
            filename=doc2_name,
            full_text=doc2_text,
            page_count=1,
            file_size_bytes=len(doc2_text),
            file_type="pdf",
            chunks=[{"chunk_index": 0, "page": 1, "heading": "Hợp đồng 2024", "text": doc2_text}],
        )

        # 2. Retriever fetches both full documents
        retriever = Retriever()
        full_docs = retriever.get_full_documents([doc1_id, doc2_id])
        assert len(full_docs) == 2, f"Expected 2 docs, got {len(full_docs)}"
        assert full_docs[0]["metadata"]["filename"] == doc1_name
        assert full_docs[1]["metadata"]["filename"] == doc2_name
        assert full_docs[0]["text"] == doc1_text
        assert full_docs[1]["text"] == doc2_text

        # 3. Prompt Builder builds structured compare prompt
        skill = Skill(
            id="business-compare",
            name="So sánh Nghiệp vụ",
            description="So sánh chi tiết 2 file",
            type=SkillType.MAIN,
            system_prompt_addon="Lập bảng so sánh chi tiết các điều khoản chính và làm nổi bật sự thay đổi.",
            created_at="2026-10-10",
            updated_at="2026-10-10",
        )

        messages = build_rag_messages(
            query="So sánh các điểm khác biệt chính giữa 2 hợp đồng về thời hạn, chi phí và bảo hành.",
            context_chunks=full_docs,
            main_skill=skill,
        )

        assert len(messages) == 2
        sys_msg = messages[0]["content"]
        user_msg = messages[1]["content"]

        assert "[MAIN DIRECTIVE - So sánh Nghiệp vụ]" in sys_msg
        assert "Lập bảng so sánh chi tiết" in sys_msg
        assert f"=== TÀI LIỆU 1 (GỐC / THAM CHIẾU): {doc1_name} ===" in user_msg
        assert f"=== TÀI LIỆU 2 (ĐỐI CHIẾU / SO SÁNH): {doc2_name} ===" in user_msg
        assert "50.000.000 VNĐ" in user_msg
        assert "85.000.000 VNĐ" in user_msg
        assert "24/7" in user_msg

        print("[PASS] Full End-to-End Compare Pipeline Test Passed Successfully!")
    finally:
        os.environ.pop("SQLITE_DB_PATH", None)
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    test_full_e2e_compare_pipeline()
