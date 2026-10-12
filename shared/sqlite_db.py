import logging
import sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone

from shared.config import get_settings

logger = logging.getLogger(__name__)

""" 
# Mục đích
Đối chiếu **BL** với **BL Check List** do người dùng cung cấp, lấy BL Check List làm chuẩn, ngoại trừ SHIPPER dùng dữ liệu chuẩn trong hướng dẫn này. Báo cáo đầy đủ, chính xác và không suy đoán.

# Nguyên tắc bắt buộc
- Chỉ bắt đầu khi có cả hai file hoặc nội dung tương ứng.
- Có dữ liệu ở cả hai nguồn: đối chiếu và kết luận **ĐÚNG** hoặc **SAI**.
- Thiếu dữ liệu ở một trong hai nguồn: kết luận **KHÔNG TÌM THẤY**.
- Không xác định được cách đối chiếu: kết luận **KHÔNG HIỂU**.
- Không suy luận dữ liệu từ BL sang BL Check List hoặc ngược lại.
- Bỏ qua khác biệt chỉ về dấu cách hoặc định dạng tại CONSIGNEE, NOTIFY PARTY 1, NOTIFY PARTY 2 và DELIVERY TO.
- Nếu thông tin dài được nối bằng `*`, `>>` hoặc ký hiệu tương tự, tìm phần nối và ghép đủ nội dung trước khi so sánh.
- Với mọi điểm sai, in đậm phần dữ liệu khác nhau và chữ **SAI**.

# Dữ liệu chuẩn SHIPPER
BROTHER INDUSTRIES (VIETNAM) LTD. PHUC DIEN INDUSTRIAL ZONE, MAO DIEN COMMUNE, HAI PHONG CITY, VIETNAM TEL NO.84-2203-545 860 FAX:545 878 MS. HARUNA WATANABE TRN+0800304173.

# Quy tắc ánh xạ
- FROM ↔ PORT OF LOADING.
- VIA ↔ PORT OF DISCHARGE.
- TO ↔ PLACE OF DELIVERY.
- REQUEST NO. ↔ SHIPMENT/SHIPMENT NO.
- NOTIFY PARTY 2 ↔ ALSO NOTIFY PARTY.
- ETD HPH ON trong REQUEST ↔ ETD trên BL.
- ISSUE DATE phải bằng ETD.
- SDD trên BL Check List ↔ DOOR trên BL. Ví dụ: `CY / BADEN SDD` ↔ `CY/DOOR`.

# Các bước kiểm tra
1. **Đọc và chuẩn hóa:** nhận diện từng trang, từng invoice, từng container và các phần thông tin nối tiếp; giữ nguyên giá trị gốc để trình bày.
2. **Số bill và invoice:**
   - Lấy chuỗi từ phần số đến hết chữ/số trên từng dòng INVOICE NO.; ví dụ `824957-0`.
   - Kiểm tra mỗi dòng BILL OF LADING (AIR WAYBILL) có số bill và số đó khớp BL.
   - Tại trang tổng hợp có G-TOTAL, kiểm tra đã bao gồm toàn bộ invoice ở các trang trước và không có invoice thừa.
   - Hiểu dạng rút gọn `824957-0 1` là `824957-0` và `824957-1`.
3. **B/L REQUIRED:** trên BL, `ON BOARD Date + ngày` nghĩa là ON BOARD; `RECEIVED DATE + ngày` nghĩa là RECEIVED. Đối chiếu với B/L REQUIRED trên Check List. BL chỉ có đúng một loại ngày là hợp lệ; có đồng thời cả hai loại là **SAI**.
4. **Description of goods**

   **4.1. Với mỗi invoice trong BL Check List:**
   - Lấy toàn bộ nội dung nằm ngay dưới dòng INVOICE NO. đến ngay trên dòng TTL đầu tiên.
   - Mỗi dòng mô tả hàng hóa được xem là một description riêng.

   **4.2. Gộp description của tất cả invoice:**
   - Gộp toàn bộ description từ tất cả invoice.
   - Loại bỏ các dòng trùng lặp hoàn toàn.
   - Sau khi loại trùng, tạo danh sách GOODS DESCRIPTION chuẩn duy nhất (unique list).

   **4.3. Trích xuất GOODS DESCRIPTION trên BL:**
   1. Đọc description theo từng container và giữ nguyên từng lần xuất hiện.
   2. Ghép các description bị ngắt dòng, nối trang hoặc tách bởi ký hiệu.
   3. **Không loại trùng, không gộp thành danh sách duy nhất và không bỏ qua description giống nhau trên BL.**
   4. Ghi lại số lần xuất hiện và container chứa từng description.

   **4.4. Thứ tự kiểm tra bắt buộc:**
   1. Tạo danh sách chuẩn duy nhất từ BL Check List bằng cách loại các description trùng hoàn toàn.
   2. Trích xuất danh sách thực tế từ BL nhưng **giữ nguyên mọi lần lặp**.
   3. **Đếm số lần xuất hiện trên BL trước khi thực hiện bất kỳ thao tác loại trùng nào.**
   4. Mỗi description chuẩn phải xuất hiện **đúng 1 lần trên toàn bộ BL**, không phải một lần cho mỗi container.
   5. Xuất hiện 0 lần → **SAI: thiếu**.
   6. Xuất hiện từ 2 lần trở lên, kể cả ở các container khác nhau → **SAI: trùng lặp**.
   7. Description không có trong danh sách chuẩn → **SAI: thừa**.
   8. Chỉ kết luận **ĐÚNG** khi tất cả description chuẩn xuất hiện đúng một lần và BL không có description thừa.

   **4.5. Bằng chứng trong kết quả:**
   - Hiển thị từng description, số lần xuất hiện và danh sách container tương ứng.
   - Không được kết luận dựa trên phép so sánh hai tập hợp đã loại trùng.
   - Với dữ liệu 830753, `LASER PRINTER`, `DIGITAL COPIER/PRINTER` và `MULTI-FUNCTIONAL COPIER/PRINTER/FAX` lặp lại nên GOODS DESCRIPTION phải là **SAI**.

   **4.6. Khi lập bảng kết quả:**
   - Hiển thị GOODS DESCRIPTION chuẩn sau khi đã gộp và loại trùng từ BL Check List.
   - Hiển thị GOODS DESCRIPTION thực tế trên BL.
   - Nêu rõ description nào thiếu, thừa hoặc bị lặp nếu kết quả là **SAI**.
5. **Đối chiếu toàn bộ hạng mục:**
   1. SHIPPER với dữ liệu chuẩn.
   2. CONSIGNEE.
   3. NOTIFY PARTY 1.
   4. NOTIFY PARTY 2.
   5. DELIVERY TO.
   6. CONTAINER NO. từng container.
   7. SEAL NO. từng container.
   8. CONTAINER TYPE từng container.
   9. P'KGS từng container.
   10. WEIGHT từng container.
   11. MEASUREMENT/M3 từng container.
   12. Tổng P'KGS, WEIGHT và MEASUREMENT/M3 toàn bộ container.
   13. GOODS DESCRIPTION.
   14. PORT OF LOADING.
   15. PORT OF DISCHARGE.
   16. PLACE OF DELIVERY.
   17. ETD.
   18. ISSUE DATE = ETD.
   19. Tổng số container + loại container, ví dụ `2x40 HC`.
   20. Delivery term: CY-CY, CY-DOOR, CFS-CFS hoặc CFS-DOOR.
   21. SHIPMENT NO.
   22. B/L REQUIRED: ON BOARD hoặc RECEIVED theo quy tắc trên.
   23. HS code: BL không được hiển thị HS code; nếu có thì **SAI**.
6. **Đếm kết quả:** đếm riêng ĐÚNG, SAI, KHÔNG HIỂU và KHÔNG TÌM THẤY. Mỗi container và mỗi thuộc tính container là một điểm kiểm tra riêng.

# Định dạng đầu ra
Tạo một file Excel `.xlsx` gồm đúng 2 sheet, định dạng tiêu đề rõ ràng, bật bộ lọc và tự điều chỉnh độ rộng cột để dễ đọc.

## Sheet 1: TỔNG HỢP ĐIỂM ĐÚNG
- Chỉ đưa các hạng mục có kết quả **ĐÚNG** vào sheet này.
- Dùng các cột: **File | Hạng mục check | Dữ liệu BL Check List | Dữ liệu BL | Kết quả**.
- Với hạng mục container, mỗi container và mỗi thuộc tính là một dòng riêng.
- Thêm dòng tổng cuối sheet gồm **Tổng số điểm đúng** theo từng file.

## Sheet 2: ĐIỂM CẦN XỬ LÝ
- Tổng hợp tất cả hạng mục có kết quả **SAI**, **KHÔNG TÌM THẤY** hoặc **KHÔNG HIỂU**.
- Dùng các cột: **File | Hạng mục check | Dữ liệu BL Check List | Dữ liệu BL | Kết quả | Nội dung cần xử lý**.
- Nêu rõ phần khác nhau, dữ liệu thiếu, nguyên nhân không hiểu hoặc hành động người dùng cần kiểm tra.
- Tô màu kết quả để phân biệt: **SAI** màu đỏ, **KHÔNG TÌM THẤY** màu cam, **KHÔNG HIỂU** màu vàng.
- Thêm dòng tổng cuối sheet gồm số điểm **SAI**, **KHÔNG TÌM THẤY** và **KHÔNG HIỂU** theo từng file.

## Lời nhắc bắt buộc
Đặt lời nhắc sau ở đầu sheet **ĐIỂM CẦN XỬ LÝ**, giữ nguyên chính xác, in hoa và in đậm:

**BẠN HÃY TỰ CHECK LOẠI BL, NUMBER OF ORINAL BL, ETA VIA, ETA TO VÀ CÁC ĐIỂM ĐẶC BIỆT DÀNH CHO THỊ TRƯỜNG MÀ BẠN ĐANG CHECK BL**

## Hoàn tất
- Sau khi kiểm tra xong, tạo và đính kèm file Excel để người dùng tải xuống.
- Nếu có nhiều BL, giữ chung 2 sheet và phân biệt từng BL bằng cột **File**.

# Xử lý lỗi
- Nếu file không đọc được hoặc không phân biệt được BL với BL Check List, nêu rõ file liên quan và dùng **KHÔNG TÌM THẤY** cho dữ liệu không trích xuất được.
- Nếu bố cục mới không khớp quy tắc đã nêu, dùng **KHÔNG HIỂU** thay vì tự diễn giải.
- Nếu có nhiều BL, tổng hợp toàn bộ kết quả vào cùng file Excel và phân biệt theo cột **File**.
"""

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
    
    _init_full_documents_table(conn)
    _init_skills_table(conn)


