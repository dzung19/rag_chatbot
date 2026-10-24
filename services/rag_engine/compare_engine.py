"""Document Role Classifier & Role Resolver for Shipping Comparison (Phase 3).

Specifically identifies:
- B/L Check List (Tài liệu chuẩn / nội bộ kiểm tra)
- Bill of Lading / Sea Waybill (Chứng từ hãng tàu phát hành)
regardless of the order they were selected/uploaded by the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DocumentRole(str, Enum):
    CHECKLIST = "checklist"
    BL = "bl"
    UNKNOWN = "unknown"


# Checklist text signals & weights
_CHECKLIST_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("header_bl_checklist", re.compile(r"\bB/?L\s+CHECK\s*LIST\b", re.I), 25),
    ("header_checklist", re.compile(r"\bCHECK\s*LIST\b", re.I), 12),
    ("field_bl_required", re.compile(r"\bB/?L\s+REQUIRED\b", re.I), 12),
    ("section_stuffing_plan", re.compile(r"\bSTUFFING\s+PLAN\b", re.I), 12),
    ("summary_g_total", re.compile(r"\bG-TOTAL\b", re.I), 10),
    ("invoice_bil", re.compile(r"\bINVOICE\s*NO\.?\s*\(BIL\s*\)", re.I), 10),
    ("field_inter_company", re.compile(r"\bINTER\s+COMPANY\s+AMOUNT\b", re.I), 8),
    ("field_total_amount_usd", re.compile(r"\bTOTAL\s+AMOUNT\s*\(\s*US\$\s*\)", re.I), 8),
    ("row_s_total", re.compile(r"\bS-TOTAL\s*\|", re.I), 8),
    ("field_order_no", re.compile(r"\bORDER\s+NO\b", re.I), 6),
    ("field_request_no", re.compile(r"\bREQUEST\s*NO\.?\s*\d+\b", re.I), 6),
    ("field_batch_no", re.compile(r"\bBATCH\s+NO\b", re.I), 5),
    ("field_reg_no", re.compile(r"\bREG\.?\s*NO\b", re.I), 5),
    ("field_material_number", re.compile(r"\bMATERIAL\s+NUMBER\b", re.I), 5),
]

# BL / Sea Waybill text signals & weights
_BL_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("doc_sea_waybill", re.compile(r"\bSEA\s+WAYBILL\b", re.I), 20),
    ("doc_not_negotiable", re.compile(r"\bNOT\s+NEGOTIABLE\s*[-–]?\s*COPY\b", re.I), 14),
    ("field_rider_pages", re.compile(r"\bNO\.\s+OF\s+RIDER\s+PAGES\b", re.I), 14),
    ("field_pod_agent", re.compile(r"\bPORT\s+OF\s+DISCHARGE\s+AGENT\b", re.I), 12),
    ("section_freight_charges", re.compile(r"\bFREIGHT\s+&\s+CHARGES\b", re.I), 12),
    ("sign_carrier", re.compile(r"\bSIGNED\s+on\s+behalf\s+of\s+the\s+Carrier\b", re.I), 12),
    ("doc_shipped_on_board_date", re.compile(r"\bSHIPPED\s+ON\s+BOARD\s+DATE\b", re.I), 10),
    ("doc_date_laden_on_board", re.compile(r"\bDATE\s+LADEN\s+ON\s+BOARD\b", re.I), 10),
    ("carrier_endorsements", re.compile(r"\bCARRIER(?:\'S|\s+AGENTS|\s+RECEIPT)\b", re.I), 8),
    ("doc_ocean_freight", re.compile(r"\bOCEAN\s+FREIGHT\b", re.I), 8),
    ("doc_declared_value", re.compile(r"\bDECLARED\s+VALUE\b", re.I), 6),
    ("carrier_terms_reverse", re.compile(r"\bTERMS\s+CONTINUED\s+ON\s+REVERSE\b", re.I), 8),
    ("carrier_major_liner", re.compile(
        r"\b(?:MEDITERRANEAN\s+SHIPPING\s+COMPANY|MAERSK|COSCO|EVERGREEN|OCEAN\s+NETWORK\s+EXPRESS|CMA\s+CGM|HAPAG[- ]LLOYD|YANG\s+MING|WAN\s+HAI|HMM|ZIM|PIL)\b",
        re.I
    ), 14),
]


@dataclass
class ClassificationResult:
    role: DocumentRole
    confidence: float
    checklist_score: int
    bl_score: int
    matched_signals: list[str] = field(default_factory=list)


def classify_document(text: str, filename: str = "") -> ClassificationResult:
    """Classify a single document as CHECKLIST, BL, or UNKNOWN based on text & filename heuristics."""
    c_score = 0
    b_score = 0
    signals: list[str] = []

    fn_lower = (filename or "").lower()

    # Filename indicators
    if any(k in fn_lower for k in ["checklist", "check_list", "check-list", "cklist"]):
        c_score += 15
        signals.append("filename_checklist")
    if any(k in fn_lower for k in ["seaway", "waybill", "bill_of_lading"]) or fn_lower.startswith(("b_", "bl_")):
        b_score += 12
        signals.append("filename_bl")

    # Probe first 15,000 chars + last 5,000 chars to cover headers, forms and summary footers efficiently
    probe_text = text if len(text) <= 25000 else (text[:18000] + "\n" + text[-7000:])

    # Checklist text signals
    for sig_name, pat, weight in _CHECKLIST_PATTERNS:
        if pat.search(probe_text):
            c_score += weight
            signals.append(f"checklist:{sig_name}")

    # BL text signals
    for sig_name, pat, weight in _BL_PATTERNS:
        if pat.search(probe_text):
            b_score += weight
            signals.append(f"bl:{sig_name}")

    # Standalone BILL OF LADING check (only when not accompanied by CHECK LIST)
    if re.search(r"\bBILL\s+OF\s+LADING\b", probe_text, re.I) and not re.search(r"\bCHECK\s*LIST\b", probe_text, re.I):
        b_score += 8
        signals.append("bl:bill_of_lading_header")

    # Decision threshold
    total_score = c_score + b_score
    if total_score < 10:
        return ClassificationResult(
            role=DocumentRole.UNKNOWN,
            confidence=0.0,
            checklist_score=c_score,
            bl_score=b_score,
            matched_signals=signals,
        )

    if c_score >= b_score + 10:
        confidence = min(0.99, round(c_score / max(1, c_score + b_score), 2))
        return ClassificationResult(
            role=DocumentRole.CHECKLIST,
            confidence=confidence,
            checklist_score=c_score,
            bl_score=b_score,
            matched_signals=signals,
        )
    elif b_score >= c_score + 10:
        confidence = min(0.99, round(b_score / max(1, c_score + b_score), 2))
        return ClassificationResult(
            role=DocumentRole.BL,
            confidence=confidence,
            checklist_score=c_score,
            bl_score=b_score,
            matched_signals=signals,
        )
    else:
        # Ambiguous
        role = DocumentRole.CHECKLIST if c_score > b_score else (DocumentRole.BL if b_score > c_score else DocumentRole.UNKNOWN)
        return ClassificationResult(
            role=role,
            confidence=0.5,
            checklist_score=c_score,
            bl_score=b_score,
            matched_signals=signals,
        )


def resolve_compare_roles(
    doc1: dict[str, Any],
    doc2: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve and order the two documents being compared so that:
    - Return element 0 is ALWAYS the B/L Check List (Tài liệu chuẩn)
    - Return element 1 is ALWAYS the Bill of Lading / Sea Waybill (Tài liệu đối chiếu)

    Returns:
        (doc_checklist, doc_bl, resolution_metadata)
    """
    fn1 = doc1.get("metadata", {}).get("filename") or "Tài liệu 1"
    fn2 = doc2.get("metadata", {}).get("filename") or "Tài liệu 2"

    text1 = doc1.get("text", "") or ""
    text2 = doc2.get("text", "") or ""

    res1 = classify_document(text1, filename=fn1)
    res2 = classify_document(text2, filename=fn2)

    logger.info(
        "Document role classification: doc1='%s' -> role=%s (c=%d, b=%d, conf=%.2f) | "
        "doc2='%s' -> role=%s (c=%d, b=%d, conf=%.2f)",
        fn1,
        res1.role.value,
        res1.checklist_score,
        res1.bl_score,
        res1.confidence,
        fn2,
        res2.role.value,
        res2.checklist_score,
        res2.bl_score,
        res2.confidence,
    )

    # Clear-cut case: one is checklist, other is BL
    if res1.role == DocumentRole.CHECKLIST and res2.role == DocumentRole.BL:
        doc_checklist, doc_bl = doc1, doc2
        swapped = False
        is_shipping = True
    elif res2.role == DocumentRole.CHECKLIST and res1.role == DocumentRole.BL:
        doc_checklist, doc_bl = doc2, doc1
        swapped = True
        is_shipping = True
    else:
        # Relative net scoring: net = checklist_score - bl_score
        net1 = res1.checklist_score - res1.bl_score
        net2 = res2.checklist_score - res2.bl_score

        is_shipping = (res1.checklist_score + res1.bl_score + res2.checklist_score + res2.bl_score) >= 15

        if net1 >= net2:
            doc_checklist, doc_bl = doc1, doc2
            swapped = False
        else:
            doc_checklist, doc_bl = doc2, doc1
            swapped = True

    meta = {
        "is_shipping_pair": is_shipping,
        "swapped": swapped,
        "checklist_filename": doc_checklist.get("metadata", {}).get("filename") or "Checklist",
        "bl_filename": doc_bl.get("metadata", {}).get("filename") or "Bill of Lading",
        "doc1_classification": {
            "role": res1.role.value,
            "checklist_score": res1.checklist_score,
            "bl_score": res1.bl_score,
            "confidence": res1.confidence,
            "signals": res1.matched_signals,
        },
        "doc2_classification": {
            "role": res2.role.value,
            "checklist_score": res2.checklist_score,
            "bl_score": res2.bl_score,
            "confidence": res2.confidence,
            "signals": res2.matched_signals,
        },
    }

    logger.info(
        "Resolved compare roles: Checklist='%s', BL='%s', swapped=%s, is_shipping=%s",
        meta["checklist_filename"],
        meta["bl_filename"],
        meta["swapped"],
        meta["is_shipping_pair"],
    )

    return doc_checklist, doc_bl, meta


