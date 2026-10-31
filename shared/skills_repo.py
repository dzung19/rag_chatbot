"""Skills Repository — Centralized storage for system and custom skills.

Supports PostgreSQL primary storage with automatic base-skill seeding and
graceful fallback to SQLite for offline/testing environments.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

try:
    from sqlalchemy import text
except ImportError:
    text = None  # type: ignore

from shared.models import Skill, SkillCreateRequest, SkillUpdateRequest, SkillType
from shared.db import get_db_engine, is_postgres_available

logger = logging.getLogger(__name__)

_pg_initialized = False


class SkillNotFoundError(Exception):
    pass


class SkillSystemError(Exception):
    pass


BUSINESS_COMPARE_BL_PROMPT = """# Mục đích
Đối chiếu **BL** với **BL Check List** do người dùng cung cấp, lấy BL Check List làm chuẩn, ngoại trừ SHIPPER dùng dữ liệu chuẩn trong hướng dẫn này. Báo cáo đầy đủ 23 hạng mục, chính xác và không suy đoán.

# Nguyên tắc bắt buộc
- Tự động nhận diện file nào là BL và file nào là BL Check List dựa trên nội dung tài liệu.
- Có dữ liệu ở cả hai nguồn: đối chiếu và kết luận **ĐÚNG** (đưa vào Bảng 1) hoặc **SAI** (đưa vào Bảng 2). Tuyệt đối không đưa hạng mục **ĐÚNG** vào Bảng 2.
- Thiếu dữ liệu ở một trong hai nguồn: kết luận **KHÔNG TÌM THẤY** (Bảng 2). Tuyệt đối không tự suy đoán và không được sao chép dữ liệu từ Check List sang cột Dữ liệu BL nếu trên BL không có thông tin (phải ghi rõ "Không tìm thấy trên BL").
- Không xác định được cách đối chiếu: kết luận **KHÔNG HIỂU** (Bảng 2).
- Bỏ qua khác biệt chỉ về dấu cách, viết hoa/thường hoặc định dạng tại CONSIGNEE, NOTIFY PARTY 1, NOTIFY PARTY 2 và DELIVERY TO.
- Với thông tin dài nối bằng `*`, `>>`, tìm phần nối và ghép đủ trước khi so sánh.
- Với mọi điểm sai, in đậm phần dữ liệu khác nhau và chữ **SAI**. Không dùng ký hiệu LaTeX (`$...$`) trong bảng.

# Dữ liệu chuẩn SHIPPER (Hạng mục 1)
BROTHER INDUSTRIES (VIETNAM) LTD. PHUC DIEN INDUSTRIAL ZONE, MAO DIEN COMMUNE, HAI PHONG CITY, VIETNAM TEL NO.84-2203-545 860 FAX:545 878 MS. HARUNA WATANABE TRN+0800304173.
- Trên BL Check List: lấy `SHIPPER: BIVN` dưới mỗi Invoice (`BIVN` là viết tắt của `# Dữ liệu chuẩn SHIPPER`), không lấy tiêu đề `SHIPPER: BIL` ở góc đầu trang, dừng tại nhãn kế tiếp (không lấy số tiền/INCOTERMS). Nếu `SHIPPER` trên BL khớp với `# Dữ liệu chuẩn SHIPPER` thì kết luận **ĐÚNG** (đưa vào Bảng 1); nếu sai lệch thông tin hoặc khác shipper thì kết luận **SAI** (đưa vào Bảng 2); nếu thiếu dữ liệu ở một bên thì kết luận **KHÔNG TÌM THẤY** (Bảng 2).

