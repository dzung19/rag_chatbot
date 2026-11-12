"""Document Role Classifier & Role Resolver for Shipping Comparison (Phase 3).

Specifically identifies:
- B/L Check List (Tài liệu chuẩn / nội bộ kiểm tra)
- Bill of Lading / Sea Waybill (Chứng từ hãng tàu phát hành)
regardless of the order they were selected/uploaded by the user.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
import logging
import re
from typing import Any, Optional
import unicodedata

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


def _parse_num_val(raw: str) -> float:
    if not raw or raw == "N/A":
        return 0.0
    clean = re.sub(r"[^0-9.]", "", raw.replace(",", ""))
    try:
        return float(clean)
    except ValueError:
        return 0.0


def extract_document_containers(full_text: str, is_checklist: bool = False) -> list[dict[str, Any]]:
    """Extract all containers across the entire multi-page document (resolving page-split S-TOTALs)."""
    # 1. Strip auto-generated markdown tables (both ### heading form and chunker breadcrumb form)
    #    and FORM BOXES section so we never double-match container IDs before the actual cargo table.
    clean_text = re.sub(
        r"### \[BẢNG CONTAINER BÓC TÁCH TỰ ĐỘNG[^\n]*\n(?:\|[^\n]*\n)+",
        "",
        full_text,
    )
    clean_text = re.sub(
        r"### \[THÔNG TIN Ô BIỂU MẪU \(FORM BOXES\)\]\n(?:- \*\*[^\n]*\n)+",
        "",
        clean_text,
    )
    # Also strip any markdown table row that starts with '| <idx> | <CONT_NO> |' from chunked tables
    clean_text = re.sub(
        r"^\|\s*(?:\d+|\*\*TỔNG\*\*)\s*\|[^\n]*$",
        "",
        clean_text,
        flags=re.M,
    )

    cont_re = re.compile(r"\b([A-Z]{4}\d{7})\b")
    matches = list(cont_re.finditer(clean_text))
    containers: list[dict[str, Any]] = []
    seen_conts: dict[str, int] = {}

    for idx, m in enumerate(matches):
        c_no = m.group(1).upper()

        start = m.start()
        # Find the next match that has a DIFFERENT container number so duplicate header mentions don't truncate the block
        next_diff_start = None
        for next_m in matches[idx + 1 :]:
            if next_m.group(1).upper() != c_no:
                next_diff_start = next_m.start()
                break

        if next_diff_start is not None:
            end = next_diff_start
        else:
            rest = clean_text[start:]
            term_m = re.search(
                r"\b(?:G-TOTAL\b|Total\s*:|TOTAL\s*[:\s]+[0-9]|SHIPMENT\s*:|PLACE AND DATE OF ISSUE|SHIPPED ON BOARD DATE|CARRIER'S RECEIPT)\b",
                rest,
                re.I,
            )
            end = (start + term_m.start()) if term_m else min(len(clean_text), start + 1800)

        # Look slightly before the container number (up to 120 chars on the same/previous line)
        # because Checklist lines have `STUFFING PLAN 40HC:40FT ... | FCL CONTAINER NO. MSDU5808970`
        prefix_start = max(0, start - 120)
        block = clean_text[prefix_start:end]
        strict_block = clean_text[start:end]

        # Match S-TOTAL whether columns are separated by pipes '|' or spaces (e.g. native PyMuPDF text)
        stot_m = re.search(
            r"S-TOTAL(?:\s*\|\s*|\s+)(\d[\d,.]*)(?:\s*\|\s*|\s+)(\d[\d,.]*)(?:\s*\|\s*|\s+)(\d[\d,.]*)(?:\s*\|\s*|\s+)(\d[\d,.]*)",
            strict_block,
            re.I,
        )
        sets_raw = "N/A"
        if stot_m:
            sets_raw = stot_m.group(1).strip()
            pkgs_raw = f"{stot_m.group(2).strip()} PKGS"
            weight_raw = f"{stot_m.group(3).strip()} KGS"
            cbm_raw = f"{stot_m.group(4).strip()} CBM"
        else:
            p_m = re.search(
                r"(?<![A-Z0-9])(\d[\d,.]*)\s*(packages?|pkgs?|pallets?|cartons?|ctns?|bags?|drums?|pcs|units?)(?:\(s\))?",
                strict_block,
                re.I,
            )
            pkgs_raw = f"{p_m.group(1)} {p_m.group(2).upper()}" if p_m else "N/A"

            # Same-line '<num> KGS' or multi-line 'kgs.\n <num>' (native PyMuPDF layout)
            w_m = re.search(r"(?<![A-Z0-9])(\d[\d,.]*)[ \t|]*(?:kgs?|kg)\b", strict_block, re.I) or re.search(
                r"\b(?:kgs?|kg)\.?\s*[:|]?\s*(\d[\d,.]*)", strict_block, re.I
            )
            weight_raw = f"{w_m.group(1).rstrip('.')} KGS" if w_m else "N/A"

            # Same-line '<num> CBM/cu.m.' or multi-line 'cu. m.\n <num>' (native PyMuPDF layout)
            c_m = re.search(
                r"(?<![A-Z0-9])(\d[\d,.]*)[ \t|]*(?:cbm|m3|mtq|cu\.?\s*m\.?)(?:\b|$)", strict_block, re.I
            ) or re.search(r"\b(?:cbm|m3|mtq|cu\.?\s*m\.?)\s*[:|]?\s*(\d[\d,.]*)", strict_block, re.I)
            cbm_raw = f"{c_m.group(1).rstrip('.')} CBM" if c_m else "N/A"

            # Fallback for Checklist: sum item rows `(0001) ... <sets> <pkgs> <weight> <cbm>` if S-TOTAL was cut off
            if is_checklist and (pkgs_raw == "N/A" or weight_raw == "N/A"):
                item_rows = re.findall(
                    r"\(\d{4}\)\s+[^\n]+?(?:\s*\|\s*|\s+)(\d[\d,.]*)(?:\s*\|\s*|\s+)(\d[\d,.]*)(?:\s*\|\s*|\s+)(\d[\d,.]*)(?:\s*\|\s*|\s+)(\d[\d,.]*)\s*$",
                    strict_block,
                    flags=re.M,
                )
                if item_rows:
                    sum_sets = int(round(sum(_parse_num_val(r[0]) for r in item_rows)))
                    sum_pkgs = int(round(sum(_parse_num_val(r[1]) for r in item_rows)))
                    sum_w = round(sum(_parse_num_val(r[2]) for r in item_rows), 1)
                    sum_c = round(sum(_parse_num_val(r[3]) for r in item_rows), 3)
                    sets_raw = str(sum_sets)
                    pkgs_raw = f"{sum_pkgs} PKGS"
                    weight_raw = f"{sum_w:,.1f} KGS"
                    cbm_raw = f"{sum_c:.3f} CBM"

        seal_no = "N/A"
        # 1. Explicit SEAL NO label (filtering out table header tokens like DescriptionofGoods)
        s_m = re.search(
            r"(?:Carrier|Seal\s*(?:Number|No\.?)?[:\s|]*Carrier?|SEAL\s*NO\.?[:\s|]*)\s+([A-Z0-9\-]{5,20})",
            strict_block,
            re.I,
        )
        if s_m:
            cand = s_m.group(1).strip()
            if not re.match(r"^(?:Description|Goods|Carrier|Container|Particulars|Marks|Number)", cand, re.I):
                seal_no = cand

        # 2. Directly adjacent seal token (e.g. '<CONT_NO> <SEAL_NO>')
        if seal_no == "N/A":
            s_m2 = re.search(r"\b" + re.escape(c_no) + r"[\s|/]+([A-Z0-9\-]{6,15})\b", strict_block)
            if s_m2:
                cand = s_m2.group(1).strip()
                if not re.match(r"^(?:40HC|40HQ|20GP|40GP|40FT|FCL|LCL|CY|CFS|SEAL|CONT|DESCRIPTION|HAIPHONG|HANOI|DANANG|VIETNAM)\b", cand, re.I):
                    seal_no = cand

        t_m = re.search(
            r"((?:20|40|45)[A-Z0-9]{0,3}:[0-9A-Z\s]+?(?=\s+FCL\b|\s+SEAL\b|\s+CY\b|\s+SDD\b|\s{2,}|\s+\d+\s*$|\n|$)|\b(?:20|40|45)(?:\'|\s*ft)?\s*(?:gp|dc|hc|hq|rf|ot|fr|tk|std|dry(?:\s*container)?|general\s*purpose|high\s*cube(?: container)?))",
            block,
            re.I | re.M,
        )
        if not t_m:
            t_m = re.search(
                r"\bNUMBER\s+OF\s+CONTAINER[^\n]*\n\s*([0-9A-Z':\s\-]+?)(?:\s+\d+\s*$|\n)",
                clean_text,
                re.I,
            )
        if not t_m:
            t_m = re.search(
                r"\b(?:PART\s+OF\s+)?(?:\d+\s*x\s*)?((?:20|40|45)(?:\'|\s*ft)?\s*(?:high\s*cube(?: container)?|hc|hq|gp|dc|std|dry(?:\s*container)?)[^\n|]*)",
                clean_text,
                re.I,
            )

        if t_m:
            raw_t_full = t_m.group(1).strip()
            raw_t = raw_t_full.upper().replace(" ", "").replace("-", "").replace("'", "")
            if any(k in raw_t for k in ["40HC", "40HQ", "HIGHCUBE", "40FT9FT6IN"]):
                cont_type = "40HC"
            elif any(k in raw_t for k in ["20GP", "20DC", "20STD", "20DRY", "20FT8FT6IN"]):
                cont_type = "20GP"
            elif any(k in raw_t for k in ["40GP", "40DC", "40STD", "40DRY", "40FT8FT6IN"]):
                cont_type = "40GP"
            elif any(k in raw_t for k in ["45HC", "45HQ"]):
                cont_type = "45HC"
            else:
                cont_type = raw_t_full
            type_raw = raw_t_full
        else:
            cont_type = "N/A"
            type_raw = "N/A"

        # Extract Delivery / Movement term (supports 'BAC NINH | CY / LONG BEACH | CY', 'CY / ... SDD', 'CY - CY', etc.)
        term_m = re.search(
            r"(\b(?:CY|CFS)(?:\s*\|\s*|\s+)[/\-](?:\s*\|\s*|\s+|[A-Z\s|]*\|\s*)(?:CY|SDD|DOOR|CFS)\b|[A-Z\s]+CY\s*/\s*[A-Z\s]+(?:CY|SDD)|\b(?:CY\s*[\-/]\s*CY|CY\s*[\-/]\s*DOOR|CFS\s*[\-/]\s*CFS|CFS\s*[\-/]\s*DOOR)\b)",
            strict_block,
            re.I,
        )
        movement = "N/A"
        movement_raw = "N/A"
        if term_m:
            movement_raw = " ".join(term_m.group(1).replace("|", " ").split())
            tm_up = movement_raw.upper()
            lhs = tm_up.split("/")[0] if "/" in tm_up else tm_up.split("-")[0]
            rhs = tm_up.split("/")[-1] if "/" in tm_up else tm_up.split("-")[-1]
            orig_term = "CFS" if "CFS" in lhs else "CY"
            if "SDD" in rhs or "DOOR" in rhs:
                dest_term = "DOOR"
            elif "CFS" in rhs:
                dest_term = "CFS"
            else:
                dest_term = "CY"
            movement = f"{orig_term}-{dest_term}"

        if weight_raw != "N/A" or pkgs_raw != "N/A" or seal_no != "N/A":
            entry = {
                "container_no": c_no,
                "seal_no": seal_no,
                "sets_raw": sets_raw,
                "pkgs_num": int(round(_parse_num_val(pkgs_raw))),
                "pkgs_raw": pkgs_raw,
                "type": cont_type,
                "type_raw": type_raw,
                "movement": movement,
                "movement_raw": movement_raw,
                "weight_num": round(_parse_num_val(weight_raw), 3),
                "weight_raw": weight_raw,
                "cbm_num": round(_parse_num_val(cbm_raw), 4),
                "cbm_raw": cbm_raw,
            }
            if c_no in seen_conts:
                prev_idx = seen_conts[c_no]
                prev = containers[prev_idx]
                if (prev["pkgs_raw"] == "N/A" or prev["weight_raw"] == "N/A") and (
                    pkgs_raw != "N/A" and weight_raw != "N/A"
                ):
                    containers[prev_idx] = entry
            else:
                seen_conts[c_no] = len(containers)
                containers.append(entry)

    return containers


def _dedup_place(raw_place: str) -> str:
    """Clean carrier signature boilerplate and duplicate words from Place of Issue."""
    if not raw_place:
        return ""
    s = re.sub(r"\*\*[^*]+\*\*:?", "", raw_place)
    s = re.sub(
        r"\b(?:SIGNED\s+on\s+behalf|by\s+MSC|As\s+Agent|Carrier|Mediterranean\s+Shipping)[^\n]*",
        "",
        s,
        flags=re.I,
    )
    tokens: list[str] = []
    for tok in s.split():
        if not tokens or tok.lower() != tokens[-1].lower():
            tokens.append(tok)
    return " ".join(tokens).strip(" ,;:-")


def extract_goods_descriptions_comparison(checklist_text: str, bl_text: str) -> Optional[dict[str, Any]]:
    """Deterministic implementation of Step 4 (Goods Description check):
    - 4.1: For each INVOICE NO. in BL Check List, collect description lines until the first TTL line.
    - 4.2: Deduplicate across all invoices to form the unique standard description list.
    - 4.3: Extract all goods description lines across all containers on the BL (keeping all repetitions).
    - 4.4: Each standard description must appear EXACTLY 1 time across the entire BL
           (0 times -> SAI: thiếu; >=2 times -> SAI: trùng lặp; not in standard list -> SAI: thừa).
    """
    # 4.1 & 4.2: Extract from Checklist invoices
    cl_unique: list[str] = []
    cl_by_inv: list[str] = []
    inv_blocks = re.finditer(
        r"INVOICE\s*NO\.?\s*(?:\([^)]*\)\s*)?([A-Z0-9\-]+)\s*\n((?:[^\n]+\n)+?)(?=\s*TTL\b)",
        checklist_text,
        re.I,
    )
    for m in inv_blocks:
        inv_no = m.group(1).strip()
        raw_lines = [ln.strip() for ln in m.group(2).splitlines() if ln.strip()]
        desc_lines: list[str] = []
        for ln in raw_lines:
            # Skip table headers or page headers if any
            if re.search(r"^(?:===|###|- \*\*|BIL\s*\||INCOTERMS)", ln, re.I):
                continue
            clean_d = " ".join(ln.replace("|", " ").split()).strip()
            if clean_d:
                desc_lines.append(clean_d)
                if clean_d not in cl_unique:
                    cl_unique.append(clean_d)
        if desc_lines:
            cl_by_inv.append(f"{', '.join(desc_lines)} ({inv_no})")

    if not cl_unique:
        return None

    # 4.3: Extract descriptions per container on BL
    clean_bl = re.sub(
        r"### \[(?:BẢNG CONTAINER|TÓM TẮT NGÀY)[^\n]*\n(?:.*\n)*?(?=### |=== |$)",
        "",
        bl_text,
    )
    bl_all_occurrences: list[str] = []
    bl_by_cont: list[str] = []

    cont_matches = list(re.finditer(r"\b([A-Z]{4}\d{7})\b", clean_bl))
    seen_c: set[str] = set()
    for idx, cm in enumerate(cont_matches):
        cno = cm.group(1).upper()
        if cno in seen_c:
            continue
        start = cm.start()
        if idx + 1 < len(cont_matches):
            end = cont_matches[idx + 1].start()
        else:
            rest = clean_bl[start:]
            tm = re.search(
                r"(?:SHIPMENT\s*:|TOTAL\s*:|DECLARED\s+BY\s+SHIPPER|PLACE\s+AND\s+DATE\s+OF\s+ISSUE)",
                rest,
                re.I,
            )
            end = (start + tm.start()) if tm else min(len(clean_bl), start + 1000)

        c_block = clean_bl[start:end]
        # Look for "<N> CARTON(S)/PACKAGE(S) of <First Description>"
        first_m = re.search(
            r"\b\d[\d,.]*\s*(?:CARTONS?|PACKAGES?|PKGS?|PALLETS?|CTNS?|PCS|UNITS?)(?:\(S\))?\s+of\s+([^\n|]+)",
            c_block,
            re.I,
        )
        if not first_m:
            continue
        seen_c.add(cno)
        c_descs: list[str] = [" ".join(first_m.group(1).split()).strip()]

        # Subsequent lines inside the container block may contain additional description lines on the right column
        after_first = c_block[first_m.end() :]
        for raw_ln in after_first.splitlines():
            ln = raw_ln.strip()
            if not ln:
                continue
            if re.search(r"(?:SHIPMENT\s*:|TOTAL\s*:|DECLARED\s+BY|THIS\s+SHIPMENT|CY\s*[\-/]\s*CY)", ln, re.I):
                break
            # Split by '|' to check columns
            parts = [p.strip() for p in ln.split("|") if p.strip()]
            for p in parts:
                # Skip container types, seal numbers, freight, weights, measurements
                if re.search(
                    r"^(?:(?:20|40|45)[\s\']*(?:HIGH\s+CUBE|HC|HQ|GP|DC|STD)|Seal\s+Number|Carrier\s+[A-Z0-9]|FREIGHT\s+(?:COLLECT|PREPAID)|\d[\d,.]*\s*(?:kgs?|cu\.?\s*m|cbm))",
                    p,
                    re.I,
                ):
                    continue
                clean_p = " ".join(p.split()).strip()
                if clean_p and len(clean_p) >= 3:
                    c_descs.append(clean_p)

        bl_all_occurrences.extend(c_descs)
        bl_by_cont.append(f"{cno}: {', '.join(c_descs)}")

    if not bl_all_occurrences:
        return None

    counts = Counter(bl_all_occurrences)
    missing = [d for d in cl_unique if counts.get(d, 0) == 0]
    duplicated = [f"{d} (lặp {counts[d]} lần)" for d in cl_unique if counts.get(d, 0) >= 2]
    extra = [f"{d} ({cnt} lần)" for d, cnt in counts.items() if d not in cl_unique]

    is_ok = not missing and not duplicated and not extra
    issues: list[str] = []
    if duplicated:
        issues.append(f"trùng lặp >=2 lần trên BL: {', '.join(duplicated)}")
    if missing:
        issues.append(f"thiếu trên BL: {', '.join(missing)}")
    if extra:
        issues.append(f"thừa ngoài danh sách chuẩn: {', '.join(extra)}")

    return {
        "cl_unique": cl_unique,
        "bl_by_cont": bl_by_cont,
        "counts": dict(counts),
        "is_ok": is_ok,
        "issues_summary": "; ".join(issues) if issues else "Mỗi description chuẩn xuất hiện đúng 1 lần",
    }


def clean_shipping_context_for_llm(checklist_text: str, bl_text: str) -> tuple[str, str]:
    """Strip misleading print-date headers, incomplete per-page container tables, and legal fine-print noise."""
    cl = checklist_text
    # 1. Remove misleading Check List Print Date, top-header '1. Shipper: BIL', and invoice amount noise in '1. Shipper'
    cl = re.sub(r"^- \*\*1\. Shipper\*\*:\s*BIL\s*\n", "", cl, flags=re.M | re.I)
    if re.search(r"\bSHIPPER\s*:\s*BIVN\b", cl, re.I):
        cl = re.sub(
            r"^- \*\*1\. Shipper\*\*:[^\n]*$",
            "- **1. Shipper**: BIVN (BROTHER INDUSTRIES (VIETNAM) LTD.)",
            cl,
            flags=re.M | re.I,
        )
    cl = re.sub(r"^- \*\*Check List Print Date[^\n]*\n", "", cl, flags=re.M | re.I)
    cl = re.sub(r"^\s*- \*\*Ngày in phiếu Check List[^\n]*\n", "", cl, flags=re.M | re.I)
    # Strip print date from 'SHIPPED PER | DATE | PAGE \n VALIANT | 2026.09.15 | 1'
    cl = re.sub(
        r"(SHIPPED\s+PER)\s*\|\s*DATE\s*\|\s*(PAGE\s*\n[^\n|]+?)\s*\|\s*\d{4}[./-]\d{2}[./-]\d{2}\s*\|\s*(\d+)",
        r"\1 | \2 | \3",
        cl,
        flags=re.I,
    )
    # Strip running header 'BIL | REQUEST NO.830753 | 2026.09.15 2' on continuation pages
    cl = re.sub(
        r"^BIL\s*\|\s*(REQUEST\s+NO\.[^\n|]+?)\s*\|\s*\d{4}[./-]\d{2}[./-]\d{2}\s+(\d+)\s*$",
        r"\1 | PAGE \2",
        cl,
        flags=re.M | re.I,
    )
    # Replace incomplete per-page container tables on Check List with assembled full summary
    cl = re.sub(
        r"\n*### \[BẢNG CONTAINER BÓC TÁCH TỰ ĐỘNG - TRANG \d+\]\n(?:\|[^\n]*\n)+",
        "\n",
        cl,
    )
    cl_conts = extract_document_containers(checklist_text, is_checklist=True)
    if cl_conts:
        # cl_conts phai duoc lay va xu ly xong truoc doan nay.
        term_summary = ", ".join(
            f"{container['container_no']}={container['movement']}"
            for container in cl_conts
            if container.get("movement")
            and container["movement"] != "N/A"
        ) or "Không xác định"

        type_summary = ", ".join(
            sorted({
                container["type"]
                for container in cl_conts
                if container.get("type")
                and container["type"] != "N/A"
            })
        ) or "Không xác định"
        
        cl_tot_p = sum(c["pkgs_num"] for c in cl_conts)
        cl_tot_w = sum(c["weight_num"] for c in cl_conts)
        cl_tot_c = sum(c["cbm_num"] for c in cl_conts)
        if cl_tot_p > 0 and cl_tot_w > 0:
            cl_pkgs_str = ", ".join(str(c["pkgs_num"]) for c in cl_conts)
            cl_w_str = ", ".join(c["weight_raw"] for c in cl_conts)
            cl_c_str = ", ".join(c["cbm_raw"] for c in cl_conts)
            cl += (
                f"\n\n### [BẢNG CONTAINER VÀ TỔNG HỢP TRÊN B/L CHECK LIST (ĐÃ GHÉP ĐỦ TRANG)]\n"
                f"- Danh sách {len(cl_conts)} Container: {', '.join(c['container_no'] for c in cl_conts)} "
                f"(Loại: {type_summary}, Term: {term_summary})\n"
                f"- P'KGS từng container: {cl_pkgs_str} PKGS (Tổng P'KGS: {cl_tot_p} PKGS)\n"
                f"- WEIGHT từng container: {cl_w_str} (Tổng Gross Weight: {cl_tot_w:,.1f} KGS)\n"
                f"- MEASUREMENT từng container: {cl_c_str} (Tổng Measurement: {cl_tot_c:.3f} CBM)\n"
            )

    bl = bl_text
    # 2. Remove Carrier's legal fine print on BL Page 1 to save ~3,000 chars of noise
    bl = re.sub(
        r"CARRIER'S AGENT ENDORSEMENTS:[\s\S]*?(?=Please see attached RIDER|\d+\s*x\s*(?:20|40|45))",
        "",
        bl,
        flags=re.I,
    )
    bl = re.sub(
        r"continued from Carrier's Agent Endorsements[\s\S]*?(?=Please see attached RIDER|\d+\s*x\s*(?:20|40|45))",
        "",
        bl,
        flags=re.I,
    )
    bl = re.sub(
        r"Unless instructed otherwise in writing by the Shipper[\s\S]*?signed this Sea\s*\n?Waybill\.",
        "",
        bl,
        flags=re.I,
    )
    # Strip MSC boilerplate inside Notify Party 1 & Notify Party 2 boxes
    bl = re.sub(
        r"\|\s*Lloyds/IMO Number:[^\n]*",
        "",
        bl,
        flags=re.I,
    )
    bl = re.sub(
        r'"Any dispute arising out of[\s\S]*?\[Continued in the Description section\]',
        "",
        bl,
        flags=re.I,
    )
    bl = re.sub(
        r"\(No responsibility shall attach to Carrier[^\n]*?Clause 20\)\s*\|?\s*",
        "",
        bl,
        flags=re.I,
    )
    bl = re.sub(
        r"\s*\|?\s*CONTINUED IN CARRIER[’']S AGENTS ENDORSEMENTS[^\n]*",
        "",
        bl,
        flags=re.I,
    )
    # Fix any legacy duplicate place words or markdown leak in BL text
    bl = bl.replace("MSC Ha Ha Noi Noi", "MSC Ha Noi")
    bl = re.sub(
        r"Nơi phát hành = `\*\*Date Laden on Board / On Board Date\*\*`",
        "Nơi phát hành = `MSC Ha Noi`",
        bl,
    )
    # Strip form clause boilerplate in 5. Place of Receipt, 6. Ocean Vessel, and 9. Place of Delivery
    bl = re.sub(
        r"(- \*\*5\. Place of Receipt\*\*:\s*)\(Combined Transport ONLY[^)]*\)\s*\|\s*",
        r"\1",
        bl,
        flags=re.I,
    )
    bl = re.sub(
        r"(- \*\*6\. Ocean Vessel / Voy\. No\.\*\*:\s*)(?:AND VOYAGE NO\s*)?\(see Clause[^)]*\)\s*\|\s*",
        r"\1",
        bl,
        flags=re.I,
    )
    bl = re.sub(
        r"(- \*\*9\. Place of Delivery\*\*:\s*)\(Combined Transport ONLY[^)]*\)\s*$",
        r"\1N/A (Trống / Không có dữ liệu trên BL)",
        bl,
        flags=re.M | re.I,
    )
    bl = re.sub(
        r"(BOOKING REF\.[^\n]*\|\s*PORT OF DISCHARGE\s*\|\s*PLACE OF DELIVERY[^\n]*\n\s*[A-Z0-9]+\s*\|\s*[A-Z\s]+?)\s*$",
        r"\1 | [PLACE OF DELIVERY: TRỐNG / N/A]",
        bl,
        flags=re.M | re.I,
    )
    return cl.strip(), bl.strip()

def extract_consignee_comparison(
    checklist_text: str,
    bl_text: str,
) -> dict[str, Any]:
    """Compare complete Consignee blocks across electronic Form Boxes and raw/OCR layouts."""

    # Leading parenthetical form captions on the Consignee header bar across carriers
    # (e.g. '(not to order)', '(if "Order" state Notify Party)', '(Complete name and address)', etc.)
    _HEADER_CAPTION_PREFIX_RE = re.compile(
        r"^(?:\s*\(\s*(?![^)]*\b(?:LTD|CORP|INC|LLC|CO|GMBH|PLC|S\.A|B\.V)\b)[^)]*\)\s*[|,;:\-]*\s*)+",
        re.IGNORECASE,
    )

    def clean_value(value: str, is_raw_multiline: bool = False) -> str:
        value = unicodedata.normalize("NFKC", value or "")
        if is_raw_multiline:
            lines_out: list[str] = []
            for raw_ln in value.splitlines():
                left_col = raw_ln.split("|", 1)[0].strip()
                if not left_col:
                    continue
                compact_ln = re.sub(r"[^A-Z0-9]", "", left_col.upper())
                if compact_ln.startswith((
                    "CARRIER",
                    "SEAWAYBILL",
                    "BILLOFLADING",
                    "COMBINEDTRANSPORT",
                    "RECEIVEDTHEGOODS",
                    "THESHIPPERSHALL",
                    "BOOKINGNO",
                    "EXPORTREFERENCES",
                    "FORWARDINGAGENT",
                )):
                    continue
                lines_out.append(left_col)
            value = " ".join(lines_out)
        else:
            value = value.replace("|", " ")

        # Remove SAP CODE metadata and leading form-header parenthetical instructions
        value = re.sub(r"(?:\(\s*\d+\s*\)\s*)?\bSAP\s*CODE\b\s*:?\s*[A-Z0-9_\-]*", " ", value, flags=re.I)
        value = _HEADER_CAPTION_PREFIX_RE.sub("", value.strip())
        return " ".join(value.split()).strip()

    def normalize(value: str) -> str:
        # Ignore formatting only; preserve all letters and digits.
        return re.sub(r"[^A-Z0-9]", "", clean_value(value).upper())

    # 1. Checklist: read the block below CONSIGNEE (or fallback to FORM BOXES).
    cl_match = re.search(
        r"^[ \t]*CONSIGNEE\b[^\n]*\n"
        r"(.*?)"
        r"(?=^[ \t]*(?:"
        r"NOTIFY\s*PARTY\b"
        r"|DELIVERY\s*TO\b"
        r"|INVOICE\s*NO\b"
        r"|ORDER\s*NO\b"
        r"|STUFFING\s*PLAN\b"
        r"|TTL\b"
        r"|===|###"
        r")|\Z)",
        checklist_text or "",
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    cl_value = clean_value(cl_match.group(1), is_raw_multiline=True) if cl_match else ""
    if not cl_value:
        cl_box_match = re.search(
            r"^[ \t]*-[ \t]*\*\*2\.[ \t]*Consignee\*\*:[ \t]*([^\r\n]*)",
            checklist_text or "",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if cl_box_match:
            cl_value = clean_value(cl_box_match.group(1))

    # 2. BL: Tier 1 — structured FORM BOXES; Tier 2 — raw/OCR layout block below Consignee.
    bl_match = re.search(
        r"^[ \t]*-[ \t]*\*\*2\.[ \t]*Consignee\*\*:"
        r"[ \t]*([^\r\n]*)",
        bl_text or "",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    bl_value = clean_value(bl_match.group(1)) if bl_match else ""
    if not bl_value:
        bl_raw_match = re.search(
            r"^[ \t]*(?:2\.?[ \t]*)?CONSIGNEE\b[^\n]*\n"
            r"(.*?)"
            r"(?=^[ \t]*(?:"
            r"(?:3\.?[ \t]*)?NOTIFY[ \t]*PART(?:Y|IES)\b"
            r"|FIRST[ \t]*NOTIFY\b"
            r"|ALSO[ \t]*NOTIFY\b"
            r"|DELIVERY[ \t]*TO\b"
            r"|(?:4\.?[ \t]*)?PRE[ \t-]*CARRIAGE\b"
            r"|(?:5\.?[ \t]*)?PLACE[ \t]*OF[ \t]*RECEIPT\b"
            r"|(?:6\.?[ \t]*)?(?:OCEAN[ \t]*)?VESSEL\b"
            r"|(?:7\.?[ \t]*)?PORT[ \t]*OF[ \t]*LOADING\b"
            r"|===|###"
            r")|\Z)",
            bl_text or "",
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if bl_raw_match:
            bl_value = clean_value(bl_raw_match.group(1), is_raw_multiline=True)

    if not cl_value or not bl_value:
        status = "KHÔNG HIỂU"
        detail = (
            "Chưa trích được đầy đủ block CONSIGNEE để đối chiếu. "
            "Không được suy ra tài liệu gốc thiếu thông tin."
        )
    elif normalize(cl_value) == normalize(bl_value):
        status = "ĐÚNG"
        detail = (
            "Toàn bộ CONSIGNEE khớp sau chuẩn hóa "
            "khoảng trắng, xuống dòng và dấu câu."
        )
    else:
        status = "SAI"
        detail = (
            "Hai block CONSIGNEE khác nhau sau chuẩn hóa. "
            "Cần kiểm tra các giá trị hiển thị; "
            "không dùng PLACE OF DELIVERY để đánh giá."
        )

    result = {
        "cl_consignee": cl_value,
        "bl_consignee": bl_value,
        "status": status,
        "is_ok": status == "ĐÚNG",
        "detail": detail,
    }

    logger.info("[DEBUG CONSIGNEE RESULT] %r", result)
    return result

def extract_shipper_comparison(checklist_text: str, bl_text: str) -> dict[str, Any] | None:
    """Dynamically extract and reconcile Shipper information between BL Check List and BL."""
    cl_shipper = ""
    # 1. BL Check List: Prioritize invoice-level 'SHIPPER: BIVN' in raw text (skip 'SHIPPER: BIL' top header
    #    and never take 'INTER COMPANY AMOUNT' / 'TOTAL AMOUNT' / 'INCOTERMS').
    for m in re.finditer(r"\bSHIPPER\s*:\s*([^\n|]+)", checklist_text, re.I):
        val = m.group(1).strip()
        val = re.split(r"\b(?:INTER\s+COMPANY\s+AMOUNT|TOTAL\s+AMOUNT|INCOTERMS|REQUEST\s+NO)\b", val, maxsplit=1, flags=re.I)[0].strip()
        if val and not re.match(r"^BIL\b", val, re.I):
            cl_shipper = val
            break

    if not cl_shipper:
        m_cl_box = re.search(r"\*\*1\.\s*Shipper\*\*:\s*([^\n]+)", checklist_text, re.I)
        if m_cl_box:
            parts = [
                p.strip() for p in m_cl_box.group(1).split("|")
                if p.strip() and not re.search(r"\b(?:INTER\s+COMPANY\s+AMOUNT|TOTAL\s+AMOUNT|INCOTERMS)\b", p, re.I)
            ]
            if parts:
                cl_shipper = parts[0]

    if cl_shipper.upper().startswith("BIVN"):
        cl_disp = "BIVN (BROTHER INDUSTRIES (VIETNAM) LTD.)"
    else:
        cl_disp = cl_shipper

    # 2. BL: Look for Shipper form box or text block
    bl_shipper = ""
    m_bl_box = re.search(r"\*\*1\.\s*Shipper\*\*:\s*([^\n|]+(?:\s*\|\s*[^\n|]+)*)", bl_text, re.I)
    if m_bl_box:
        bl_shipper = m_bl_box.group(1).strip()
    else:
        m_bl_sec = re.search(
            r"(?:^|\n)\s*(?:1\.?\s*)?Shipper\b[^\n]*\n\s*([^\n|]+(?:\n\s*[^\n|]+){0,2})",
            bl_text,
            re.I,
        )
        if m_bl_sec:
            bl_shipper = " | ".join(l.strip() for l in m_bl_sec.group(1).splitlines() if l.strip())

    if not cl_shipper and not bl_shipper:
        return None

    # 3. Objective comparison
    is_bivn_cl = "BIVN" in cl_disp.upper() or "BROTHER INDUSTRIES (VIETNAM)" in cl_disp.upper()
    is_bivn_bl = "BROTHER INDUSTRIES (VIETNAM)" in bl_shipper.upper() or "BIVN" in bl_shipper.upper()

    if is_bivn_cl and is_bivn_bl:
        is_ok = True
    elif cl_disp and bl_shipper:
        clean_cl = re.sub(r"[^A-Z0-9]", "", cl_disp.upper())
        clean_bl = re.sub(r"[^A-Z0-9]", "", bl_shipper.upper())
        is_ok = bool(clean_cl and clean_bl and ((clean_cl in clean_bl) or (clean_bl in clean_cl)))
    else:
        is_ok = False

    cl_final = cl_disp if cl_disp else "Không tìm thấy trên Check List"
    bl_final = bl_shipper if bl_shipper else "Không tìm thấy trên BL"

    if not cl_shipper or not bl_shipper:
        verdict = "**KHÔNG TÌM THẤY** (Bảng 2)"
    elif is_ok:
        verdict = "**ĐÚNG** (Bảng 1)"
    else:
        verdict = "**SAI** (Bảng 2)"

    return {
        "cl_shipper": cl_final,
        "bl_shipper": bl_final,
        "is_ok": is_ok,
        "verdict": verdict,
    }


def extract_delivery_to_comparison(
    checklist_text: str,
    bl_text: str,
    equivalent_bl_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Compare Checklist DELIVERY TO with an explicitly allowed BL field."""

    def normalize(value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "")
        return re.sub(r"[^A-Z0-9]", "", value.upper())

    def clean_value(value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "")
        value = re.sub(r"(?:\(\s*\d+\s*\)\s*)?\bSAP\s*CODE\b\s*:?\s*[A-Z0-9_\-]*", " ", value, flags=re.I)
        return " ".join(value.replace("|", " ").split()).strip(" .;")

    def has_value(value: str) -> bool:
        compact = normalize(value)
        return (
            compact not in {
                "", "NA", "NONE", "NOTAPPLICABLE",
                "KHONGKHAIBAO", "KHONGCO",
            }
            and re.fullmatch(r"X+", compact) is None
        )

    def consolidate_candidates(candidates: list[str]) -> list[str]:
        # 1. Deduplicate by normalized alphanumeric string (keep longest formatted string)
        by_norm: dict[str, str] = {}
        for c in candidates:
            nc = normalize(c)
            if not nc:
                continue
            if nc not in by_norm or len(c) > len(by_norm[nc]):
                by_norm[nc] = c

        norms = list(by_norm.keys())
        # 2. Subsumption: if candidate A is a prefix/substring of candidate B (e.g. the 1-line
        #    'DELIVERY TO: BROTHER INTERNATIONAL CORP.' summary under TO/ETA vs the full 7-line
        #    'DELIVERY TO SAP CODE:...' party block, or a height-truncated Form Box vs full block),
        #    keep the complete block B.
        kept_norms = [
            na for na in norms
            if not any(na != nb and len(na) < len(nb) and na in nb for nb in norms)
        ]
        return [by_norm[na] for na in kept_norms]

    def extract_values(text: str, label: str) -> list[str]:
        text = unicodedata.normalize("NFKC", text or "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        values: list[str] = []

        label_re = r"[ \t]+".join(
            re.escape(part) for part in label.split()
        )

        # Structured FORM BOXES: keep the value on the same line.
        form_pattern = re.compile(
            rf"^[ \t]*-[ \t]*\*\*"
            rf"(?:\d+\.[ \t]*)?{label_re}"
            rf"\*\*[ \t]*:[ \t]*([^\n]*)$",
            re.IGNORECASE | re.MULTILINE,
        )

        for match in form_pattern.finditer(text):
            value = clean_value(match.group(1))
            if has_value(value) and value not in values:
                values.append(value)

        # Raw text: support a value on the label line or below it.
        raw_pattern = re.compile(
            rf"^[ \t]*{label_re}\b[ \t]*:?[ \t]*(.*)$",
            re.IGNORECASE,
        )

        stop_pattern = re.compile(
            r"^(?:"
            r"CONSIGNEE\b|SHIPPER\b|NOTIFY\b"
            r"|DELIVERY[ \t]+TO\b"
            r"|PLACE[ \t]+OF\b|PORT[ \t]+OF\b"
            r"|INVOICE[ \t]*NO\b|ORDER\b|TTL\b|TOTAL\b|G-TOTAL\b|S-TOTAL\b"
            r"|STUFFING[ \t]*PLAN\b|SAP[ \t]*CODE\b"
            r"|VESSEL\b|OCEAN[ \t]+VESSEL\b"
            r"|AS[ \t]+(?:THE[ \t]+)?CARRIER\b"
            r"|\d[\d,.]*[ \t]+\d[\d,.]*$"
            r"|===|###"
            r")",
            re.IGNORECASE,
        )

        lines = text.splitlines()

        for index, line in enumerate(lines):
            match = raw_pattern.match(line)
            if not match:
                continue

            # Multi-column raw text needs a coordinate-aware extractor.
            # Do not guess which following column belongs to this field.
            if "|" in line:
                continue

            inline = re.split(
                r"\bSAP[ \t]*CODE\b",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()

            block: list[str] = [inline] if inline else []

            for next_line in lines[index + 1:]:
                next_line = next_line.strip()

                if not next_line:
                    continue

                if stop_pattern.match(next_line):
                    break

                if "|" in next_line:
                    break

                block.append(next_line)

            value = clean_value("\n".join(block))
            if has_value(value) and value not in values:
                values.append(value)

        return consolidate_candidates(values)

    # Checklist must always use DELIVERY TO, never the standalone TO field.
    cl_values = extract_values(checklist_text, "DELIVERY TO")

    # Exact field first. Alternatives must be explicitly configured.
    bl_values: list[str] = []
    bl_source = ""

    for label in dict.fromkeys(("DELIVERY TO", *equivalent_bl_fields)):
        candidates = extract_values(bl_text, label)
        if candidates:
            bl_values = candidates
            bl_source = label
            break

    cl_deliv = cl_values[0] if len(cl_values) == 1 else ""
    bl_deliv = bl_values[0] if len(bl_values) == 1 else ""
    
    # Distinguish "label absent on BL" from "label present but unread".
    bl_label_present = re.search(
        r"\bDELIVERY[ \t]+TO\b",
        unicodedata.normalize("NFKC", bl_text or ""),
        flags=re.IGNORECASE,
    ) is not None

    if len(cl_values) > 1 or len(bl_values) > 1:
        status = "KHÔNG HIỂU"
        detail = (
            "Có nhiều giá trị DELIVERY TO hoặc trường tương ứng; "
            "chưa đủ cơ sở chọn một giá trị."
        )
    elif cl_deliv and not bl_deliv and not bl_label_present:
        status = "KHÔNG TÌM THẤY"
        detail = "BL không có ô DELIVERY TO."
    elif not cl_deliv or not bl_deliv:
        status = "KHÔNG HIỂU"
        detail = "Chưa trích được đủ hai giá trị để đối chiếu."
    elif normalize(cl_deliv) == normalize(bl_deliv):
        status = "ĐÚNG"
        detail = (
            f"DELIVERY TO trên Checklist khớp trường "
            f"{bl_source} trên BL sau chuẩn hóa định dạng."
        )
    else:
        status = "SAI"
        detail = (
            f"DELIVERY TO trên Checklist khác trường "
            f"{bl_source} trên BL sau chuẩn hóa định dạng."
        )

    result = {
        "cl_deliv": cl_deliv or "Chưa xác định",
        "bl_deliv": bl_deliv or "Chưa xác định",
        "bl_source": bl_source or "Chưa xác định",
        "cl_candidates": cl_values,
        "bl_candidates": bl_values,
        "status": status,
        "is_ok": status == "ĐÚNG",
        "verdict": (
            f"**{status}** "
            f"({'Bảng 1' if status == 'ĐÚNG' else 'Bảng 2'})"
        ),
        "detail": detail,
    }

    logger.info("[DEBUG DELIVERY TO RESULT] %r", result)
    return result
    
# ---------------------------------------------------------------------------
# Deterministic Notify Party extraction and comparison
# ---------------------------------------------------------------------------

_EMPTY_PARTY_MARKERS = {
    "",
    "SAPCODE",
    "NA",
    "NONE",
    "NOTAPPLICABLE",
    "KHONGCO",
    "KHONGKHAIBAO",
}


def _compact_party_text(value: str) -> str:
    """Normalize party text for reliable comparison."""
    normalized = unicodedata.normalize(
        "NFKC",
        value or "",
    ).upper()

    return re.sub(
        r"[^A-Z0-9@]+",
        "",
        normalized,
    )


def _is_empty_party_value(value: str) -> bool:
    """Return True when a party field contains no business value."""
    compact = _compact_party_text(value)

    if compact in _EMPTY_PARTY_MARKERS:
        return True

    # Chỉ có nhãn SAP CODE, không có tên công ty hoặc địa chỉ.
    if compact.startswith("SAPCODE") and len(compact) <= 15:
        return True

    return False


def _clean_party_block(value: str) -> str:
    """
    Remove legal text from the right-hand column while preserving
    the company/address block from the left-hand column.
    """
    cleaned_lines: list[str] = []

    stop_patterns = (
        "FORWARDINGAGENTREFERENCE",
        "EXPORTIMPORTREFERENCE",
        "DOCUMENTOFTITLE",
        "TERMSANDCONDITIONS",
        "OCEANFREIGHTFORWARDING",
        "WEBSITEATHTTPS",
        "VESSELANDVOYAGE",
        "PLACEANDDATEOFISSUE",
        "PLACEOFDELIVERY",
        "PORTOFLOADING",
        "PORTOFDISCHARGE",
    )

    for raw_line in (value or "").splitlines():
        # OCR có thể trộn cột trái và cột phải bằng dấu "|".
        left_column = raw_line.split("|", 1)[0].strip()

        if not left_column:
            continue

        compact_line = _compact_party_text(left_column)

        if any(
            pattern in compact_line
            for pattern in stop_patterns
        ):
            break

        cleaned_lines.append(left_column)

    return "\n".join(cleaned_lines).strip()


def _extract_checklist_notify_parties(
    text: str,
) -> dict[int, str]:
    """
    Extract NOTIFY PARTY (1) and (2) from a BL Check List.

    A block ends at the next party or another known checklist field.
    """
    parties: dict[int, str] = {}

    pattern = re.compile(
        r"NOTIFY\s*PARTY\s*\(\s*([12])\s*\)"
        r"[^\n]*"
        r"\n"
        r"(.*?)"
        r"(?="
        r"NOTIFY\s*PARTY\s*\(\s*[12]\s*\)"
        r"|DELIVERY\s*TO"
        r"|CONSIGNEE"
        r"|INVOICE\s*NO"
        r"|ORDER\s*NO"
        r"|TTL\b"
        r"|STUFFING\s*PLAN"
        r"|$"
        r")",
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(text or ""):
        number = int(match.group(1))
        block = _clean_party_block(
            match.group(2)
        )

        parties[number] = block

    # Một số parser giữ "SAP CODE" trên dòng riêng nhưng không có value.
    for number in (1, 2):
        parties.setdefault(number, "")

    return parties


def _extract_bl_notify_parties(
    text: str,
) -> dict[int, str]:
    """
    Extract Notify Party blocks from a BL/Sea Waybill.

    Unnumbered Notify Party is party 1.
    Also Notify / Second Notify is party 2.
    OCR forms such as NotifyParty are supported.
    """
    parties: dict[int, str] = {}

    bl_text = unicodedata.normalize("NFKC", text or "")
    bl_text = bl_text.replace("\r\n", "\n").replace("\r", "\n")
    
    # Exclude generated FORM BOXES rows from this raw-layout extractor.
    # Do not modify the original text used by other extractors.
    raw_lines = [
        line
        for line in bl_text.splitlines()
        if not re.match(r"^[ \t]*-[ \t]*\*\*", line)
    ]
    bl_text = "\n".join(raw_lines)

    # Remove the complete parenthesized disclaimer, including line breaks.
    bl_text = re.sub(
        r"\(\s*No\s+responsibility\b[^)]*\)",
        "",
        bl_text,
        flags=re.IGNORECASE,
    )
    # Tìm Notify Party 2 trước để tránh Party 1 nuốt toàn bộ phần sau.
    party2_pattern = re.compile(
        r"^[ \t.*-]*(?:"
        r"ALSO[ \t]*NOTIFY(?:[ \t]*PARTY)?"
        r"|SECOND[ \t]*NOTIFY(?:[ \t]*PARTY)?"
        r"|NOTIFY[ \t]*PARTY[ \t]*(?:2\b|\([ \t]*2[ \t]*\))"
        r"|CONTINUED[ \t]*FROM[ \t]*NOTIFY[ \t]*PART(?:Y|IES)"
        r")"
        r"[^\n]*\n"
        r"(.*?)"
        r"(?="
        r"^[ \t]*(?:"
        r"EMPLOYEE[ \t]+NUMBER\b"
        r"|LLOYD[S']*[ \t]*/?[ \t]*IMO\b"
        r"|[\"']?ANY[ \t]+DISPUTE\b"
        r"|PORT[ \t]*OF[ \t]*DISCHARGE\b"
        r"|FORWARDING[ \t]*AGENT\b"
        r"|EXPORT[ \t]*/?[ \t]*IMPORT[ \t]*REFERENCE\b"
        r"|DELIVERY[ \t]*TO\b"
        r"|VESSEL\b"
        r"|PORT[ \t]*OF[ \t]*LOADING\b"
        r")"
        r"|\Z"
        r")",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )

    party2_match = party2_pattern.search(bl_text)
    
        # Diagnostic only: inspect the actual continuation text.
    source_match = re.search(
        r"NOTIFY|WM\s*STONE",
        bl_text,
        flags=re.IGNORECASE,
    )

    if source_match is None:
        logger.info(
            "[DEBUG NOTIFY 2 INPUT] NO_NOTIFY_OR_WM_STONE"
        )


    if party2_match:
        raw_party2 = party2_match.group(1)
        parties[2] = _clean_party_block(raw_party2)

        logger.info(
            "[DEBUG NOTIFY 2 MATCH] header=%r raw=%r cleaned=%r",
            bl_text[party2_match.start():party2_match.start(1)],
            raw_party2,
            parties[2],
        )
    else:
        logger.info("[DEBUG NOTIFY 2 MATCH] NOT_FOUND")
        
        # Fallback: continuation header and values in the right column.
    if not parties.get(2):
        collecting = False
        notify2_lines: list[str] = []

        for raw_line in bl_text.splitlines():
            left, separator, right = raw_line.partition("|")

            if not collecting:
                if separator and re.search(
                    r"\bCONTINUED[ \t]+FROM[ \t]+"
                    r"NOTIFY[ \t]*PART(?:Y|IES)\b",
                    right,
                    flags=re.IGNORECASE,
                ):
                    collecting = True

                continue

            # Read the right column when present.
            # Otherwise preserve standalone address/contact lines.
            value = (right if separator else left).strip()

            if not value:
                continue

            # Stop at the next field or the start of legal terms.
            if re.match(
                r"^(?:"
                r"LLOYD[S'’]*[ \t]*/?[ \t]*IMO\b"
                r"|EMPLOYEE[ \t]+NUMBER\b"
                r"|[\"“']?ANY[ \t]+DISPUTE\b"
                r"|PORT[ \t]+OF[ \t]+DISCHARGE\b"
                r"|NOTIFY[ \t]*PART(?:Y|IES)\b"
                r"|===|###"
                r")",
                value,
                flags=re.IGNORECASE,
            ):
                break

            notify2_lines.append(value)

        if notify2_lines:
            raw_party2 = "\n".join(notify2_lines)
            parties[2] = _clean_party_block(raw_party2)

            logger.info(
                "[DEBUG NOTIFY 2 RIGHT COLUMN] raw=%r cleaned=%r",
                raw_party2,
                parties[2],
            )
 
    party1_pattern = re.compile(
        r"^[ \t]*(?:"
        r"FIRST[ \t]*NOTIFY(?:[ \t]*PARTY)?"
        r"|NOTIFY[ \t]*PART(?:Y|IES)"
        r"(?:[ \t]*\([ \t]*1[ \t]*\))?"
        r")"
        r"(?![ \t]*(?:2\b|\([ \t]*2[ \t]*\)))"
        r"[^\n]*\n"
        r"(.*?)"
        r"(?="
        r"^[ \t.*-]*(?:"
        r"ALSO[ \t]*NOTIFY"
        r"|SECOND[ \t]*NOTIFY"
        r"|NOTIFY[ \t]*PARTY[ \t]*(?:2\b|\([ \t]*2[ \t]*\))"
        r"|CONTINUED[ \t]*FROM[ \t]*NOTIFY[ \t]*PART(?:Y|IES)"
        r"|POINT[ \t]*AND[ \t]*COUNTRY[ \t]*OF[ \t]*ORIGIN"
        r"|PRE[ \t-]*CARRIAGE[ \t]*BY"
        r"|OCEAN[ \t]*VESSEL"
        r"|VESSEL"
        r"|PORT[ \t]*OF[ \t]*LOADING"
        r"|DELIVERY[ \t]*TO"
        r")"
        r"|\Z"
        r")",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )

    party1_match = party1_pattern.search(bl_text)

    if party1_match:
        raw_block = party1_match.group(1)
        filtered_lines: list[str] = []
        
        for line in raw_block.splitlines():
            # Chi giu cot trai, bo noi dung cot ben phai.
            left_column = line.split("|", 1)[0].strip()

            if not left_column:
                continue

            normalized = re.sub(
                r"[^a-z0-9]",
                "",
                left_column.lower(),
            )

            # Bo dong nhieu, khong dung vong lap.
            if normalized.startswith((
                "freightforwardingtermsandconditions",
                "websiteathttps",
                "https",
                "http",
                "forwardingagent",
                "exportimportreference",
                "exportlmportreference",
            )):
                continue

            filtered_lines.append(left_column)

        block_for_clean = "\n".join(filtered_lines)
        parties[1] = _clean_party_block(block_for_clean)

    parties.setdefault(1, "")
    parties.setdefault(2, "")

    return parties


def _extract_party_identity_tokens(
    value: str,
) -> set[str]:
    """
    Extract stable identifiers:
    company name, email, postcode, VAT, EORI, EIN and TRN.
    """
    normalized = unicodedata.normalize(
        "NFKC",
        value or "",
    ).upper()

    tokens: set[str] = set()

    email_matches = re.findall(
        r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
        normalized,
    )

    for email in email_matches:
        tokens.add(
            _compact_party_text(email)
        )

    registration_matches = re.findall(
        r"\b(?:VAT|EORI|EIN|TRN)"
        r"\s*[+:]?\s*[A-Z0-9 ]{5,25}",
        normalized,
    )

    for registration in registration_matches:
        tokens.add(
            _compact_party_text(registration)
        )

    postcode_matches = re.findall(
        r"\b[A-Z]{1,2}\d{1,2}[A-Z]?"
        r"\s*\d[A-Z]{2}\b"
        r"|\b\d{5}(?:-\d{4})?\b",
        normalized,
    )

    for postcode in postcode_matches:
        tokens.add(
            _compact_party_text(postcode)
        )

    # Tên pháp nhân thường nằm ở dòng đầu tiên.
    meaningful_lines = [
        line.strip()
        for line in (value or "").splitlines()
        if line.strip()
    ]

    for line in meaningful_lines[:2]:
        compact_line = _compact_party_text(
            line
        )

        if len(compact_line) >= 8:
            tokens.add(compact_line)

    return {
        token
        for token in tokens
        if token
    }


def _compare_party_values(
    checklist_value: str,
    bl_value: str,
) -> tuple[bool, list[str]]:
    """Compare two party blocks using stable identifiers."""
    checklist_compact = _compact_party_text(
        checklist_value
    )
    bl_compact = _compact_party_text(
        bl_value
    )

    # Toàn bộ một bên nằm trong bên còn lại sau chuẩn hóa.
    direct_match = bool(
        checklist_compact
        and bl_compact
        and (
            checklist_compact in bl_compact
            or bl_compact in checklist_compact
        )
    )

    checklist_tokens = (
        _extract_party_identity_tokens(
            checklist_value
        )
    )
    bl_tokens = _extract_party_identity_tokens(
        bl_value
    )

    matched_tokens: list[str] = []

    for checklist_token in checklist_tokens:
        if checklist_token in bl_compact:
            matched_tokens.append(
                checklist_token
            )
            continue

        for bl_token in bl_tokens:
            if (
                checklist_token in bl_token
                or bl_token in checklist_token
            ):
                matched_tokens.append(
                    checklist_token
                )
                break

    # Không chỉ dựa vào một token đơn lẻ.
    identity_match = len(set(matched_tokens)) >= 2

    return (
        direct_match or identity_match,
        sorted(set(matched_tokens)),
    )


def extract_notify_party_comparison(
    checklist_text: str,
    bl_text: str,
) -> list[dict[str, Any]]:
    """
    Deterministically compare Notify Party 1 and 2.

    The model is not involved in deciding the status.
    """
    checklist_parties = (
        _extract_checklist_notify_parties(
            checklist_text
        )
    )

    bl_parties = _extract_bl_notify_parties(
        bl_text
    )
    
        # Debug text dau vao, khong thay doi logic trich xuat.
    logger.info(
        "[DEBUG NOTIFY EXTRACTED] checklist=%r bl=%r",
        checklist_parties,
        bl_parties,
    )
    
    comparison_results: list[
        dict[str, Any]
    ] = []

    for number in (1, 2):
        checklist_value = (
            checklist_parties.get(number, "")
        )
        bl_value = bl_parties.get(number, "")

        checklist_has_value = (
            not _is_empty_party_value(
                checklist_value
            )
        )
        bl_has_value = (
            not _is_empty_party_value(
                bl_value
            )
        )

        if (
            not checklist_has_value
            and not bl_has_value
        ):
            status = "ĐÚNG"
            detail = (
                "Hai tài liệu đều không khai báo "
                f"Notify Party {number}."
            )
            matched_tokens: list[str] = []

        elif (
            checklist_has_value
            and not bl_has_value
        ):
            status = "KHÔNG TÌM THẤY"
            detail = (
                f"Check List có Notify Party {number}, "
                "nhưng không trích được dữ liệu tương ứng trên BL."
            )
            matched_tokens = []

        elif (
            not checklist_has_value
            and bl_has_value
        ):
            status = "SAI"
            detail = (
                f"Check List không khai báo Notify Party {number}, "
                "nhưng BL có Notify Party tương ứng."
            )
            matched_tokens = []

        else:
            is_match, matched_tokens = (
                _compare_party_values(
                    checklist_value,
                    bl_value,
                )
            )

            if is_match:
                status = "ĐÚNG"
                detail = (
                    "Khớp theo tên pháp nhân và "
                    "các dấu hiệu định danh đã chuẩn hóa."
                )
            else:
                status = "SAI"
                detail = (
                    "Có dữ liệu ở cả hai tài liệu nhưng "
                    "không đủ dấu hiệu để xác nhận cùng một party."
                )

        comparison_results.append({
            "number": number,
            "checklist_value": (
                checklist_value
                if checklist_has_value
                else "Không khai báo"
            ),
            "bl_value": (
                bl_value
                if bl_has_value
                else "Không khai báo"
            ),
            "status": status,
            "is_ok": status == "ĐÚNG",
            "matched_tokens": matched_tokens,
            "detail": detail,
        })

    return comparison_results

def extract_port_of_loading_comparison(
    checklist_text: str,
    bl_text: str,
) -> dict[str, str]:
    """Compare the Checklist FROM field with BL PORT OF LOADING."""

    def prepare(text: str) -> str:
        return (
            unicodedata.normalize("NFKC", text or "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    def clean(value: str) -> str:
        return " ".join(value.split()).strip(" :;")

    def normalize(value: str) -> str:
        return re.sub(r"[\W_]+", "", clean(value).upper())

    def is_usable(value: str) -> bool:
        compact = normalize(value)

        return (
            compact not in {
                "",
                "NA",
                "NONE",
                "NULL",
                "NOTAPPLICABLE",
                "CHUAXACDINH",
            }
            and re.fullmatch(r"X+", compact) is None
        )

    def unique_values(values: list[str]) -> list[str]:
        result: dict[str, str] = {}

        for value in values:
            value = clean(value)

            if is_usable(value):
                result.setdefault(normalize(value), value)

        return list(result.values())

    def split_cells(line: str) -> list[str]:
        # Preserve empty pipe-delimited cells to avoid shifting columns.
        if "|" in line:
            return [cell.strip() for cell in line.split("|")]

        return [
            cell.strip()
            for cell in re.split(r"[ \t]{2,}", line.strip())
        ]
    
    def cell_starts(line: str) -> list[int]:
        # Start offset of each cell; used to check column alignment.
        if "|" in line:
            return []

        return [
            match.start()
            for match in re.finditer(r"\S+(?:[ \t]\S+)*", line)
        ]

    field_start = re.compile(
        r"^(?:"
        r"FROM\b|TO\b|VIA\b|ETD\b|ETA\b"
        r"|PORT[ \t]+OF\b|PLACE[ \t]+OF\b"
        r"|DELIVERY[ \t]+TO\b"
        r"|CONSIGNEE\b|SHIPPER\b|NOTIFY\b"
        r"|VESSEL\b|OCEAN[ \t]+VESSEL\b"
        r"|INVOICE\b|ORDER\b|SAP[ \t]+CODE\b"
        r"|===|###"
        r")",
        flags=re.IGNORECASE,
    )

    route_pattern = re.compile(
        r"\bFROM\b[^\n]*\bTO\b",
        flags=re.IGNORECASE,
    )

    def valid_candidate(value: str) -> bool:
        value = clean(value)

        return (
            is_usable(value)
            and not field_start.match(value)
            and not route_pattern.search(value)
        )

    def extract_values(text: str, label: str) -> list[str]:
        text = prepare(text)

        label_re = r"[ \t]+".join(
            re.escape(word) for word in label.split()
        )

        # Structured field:
        # - **7. Port of Loading**: Haiphong
        # - **FROM**: BAC NINH
        form_pattern = re.compile(
            rf"^[ \t]*-[ \t]*\*\*"
            rf"(?:\d+\.[ \t]*)?{label_re}"
            rf"[ \t]*\*\*[ \t]*:[ \t]*([^\n]*)$",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        form_values: list[str] = []

        for match in form_pattern.finditer(text):
            candidate = clean(match.group(1))

            # Reject values containing merged columns or route sentences.
            if "|" not in candidate and valid_candidate(candidate):
                form_values.append(candidate)

        form_values = unique_values(form_values)

        if form_values:
            return form_values

        # Raw layout: accept only an exact label cell.
        # "FROM HAI PHONG TO LONG BEACH" is not an exact FROM label.
        exact_label = re.compile(
            rf"{label_re}[ \t]*:?",
            flags=re.IGNORECASE,
        )

        lines = text.splitlines()
        raw_values: list[str] = []

        for index, line in enumerate(lines):
            header_cells = split_cells(line)

            target_columns = [
                column
                for column, cell in enumerate(header_cells)
                if exact_label.fullmatch(cell)
            ]

            if not target_columns:
                continue

            header_starts = cell_starts(line)
            matched_row = None

            # Look a few lines below: interleaved text from another
            # column (e.g. "REQUEST") may sit between label and value.
            for next_row in lines[index + 1:index + 4]:
                if not next_row.strip():
                    continue

                value_cells = split_cells(next_row)

                # Stop at the next label row, e.g. "VIA   ETA".
                if any(field_start.match(cell) for cell in value_cells):
                    break

                if len(value_cells) != len(header_cells):
                    continue

                # Require every cell to start near its header cell.
                value_starts = cell_starts(next_row)
                if all(
                    abs(h - v) <= 3
                    for h, v in zip(header_starts, value_starts)
                ):
                    matched_row = value_cells
                    break

            if matched_row is None:
                logger.info(
                    "[DEBUG POL COLUMN MISMATCH] label=%r header=%r",
                    label,
                    line,
                )
                continue

            for column in target_columns:
                candidate = clean(matched_row[column])

                if valid_candidate(candidate):
                    raw_values.append(candidate)

            # if not next_row:
            #     continue

            # value_cells = split_cells(next_row)

            # # Without coordinates, do not guess a shifted column.
            # if len(header_cells) != len(value_cells):
            #     logger.info(
            #         "[DEBUG POL COLUMN MISMATCH] "
            #         "label=%r header=%r next_row=%r",
            #         label,
            #         line,
            #         next_row,
            #     )
            #     continue

            # for column in target_columns:
            #     candidate = clean(value_cells[column])

            #     if valid_candidate(candidate):
            #         raw_values.append(candidate)

        return unique_values(raw_values)

    checklist_source = prepare(checklist_text)

    for index, match in enumerate(
        re.finditer(
            r"\bFROM\b",
            checklist_source,
            flags=re.IGNORECASE,
        ),
        start=1,
    ):
        logger.info(
            "[DEBUG CHECKLIST FROM SOURCE %d] %r",
            index,
            checklist_source[
                max(0, match.start() - 100):
                min(len(checklist_source), match.end() + 350)
            ],
        )
    checklist_values = extract_values(checklist_text, "FROM")
    bl_values = extract_values(bl_text, "PORT OF LOADING")

    checklist_value = (
        checklist_values[0]
        if len(checklist_values) == 1
        else ""
    )
    bl_value = (
        bl_values[0]
        if len(bl_values) == 1
        else ""
    )

    if len(checklist_values) > 1 or len(bl_values) > 1:
        status = "KHÔNG HIỂU"
        detail = (
            "Có nhiều giá trị khác nhau cho ô FROM trên Checklist "
            "hoặc PORT OF LOADING trên BL; "
            "chưa đủ cơ sở chọn giá trị đối chiếu."
        )

    elif not checklist_value or not bl_value:
        status = "KHÔNG HIỂU"
        detail = (
            "Chưa trích được đủ giá trị ô FROM trên Checklist "
            "và PORT OF LOADING trên BL. "
            "Không dùng dòng FROM ... TO ... trong REQUEST "
            "để thay thế ô FROM."
        )

    elif normalize(checklist_value) == normalize(bl_value):
        status = "ĐÚNG"
        detail = (
            "Ô FROM trên Checklist khớp PORT OF LOADING trên BL "
            "sau chuẩn hóa chữ hoa/thường, khoảng trắng và dấu câu."
        )

    else:
        status = "SAI"
        detail = (
            f"Ô FROM trên Checklist ({checklist_value}) khác "
            f"PORT OF LOADING trên BL ({bl_value})."
        )

    result = {
        "checklist_value": checklist_value or "Chưa xác định",
        "bl_value": bl_value or "Chưa xác định",
        "status": status,
        "detail": detail,
    }

    logger.info(
        "[DEBUG PORT OF LOADING RESULT] "
        "checklist_candidates=%r bl_candidates=%r result=%r",
        checklist_values,
        bl_values,
        result,
    )

    return result

def build_shipping_dates_reference_banner(checklist_text: str, bl_text: str) -> str:
    """Build a compact, high-signal pre-reconciled reference note for the 23-item comparison.

    IMPORTANT: Keep this note concise and in pure bullet-point format (NO Markdown tables `|...|`).
    """
    lines: list[str] = [
        "[GHI CHÚ ĐỐI CHIẾU TỰ ĐỘNG TỪ 2 FILE — DÙNG ĐỂ KIỂM TRA ĐẦY ĐỦ 23 HẠNG MỤC (TỪ 1 ĐẾN 23)]:"
    ]
    shipper_res = extract_shipper_comparison(checklist_text, bl_text)
    if shipper_res:
        lines.append(
            f"- Hạng mục 1 (`SHIPPER`): BL Check List = `{shipper_res['cl_shipper']}` | "
            f"BL = `{shipper_res['bl_shipper']}` => Kết quả: {shipper_res['verdict']}."
        )
        # Add only business-approved equivalent BL field labels here.
    
    delivery_to_equivalent_fields: tuple[str, ...] = ()

    deliv_res = extract_delivery_to_comparison(
        checklist_text,
        bl_text,
        equivalent_bl_fields=delivery_to_equivalent_fields,
    )

    if deliv_res:
        lines.append(
            "- Hạng mục 5 (`DELIVERY TO`): "
            f"BL Check List, nguồn `DELIVERY TO` "
            f"= `{deliv_res['cl_deliv']}`; "
            f"BL, nguồn `{deliv_res['bl_source']}` "
            f"= `{deliv_res['bl_deliv']}`; "
            f"Kết quả: {deliv_res['verdict']}. "
            f"Chi tiết: {deliv_res['detail']}\n"
            "Chỉ dẫn trình bày: Giữ nguyên nguồn và kết quả đối chiếu này. "
            "Không lấy trường TO của Checklist hoặc kết quả hạng mục 16 để thay thế hạng mục 5. "
            "Cột giải thích chỉ ghi nội dung Chi tiết ở trên, không chép các chỉ dẫn trình bày này vào bảng."
        )
        
    notify_results = extract_notify_party_comparison(
        checklist_text,
        bl_text,
    )

    notify_1_res = next(
        (
            item
            for item in notify_results
            if item["number"] == 1
        ),
        None,
    )

    if notify_1_res:
        lines.append(
            f"- Hạng mục 3 (`NOTIFY PARTY 1`): "
            f"BL Check List = `{notify_1_res['checklist_value']}` | "
            f"BL = `{notify_1_res['bl_value']}` "
            f"=> Kết quả: {notify_1_res['status']}. "
            f"Chi tiết: {notify_1_res['detail']}"
        )

    notify_2_res = next(
        (
            item
            for item in notify_results
            if item["number"] == 2
        ),
        None,
    )

    if notify_2_res:
        lines.append(
            f"- Hạng mục 4 (`NOTIFY PARTY 2`): "
            f"BL Check List = `{notify_2_res['checklist_value']}` | "
            f"BL = `{notify_2_res['bl_value']}` "
            f"=> Kết quả: {notify_2_res['status']}. "
            f"Chi tiết: {notify_2_res['detail']}"
        )
        
    consignee_res = extract_consignee_comparison(
        checklist_text,
        bl_text,
    )

    # Keep each value on one line and avoid table separators.
    cl_consignee = consignee_res["cl_consignee"] or "Chưa trích được"
    bl_consignee = consignee_res["bl_consignee"] or "Chưa trích được"
    
    if consignee_res:
        verdict_2 = f"**{consignee_res['status']}** ({'Bảng 1' if consignee_res['is_ok'] else 'Bảng 2'})"
        lines.append(
            "- Hạng mục 2 (`CONSIGNEE`): "
            f"BL Check List = `{cl_consignee}`; "
            f"BL = `{bl_consignee}`; "
            f"Kết quả đối chiếu bằng code: {verdict_2}. "
            f"Chi tiết: {consignee_res['detail']}\n"
            "Chỉ dẫn trình bày: Khi trình bày hạng mục 2, dùng đầy đủ hai giá trị trên, "
            "không thay bằng block CONSIGNEE trong text layout thô. "
            "Cột giải thích chỉ ghi nội dung Chi tiết ở trên, không chép các chỉ dẫn trình bày này vào bảng."
        )
    
    pol_res = extract_port_of_loading_comparison(
        checklist_text,
        bl_text,
    )

    if pol_res:
        lines.append(
            "- Hạng mục 14 (`PORT OF LOADING`): "
            "Checklist, nguồn ô `FROM` = "
            f"`{pol_res['checklist_value']}`; "
            "BL, nguồn `PORT OF LOADING` = "
            f"`{pol_res['bl_value']}`; "
            f"Kết quả: **{pol_res['status']}**. "
            f"Chi tiết: {pol_res['detail']}\n"
            "Chỉ dẫn trình bày: "
            "Xuất một dòng riêng với STT 14; "
            "giữ nguyên giá trị và trạng thái do code trả về. "
            "Không lấy dòng FROM ... TO ... trong REQUEST "
            "thay thế ô FROM trên Checklist. "
            "Số 7 trong FORM BOXES không phải STT đối chiếu. "
            "Cột giải thích chỉ ghi nội dung Chi tiết ở trên, "
            "không chép các chỉ dẫn trình bày này vào bảng."
        )
    
    # 1. Full-document cross-page Container & Totals reconciliation
    cl_conts = extract_document_containers(checklist_text, is_checklist=True)
    bl_conts = extract_document_containers(bl_text, is_checklist=False)

    if cl_conts and bl_conts:
        cl_pkgs_list = ", ".join(str(c["pkgs_num"]) for c in cl_conts)
        bl_pkgs_list = ", ".join(f"{c['pkgs_num']} CARTON(S)" for c in bl_conts)
        cl_tot_pkgs = sum(c["pkgs_num"] for c in cl_conts)
        bl_tot_pkgs = sum(c["pkgs_num"] for c in bl_conts)
        cl_tot_w = sum(c["weight_num"] for c in cl_conts)
        bl_tot_w = sum(c["weight_num"] for c in bl_conts)
        cl_tot_c = sum(c["cbm_num"] for c in cl_conts)
        bl_tot_c = sum(c["cbm_num"] for c in bl_conts)

        bl_by_no = {c["container_no"]: c for c in bl_conts}
        all_pkgs_ok = (
            len(cl_conts) == len(bl_conts)
            and all(bl_by_no.get(c["container_no"], {}).get("pkgs_num") == c["pkgs_num"] for c in cl_conts)
            and cl_tot_pkgs == bl_tot_pkgs
        )
        all_no_ok = len(cl_conts) == len(bl_conts) and all(c["container_no"] in bl_by_no for c in cl_conts)
        all_seal_ok = (
            len(cl_conts) == len(bl_conts)
            and all(bl_by_no.get(c["container_no"], {}).get("seal_no") == c["seal_no"] for c in cl_conts)
        )
        all_type_ok = (
            len(cl_conts) == len(bl_conts)
            and all(bl_by_no.get(c["container_no"], {}).get("type") == c["type"] for c in cl_conts)
        )
        all_w_ok = (
            len(cl_conts) == len(bl_conts)
            and all(abs(bl_by_no.get(c["container_no"], {}).get("weight_num", -1.0) - c["weight_num"]) < 0.01 for c in cl_conts)
        )
        all_c_ok = (
            len(cl_conts) == len(bl_conts)
            and all(abs(bl_by_no.get(c["container_no"], {}).get("cbm_num", -1.0) - c["cbm_num"]) < 0.001 for c in cl_conts)
        )
        cl_cont_nos = ", ".join(c["container_no"] for c in cl_conts)
        bl_cont_nos = ", ".join(c["container_no"] for c in bl_conts)
        cl_seals = ", ".join(c["seal_no"] for c in cl_conts)
        bl_seals = ", ".join(c["seal_no"] for c in bl_conts)
        cl_types_norm_list = sorted({c["type"] for c in cl_conts if c.get("type") and c["type"] != "N/A"})
        cl_types_norm = ", ".join(cl_types_norm_list) if cl_types_norm_list else "N/A"

        bl_types_norm_list = sorted({c["type"] for c in bl_conts if c.get("type") and c["type"] != "N/A"})
        bl_types_norm = ", ".join(bl_types_norm_list) if bl_types_norm_list else "N/A"

        cl_types_raw_list = [c["type_raw"] for c in cl_conts if c.get("type_raw") and c["type_raw"] != "N/A"]
        cl_types_raw = ", ".join(dict.fromkeys(cl_types_raw_list)) if cl_types_raw_list else cl_types_norm

        bl_types_raw_list = [c["type_raw"] for c in bl_conts if c.get("type_raw") and c["type_raw"] != "N/A"]
        bl_types_raw = ", ".join(dict.fromkeys(bl_types_raw_list)) if bl_types_raw_list else bl_types_norm

        cl_disp_8 = f"{cl_types_raw} ({cl_types_norm})" if (cl_types_raw and cl_types_raw != cl_types_norm and cl_types_norm != "N/A") else (cl_types_raw or cl_types_norm)
        bl_disp_8 = f"{bl_types_raw} ({bl_types_norm})" if (bl_types_raw and bl_types_raw != bl_types_norm and bl_types_norm != "N/A") else (bl_types_raw or bl_types_norm)

        cl_w_list = ", ".join(c["weight_raw"] for c in cl_conts)
        bl_w_list = ", ".join(c["weight_raw"] for c in bl_conts)
        cl_c_list = ", ".join(c["cbm_raw"] for c in cl_conts)
        bl_c_list = ", ".join(c["cbm_raw"] for c in bl_conts)

        lines.append(
            f"- Hạng mục 6 (`CONTAINER NO.`): BL Check List = `{cl_cont_nos}` | BL = `{bl_cont_nos}` => "
            f"Kết quả: **{'ĐÚNG' if all_no_ok else 'SAI'}** ({'Bảng 1' if all_no_ok else 'Bảng 2'})."
        )
        lines.append(
            f"- Hạng mục 7 (`SEAL NO.`): BL Check List = `{cl_seals}` | BL = `{bl_seals}` => "
            f"Kết quả: **{'ĐÚNG' if all_seal_ok else 'SAI'}** ({'Bảng 1' if all_seal_ok else 'Bảng 2'})."
        )
        v_8 = "**ĐÚNG** (Bảng 1)" if all_type_ok else "**SAI** (Bảng 2)"
        lines.append(
            f"- Hạng mục 8 (`CONTAINER TYPE` - 1 dòng): BL Check List = `{cl_disp_8}` | "
            f"BL = `{bl_disp_8}` => Kết quả: {v_8}."
        )
        if cl_tot_pkgs > 0 and cl_tot_w > 0 and bl_tot_pkgs > 0 and bl_tot_w > 0:
            v_9 = "**ĐÚNG** (Bảng 1)" if all_pkgs_ok else "**SAI** (Bảng 2)"
            lines.append(
                f"- Hạng mục 9 (`P'KGS` - trình bày gộp trên 1 dòng duy nhất): "
                f"Cột `Dữ liệu BL Check List` ghi `{cl_pkgs_list} (Tổng: {cl_tot_pkgs} PKGS)` | "
                f"Cột `Dữ liệu BL` ghi `{bl_pkgs_list} (Tổng: {bl_tot_pkgs} CARTONS)` => Kết quả bắt buộc: {v_9}."
            )
            v_10 = "**ĐÚNG** (Bảng 1)" if all_w_ok else "**SAI** (Bảng 2)"
            lines.append(
                f"- Hạng mục 10 (`WEIGHT` - trình bày gộp trên 1 dòng duy nhất): "
                f"Cột `Dữ liệu BL Check List` ghi `{cl_w_list}` | "
                f"Cột `Dữ liệu BL` ghi `{bl_w_list}` => Kết quả bắt buộc: {v_10}."
            )
            v_11 = "**ĐÚNG** (Bảng 1)" if all_c_ok else "**SAI** (Bảng 2)"
            lines.append(
                f"- Hạng mục 11 (`MEASUREMENT/M3` - trình bày gộp trên 1 dòng duy nhất): "
                f"Cột `Dữ liệu BL Check List` ghi `{cl_c_list}` | "
                f"Cột `Dữ liệu BL` ghi `{bl_c_list}` => Kết quả bắt buộc: {v_11}."
            )
            v_12 = "**ĐÚNG** (Bảng 1)" if (all_pkgs_ok and all_w_ok and all_c_ok) else "**SAI** (Bảng 2)"
            lines.append(
                f"- Hạng mục 12 (`Tổng P'KGS, WEIGHT và MEASUREMENT/M3`): "
                f"Cột `Dữ liệu BL Check List` ghi `{cl_tot_pkgs} PKGS / {cl_tot_w:,.1f} KGS / {cl_tot_c:.3f} M3` | "
                f"Cột `Dữ liệu BL` ghi `{bl_tot_pkgs} CARTONS / {bl_tot_w:,.3f} KGS / {bl_tot_c:.3f} CBM` => Kết quả bắt buộc: {v_12}."
            )

        desc_res = extract_goods_descriptions_comparison(checklist_text, bl_text)
        if desc_res:
            cl_desc_str = ", ".join(desc_res["cl_unique"])
            bl_desc_str = " / ".join(desc_res["bl_by_cont"])
            verdict_13 = "**ĐÚNG** (Bảng 1)" if desc_res["is_ok"] else f"**SAI** ({desc_res['issues_summary']}) (Bảng 2)"
            lines.append(
                f"- Hạng mục 13 (`GOODS DESCRIPTION` - Bước 4.4): Danh sách chuẩn Check List = `{cl_desc_str}` | "
                f"Theo từng container trên BL = `{bl_desc_str}` => Kết quả bắt buộc: {verdict_13}."
            )

        v_19 = "**ĐÚNG** (Bảng 1)" if all_no_ok else "**SAI** (Bảng 2)"
        lines.append(
            f"- Hạng mục 19 (`Tổng số container + loại container`): BL Check List = `{len(cl_conts)} ({cl_cont_nos}) / {cl_disp_8}` | "
            f"BL = `{len(bl_conts)} ({bl_cont_nos}) / {bl_disp_8}` => Kết quả: {v_19}."
        )

        cl_terms = [c["movement"] for c in cl_conts if c.get("movement") and c["movement"] != "N/A"]
        bl_terms = [c["movement"] for c in bl_conts if c.get("movement") and c["movement"] != "N/A"]
        if not bl_terms:
            m_bl_term = re.search(r"\b(CY\s*[\-/]\s*CY|CY\s*[\-/]\s*DOOR|CFS\s*[\-/]\s*CFS|CFS\s*[\-/]\s*DOOR)\b", bl_text, re.I)
            if m_bl_term:
                bl_terms = [re.sub(r"\s+", "", m_bl_term.group(1).upper()).replace("/", "-")]
        if cl_terms and bl_terms:
            cl_term_norm = cl_terms[0]
            bl_term_norm = bl_terms[0]
            cl_raw_ex = next((c["movement_raw"] for c in cl_conts if c.get("movement_raw") != "N/A"), cl_term_norm)
            term_ok = cl_term_norm == bl_term_norm
            v_20 = "**ĐÚNG** (Bảng 1)" if term_ok else "**SAI** (Bảng 2)"
            lines.append(
                f"- Hạng mục 20 (`Delivery term`): BL Check List = `{cl_raw_ex} ({cl_term_norm})` | "
                f"BL = `{bl_term_norm}` => Kết quả: {v_20}."
            )

    # 1.5 Extract Item 15 (PORT OF DISCHARGE) & Item 16 (PLACE OF DELIVERY)
    cl_to = ""
    m_to_box = re.search(r"\*\*To \(Request\)\*\*:\s*([^\n|]+)", checklist_text, re.I)
    if m_to_box:
        cl_to = m_to_box.group(1).strip()
    if not cl_to:
        m_to_col = re.search(
            r"\bTO(?:\s*\|\s*|\s+)ETA\b[^\n]*\n\s*([^\n|]+?)(?:\s*\|\s*|\s{2,})\d{4}",
            checklist_text,
            re.I,
        )
        if m_to_col:
            cl_to = m_to_col.group(1).strip()

    bl_podeliv = ""
    m_podeliv_box = re.search(r"\*\*9\. Place of Delivery\*\*:\s*([^\n]+)", bl_text, re.I)
    if m_podeliv_box:
        raw_podeliv = m_podeliv_box.group(1).strip()
        raw_podeliv = re.sub(r"\(\s*(?:Combined\s+Transport\s+ONLY|see\s+Clause)[^)]*\)", "", raw_podeliv, flags=re.I)
        raw_podeliv = re.sub(r"\bN/A\b.*", "", raw_podeliv, flags=re.I)
        raw_podeliv = raw_podeliv.replace("|", " ").strip(" :.-*\t")
        if raw_podeliv:
            bl_podeliv = raw_podeliv
    else:
        m_podeliv_line = re.search(
            r"BOOKING\s+REF\.[^\n]*\|\s*PORT\s+OF\s+DISCHARGE\s*\|\s*PLACE\s+OF\s+DELIVERY[^\n]*\n\s*([^\n]+)",
            bl_text,
            re.I,
        )
        if m_podeliv_line:
            cols = [c.strip() for c in m_podeliv_line.group(1).split("|") if c.strip()]
            # If only 2 columns exist (<Booking Ref> | <Port of Discharge>), Place of Delivery is blank
            if len(cols) >= 3 and not re.search(r"TRỐNG|N/A|^X{4,}$", cols[-1], re.I):
                bl_podeliv = cols[-1]

    if not bl_podeliv:
        m_podeliv_tbl = re.search(
            r"(?:Place\s*of\s*Delivery|PlaceofDelivery)[^\n]*\n[^\n]*?\|\s*([A-Z\s,]+?)(?:\s*\|\s*DOOR|\s*\|\s*CY|\s*$)",
            bl_text,
            re.I,
        )
        if m_podeliv_tbl:
            cand = m_podeliv_tbl.group(1).strip()
            if not re.search(r"TRỐNG|N/A|^X{4,}$", cand, re.I):
                bl_podeliv = cand

    if cl_to:
        if not bl_podeliv:
            lines.append(
                f"- Hạng mục 16 (`PLACE OF DELIVERY`): Cột `Dữ liệu BL Check List` = `{cl_to}` (trường `TO`) | "
                f"Cột `Dữ liệu BL` = `N/A (Không có dữ liệu)` (ô `PLACE OF DELIVERY` trên BL để trống; lưu ý `{cl_to}` trên BL là `PORT OF DISCHARGE`, không phải `PLACE OF DELIVERY`) => "
                f"Kết quả bắt buộc: **KHÔNG TÌM THẤY** (đưa vào Bảng 2 - ĐIỂM CẦN XỬ LÝ, tuyệt đối không đưa vào Bảng 1)."
            )
        else:
            clean_cl_to = re.sub(r"[^A-Z0-9]", "", cl_to.upper())
            clean_bl_pod = re.sub(r"[^A-Z0-9]", "", bl_podeliv.upper())
            podeliv_ok = bool(clean_cl_to and clean_bl_pod and ((clean_cl_to in clean_bl_pod) or (clean_bl_pod in clean_cl_to)))
            lines.append(
                f"- Hạng mục 16 (`PLACE OF DELIVERY`): Cột `Dữ liệu BL Check List` = `{cl_to}` | "
                f"Cột `Dữ liệu BL` = `{bl_podeliv}` => Kết quả bắt buộc: **{'ĐÚNG' if podeliv_ok else 'SAI'}** "
                f"({'Bảng 1' if podeliv_ok else 'Bảng 2'})."
            )

    # 2. Extract ETD, On-Board Date, and Place/Date of Issue (Items 17 & 18)
    cl_etd_raw: list[str] = []
    m_etd_box = re.search(r"\*\*ETD[^*]*\*\*:\s*([^\n|]+)", checklist_text, re.I)
    if m_etd_box:
        for d in _DATE_TOKEN_RE.findall(m_etd_box.group(1)):
            if d not in cl_etd_raw:
                cl_etd_raw.append(d.strip())

    m_etd_col = re.search(
        r"\bFROM(?:\s*\|\s*|\s+)ETD\b[^\n]*\n(?:REQUEST[^\n]*\n)?\s*([^\n|]+?)(?:\s*\|\s*|\s{2,})(\d{4}[./-]\d{2}[./-]\d{2}|\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{4})",
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

    bl_issue_place = ""
    bl_issue_date = ""

    m_form_issue = re.search(
        r"\*\*Place and Date of Issue[^*]*\*\*:\s*([^\n]+)",
        bl_text,
        re.I,
    )
    m_inline_issue = re.search(
        r"\bDate\s+of\s+Issue\s+(\d{1,2}[-\s]+[A-Za-z]{3,9}[-\s]+\d{4}|\d{4}[./-]\d{2}[./-]\d{2})(?:\s*\|\s*|\s+)Place\s+of\s+Issue\s+([^\n|]+)",
        bl_text,
        re.I,
    )
    if m_form_issue:
        raw_val = m_form_issue.group(1).strip()
        found_d = _DATE_TOKEN_RE.findall(raw_val)
        if found_d:
            bl_issue_date = found_d[-1].strip()
            bl_issue_place = _dedup_place(_DATE_TOKEN_RE.sub("", raw_val).replace("|", " "))
    if not bl_issue_date and m_inline_issue:
        bl_issue_date = m_inline_issue.group(1).strip()
        bl_issue_place = _dedup_place(m_inline_issue.group(2).strip())
    if not bl_issue_date:
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
                bl_issue_place = _dedup_place(_DATE_TOKEN_RE.sub("", joined_left).replace("|", " "))
                break

    bl_issue_iso = normalize_shipping_date(bl_issue_date)

    bl_onboard_date = ""
    m_ob_box = re.search(r"\*\*Date Laden on Board / On Board Date\*\*:\s*([^\n]+)", bl_text, re.I)
    if m_ob_box:
        ob_d = _DATE_TOKEN_RE.findall(m_ob_box.group(1))
        if ob_d:
            bl_onboard_date = ob_d[0].strip()
    if not bl_onboard_date:
        for m_ob in re.finditer(
            r"(?:SHIPPED\s+ON\s+BOARD\s+DATE|Date\s+Laden\s+on\s+Board)[^\n]*\n((?:[^\n]+\n?){1,3})",
            bl_text,
            re.I,
        ):
            ob_d = _DATE_TOKEN_RE.findall(m_ob.group(1))
            if ob_d:
                bl_onboard_date = ob_d[0].strip()
                break
    if not bl_onboard_date:
        # Native PyMuPDF layout where the date is 1-5 lines above 'SHIPPED ON BOARD DATE'
        for m_ob_before in re.finditer(
            r"((?:[^\n]+\n){1,5})\s*(?:SHIPPED\s+ON\s+BOARD\s+DATE|Date\s+Laden\s+on\s+Board)\b",
            bl_text,
            re.I,
        ):
            ob_d = _DATE_TOKEN_RE.findall(m_ob_before.group(1))
            if ob_d:
                bl_onboard_date = ob_d[-1].strip()
                break

    bl_onboard_iso = normalize_shipping_date(bl_onboard_date)

    if cl_etd_iso and bl_onboard_iso:
        verdict_17 = "**ĐÚNG** (Bảng 1)" if cl_etd_iso == bl_onboard_iso else "**SAI** (Bảng 2)"
        lines.append(
            f"- Hạng mục 17 (`ETD`): Cột `Dữ liệu BL Check List` = `{' / '.join(cl_etd_raw)}` | "
            f"Cột `Dữ liệu BL` = `{bl_onboard_date} (On-Board Date)` => Kết quả bắt buộc: {verdict_17}."
        )
    if cl_etd_iso and bl_issue_iso:
        verdict_18 = "**ĐÚNG** (Bảng 1)" if cl_etd_iso == bl_issue_iso else "**SAI** (Bảng 2)"
        place_prefix = f"{bl_issue_place}, " if bl_issue_place else ""
        if cl_etd_iso == bl_issue_iso:
            diff_reason_18 = f"Hai bên cùng ngày `{cl_etd_iso}`"
        else:
            diff_reason_18 = f"Hai bên khác ngày (BL Check List: `{cl_etd_iso}` vs BL Place and Date of Issue: `{bl_issue_iso}`)"
        lines.append(
            f"- Hạng mục 18 (`ISSUE DATE = ETD`): Đối chiếu ETD của Check List với Place and Date of Issue của BL. "
            f"Cột `Dữ liệu BL Check List` bắt buộc ghi `{' / '.join(cl_etd_raw)}` | "
            f"Cột `Dữ liệu BL` bắt buộc ghi `{place_prefix}{bl_issue_date}` => {diff_reason_18} nên Kết quả bắt buộc: {verdict_18}."
        )

    # 3. Item 22: B/L REQUIRED (ON BOARD vs RECEIVED)
    cl_bl_req = ""
    m_req_box = re.search(r"\*\*B/L Required\*\*:\s*([^\n|]+)", checklist_text, re.I)
    if m_req_box:
        cl_bl_req = m_req_box.group(1).strip().upper()
    else:
        m_req_inline = re.search(r"\b(ON\s+BOARD|RECEIVED)\b", checklist_text[:2500], re.I)
        if m_req_inline:
            cl_bl_req = " ".join(m_req_inline.group(1).upper().split())

    m_bl_rec_date = re.search(
        r"\bRECEIVED\s+DATE\b[^\n]*\n?([^\n]{0,80})",
        bl_text,
        re.I,
    )
    bl_rec_date = ""
    if m_bl_rec_date:
        rec_d = _DATE_TOKEN_RE.findall(m_bl_rec_date.group(0))
        if rec_d:
            bl_rec_date = rec_d[0].strip()

    if cl_bl_req and (bl_onboard_date or bl_rec_date):
        if bl_onboard_date and bl_rec_date:
            bl_req_val = f"Có cả ON BOARD ({bl_onboard_date}) và RECEIVED DATE ({bl_rec_date})"
            verdict_22 = "**SAI** (Bảng 2)"
        elif bl_onboard_date:
            bl_req_val = f"ON BOARD (SHIPPED ON BOARD DATE: {bl_onboard_date})"
            verdict_22 = "**ĐÚNG** (Bảng 1)" if "ON BOARD" in cl_bl_req else "**SAI** (Bảng 2)"
        else:
            bl_req_val = f"RECEIVED (RECEIVED DATE: {bl_rec_date})"
            verdict_22 = "**ĐÚNG** (Bảng 1)" if "RECEIVED" in cl_bl_req else "**SAI** (Bảng 2)"

        lines.append(
            f"- Hạng mục 22 (`B/L REQUIRED` - Bước 3): Cột `Dữ liệu BL Check List` = `{cl_bl_req}` | "
            f"Cột `Dữ liệu BL` = `{bl_req_val}` => Kết quả bắt buộc: {verdict_22}."
        )

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


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