def _init_full_documents_table(conn: sqlite3.Connection) -> None:
    """Initialize full_documents table for clean, unchunked full-text document storage."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS full_documents (
            document_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            full_text TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 1,
            file_size_bytes INTEGER NOT NULL DEFAULT 0,
            file_type TEXT NOT NULL DEFAULT 'txt',
            created_at TEXT NOT NULL
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_full_docs_id ON full_documents(document_id);")
    conn.commit()

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
        {
            "id": "business-compare", "name": "So sánh Nghiệp vụ", "description": "Yêu cầu upload chính xác 2 file để đối chiếu chuyên sâu.",
            "type": "main", "is_system": 1, "icon": "GitCompare", "temp": 0.2, "top_k": 30,
            "prompt": "[SKILL TEMPLATE] Bạn là một chuyên gia phân tích và đối chiếu nghiệp vụ. Nhiệm vụ của bạn là so sánh sự khác biệt chi tiết giữa 2 file tài liệu mà người dùng đã tải lên. Hãy lập bảng so sánh rõ ràng các điều khoản và thông tin quan trọng."
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


def save_document_full_and_chunks(
    document_id: str,
    filename: str,
    full_text: str,
    page_count: int,
    file_size_bytes: int,
    file_type: str,
    chunks: list[dict],
) -> None:
    """Save full document text and chunked representations to SQLite in a single transaction."""
    conn = get_sqlite_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO full_documents (
                document_id, filename, full_text, page_count, file_size_bytes, file_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, filename, full_text, page_count, file_size_bytes, file_type, now),
        )

        conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        sqlite_data = [
            (
                f"{document_id}_chunk_{c['chunk_index']}",
                document_id,
                filename,
                c.get("page", 0),
                c.get("heading", ""),
                c["text"],
            )
            for c in chunks
        ]
        if sqlite_data:
            conn.executemany(
                "INSERT INTO document_chunks (id, document_id, filename, page, heading, text) VALUES (?, ?, ?, ?, ?, ?)",
                sqlite_data,
            )
        conn.commit()
    finally:
        conn.close()


def delete_document_from_sqlite(document_id: str) -> None:
    """Delete document from both full_documents and document_chunks tables."""
    conn = get_sqlite_connection()
    try:
        conn.execute("DELETE FROM full_documents WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        conn.commit()
    finally:
        conn.close()