# Quy tắc ánh xạ & nghiệp vụ
- `FROM` ↔ `PORT OF LOADING` (Hạng mục 14).
- `VIA` ↔ `PORT OF DISCHARGE` (Hạng mục 15).
- `TO` ↔ `PLACE OF DELIVERY` (Hạng mục 16): Chỉ lấy giá trị thực tế trong ô `PLACE OF DELIVERY` trên BL (bỏ qua dòng chú thích mẫu đơn `(Combined Transport ONLY - see Clause 1 & 5.2)` và tuyệt đối không lấy giá trị của `PORT OF DISCHARGE` gán sang `PLACE OF DELIVERY`). Nếu ô `PLACE OF DELIVERY` trên BL để trống (`N/A`) trong khi Check List có dữ liệu `TO`, bắt buộc kết luận **KHÔNG TÌM THẤY** (đưa vào Bảng 2).
- `REQUEST NO.` ↔ `SHIPMENT / SHIPMENT NO.` (Hạng mục 21).
- `NOTIFY PARTY 2` ↔ `ALSO NOTIFY PARTY` (Hạng mục 4).
- **Số bill và invoice**: Kiểm tra số bill tại `BILL OF LADING (AIR WAYBILL)` khớp BL; tại trang `G-TOTAL` kiểm tra đủ toàn bộ invoice (dạng `824957-0 1` hiểu là `-0` và `-1`).
- **Hạng mục 8 (`CONTAINER TYPE`)**: Các cách ghi cùng loại container như `40HC`, `40HQ`, `40' HIGH CUBE`, `40HC:40FT 9FT 6IN DRY CONTAINER` là tương đương → **ĐÚNG** (đưa vào Bảng 1, trình bày gộp 1 dòng); nếu khác loại container kết luận **SAI** (đưa vào Bảng 2).
- **Hạng mục 9 (`P'KGS`)**: Trình bày toàn bộ các container gộp trên **1 dòng duy nhất**. Cột `Dữ liệu BL Check List` ghi số kiện từng container và tổng PKGS; Cột `Dữ liệu BL` ghi số kiện từng container và tổng CARTONS. Hai bên khớp số kiện kết luận **ĐÚNG** (Bảng 1); nếu sai lệch số lượng hoặc một bên không có số kiện (0 PKGS/N/A) kết luận **SAI** (Bảng 2).
- **Hạng mục 10 (`WEIGHT`)**: Trình bày toàn bộ các container gộp trên **1 dòng duy nhất**. Cột `Dữ liệu BL Check List` ghi trọng lượng từng container (KGS); Cột `Dữ liệu BL` ghi trọng lượng từng container (KGS). Hai bên khớp nhau kết luận **ĐÚNG** (Bảng 1); nếu sai lệch số liệu hoặc một bên thiếu dữ liệu (N/A) kết luận **SAI** (Bảng 2).
- **Hạng mục 11 (`MEASUREMENT/M3`)**: Trình bày toàn bộ các container gộp trên **1 dòng duy nhất**. Cột `Dữ liệu BL Check List` ghi thể tích từng container (M3/CBM); Cột `Dữ liệu BL` ghi thể tích từng container (CBM). Hai bên khớp nhau kết luận **ĐÚNG** (Bảng 1); nếu sai lệch số liệu hoặc một bên thiếu dữ liệu (N/A) kết luận **SAI** (Bảng 2).
- **Hạng mục 12 (`Tổng P'KGS, WEIGHT và MEASUREMENT/M3`)**: Trình bày tổng P'KGS, Gross Weight và Measurement của toàn bộ lô hàng trên 1 dòng duy nhất. Khớp nhau toàn bộ 3 chỉ số kết luận **ĐÚNG** (Bảng 1); nếu sai lệch bất kỳ chỉ số nào kết luận **SAI** (Bảng 2).
- **Hạng mục 13 (`GOODS DESCRIPTION`)**:
  - 4.1. Lấy các dòng mô tả hàng hóa từ dưới `INVOICE NO.` đến ngay trên dòng `TTL` đầu tiên của từng invoice trên Check List.
  - 4.2. Gộp tất cả invoice và loại trùng để có **danh sách chuẩn duy nhất**.
  - 4.3. Trích xuất description trên BL theo từng container, giữ nguyên mọi lần lặp.
  - 4.4. Mỗi description chuẩn chỉ được xuất hiện **đúng 1 lần** trên toàn bộ BL (0 lần → **SAI: thiếu**, >=2 lần trên các container → **SAI: trùng lặp**, ngoài danh sách chuẩn → **SAI: thừa**).
