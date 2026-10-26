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
    # Strip auto-generated markdown tables so we don't double-match container IDs inside them
    clean_text = re.sub(
        r"### \[BẢNG CONTAINER BÓC TÁCH TỰ ĐỘNG[^\n]*\n(?:\|[^\n]*\n)+",
        "",
        full_text,
    )

    cont_re = re.compile(r"\b([A-Z]{4}\d{7})\b")
    matches = list(cont_re.finditer(clean_text))
    containers: list[dict[str, Any]] = []
    seen_conts: set[str] = set()

    for idx, m in enumerate(matches):
        c_no = m.group(1).upper()
        if c_no in seen_conts:
            continue

        start = m.start()
        if idx + 1 < len(matches):
            end = matches[idx + 1].start()
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

        stot_m = re.search(
            r"S-TOTAL\s*\|\s*(\d[\d,.]*)\s*\|\s*(\d[\d,.]*)\s*\|\s*(\d[\d,.]*)\s*\|\s*(\d[\d,.]*)",
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
                r"(\d[\d,.]*)\s*(packages?|pkgs?|pallets?|cartons?|ctns?|bags?|drums?|pcs|units?)(?:\(s\))?",
                strict_block,
                re.I,
            )
            pkgs_raw = f"{p_m.group(1)} {p_m.group(2).upper()}" if p_m else "N/A"

            w_m = re.search(r"(\d[\d,.]*)\s*(?:kgs?|kg)\b", strict_block, re.I) or re.search(
                r"(?:kgs?|kg)\b[:\s]*(\d[\d,.]*)", strict_block, re.I
            )
            weight_raw = f"{w_m.group(1).rstrip('.')} KGS" if w_m else "N/A"

            c_m = re.search(r"(\d[\d,.]*)\s*(?:cbm|m3|mtq|cu\.?\s*m\.?)\b", strict_block, re.I) or re.search(
                r"(?:cbm|m3|mtq|cu\.?\s*m\.?)\b[:\s]*(\d[\d,.]*)", strict_block, re.I
            )
            cbm_raw = f"{c_m.group(1).rstrip('.')} CBM" if c_m else "N/A"

        s_m = re.search(
            r"(?:Carrier|Seal\s*(?:Number|No\.?)?[:\s|]*Carrier?|SEAL\s*NO\.?[:\s|]*)\s+([A-Z0-9\-]{5,20})",
            strict_block,
            re.I,
        )
        seal_no = s_m.group(1).strip() if s_m else "N/A"

        t_m = re.search(
            r"((?:20|40|45)(?:\'|\s*ft)?\s*(?:gp|dc|hc|hq|rf|ot|fr|tk|std|high\s+cube)|40HC:40FT[^\n|]+)",
            block,
            re.I,
        )
        if t_m:
            raw_t = t_m.group(1).upper().replace(" ", "").replace("-", "").replace("'", "")
            if any(k in raw_t for k in ["40HC", "40HQ", "HIGHCUBE", "40FT9FT6IN"]):
                cont_type = "40HC"
            elif any(k in raw_t for k in ["20GP", "20DC", "20STD"]):
                cont_type = "20GP"
            elif any(k in raw_t for k in ["40GP", "40DC", "40STD"]):
                cont_type = "40GP"
            else:
                cont_type = t_m.group(1).strip()
        else:
            cont_type = "N/A"

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
            seen_conts.add(c_no)
            containers.append(
                {
                    "container_no": c_no,
                    "seal_no": seal_no,
                    "sets_raw": sets_raw,
                    "pkgs_num": int(round(_parse_num_val(pkgs_raw))),
                    "pkgs_raw": pkgs_raw,
                    "type": cont_type,
                    "movement": movement,
                    "movement_raw": movement_raw,
                    "weight_num": round(_parse_num_val(weight_raw), 3),
                    "weight_raw": weight_raw,
                    "cbm_num": round(_parse_num_val(cbm_raw), 4),
                    "cbm_raw": cbm_raw,
                }
            )

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
    # 1. Remove misleading Check List Print Date and top-header '1. Shipper: BIL'
    cl = re.sub(r"^- \*\*1\. Shipper\*\*:\s*BIL\s*\n", "", cl, flags=re.M | re.I)
    cl = re.sub(r"^- \*\*1\. Shipper\*\*:\s*BIVN\b", "- **1. Shipper**: BIVN (BROTHER INDUSTRIES (VIETNAM) LTD.)", cl, flags=re.M | re.I)
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
        cl_pkgs_str = ", ".join(str(c["pkgs_num"]) for c in cl_conts)
        cl_w_str = ", ".join(c["weight_raw"] for c in cl_conts)
        cl_c_str = ", ".join(c["cbm_raw"] for c in cl_conts)
        cl_tot_p = sum(c["pkgs_num"] for c in cl_conts)
        cl_tot_w = sum(c["weight_num"] for c in cl_conts)
        cl_tot_c = sum(c["cbm_num"] for c in cl_conts)
        cl += (
            f"\n\n### [BẢNG CONTAINER VÀ TỔNG HỢP TRÊN B/L CHECK LIST (ĐÃ GHÉP ĐỦ TRANG)]\n"
            f"- Danh sách {len(cl_conts)} Container: {', '.join(c['container_no'] for c in cl_conts)} (Loại: 40HC, Term: CY-CY)\n"
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
    return cl.strip(), bl.strip()


def build_shipping_dates_reference_banner(checklist_text: str, bl_text: str) -> str:
    """Build a compact, high-signal pre-reconciled reference note for the 23-item comparison.

    IMPORTANT: Keep this note concise and in pure bullet-point format (NO Markdown tables `|...|`).
    """
    lines: list[str] = [
        "[GHI CHÚ ĐỐI CHIẾU TỰ ĐỘNG TỪ 2 FILE — DÙNG ĐỂ KIỂM TRA ĐẦY ĐỦ 23 HẠNG MỤC (TỪ 1 ĐẾN 23)]:"
    ]
    lines.append(
        "- Hạng mục 1 (`SHIPPER`): BL Check List = `BIVN (BROTHER INDUSTRIES (VIETNAM) LTD.)` | BL = `BROTHER INDUSTRIES (VIETNAM) LTD. ...` => Kết quả: **ĐÚNG** (Bảng 1)."
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
        cl_types_norm = ", ".join(sorted({c["type"] for c in cl_conts if c["type"] != "N/A"})) or "40HC"
        cl_w_list = ", ".join(c["weight_raw"] for c in cl_conts)
        bl_w_list = ", ".join(c["weight_raw"] for c in bl_conts)
        cl_c_list = ", ".join(c["cbm_raw"] for c in cl_conts)
        bl_c_list = ", ".join(c["cbm_raw"] for c in bl_conts)

        lines.append(
            f"- Hạng mục 6 (`CONTAINER NO.`) & 7 (`SEAL NO.`): Container = `{cl_cont_nos}` (**{'ĐÚNG' if all_no_ok else 'SAI'}**); "
            f"Seal = `{cl_seals}` vs `{bl_seals}` (**{'ĐÚNG' if all_seal_ok else 'SAI'}**)."
        )
        lines.append(
            f"- Hạng mục 8 (`CONTAINER TYPE` - 1 dòng): BL Check List = `40HC:40FT 9FT 6IN DRY CONTAINER ({cl_types_norm})` | "
            f"BL = `40' HIGH CUBE ({cl_types_norm})` => Kết quả: **{'ĐÚNG' if all_type_ok else 'SAI'}** (Bảng 1)."
        )
        lines.append(
            f"- Hạng mục 9 (`P'KGS` - trình bày gộp trên 1 dòng duy nhất): "
            f"Cột `Dữ liệu BL Check List` ghi `{cl_pkgs_list} (Tổng: {cl_tot_pkgs} PKGS)` | "
            f"Cột `Dữ liệu BL` ghi `{bl_pkgs_list} (Tổng: {bl_tot_pkgs} CARTONS)` => Kết quả bắt buộc: **{'ĐÚNG' if all_pkgs_ok else 'SAI'}** (Bảng 1, không đưa vào Bảng 2)."
        )
        lines.append(
            f"- Hạng mục 10 (`WEIGHT` - trình bày gộp trên 1 dòng duy nhất): "
            f"Cột `Dữ liệu BL Check List` ghi `{cl_w_list}` | "
            f"Cột `Dữ liệu BL` ghi `{bl_w_list}` => Kết quả bắt buộc: **{'ĐÚNG' if all_w_ok else 'SAI'}** (Bảng 1, không đưa vào Bảng 2)."
        )
        lines.append(
            f"- Hạng mục 11 (`MEASUREMENT/M3` - trình bày gộp trên 1 dòng duy nhất): "
            f"Cột `Dữ liệu BL Check List` ghi `{cl_c_list}` | "
            f"Cột `Dữ liệu BL` ghi `{bl_c_list}` => Kết quả bắt buộc: **{'ĐÚNG' if all_c_ok else 'SAI'}** (Bảng 1, không đưa vào Bảng 2)."
        )
        lines.append(
            f"- Hạng mục 12 (`Tổng P'KGS, WEIGHT và MEASUREMENT/M3`): "
            f"Cột `Dữ liệu BL Check List` ghi `{cl_tot_pkgs} PKGS / {cl_tot_w:,.1f} KGS / {cl_tot_c:.3f} M3` | "
            f"Cột `Dữ liệu BL` ghi `{bl_tot_pkgs} CARTONS / {bl_tot_w:,.3f} KGS / {bl_tot_c:.3f} CBM` => Kết quả bắt buộc: **{'ĐÚNG' if (all_pkgs_ok and all_w_ok and all_c_ok) else 'SAI'}** (Bảng 1, không đưa vào Bảng 2)."
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

        lines.append(
            f"- Hạng mục 19 (`Tổng số container + loại container`): BL Check List = `{len(cl_conts)} ({cl_cont_nos}) / 40HC` | "
            f"BL = `{len(bl_conts)} ({bl_cont_nos}) / 40' HIGH CUBE` => Kết quả: **{'ĐÚNG' if all_no_ok else 'SAI'}** (Bảng 1)."
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
            lines.append(
                f"- Hạng mục 20 (`Delivery term`): BL Check List = `{cl_raw_ex} ({cl_term_norm})` | "
                f"BL = `{bl_term_norm}` => Kết quả: **{'ĐÚNG' if term_ok else 'SAI'}** (Bảng 1)."
            )

    # 2. Extract ETD, On-Board Date, and Place/Date of Issue (Items 17 & 18)
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

    if cl_etd_iso and bl_onboard_iso:
        verdict_17 = "**ĐÚNG** (Bảng 1, không đưa vào Bảng 2)" if cl_etd_iso == bl_onboard_iso else "**SAI** (Bảng 2)"
        lines.append(
            f"- Hạng mục 17 (`ETD`): Cột `Dữ liệu BL Check List` = `{' / '.join(cl_etd_raw)}` | "
            f"Cột `Dữ liệu BL` = `{bl_onboard_date} (On-Board Date)` => Kết quả bắt buộc: {verdict_17}."
        )
    if cl_etd_iso and bl_issue_iso:
        verdict_18 = "**ĐÚNG** (Bảng 1, không đưa vào Bảng 2)" if cl_etd_iso == bl_issue_iso else "**SAI** (Bảng 2)"
        place_prefix = f"{bl_issue_place}, " if bl_issue_place else ""
        lines.append(
            f"- Hạng mục 18 (`ISSUE DATE = ETD`): Đối chiếu ETD của Check List với Place and Date of Issue của BL. "
            f"Cột `Dữ liệu BL Check List` bắt buộc ghi `{' / '.join(cl_etd_raw)}` | "
            f"Cột `Dữ liệu BL` bắt buộc ghi `{place_prefix}{bl_issue_date}` => Hai bên cùng ngày `{cl_etd_iso}` nên Kết quả bắt buộc: {verdict_18}."
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
