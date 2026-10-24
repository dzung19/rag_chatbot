"""Skills Repository — Centralized storage for system and custom skills.

Supports PostgreSQL primary storage with automatic base-skill seeding and
graceful fallback to SQLite for offline/testing environments.
"""

from __future__ import annotations

import json
import logging
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
Đối chiếu **BL** với **BL Check List** do người dùng cung cấp, lấy BL Check List làm chuẩn, ngoại trừ SHIPPER dùng dữ liệu chuẩn trong hướng dẫn này. Báo cáo đầy đủ, chính xác và không suy đoán.

# Nguyên tắc bắt buộc
- Chỉ bắt đầu khi có cả hai file hoặc nội dung tương ứng. Tự động nhận diện file nào là BL và file nào là BL Check List dựa trên nội dung tài liệu.
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
- ETD: lấy ETD/ETD HPH của đúng cảng đi và chặng.
- ON-BOARD DATE: lấy ngày tại Laden/Shipped on Board.
- ISSUE DATE (Place and Date of Issue): Chỉ lấy trên file BL tại ô `PLACE AND DATE OF ISSUE` / `Date of Issue` ở cuối trang BL (lưu ý đọc đầy đủ cả dòng tên nơi phát hành như `MSC Ha Noi` VÀ dòng ngày phát hành nằm ngay bên dưới như `14-Sep-2026` trong cùng ô). CẢNH BÁO TRÊN BL CHECK LIST: File BL Check List KHÔNG CÓ ngày `ISSUE DATE` (mục `B/L ISSUED AT` chỉ ghi nơi phát hành). Trường `DATE` ở góc trên cùng cạnh `PAGE` (ví dụ `DATE 2026.09.15` — ngày đầu tiên ở đầu trang Check List) chỉ là ngày in phiếu Check List, và `CUSTOM CLEARANCE DAY` là ngày thông quan; TUYỆT ĐỐI KHÔNG lấy trường `DATE` ở đầu trang BL Check List làm `ISSUE DATE` hay `ETD`!
- Nhãn Date/Dated phải được xác định theo vùng chứa, không lấy ngày bất kỳ.
- Ưu tiên đối chiếu ETD trực tiếp. Nếu BL chỉ có on-board date,
  ghi rõ đây là ngày liên quan; chỉ dùng thay ETD khi nghiệp vụ cho phép.
- Kiểm tra ISSUE DATE = ETD (Hạng mục 18): lấy ISSUE DATE thực tế trong ô `PLACE AND DATE OF ISSUE` trên BL (ví dụ `14-Sep-2026`) đối chiếu với ETD chuẩn trên BL Check List (tại ô `ETD` / `ETD HPH ON`, ví dụ `2026.09.14` / `14 SEP 2026`). Khi trình bày dòng `18. ISSUE DATE = ETD`: cột `Dữ liệu BL Check List` ghi ETD chuẩn của Check List (`2026.09.14 / 14 SEP 2026`), cột `Dữ liệu BL` ghi Place and Date of Issue trên BL (`MSC Ha Noi, 14-Sep-2026`); nếu cùng ngày/tháng/năm thì kết luận **ĐÚNG**. Không dùng issue date để điền thay ETD ở Hạng mục 17.
- Chuẩn hóa định dạng ngày nhưng giữ giá trị gốc và trang nguồn.
- Nếu có ngày nhưng ánh xạ chưa rõ, kết luận KHÔNG HIỂU.
  Chỉ kết luận KHÔNG TÌM THẤY sau khi kiểm tra đủ nội dung;
  nếu context bị cắt, ghi CHƯA ĐỦ BẰNG CHỨNG.
- SDD trên BL Check List ↔ DOOR trên BL. Ví dụ: `CY / BADEN SDD` ↔ `CY/DOOR`.

# Các bước kiểm tra
1. **Đọc và chuẩn hóa:** nhận diện từng trang, từng invoice, từng container và các phần thông tin nối tiếp; giữ nguyên giá trị gốc để trình bày.
2. **Số bill và invoice:**
   - Lấy chuỗi từ phần số đến hết chữ/số trên từng dòng INVOICE NO.; ví dụ `824957-0`.
   - Kiểm tra mỗi dòng BILL OF LADING (AIR WAYBILL) có số bill và số đó khớp BL.
   - Tại trang tổng hợp có G-TOTAL, kiểm tra đã bao gồm toàn bộ invoice ở các trang trước và không có invoice thừa.
   - Hiểu dạng rút gọn `824957-0 1` là `824957-0` và `824957-1`.