- **Hạng mục 17 (`ETD`)**: Đối chiếu `ETD` / `ETD HPH ON` trên BL Check List với `SHIPPED ON BOARD DATE` / `ETD` trên BL. Khác định dạng ngày (`YYYY.MM.DD` vs `DD-Mon-YYYY`) nhưng cùng ngày/tháng/năm là **ĐÚNG** (Bảng 1); khác ngày/tháng/năm kết luận **SAI** (Bảng 2).
- **Hạng mục 18 (`ISSUE DATE = ETD`)**: Đối chiếu ngày `ETD` thực tế trên BL Check List (ghi ở cột `Dữ liệu BL Check List`) với nơi & ngày phát hành tại ô `PLACE AND DATE OF ISSUE` ở cuối trang BL (ghi ở cột `Dữ liệu BL` dạng `<Nơi phát hành>, <Ngày phát hành>`). Nếu cùng ngày/tháng/năm thì kết luận **ĐÚNG** (đưa vào Bảng 1); nếu khác ngày/tháng/năm kết luận **SAI** (đưa vào Bảng 2). Không lấy ngày in phiếu cạnh `PAGE` ở đầu trang Check List.
- Hạng mục 20 (Delivery term): Trên Check List, dòng ngay dưới STUFFING PLAN có dạng
  "<cảng> | CY / <điểm giao> | <loại giao> | SEAL NO. | <số seal>".
  Ô <loại giao> nằm giữa "CY / <điểm giao>" và "SEAL NO.":
  + Là "SDD" hoặc "DOOR" → CY-DOOR.
  + Là "CY" → CY-CY.
  "SDD" là loại giao hàng, KHÔNG phải số seal.
  Đối chiếu với Delivery term trên BL (CY-CY, CY-DOOR, CFS-CFS, CFS-DOOR).
  Khớp → ĐÚNG (Bảng 1); khác → SAI (Bảng 2).
- **Hạng mục 22 (`B/L REQUIRED`)**: Trên BL, có `SHIPPED ON BOARD DATE + ngày` nghĩa là `ON BOARD`; có `RECEIVED DATE + ngày` nghĩa là `RECEIVED` (có đồng thời cả hai là **SAI**). Đối chiếu với `B/L REQUIRED` trên Check List; nếu khớp thì kết luận **ĐÚNG** (Bảng 1); không khớp hoặc mâu thuẫn kết luận **SAI** (Bảng 2).
- **Hạng mục 23 (`HS code`)**: BL không được hiển thị HS code (cả hai không có hoặc chỉ Check List không yêu cầu hiển thị trên BL → kiểm tra theo thực tế tài liệu; nếu BL hiển thị HS code là **SAI**).

- **Hạng mục 6 (`CONTAINER NO.`)**: Lấy danh sách số container (dạng 4 chữ cái + 7 chữ số như `TXGU7489205`, `EGSU9442843`...) từ dòng `STUFFING PLAN` trên Check List và ô Container trên BL. Tuyệt đối không lấy chuỗi loại container (`40HC:40FT...`) điền vào ô Container No.

# Danh sách 23 hạng mục bắt buộc kiểm tra (giữ đúng STT 1 -> 23)
1. SHIPPER. 2. CONSIGNEE. 3. NOTIFY PARTY 1. 4. NOTIFY PARTY 2. 5. DELIVERY TO. 6. CONTAINER NO. 7. SEAL NO. 8. CONTAINER TYPE. 9. P'KGS. 10. WEIGHT. 11. MEASUREMENT/M3. 12. Tổng P'KGS, WEIGHT và MEASUREMENT/M3. 13. GOODS DESCRIPTION. 14. PORT OF LOADING. 15. PORT OF DISCHARGE. 16. PLACE OF DELIVERY. 17. ETD. 18. ISSUE DATE = ETD. 19. Tổng số container + loại container. 20. Delivery term. 21. SHIPMENT NO. 22. B/L REQUIRED. 23. HS code.

# Định dạng đầu ra (Báo cáo dạng Bảng Markdown)

## 1. TỔNG HỢP ĐIỂM ĐÚNG
| STT | Hạng mục check | Dữ liệu BL Check List | Dữ liệu BL | Kết quả |
|---|---|---|---|---|
| ... | ... | ... | ... | **ĐÚNG** |

*(Mỗi hạng mục ĐÚNG từ 1 đến 23 trình bày gọn thành 1 dòng theo đúng STT. Cuối bảng ghi rõ: **Tổng số điểm đúng: X**)*

# Quy tắc thống kê tổng số điểm
- Cuối Bảng 1: BẮT BUỘC đếm chính xác số lượng dòng thực tế đã liệt kê trong Bảng 1 để ghi vào **Tổng số điểm đúng: X** (Ví dụ nếu có 19 dòng trong Bảng 1 thì X bắt buộc là 19, tuyệt đối không đoán số).
- Cuối Bảng 2: BẮT BUỘC đếm chính xác số lượng dòng thực tế theo từng loại kết quả trong Bảng 2 để ghi vào **Tổng số điểm SAI: X | KHÔNG TÌM THẤY: Y | KHÔNG HIỂU: Z**.
- Tổng số điểm của cả 2 bảng (Đúng + SAI + Không tìm thấy + Không hiểu) phải bằng đúng 23 hạng mục kiểm tra.