_MONTH_MAP: dict[str, int] = {
    "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRUARY": 2,
    "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4,
    "MAY": 5,
    "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10,
    "NOV": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBER": 12,
}

_DATE_TOKEN_RE = re.compile(
    r"\b(\d{1,2}[-\s]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[-\s]+\d{4}|\d{4}[./-]\d{2}[./-]\d{2})\b",
    re.I,
)


def normalize_shipping_date(raw_date: str) -> Optional[str]:
    """Normalize shipping date strings (e.g. '2026.09.14', '14 SEP 2026', '14-Sep-2026') to 'YYYY-MM-DD'."""
    if not raw_date:
        return None
    s = raw_date.strip()
    m_ymd = re.match(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})$", s)
    if m_ymd:
        return f"{int(m_ymd.group(1)):04d}-{int(m_ymd.group(2)):02d}-{int(m_ymd.group(3)):02d}"

    m_dmy = re.match(r"^(\d{1,2})[-\s]+([A-Za-z]+)[-\s]+(\d{4})$", s)
    if m_dmy:
        day = int(m_dmy.group(1))
        mon_str = m_dmy.group(2).upper()
        year = int(m_dmy.group(3))
        mon = _MONTH_MAP.get(mon_str) or _MONTH_MAP.get(mon_str[:3])
        if mon:
            return f"{year:04d}-{mon:02d}-{day:02d}"
    return None