3. **B/L REQUIRED:** trên BL, `ON BOARD Date + ngày` nghĩa là ON BOARD; `RECEIVED DATE + ngày` nghĩa là RECEIVED. Đối chiếu với B/L REQUIRED trên Check List. BL chỉ có đúng một loại ngày là hợp lệ; có đồng thời cả hai loại là **SAI**.
4. **Description of goods:**
   - 4.1. Với mỗi invoice trong BL Check List: lấy toàn bộ nội dung từ dưới dòng INVOICE NO. đến ngay trên dòng TTL đầu tiên. Mỗi dòng mô tả hàng hóa là một description riêng.
   - 4.2. Gộp description của tất cả invoice và loại trùng hoàn toàn để có danh sách chuẩn duy nhất.
   - 4.3. Trích xuất description trên BL theo từng container, giữ nguyên mọi lần lặp.
   - 4.4. Thứ tự kiểm tra: Mỗi description chuẩn phải xuất hiện đúng 1 lần trên toàn bộ BL (0 lần → **SAI: thiếu**, >=2 lần → **SAI: trùng lặp**, ngoài danh sách chuẩn → **SAI: thừa**).
5. **Đối chiếu toàn bộ 23 hạng mục:**
   1. SHIPPER với dữ liệu chuẩn.(Lấy SHIPPER riêng từng file, dừng tại nhãn kế tiếp; không lấy số tiền hoặc INCOTERMS. Không coi SHIPPER lặp ở nhiều invoice là sai.) 2. CONSIGNEE. 3. NOTIFY PARTY 1. 4. NOTIFY PARTY 2. 5. DELIVERY TO. 6. CONTAINER NO. 7. SEAL NO. 8. CONTAINER TYPE. 9. P'KGS. 10. WEIGHT (WEIGHT phải lấy từ dòng TOTAL WEIGHT/TOTAL GROSS WEIGHT nếu có. Không suy WEIGHT từ P'KGS. Nếu không có, cộng tất cả WEIGHT của từng container/invoice.). 11. MEASUREMENT/M3. 12. Tổng P'KGS, WEIGHT và MEASUREMENT/M3. 13. GOODS DESCRIPTION. 14. PORT OF LOADING. 15. PORT OF DISCHARGE. 16. PLACE OF DELIVERY. 17. ETD (ETD trên Check List ↔ On-Board Date/ETD trên BL). 18. ISSUE DATE = ETD (ETD trên Check List ↔ Ngày phát hành trong ô PLACE AND DATE OF ISSUE trên BL; tuyệt đối không lấy trường DATE ở đầu trang Check List). 19. Tổng số container + loại container. 20. Delivery term (CY-CY, CY-DOOR, CFS-CFS, CFS-DOOR). 21. SHIPMENT NO. 22. B/L REQUIRED. 23. HS code (BL không được hiển thị HS code, nếu có là **SAI**).

# Định dạng đầu ra (Báo cáo dạng Bảng Markdown)

Báo cáo kết quả rõ ràng theo đúng 2 phần:

## 1. TỔNG HỢP ĐIỂM ĐÚNG
| STT | Hạng mục check | Dữ liệu BL Check List | Dữ liệu BL | Kết quả |
|---|---|---|---|---|
| ... | ... | ... | ... | **ĐÚNG** |

*(Với hạng mục container, mỗi container và thuộc tính là một dòng riêng. Cuối bảng ghi rõ: **Tổng số điểm đúng: X**)*

---

## 2. ĐIỂM CẦN XỬ LÝ

> **BẠN HÃY TỰ CHECK LOẠI BL, NUMBER OF ORIGINAL BL, ETA VIA, ETA TO VÀ CÁC ĐIỂM ĐẶC BIỆT DÀNH CHO THỊ TRƯỜNG MÀ BẠN ĐANG CHECK BL**

| STT | Hạng mục check | Dữ liệu BL Check List | Dữ liệu BL | Kết quả | Chi tiết khác biệt & Hành động xử lý |
|---|---|---|---|---|---|
| ... | ... | ... | ... | **SAI** / **KHÔNG TÌM THẤY** / **KHÔNG HIỂU** | ... |

*(In đậm phần khác biệt. Cuối bảng tổng hợp rõ: **Tổng số điểm SAI: X | KHÔNG TÌM THẤY: Y | KHÔNG HIỂU: Z**)*
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


# --- SQLite Fallback Helpers ---
def _sqlite_get_connection():
    from shared.sqlite_db import get_sqlite_connection
    return get_sqlite_connection()


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