---

## 2. ĐIỂM CẦN XỬ LÝ

> **BẠN HÃY TỰ CHECK LOẠI BL, NUMBER OF ORIGINAL BL, ETA VIA, ETA TO VÀ CÁC ĐIỂM ĐẶC BIỆT DÀNH CHO THỊ TRƯỜNG MÀ BẠN ĐANG CHECK BL**

| STT | Hạng mục check | Dữ liệu BL Check List | Dữ liệu BL | Kết quả | Chi tiết khác biệt & Hành động xử lý |
|---|---|---|---|---|---|
| ... | ... | ... | ... | **SAI** / **KHÔNG TÌM THẤY** / **KHÔNG HIỂU** | ... |

*(Chỉ liệt kê các hạng mục SAI / KHÔNG TÌM THẤY / KHÔNG HIỂU. Cuối bảng ghi rõ: **Tổng số điểm SAI: X | KHÔNG TÌM THẤY: Y | KHÔNG HIỂU: Z**)*
"""


BASE_SKILLS = [
    # MAIN SKILLS
    {
        "id": "general-assistant",
        "name": "General Assistant",
        "description": "Default balanced assistant with full grounding & source citations.",
        "type": "main",
        "is_system": True,
        "icon": "Bot",
        "temp": 0.7,
        "top_k": 5,
        "prompt": "You are a helpful and polite company assistant. Focus on answering directly and clearly based only on the provided context.",
    },
    {
        "id": "executive-summary",
        "name": "Executive Summarizer",
        "description": "Generates high-level briefing.",
        "type": "main",
        "is_system": True,
        "icon": "FileText",
        "temp": 0.3,
        "top_k": 6,
        "prompt": "Structure your response as an executive briefing with the following sections: Executive Summary, Key Findings, Action Items, and Metrics/Deadlines. Be highly concise.",
    },
    {
        "id": "policy-compliance",
        "name": "Policy & Compliance",
        "description": "Strict factual auditor.",
        "type": "main",
        "is_system": True,
        "icon": "ShieldCheck",
        "temp": 0.1,
        "top_k": 5,
        "prompt": "Act as a strict compliance auditor. Quote section headers, highlight conditions, prerequisites, penalties, and explicit constraints. Do not make assumptions.",
    },
    {
        "id": "email-memo-drafter",
        "name": "Email & Memo Drafter",
        "description": "Formats response as a ready-to-send corporate email/memo.",
        "type": "main",
        "is_system": True,
        "icon": "Mail",
        "temp": 0.7,
        "top_k": 4,
        "prompt": "Format your response as a professional corporate email or memo. Include a Subject line, Context, Body, Next Steps, and a professional Sign-off.",
    },
    {
        "id": "comparative-analyst",
        "name": "Comparative Analyst",
        "description": "Synthesizes across multiple documents using markdown tables.",
        "type": "main",
        "is_system": True,
        "icon": "Scale",
        "temp": 0.2,
        "top_k": 8,
        "prompt": "Act as a comparative analyst. Synthesize information across multiple sources and present the core differences and similarities using Markdown tables.",
    },
    {
        "id": "troubleshooter",
        "name": "Technical Troubleshooter",
        "description": "Step-by-step diagnostic workflow.",
        "type": "main",
        "is_system": True,
        "icon": "Wrench",
        "temp": 0.2,
        "top_k": 5,
        "prompt": "Provide a step-by-step diagnostic workflow. Start with the Problem Statement, analyze the Root Cause, and then provide Actionable Resolution Steps.",
    },
    {
        "id": "business-compare",
        "name": "So sánh Nghiệp vụ (BL vs Check List)",
        "description": "Yêu cầu upload chính xác 2 file (BL & BL Check List) để đối chiếu chi tiết 23 hạng mục.",
        "type": "main",
        "is_system": True,
        "icon": "GitCompare",
        "temp": 0.1,
        "top_k": 2,
        "prompt": BUSINESS_COMPARE_BL_PROMPT,
    },
    # MODIFIER SKILLS
    {
        "id": "lang-vietnamese",
        "name": "Tiếng Việt Chuẩn",
        "description": "Always answer in Vietnamese.",
        "type": "modifier",
        "is_system": True,
        "icon": "Languages",
        "temp": None,
        "top_k": None,
        "prompt": "Always formulate your answers in professional, natural Vietnamese business terminology, maintaining English technical terms in parentheses if necessary. Do NOT answer in English, even if the context is English.",
    },
    {
        "id": "concise-tldr",
        "name": "Brief / TL;DR",
        "description": "Restrict output to maximum 3 concise bullet points.",
        "type": "modifier",
        "is_system": True,
        "icon": "Zap",
        "temp": None,
        "top_k": None,
        "prompt": "Restrict your output to a maximum of 3 extremely concise bullet points. No pleasantries, no introductory phrases. Keep the entire response under 80 words.",
    },
    {
        "id": "quote-grounded",
        "name": "Strict Verbatim Quotes",
        "description": "Include verbatim quotation blocks.",
        "type": "modifier",
        "is_system": True,
        "icon": "Quote",
        "temp": None,
        "top_k": None,
        "prompt": "Include verbatim quotation blocks (`> \"quote\"`) for each key assertion alongside the document name and page number. Be very strict about only quoting.",
    },
]


def _ensure_pg_skills_table() -> bool:
    """Ensure PostgreSQL skills table exists and base skills are seeded."""
    global _pg_initialized
    if _pg_initialized:
        return True

    if not is_postgres_available():
        return False

    try:
        engine = get_db_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS skills (
                        id VARCHAR(64) PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        description TEXT NOT NULL,
                        type VARCHAR(32) NOT NULL CHECK (type IN ('main', 'modifier')),
                        category VARCHAR(32) NOT NULL CHECK (category IN ('base', 'custom')),
                        is_system BOOLEAN NOT NULL DEFAULT FALSE,
                        system_prompt_addon TEXT NOT NULL,
                        temperature_override REAL,
                        top_k_override INTEGER,
                        icon VARCHAR(64) NOT NULL DEFAULT 'Sparkles',
                        enabled_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_skills_type ON skills(type);
                    CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
                    """
                )
            )

            now = datetime.now(timezone.utc)
            for s in BASE_SKILLS:
                conn.execute(
                    text(
                        """
                        INSERT INTO skills (
                            id, name, description, type, category, is_system,
                            system_prompt_addon, temperature_override, top_k_override,
                            icon, enabled_tools, created_at, updated_at
                        ) VALUES (
                            :id, :name, :description, :type, 'base', :is_system,
                            :prompt, :temp, :top_k, :icon, '[]'::jsonb, :now, :now
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            type = EXCLUDED.type,
                            system_prompt_addon = EXCLUDED.system_prompt_addon,
                            temperature_override = EXCLUDED.temperature_override,
                            top_k_override = EXCLUDED.top_k_override,
                            icon = EXCLUDED.icon,
                            updated_at = EXCLUDED.updated_at
                        WHERE skills.is_system = TRUE;
                        """
                    ),
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "description": s["description"],
                        "type": s["type"],
                        "is_system": s["is_system"],
                        "prompt": s["prompt"],
                        "temp": s["temp"],
                        "top_k": s["top_k"],
                        "icon": s["icon"],
                        "now": now,
                    },
                )
        _pg_initialized = True
        return True
    except Exception as e:
        logger.warning("Failed to initialize PostgreSQL skills table: %s. Using SQLite fallback.", str(e))
        return False