def build_shipping_dates_reference_banner(checklist_text: str, bl_text: str) -> str:
    """Extract and cross-check ETD, On-Board Date, and Place/Date of Issue between Checklist and BL.

    Prevents the LLM from mistaking the Checklist's top-right print 'DATE' (next to 'PAGE 1')
    for the B/L Issue Date or overlooking the second line of 'PLACE AND DATE OF ISSUE' on the BL.
    """
    lines: list[str] = [
        "=== BẢNG THAM CHIẾU NGÀY THÁNG ĐÃ BÓC TÁCH TỰ ĐỘNG (DÙNG CHO HẠNG MỤC 17 & 18) ==="
    ]

    # 1. Extract ETD and Report Date from B/L Check List
    cl_etd_raw: list[str] = []
    m_etd_box = re.search(r"\*\*ETD[^*]*\*\*:\s*([^\n|]+)", checklist_text, re.I)
    if m_etd_box:
        for d in _DATE_TOKEN_RE.findall(m_etd_box.group(1)):
            if d not in cl_etd_raw:
                cl_etd_raw.append(d.strip())

    m_etd_col = re.search(
        r"\bFROM\s*\|\s*ETD\b[^\n]*\n\s*([^\n|]+?)\s*\|\s*(\d{4}[./-]\d{2}[./-]\d{2}|\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{4})",
        checklist_text,
        re.I,
    )
    if m_etd_col and m_etd_col.group(2).strip() not in cl_etd_raw:
        cl_etd_raw.append(m_etd_col.group(2).strip())

    m_etd_req = re.search(
        r"\bETD\s+(?:[A-Z]{2,5}\s+)?ON\s+(\d{1,2}[-\s]+[A-Za-z]{3,9}[-\s]+\d{4}|\d{4}[./-]\d{2}[./-]\d{2})",
        checklist_text,
        re.I,
    )
    if m_etd_req and m_etd_req.group(1).strip() not in cl_etd_raw:
        cl_etd_raw.append(m_etd_req.group(1).strip())

    cl_etd_iso = normalize_shipping_date(cl_etd_raw[0]) if cl_etd_raw else None
    if cl_etd_raw:
        lines.append(
            f"- **[BL CHECK LIST] ETD chuẩn**: `{' / '.join(cl_etd_raw)}` "
            f"(Chuẩn hóa: `{cl_etd_iso or cl_etd_raw[0]}`)"
        )

    m_cl_print_date = re.search(
        r"\bSHIPPED\s+PER\s*\|\s*DATE\s*\|\s*PAGE\s*\n[^\n|]+\|\s*(\d{4}[./-]\d{2}[./-]\d{2})",
        checklist_text,
        re.I,
    )
    if m_cl_print_date:
        lines.append(
            f"- **[BL CHECK LIST] Cảnh báo trường `DATE` cạnh `PAGE`**: `{m_cl_print_date.group(1).strip()}` "
            "chỉ là **Ngày in/xuất phiếu Check List** (và `CUSTOM CLEARANCE DAY` là ngày thông quan) — "
            "**TUYỆT ĐỐI KHÔNG** lấy làm `ISSUE DATE` (Place and Date of Issue) hay `ETD`!"
        )

    # 2. Extract Place and Date of Issue & On-Board Date from BL
    bl_issue_place = ""
    bl_issue_date = ""

    m_inline_issue = re.search(
        r"\bDate\s+of\s+Issue\s+(\d{1,2}[-\s]+[A-Za-z]{3,9}[-\s]+\d{4}|\d{4}[./-]\d{2}[./-]\d{2})(?:\s*\|\s*|\s+)Place\s+of\s+Issue\s+([^\n|]+)",
        bl_text,
        re.I,
    )
    if m_inline_issue:
        bl_issue_date = m_inline_issue.group(1).strip()
        bl_issue_place = m_inline_issue.group(2).strip()
    else:
        for m_box in re.finditer(
            r"PLACE\s+AND\s+DATE\s+OF\s+ISSUE([^\n]*)\n((?:[^\n]+\n?){1,4})",
            bl_text,
            re.I,
        ):
            window_lines = [ln.strip() for ln in m_box.group(2).splitlines() if ln.strip()]
            left_parts: list[str] = []
            for ln in window_lines[:3]:
                if re.search(r"Sea\s+Waybill\s+Standard|TERMS\s+CONTINUED|See\s+website|###", ln, re.I):
                    break
                first_col = ln.split("|")[0].strip()
                if first_col:
                    left_parts.append(first_col)
            joined_left = " | ".join(left_parts)
            found_dates = _DATE_TOKEN_RE.findall(joined_left)
            if found_dates:
                bl_issue_date = found_dates[-1].strip()
                bl_issue_place = _DATE_TOKEN_RE.sub("", joined_left).replace("|", " ").strip(" ,;:-")
                break

    bl_issue_iso = normalize_shipping_date(bl_issue_date)
    if bl_issue_date:
        lines.append(
            f"- **[BL / SEA WAYBILL] PLACE AND DATE OF ISSUE (Nơi & Ngày phát hành BL)**: "
            f"Nơi phát hành = `{bl_issue_place or 'N/A'}` | **ISSUE DATE = `{bl_issue_date}`** "
            f"(Chuẩn hóa: `{bl_issue_iso or bl_issue_date}`)"
        )

    bl_onboard_date = ""
    m_ob_box = re.search(r"\*\*Date Laden on Board / On Board Date\*\*:\s*([^\n]+)", bl_text, re.I)
    if m_ob_box:
        ob_d = _DATE_TOKEN_RE.findall(m_ob_box.group(1))
        if ob_d:
            bl_onboard_date = ob_d[0].strip()
    if not bl_onboard_date:
        m_ob = re.search(
            r"(?:SHIPPED\s+ON\s+BOARD\s+DATE|Date\s+Laden\s+on\s+Board)[^\n]*\n((?:[^\n]+\n?){1,3})",
            bl_text,
            re.I,
        )
        if m_ob:
            ob_d = _DATE_TOKEN_RE.findall(m_ob.group(1))
            if ob_d:
                bl_onboard_date = ob_d[0].strip()

    bl_onboard_iso = normalize_shipping_date(bl_onboard_date)
    if bl_onboard_date:
        lines.append(
            f"- **[BL / SEA WAYBILL] ON-BOARD DATE (`SHIPPED ON BOARD DATE` / `Date Laden on Board`)**: "
            f"**`{bl_onboard_date}`** (Chuẩn hóa: `{bl_onboard_iso or bl_onboard_date}`)"
        )

    # 3. Pre-verified comparison summary for Items 17 & 18
    if cl_etd_iso and bl_onboard_iso:
        verdict_17 = "**ĐÚNG** (Cùng ngày)" if cl_etd_iso == bl_onboard_iso else "**SAI** (Khác ngày)"
        lines.append(
            f"- **[ĐỐI CHIẾU HẠNG MỤC 17 — ETD]**: "
            f"BL Check List (`{' / '.join(cl_etd_raw)}`) ↔ BL On-Board Date (`{bl_onboard_date}`) => {verdict_17}"
        )
    if cl_etd_iso and bl_issue_iso:
        verdict_18 = "**ĐÚNG** (Cùng ngày)" if cl_etd_iso == bl_issue_iso else "**SAI** (Khác ngày)"
        place_prefix = f"{bl_issue_place}, " if bl_issue_place else ""
        lines.append(
            f"- **[ĐỐI CHIẾU HẠNG MỤC 18 — ISSUE DATE = ETD]**: "
            f"Dữ liệu BL Check List (ETD: `{' / '.join(cl_etd_raw)}`) ↔ "
            f"Dữ liệu BL (Place and Date of Issue: `{place_prefix}{bl_issue_date}`) => {verdict_18}"
        )

    if len(lines) == 1:
        return ""
    lines.append("===================================================================================")
    return "\n".join(lines)
