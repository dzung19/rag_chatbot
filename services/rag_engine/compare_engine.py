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