def _row_to_skill(row: dict) -> Skill:
    enabled_tools = row.get("enabled_tools")
    if isinstance(enabled_tools, str):
        try:
            enabled_tools = json.loads(enabled_tools)
        except Exception:
            enabled_tools = []
    elif not isinstance(enabled_tools, list):
        enabled_tools = []

    created_at = row["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    updated_at = row["updated_at"]
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat()

    return Skill(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        type=row["type"],
        category=row["category"],
        is_system=bool(row["is_system"]),
        system_prompt_addon=row["system_prompt_addon"],
        temperature_override=row["temperature_override"],
        top_k_override=row["top_k_override"],
        icon=row["icon"],
        enabled_tools=enabled_tools,
        created_at=str(created_at),
        updated_at=str(updated_at),
    )


_sqlite_initialized = False

# --- SQLite Fallback Helpers ---
def _sqlite_get_connection():
    global _sqlite_initialized
    from shared.sqlite_db import get_sqlite_connection
    conn = get_sqlite_connection()
    if not _sqlite_initialized:
        try:
            now = datetime.now(timezone.utc).isoformat()
            for s in BASE_SKILLS:
                conn.execute(
                    """
                    UPDATE skills
                    SET name = ?, description = ?, type = ?, system_prompt_addon = ?,
                        temperature_override = ?, top_k_override = ?, icon = ?, updated_at = ?
                    WHERE id = ? AND is_system = 1
                    """,
                    (
                        s["name"], s["description"], s["type"], s["prompt"],
                        s["temp"], s["top_k"], s["icon"], now, s["id"],
                    ),
                )
            conn.commit()
            _sqlite_initialized = True
        except Exception:
            pass
    return conn


def get_skill(skill_id: str) -> Optional[Skill]:
    """Retrieve a skill by its ID (PostgreSQL with SQLite fallback)."""
    if _ensure_pg_skills_table():
        try:
            engine = get_db_engine()
            with engine.connect() as conn:
                res = conn.execute(
                    text("SELECT * FROM skills WHERE id = :id"),
                    {"id": skill_id},
                ).mappings().fetchone()
                if res:
                    return _row_to_skill(dict(res))
                return None
        except Exception as e:
            logger.warning("PostgreSQL get_skill failed: %s, trying SQLite fallback", str(e))

    # SQLite fallback
    conn = _sqlite_get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
        row = cur.fetchone()
        if not row:
            return None
        return _row_to_skill(dict(row))
    finally:
        conn.close()


def list_skills(skill_type: Optional[SkillType] = None) -> List[Skill]:
    """List all skills, optionally filtered by type (PostgreSQL with SQLite fallback)."""
    if _ensure_pg_skills_table():
        try:
            engine = get_db_engine()
            with engine.connect() as conn:
                if skill_type:
                    query = text("SELECT * FROM skills WHERE type = :type ORDER BY category ASC, name ASC")
                    rows = conn.execute(query, {"type": skill_type.value}).mappings().fetchall()
                else:
                    query = text("SELECT * FROM skills ORDER BY category ASC, name ASC")
                    rows = conn.execute(query).mappings().fetchall()
                return [_row_to_skill(dict(r)) for r in rows]
        except Exception as e:
            logger.warning("PostgreSQL list_skills failed: %s, trying SQLite fallback", str(e))

    # SQLite fallback
    conn = _sqlite_get_connection()
    try:
        cur = conn.cursor()
        if skill_type:
            cur.execute(
                "SELECT * FROM skills WHERE type = ? ORDER BY category ASC, name ASC",
                (skill_type.value,),
            )
        else:
            cur.execute("SELECT * FROM skills ORDER BY category ASC, name ASC")
        rows = cur.fetchall()
        return [_row_to_skill(dict(r)) for r in rows]
    finally:
        conn.close()


def create_skill(request: SkillCreateRequest) -> Skill:
    """Create a new custom skill."""
    from shared.security import detect_prompt_injection

    if detect_prompt_injection(request.system_prompt_addon):
        raise ValueError("Prompt injection detected in system_prompt_addon.")

    skill_id = request.id or str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if _ensure_pg_skills_table():
        try:
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO skills (
                            id, name, description, type, category, is_system,
                            system_prompt_addon, temperature_override, top_k_override,
                            icon, enabled_tools, created_at, updated_at
                        ) VALUES (
                            :id, :name, :description, :type, 'custom', FALSE,
                            :prompt, :temp, :top_k, :icon, '[]'::jsonb, :now, :now
                        )
                        """
                    ),
                    {
                        "id": skill_id,
                        "name": request.name,
                        "description": request.description,
                        "type": request.type.value,
                        "prompt": request.system_prompt_addon,
                        "temp": request.temperature_override,
                        "top_k": request.top_k_override,
                        "icon": request.icon,
                        "now": now,
                    },
                )
            created = get_skill(skill_id)
            if created:
                return created
        except Exception as e:
            logger.error("Failed to create skill in PostgreSQL: %s", str(e))
            raise

    # SQLite fallback
    conn = _sqlite_get_connection()
    try:
        now_str = now.isoformat()
        conn.execute(
            """
            INSERT INTO skills (
                id, name, description, type, category, is_system,
                system_prompt_addon, temperature_override, top_k_override,
                icon, enabled_tools, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'custom', 0, ?, ?, ?, ?, '[]', ?, ?)
            """,
            (
                skill_id,
                request.name,
                request.description,
                request.type.value,
                request.system_prompt_addon,
                request.temperature_override,
                request.top_k_override,
                request.icon,
                now_str,
                now_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()



    skill = get_skill(skill_id)
    if not skill:
        raise RuntimeError("Failed to retrieve created skill.")
    return skill


def update_skill(skill_id: str, request: SkillUpdateRequest) -> Skill:
    """Update an existing custom skill."""
    existing = get_skill(skill_id)
    if not existing:
        raise SkillNotFoundError(f"Skill {skill_id} not found.")
    if existing.is_system:
        raise SkillSystemError("Built-in base skills cannot be modified.")

    from shared.security import detect_prompt_injection

    if request.system_prompt_addon and detect_prompt_injection(request.system_prompt_addon):
        raise ValueError("Prompt injection detected in system_prompt_addon.")

    now = datetime.now(timezone.utc)
    dumped = request.model_dump(exclude_unset=True)
    if not dumped:
        return existing

    if _ensure_pg_skills_table():
        try:
            set_clauses = [f"{k} = :{k}" for k in dumped]
            set_clauses.append("updated_at = :updated_at")
            params = dict(dumped)
            params["updated_at"] = now
            params["skill_id"] = skill_id

            sql = f"UPDATE skills SET {', '.join(set_clauses)} WHERE id = :skill_id"
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(text(sql), params)

            updated = get_skill(skill_id)
            if updated:
                return updated
        except Exception as e:
            logger.error("Failed to update skill in PostgreSQL: %s", str(e))
            raise

    # SQLite fallback
    conn = _sqlite_get_connection()
    try:
        updates = []
        params = []
        for field, value in dumped.items():
            updates.append(f"{field} = ?")
            params.append(value)
        updates.append("updated_at = ?")
        params.append(now.isoformat())
        params.append(skill_id)

        query = f"UPDATE skills SET {', '.join(updates)} WHERE id = ?"
        conn.execute(query, tuple(params))
        conn.commit()
    finally:
        conn.close()



    updated = get_skill(skill_id)
    if not updated:
        raise SkillNotFoundError(f"Skill {skill_id} not found.")
    return updated


def delete_skill(skill_id: str) -> bool:
    """Delete a custom skill."""
    existing = get_skill(skill_id)
    if not existing:
        return False
    if existing.is_system:
        raise SkillSystemError("Built-in base skills cannot be deleted.")

    if _ensure_pg_skills_table():
        try:
            engine = get_db_engine()
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM skills WHERE id = :id"), {"id": skill_id})
            return True
        except Exception as e:
            logger.error("Failed to delete skill in PostgreSQL: %s", str(e))
            raise

    # SQLite fallback
    conn = _sqlite_get_connection()
    try:
        conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def migrate_skills_from_sqlite_to_pg() -> int:
    """Utility to migrate custom skills from SQLite to PostgreSQL."""
    if not _ensure_pg_skills_table():
        logger.warning("PostgreSQL not available for skills migration.")
        return 0

    conn = _sqlite_get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM skills WHERE is_system = 0")
        rows = cur.fetchall()
        if not rows:
            return 0

        engine = get_db_engine()
        migrated = 0
        with engine.begin() as pg_conn:
            for r in rows:
                d = dict(r)
                pg_conn.execute(
                    text(
                        """
                        INSERT INTO skills (
                            id, name, description, type, category, is_system,
                            system_prompt_addon, temperature_override, top_k_override,
                            icon, enabled_tools, created_at, updated_at
                        ) VALUES (
                            :id, :name, :description, :type, :category, FALSE,
                            :prompt, :temp, :top_k, :icon, '[]'::jsonb, :created_at, :updated_at
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": d["id"],
                        "name": d["name"],
                        "description": d["description"],
                        "type": d["type"],
                        "category": d.get("category", "custom"),
                        "prompt": d["system_prompt_addon"],
                        "temp": d.get("temperature_override"),
                        "top_k": d.get("top_k_override"),
                        "icon": d.get("icon", "Sparkles"),
                        "created_at": d.get("created_at"),
                        "updated_at": d.get("updated_at"),
                    },
                )
                migrated += 1
        logger.info("Migrated %d custom skills from SQLite to PostgreSQL", migrated)
        return migrated
    finally:
        conn.close()


def reconcile_compare_counts(md: str) -> str:
    """Reconcile table summary counts and ensure strict separation between Table 1 and Table 2."""
    if not md:
        return md

    t1_match = re.search(r"(##\s*1\.\s*T[^\n]*\n+)(\|.*?\n\|[-:\s|]+\n)(.*?)(\n+\*\*T[^\n]*?\n|(?=##\s*2|\Z))", md, re.DOTALL | re.I)
    t2_match = re.search(r"(##\s*2\.\s*[^|\n]*\n+)(\|.*?\n\|[-:\s|]+\n)(.*?)(\n+\*\*T[^\n]*?\n|\Z)", md, re.DOTALL | re.I)

    if not t1_match or not t2_match:
        return md

    t1_head, t1_cols, t1_body, t1_foot = t1_match.groups()
    t2_head, t2_cols, t2_body, t2_foot = t2_match.groups()

    def get_rows(body: str) -> list[str]:
        return [
            l.strip() for l in body.splitlines()
            if l.strip().startswith("|")
            and not re.match(r"^\|\s*[-:]+\s*\|", l.strip())
            and not re.search(r"\bSTT\b", l, re.I)
        ]

    t1_rows = get_rows(t1_body)
    t2_rows = get_rows(t2_body)

    clean_t1: list[str] = []
    clean_t2: list[str] = []

    # 1. Check Table 1: move any SAI/KHÔNG TÌM THẤY/KHÔNG HIỂU rows to Table 2
    for r in t1_rows:
        parts = [p.strip() for p in r.split("|")]
        res_col = parts[5].upper() if len(parts) >= 6 else r.upper()
        if any(k in res_col for k in ["SAI", "KHÔNG TÌM THẤY", "KHONG TIM THAY", "KHÔNG HIỂU", "KHONG HIEU"]):
            while len(parts) < 8:
                parts.append("")
            clean_t2.append("| " + " | ".join(parts[1:7]) + " |")
        else:
            clean_t1.append(r)

    # 2. Check Table 2: move any ĐÚNG rows to Table 1
    for r in t2_rows:
        parts = [p.strip() for p in r.split("|")]
        res_col = parts[5].upper() if len(parts) >= 6 else r.upper()
        if ("ĐÚNG" in res_col or "DUNG" in res_col) and not any(k in res_col for k in ["SAI", "KHÔNG TÌM THẤY", "KHONG TIM THAY", "KHÔNG HIỂU", "KHONG HIEU"]):
            p1 = parts[1:6] if len(parts) >= 6 else parts[1:]
            clean_t1.append("| " + " | ".join(p1) + " |")
        else:
            clean_t2.append(r)

    def get_stt(row: str) -> int:
        parts = [p.strip() for p in row.split("|")]
        m = re.search(r"\d+", parts[1] if len(parts) > 1 else "99")
        return int(m.group(0)) if m else 99

    clean_t1.sort(key=get_stt)
    clean_t2.sort(key=get_stt)

    actual_dung = len(clean_t1)
    sai_count = 0
    miss_count = 0
    unk_count = 0
    for r in clean_t2:
        parts = [p.strip() for p in r.split("|")]
        c = parts[5].upper() if len(parts) >= 6 else r.upper()
        if "KHÔNG TÌM THẤY" in c or "KHONG TIM THAY" in c:
            miss_count += 1
        elif "KHÔNG HIỂU" in c or "KHONG HIEU" in c:
            unk_count += 1
        elif "SAI" in c:
            sai_count += 1

    nl = chr(10)
    new_t1 = f"{t1_head}{t1_cols}" + (nl.join(clean_t1) + nl if clean_t1 else "") + f"{nl}**Tổng số điểm đúng: {actual_dung}**{nl}"
    new_t2 = f"{t2_head}{t2_cols}" + (nl.join(clean_t2) + nl if clean_t2 else "") + f"{nl}**Tổng số điểm SAI: {sai_count} | KHÔNG TÌM THẤY: {miss_count} | KHÔNG HIỂU: {unk_count}**{nl}"

    prefix = md[:t1_match.start(1)]
    middle = md[t1_match.end(0):t2_match.start(1)]
    suffix = md[t2_match.end(0):]
    return f"{prefix}{new_t1}{middle}{new_t2}{suffix}"
