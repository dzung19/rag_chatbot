"""Advanced Layout-Aware & Box-Grouping PDF Parser for RAG Ingestion.

Optimized for:
1. Complex boxed shipping forms (Bill of Lading, Sea Waybill, BL Check List, Invoices):
   - Groups text inside rectangular vector boxes & spatial label regions so multi-column
     fields (Shipper, Consignee, Booking No., Sea Waybill No., Port of Loading/Discharge,
     Place of Delivery, Date Laden on Board, Date of Issue) never interleave.
   - Reconstructs horizontal visual lines by Y-coordinate clustering so typewriter/monospace
     container rows crossing vertical grid lines (e.g. `BEAU2247050 /00001160 / 20 PACKAGES...`)
     stay 100% intact instead of being sliced by table cell borders.
   - Automatically parses container rows, totals (P'KGS, Weight, Measurement), Invoice numbers,
     and HS Code warnings into structured Markdown tables appended to the page.
2. General corporate PDFs (bus schedules, SOPs, HR policies):
   - Extracts clean Markdown tables via PyMuPDF `find_tables()` when text does not cross
     vertical cell boundaries.
   - Strips repeating boilerplate headers/footers across multi-page documents.
3. Scanned / Image-only PDFs:
   - Falls back to RapidOCR / Tesseract (if installed) or Ollama Multimodal Vision API.
"""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass, field
import logging
import os
import re
from typing import Any, Generator, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex Patterns for Shipping Documents (BL & BL Check List)
# ---------------------------------------------------------------------------

# Matches standard ISO container numbers: 4 letters + 7 digits (e.g., BEAU2247050, CSNU1725310)
_CONTAINER_LINE_RE = re.compile(
    r"\b([A-Z]{4}\d{7})\b"
    r"(?:[\s/|]+([A-Z0-9\-]{4,15}))?"  # Optional Seal No (e.g., 00001160)
    r"[^\n]*?"
    r"(\d+(?:[.,]\d+)?)\s*(PACKAGES?|PKGS?|PALLETS?|CARTONS?|CTNS?|BAGS?|DRUMS?|PCS|UNITS?)"
    r"([^\n]*?)"  # Middle segment containing Movement Term (FCL/FCL) and Container Type (20GP, 40HC, etc.)
    r"(\d+(?:[.,]\d+)?)\s*(?:KGS?|KG)\b"
    r"(?:[^\n]*?(\d+(?:[.,]\d+)?)\s*(?:CBM|M3|MTQ)\b)?",
    re.IGNORECASE,
)

_MOVEMENT_TERM_RE = re.compile(
    r"\b(FCL\s*/\s*FCL|LCL\s*/\s*LCL|FCL\s*/\s*LCL|LCL\s*/\s*FCL|CY\s*[\-/]\s*CY|CY\s*[\-/]\s*DOOR|CFS\s*[\-/]\s*CFS|CFS\s*[\-/]\s*DOOR)\b",
    re.IGNORECASE,
)

_CONT_TYPE_RE = re.compile(
    r"(?:^|[\s/|])((?:20|40|45)\s*(?:GP|DC|HC|HQ|RF|OT|FR|TK|STD)|(?:20|40|45)'\s*[A-Z]{0,4})(?=$|[\s/|0-9])",
    re.IGNORECASE,
)

# Simpler fallback pattern for container rows formatted in columns (BL Check List style)
_CONTAINER_SIMPLE_RE = re.compile(r"\b([A-Z]{4}\d{7})\b")

# HS Code detection (Rule 23: BL must NOT display HS Code)
_HS_CODE_RE = re.compile(
    r"\b(?:H\.?S\.?\s*CODE|HARMONIZED\s+CODE|TARIFF\s+CODE)\s*[:#\-]?\s*([0-9.]{4,12})\b",
    re.IGNORECASE,
)

# Invoice number detection (Step 2 of BL Check List)
_INVOICE_NO_RE = re.compile(
    r"\bINVOICE\s*NO\.?\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\-/\s]*)",
    re.IGNORECASE,
)

# Standard BL / Shipping Form Box Labels used as spatial anchors when vector boxes are incomplete
_BL_LABEL_ANCHORS: list[tuple[str, re.Pattern[str]]] = [
    ("1. Shipper", re.compile(r"^(?:1\.?\s*)?Shipper\b", re.I)),
    ("2. Consignee", re.compile(r"^(?:2\.?\s*)?Consignee\b", re.I)),
    ("3. Notify Party", re.compile(r"^(?:3\.?\s*)?Notify\s+Party\b(?!\s*2)", re.I)),
    (
        "Notify Party 2 / Also Notify",
        re.compile(r"^(?:Also\s+Notify(?:\s+Party)?(?:\s*[-–]?\s*routing\s*&\s*Instructions)?|Notify\s+Party\s*2)\b", re.I),
    ),
    ("Booking No.", re.compile(r"^Booking\s+No\.?", re.I)),
    ("Sea Waybill / B/L No.", re.compile(r"^(?:Sea\s+Waybill\s+No\.?|B/L\s+No\.?|Bill\s+of\s+Lading\s+No\.?)", re.I)),
    ("Export References", re.compile(r"^Export\s+References\b", re.I)),
    ("Forwarding Agent", re.compile(r"^Forwarding\s+Agent(?:\s+and\s+References)?(?:\s+FMC/CHB\s+No\.?)?\b", re.I)),
    ("Point and Country of Origin", re.compile(r"^Point\s+and\s+Country\s+of\s+Origin\b", re.I)),
    ("Delivery To", re.compile(r"^Delivery\s+To\b", re.I)),
    ("4. Pre-Carriage by", re.compile(r"^(?:4\.?\s*)?(?:Combined\s+Transport\s*\*?\s*)?Pre-Carriage\s+by\b", re.I)),
    ("5. Place of Receipt", re.compile(r"^(?:5\.?\s*)?(?:Combined\s+Transport\s*\*?\s*)?Place\s+of\s+Receipt\b", re.I)),
    ("6. Ocean Vessel / Voy. No.", re.compile(r"^(?:6\.?\s*)?(?:Ocean\s+)?Vessel(?:\s*[/]?\s*Voy\.?\s*No\.?)?", re.I)),
    ("7. Port of Loading", re.compile(r"^(?:7\.?\s*)?Port\s+of\s+Loading\b", re.I)),
    ("Service Contract No.", re.compile(r"^Service\s+Contract\s+No\.?", re.I)),
    ("Doc. Form No.", re.compile(r"^Doc\.?\s+Form\s+No\.?", re.I)),
    ("8. Port of Discharge", re.compile(r"^(?:8\.?\s*)?Port\s+of\s+Discharge\b", re.I)),
    ("9. Place of Delivery", re.compile(r"^(?:9\.?\s*)?(?:Combined\s+Transport\s*\*?\s*)?Place\s+of\s+Delivery\b", re.I)),
    ("Type of Movement", re.compile(r"^Type\s+of\s+Movement\b", re.I)),
    ("10. Total Number of Containers/Packages", re.compile(r"^10\.?\s*Total\s+Number\s+of\s+Containers(?:\s+and/or\s+Packages\s*\(in\s+words\))?", re.I)),
    ("Date Laden on Board / On Board Date", re.compile(r"^(?:Date\s+Laden\s+on\s+Board|Laden\s+on\s+Board|Shipped\s+on\s+Board|On\s+Board\s+Date|Received\s+Date)\b", re.I)),
    ("Date of Issue", re.compile(r"^(?:Date\s+of\s+Issue|Issue\s+Date)\b", re.I)),
    ("Place of Issue", re.compile(r"^Place\s+of\s+Issue\b", re.I)),
]


@dataclass
class WordBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_no: int = 0
    line_no: int = 0
    word_no: int = 0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) * 0.5

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) * 0.5

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class VisualLine:
    y0: float
    y1: float
    words: list[WordBox] = field(default_factory=list)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) * 0.5

    @property
    def x0(self) -> float:
        return min((w.x0 for w in self.words), default=0.0)

    @property
    def x1(self) -> float:
        return max((w.x1 for w in self.words), default=0.0)

    def render(self, col_gap_pt: float = 18.0) -> str:
        """Render words sorted left-to-right, inserting ' | ' on wide column gaps."""
        if not self.words:
            return ""
        sorted_w = sorted(self.words, key=lambda w: w.x0)
        parts: list[str] = [sorted_w[0].text]
        prev_x1 = sorted_w[0].x1
        for w in sorted_w[1:]:
            gap = w.x0 - prev_x1
            if gap >= col_gap_pt:
                parts.append(" | ")
                parts.append(w.text)
            elif gap < -1.0:
                # Overlapping duplicate glyph in some PDFs; skip if identical
                if w.text != parts[-1]:
                    parts.append(" " + w.text)
            else:
                parts.append(" " + w.text)
            prev_x1 = max(prev_x1, w.x1)
        return "".join(parts).strip()

    def render_plain(self) -> str:
        """Render words with single spaces."""
        if not self.words:
            return ""
        sorted_w = sorted(self.words, key=lambda w: w.x0)
        return " ".join(w.text for w in sorted_w).strip()


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------


def parse_pdf_document(file_path: str) -> Generator[str, None, None]:
    """Parse a PDF file page-by-page with layout, box, table, and OCR/Vision support."""
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        logger.error("Failed to open PDF %s: %s", file_path, str(e))
        raise

    try:
        total_pages = len(doc)
        if total_pages == 0:
            return

        # Pre-scan repeating margin headers/footers across multi-page documents (>= 3 pages)
        repeating_margins = _detect_repeating_margin_lines(doc) if total_pages >= 3 else set()

        for page_idx in range(total_pages):
            page = doc[page_idx]
            page_num = page_idx + 1
            page_text = _extract_single_page(
                page=page,
                page_num=page_num,
                total_pages=total_pages,
                repeating_margins=repeating_margins,
                file_path=file_path,
            )
            if page_text and page_text.strip():
                yield page_text.strip()
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Single-Page Extraction Pipeline
# ---------------------------------------------------------------------------


def _extract_single_page(
    page: Any,
    page_num: int,
    total_pages: int,
    repeating_margins: set[str],
    file_path: str,
) -> str:
    """Extract structured markdown text from a single PDF page."""
    rect = page.rect
    page_width = float(rect.width or 600.0)
    page_height = float(rect.height or 800.0)

    raw_words = page.get_text("words") or []
    words: list[WordBox] = []
    for rw in raw_words:
        text_val = str(rw[4]).strip()
        if not text_val:
            continue
        w_box = WordBox(
            x0=float(rw[0]),
            y0=float(rw[1]),
            x1=float(rw[2]),
            y1=float(rw[3]),
            text=text_val,
            block_no=int(rw[5]),
            line_no=int(rw[6]),
            word_no=int(rw[7]),
        )
        # Filter repeating margin boilerplate on multi-page docs
        if repeating_margins and (w_box.y0 < page_height * 0.055 or w_box.y1 > page_height * 0.945):
            if text_val.lower() in repeating_margins:
                continue
        words.append(w_box)

    total_chars = sum(len(w.text) for w in words)

    # 1. Check if page is Scanned / Image-only (or hybrid with missing text layer)
    if len(words) < 15 or total_chars < 45:
        ocr_text = _extract_scanned_page_text(page, page_num, total_pages, file_path)
        if ocr_text and len(ocr_text.strip()) > total_chars:
            enriched_ocr = _append_shipping_structured_summary(ocr_text, page_num)
            return f"=== TRANG {page_num}/{total_pages} ===\n{enriched_ocr}"

    if not words:
        return ""

    # 2. Group words into horizontal visual lines (sorted top-to-bottom)
    visual_lines = _cluster_words_into_visual_lines(words, y_tolerance=3.2)

    # Filter out full visual lines that match repeating header/footer boilerplate
    if repeating_margins:
        filtered_lines: list[VisualLine] = []
        for vl in visual_lines:
            if vl.y0 < page_height * 0.06 or vl.y1 > page_height * 0.94:
                norm = _normalize_margin_text(vl.render_plain())
                if norm in repeating_margins:
                    continue
            filtered_lines.append(vl)
        visual_lines = filtered_lines

    # 3. Check if this page looks like a Shipping / Form Document (BL, Sea Waybill, Check List)
    full_plain_probe = "\n".join(vl.render_plain() for vl in visual_lines)
    is_shipping_doc = _is_shipping_or_form_page(full_plain_probe)

    if is_shipping_doc:
        body_text = _reconstruct_shipping_or_form_page(
            page=page,
            words=words,
            visual_lines=visual_lines,
            page_width=page_width,
            page_height=page_height,
        )
        body_text = _append_shipping_structured_summary(body_text, page_num)
    else:
        # Standard corporate document (policies, schedules, SOPs):
        # Use safe table extraction when tables don't slice words, combined with visual lines
        body_text = _reconstruct_standard_document_page(
            page=page,
            words=words,
            visual_lines=visual_lines,
            page_width=page_width,
        )

    return f"=== TRANG {page_num}/{total_pages} ===\n{body_text.strip()}"


# ---------------------------------------------------------------------------
# Visual Line Clustering (Y-coordinate grouping)
# ---------------------------------------------------------------------------


def _cluster_words_into_visual_lines(
    words: list[WordBox],
    y_tolerance: float = 3.2,
) -> list[VisualLine]:
    """Group words into visual horizontal lines based on vertical center / overlap."""
    if not words:
        return []

    # Sort primarily by vertical center, secondarily by x0
    sorted_words = sorted(words, key=lambda w: (round(w.cy, 1), w.x0))
    lines: list[VisualLine] = []

    for w in sorted_words:
        placed = False
        # Check recent lines for vertical alignment
        for line in reversed(lines[-6:]):
            # Same line if vertical centers are within y_tolerance
            # or if vertical overlap is > 55% of the smaller height
            cy_diff = abs(w.cy - line.cy)
            overlap = min(w.y1, line.y1) - max(w.y0, line.y0)
            min_h = min(w.height, max(1.0, line.y1 - line.y0))
            if cy_diff <= y_tolerance or (overlap > 0 and (overlap / min_h) >= 0.55):
                line.words.append(w)
                # Update line vertical bounds smoothly
                line.y0 = min(line.y0, w.y0)
                line.y1 = max(line.y1, w.y1)
                placed = True
                break
        if not placed:
            lines.append(VisualLine(y0=w.y0, y1=w.y1, words=[w]))

    # Sort lines top-to-bottom and words within each line left-to-right
    lines.sort(key=lambda l: (round(l.cy, 1), l.x0))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)

    return lines


# ---------------------------------------------------------------------------
# Shipping / Boxed Form Reconstruction (BL, Sea Waybill, BL Check List)
# ---------------------------------------------------------------------------


def _is_shipping_or_form_page(text_probe: str) -> bool:
    """Detect if a page is a Bill of Lading, Sea Waybill, BL Check List, or Shipping Invoice."""
    upper = text_probe.upper()
    keywords = (
        "SHIPPER",
        "CONSIGNEE",
        "NOTIFY PARTY",
        "PORT OF LOADING",
        "PORT OF DISCHARGE",
        "PLACE OF DELIVERY",
        "SEA WAYBILL",
        "BILL OF LADING",
        "LADEN ON BOARD",
        "ON BOARD",
        "INVOICE NO",
        "G-TOTAL",
        "CHECK LIST",
        "CONTAINER",
        "GROSS WEIGHT",
        "MEASUREMENT",
    )
    hits = sum(1 for kw in keywords if kw in upper)
    if hits >= 2:
        return True
    if _CONTAINER_SIMPLE_RE.search(upper):
        return True
    return False


def _reconstruct_shipping_or_form_page(
    page: Any,
    words: list[WordBox],
    visual_lines: list[VisualLine],
    page_width: float,
    page_height: float,
) -> str:
    """Reconstruct a shipping/form page by combining:
    1. Spatial Box Extraction for form fields (Shipper, Consignee, Ports, Booking/BL No, Dates)
    2. Intact Visual Lines with column separators (preserving container overlay lines)
    """
    sections: list[str] = []

    # Step 1: Extract structured form boxes via Label Anchors + Vector Split Lines
    boxed_fields = _extract_bl_anchor_boxes(words, visual_lines, page, page_width, page_height)
    if boxed_fields:
        box_lines = ["### [THÔNG TIN Ô BIỂU MẪU (FORM BOXES)]"]
        for label, val in boxed_fields:
            clean_val = " | ".join(line.strip() for line in val.splitlines() if line.strip())
            if clean_val:
                box_lines.append(f"- **{label}**: {clean_val}")
        if len(box_lines) > 1:
            sections.append("\n".join(box_lines))

    # Step 2: Render full page in visual layout order (preserving exact horizontal alignment)
    # Also detect if there is a multi-column split in the upper form section so we don't
    # mix left-column address lines with right-column reference numbers.
    layout_lines = _render_layout_aware_lines(visual_lines, page_width)
    if layout_lines:
        sections.append("### [NỘI DUNG CHI TIẾT TRANG (LAYOUT PRESERVED)]\n" + "\n".join(layout_lines))

    return "\n\n".join(sections)


def _extract_bl_anchor_boxes(
    words: list[WordBox],
    visual_lines: list[VisualLine],
    page: Any,
    page_width: float,
    page_height: float,
) -> list[tuple[str, str]]:
    """Locate standard BL form labels and extract the text confined within each label's box."""
    # Find horizontal and vertical grid lines from PDF vector drawings
    h_lines, v_lines = _extract_grid_lines_from_drawings(page, page_width, page_height)

    # Locate label anchors from visual lines (splitting each visual line by wide column gaps)
    anchors: list[dict[str, Any]] = []
    for vl in visual_lines:
        segments = _split_visual_line_into_segments(vl, gap_threshold=14.0)
        for seg_words in segments:
            seg_text = " ".join(w.text for w in seg_words).strip()
            if not seg_text:
                continue
            for canonical_label, pattern in _BL_LABEL_ANCHORS:
                m = pattern.search(seg_text)
                if m:
                    seg_x0 = min(w.x0 for w in seg_words)
                    seg_y0 = min(w.y0 for w in seg_words)
                    seg_x1 = max(w.x1 for w in seg_words)
                    seg_y1 = max(w.y1 for w in seg_words)
                    # Avoid duplicate detections of the exact same label in the same vicinity
                    if any(
                        a["label"] == canonical_label and abs(a["y0"] - seg_y0) < 18.0
                        for a in anchors
                    ):
                        break
                    anchors.append(
                        {
                            "label": canonical_label,
                            "raw_header": seg_text,
                            "match_end": m.end(),
                            "x0": seg_x0,
                            "y0": seg_y0,
                            "x1": seg_x1,
                            "y1": seg_y1,
                            "header_words": set(id(w) for w in seg_words),
                        }
                    )
                    break

    if not anchors:
        return []

    # Sort anchors top-to-bottom, left-to-right
    anchors.sort(key=lambda a: (round(a["y0"] / 6.0), a["x0"]))

    # Also detect where the Cargo Table starts (e.g. "Marks & Nos." or "Description of Goods")
    cargo_top_y = page_height * 0.85
    for vl in visual_lines:
        line_up = vl.render_plain().upper()
        if ("MARKS" in line_up and "NOS" in line_up) or "DESCRIPTION OF GOODS" in line_up:
            cargo_top_y = vl.y0 - 2.0
            break

    extracted: list[tuple[str, str]] = []

    for idx, anc in enumerate(anchors):
        ax0 = anc["x0"] - 4.0
        ay0 = anc["y0"] - 2.0

        # 1. Determine right boundary (box_x1):
        # Look for another anchor on roughly the same row or a vertical grid line to the right
        right_candidates = [page_width - 5.0]
        for other in anchors:
            if other is anc:
                continue
            if other["x0"] > anc["x0"] + 25.0 and abs(other["y0"] - anc["y0"]) < 55.0:
                right_candidates.append(other["x0"] - 3.0)
        for vx, vy0, vy1 in v_lines:
            if vx > anc["x0"] + 35.0 and vy0 <= anc["y1"] + 10.0 and vy1 >= anc["y0"] - 10.0:
                right_candidates.append(vx)
        box_x1 = min(right_candidates)

        # 2. Determine bottom boundary (box_y1):
        bottom_candidates = [page_height - 5.0]
        if anc["y0"] < cargo_top_y:
            bottom_candidates.append(cargo_top_y)

        for other in anchors:
            if other is anc:
                continue
            # Another anchor below that overlaps horizontally with [ax0, box_x1]
            if other["y0"] > anc["y0"] + 8.0:
                horiz_overlap = min(box_x1, other["x1"] + 40.0) - max(ax0, other["x0"])
                if horiz_overlap > 10.0 or abs(other["x0"] - anc["x0"]) < 45.0:
                    bottom_candidates.append(other["y0"] - 2.0)

        for hy, hx0, hx1 in h_lines:
            if hy > anc["y1"] + 4.0 and hx0 <= anc["x0"] + 30.0 and hx1 >= anc["x0"] + 40.0:
                bottom_candidates.append(hy)

        box_y1 = min(bottom_candidates)
        # Cap box height reasonably so a bottom label doesn't swallow the whole footer
        max_h = 95.0 if anc["label"].startswith(("1.", "2.", "3.", "Notify")) else 42.0
        box_y1 = min(box_y1, anc["y0"] + max_h)

        # Collect words inside [ax0, box_x1] x [ay0, box_y1]
        box_words: list[WordBox] = []
        # Check if the header line itself contains inline value after the label
        # (e.g. "Date Laden on Board 4 SEP 2021" or "Date of Issue 4 SEP 2021")
        inline_val = _extract_inline_value_from_header(anc["label"], anc["raw_header"])

        for w in words:
            if id(w) in anc["header_words"]:
                continue
            if ax0 <= w.cx <= box_x1 and anc["y1"] - 2.0 <= w.cy <= box_y1:
                box_words.append(w)

        box_lines = _cluster_words_into_visual_lines(box_words, y_tolerance=3.0)
        lines_str = [bl.render_plain() for bl in box_lines if bl.render_plain()]
        # Strip noisy boilerplate instruction sub-labels inside boxes
        cleaned_lines = [
            ln for ln in lines_str
            if not re.match(
                r"^(?:Insert\s+Name\s+Address|It\s+is\s+agreed\s+that\s+no\s+responsibility|"
                r"to\s+the\s+Carrier\s+or\s+his\s+agents|Subject\s+to\s+Clause\s+7)",
                ln,
                re.I,
            )
        ]
        if inline_val:
            cleaned_lines.insert(0, inline_val)

        final_val = "\n".join(cleaned_lines).strip()
        if final_val:
            extracted.append((anc["label"], final_val))

    return extracted


def _extract_inline_value_from_header(canonical_label: str, raw_header: str) -> str:
    """Extract inline values when label and value sit close together on the same line."""
    # Remove known boilerplate instructions inside header
    cleaned = re.sub(
        r"Insert\s+Name\s+Address\s+and\s+Phone/Fax",
        "",
        raw_header,
        flags=re.I,
    ).strip()

    for lbl, pat in _BL_LABEL_ANCHORS:
        if lbl == canonical_label:
            m = pat.search(cleaned)
            if m:
                remainder = cleaned[m.end() :].strip(" :.-*\t")
                # Ignore boilerplate remainder words or another adjacent label header
                if remainder and not re.match(
                    r"^(?:routing\s*&\s*Instructions|or\s+Packages\s*\(in\s+words\)|No\.?)$",
                    remainder,
                    re.I,
                ):
                    if any(other_pat.search(remainder) for _, other_pat in _BL_LABEL_ANCHORS):
                        return ""
                    return remainder
    return ""


def _split_visual_line_into_segments(
    vl: VisualLine,
    gap_threshold: float = 14.0,
) -> list[list[WordBox]]:
    """Split a visual line into horizontal word clusters separated by >= gap_threshold pt."""
    if not vl.words:
        return []
    sorted_w = sorted(vl.words, key=lambda w: w.x0)
    segments: list[list[WordBox]] = [[sorted_w[0]]]
    prev_x1 = sorted_w[0].x1
    for w in sorted_w[1:]:
        if w.x0 - prev_x1 >= gap_threshold:
            segments.append([w])
        else:
            segments[-1].append(w)
        prev_x1 = max(prev_x1, w.x1)
    return segments


def _extract_grid_lines_from_drawings(
    page: Any,
    page_width: float,
    page_height: float,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Extract horizontal (y, x0, x1) and vertical (x, y0, y1) rule segments from PDF drawings."""
    h_lines: list[tuple[float, float, float]] = []
    v_lines: list[tuple[float, float, float]] = []

    try:
        drawings = page.get_drawings() or []
    except Exception:
        return h_lines, v_lines

    for d in drawings:
        for item in d.get("items", []):
            kind = item[0]
            if kind == "l":  # line segment: ("l", p1, p2)
                p1, p2 = item[1], item[2]
                x0, y0 = float(p1.x), float(p1.y)
                x1, y1 = float(p2.x), float(p2.y)
                if abs(y0 - y1) <= 1.5 and abs(x1 - x0) >= 20.0:
                    h_lines.append(((y0 + y1) * 0.5, min(x0, x1), max(x0, x1)))
                elif abs(x0 - x1) <= 1.5 and abs(y1 - y0) >= 12.0:
                    v_lines.append(((x0 + x1) * 0.5, min(y0, y1), max(y0, y1)))
            elif kind == "re":  # rectangle: ("re", Rect)
                r = item[1]
                rx0, ry0, rx1, ry1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
                w = rx1 - rx0
                h = ry1 - ry0
                if w >= 20.0 and h >= 10.0:
                    h_lines.append((ry0, rx0, rx1))
                    h_lines.append((ry1, rx0, rx1))
                    v_lines.append((rx0, ry0, ry1))
                    v_lines.append((rx1, ry0, ry1))
                elif w >= 20.0 and h <= 2.5:
                    h_lines.append(((ry0 + ry1) * 0.5, rx0, rx1))
                elif h >= 12.0 and w <= 2.5:
                    v_lines.append(((rx0 + rx1) * 0.5, ry0, ry1))

    return h_lines, v_lines


def _render_layout_aware_lines(
    visual_lines: list[VisualLine],
    page_width: float,
) -> list[str]:
    """Render visual lines into clean layout-preserving text lines with column delimiters."""
    rendered: list[str] = []
    for vl in visual_lines:
        line_str = vl.render(col_gap_pt=16.0)
        if line_str:
            rendered.append(line_str)
    return rendered


# ---------------------------------------------------------------------------
# Automatic Structured Summary for Shipping Pages (Containers, Totals, Invoices, HS Code)
# ---------------------------------------------------------------------------


def _parse_float_safe(val: str) -> float:
    """Convert numeric string like '18,929.000' or '18929.000' to float."""
    if not val:
        return 0.0
    clean = val.strip().replace(",", "")
    try:
        return float(clean)
    except ValueError:
        return 0.0


def _append_shipping_structured_summary(page_text: str, page_num: int) -> str:
    """Scan extracted page text for Container rows, Invoice Nos, and HS Codes,
    and append a deterministic structured summary block to aid LLM comparison.
    """
    summary_parts: list[str] = []

    # 1. Extract Container rows (e.g. BEAU2247050 /00001160 / 20 PACKAGES /FCL/FCL /20GP/18929.000KGS/26.000CBM)
    containers: list[dict[str, Any]] = []
    seen_conts: set[str] = set()

    for line in page_text.splitlines():
        m = _CONTAINER_LINE_RE.search(line)
        if m:
            cont_no = m.group(1).upper()
            seal_no = (m.group(2) or "").strip(" /|-")
            pkgs_str = m.group(3) or "0"
            pkg_unit = (m.group(4) or "PACKAGES").upper()
            middle_seg = m.group(5) or ""
            weight_str = m.group(6) or "0"
            cbm_str = m.group(7) or "0"

            term_m = _MOVEMENT_TERM_RE.search(middle_seg) or _MOVEMENT_TERM_RE.search(line)
            movement = term_m.group(1).upper().replace(" ", "") if term_m else ""

            type_m = _CONT_TYPE_RE.search(middle_seg) or _CONT_TYPE_RE.search(line)
            cont_type = type_m.group(1).upper().replace(" ", "") if type_m else ""

            key = f"{cont_no}_{seal_no}_{pkgs_str}_{weight_str}"
            if key in seen_conts:
                continue
            seen_conts.add(key)

            containers.append(
                {
                    "container_no": cont_no,
                    "seal_no": seal_no or "N/A",
                    "pkgs": _parse_float_safe(pkgs_str),
                    "pkgs_raw": f"{pkgs_str} {pkg_unit}",
                    "movement": movement or "N/A",
                    "type": cont_type or "N/A",
                    "weight": _parse_float_safe(weight_str),
                    "weight_raw": f"{weight_str} KGS",
                    "cbm": _parse_float_safe(cbm_str),
                    "cbm_raw": f"{cbm_str} CBM" if cbm_str != "0" else "N/A",
                }
            )

    if containers:
        cont_table = [
            f"### [BẢNG CONTAINER BÓC TÁCH TỰ ĐỘNG - TRANG {page_num}]",
            "| STT | Container No. | Seal No. | P'KGS | Term | Type | Gross Weight | Measurement |",
            "|---|---|---|---|---|---|---|---|",
        ]
        total_pkgs = 0.0
        total_weight = 0.0
        total_cbm = 0.0
        type_counter: Counter[str] = Counter()

        for i, c in enumerate(containers, 1):
            cont_table.append(
                f"| {i} | {c['container_no']} | {c['seal_no']} | {c['pkgs_raw']} | "
                f"{c['movement']} | {c['type']} | {c['weight_raw']} | {c['cbm_raw']} |"
            )
            total_pkgs += c["pkgs"]
            total_weight += c["weight"]
            total_cbm += c["cbm"]
            if c["type"] != "N/A":
                type_counter[c["type"]] += 1

        type_summary = ", ".join(f"{cnt}x{t}" for t, cnt in type_counter.items()) or f"{len(containers)} container"
        pkgs_fmt = int(total_pkgs) if total_pkgs.is_integer() else round(total_pkgs, 3)
        cont_table.append(
            f"| **TỔNG** | **{len(containers)} CONT ({type_summary})** | - | "
            f"**{pkgs_fmt}** | - | - | **{total_weight:.3f} KGS** | **{total_cbm:.4f} CBM** |"
        )
        summary_parts.append("\n".join(cont_table))

    # 2. Detect HS Code presence (Rule 23 check)
    hs_matches = _HS_CODE_RE.findall(page_text)
    if hs_matches:
        unique_hs = sorted(set(hs_matches))
        summary_parts.append(
            f"- **[PHÁT HIỆN HS CODE TRÊN TRANG {page_num}]**: `{', '.join(unique_hs)}` "
            f"(Lưu ý quy tắc 23: Nếu tài liệu này là BL chính thức, BL không được phép hiển thị HS Code -> kết luận **SAI**)."
        )

    # 3. Detect Invoice Numbers (Step 2 check for BL Check List)
    inv_matches: list[str] = []
    for line in page_text.splitlines():
        if "INVOICE" in line.upper():
            for m in _INVOICE_NO_RE.finditer(line):
                raw_inv = m.group(1).strip()
                if raw_inv:
                    inv_matches.append(raw_inv)
    if inv_matches:
        summary_parts.append(
            f"- **[DANH SÁCH INVOICE NO. TRÊN TRANG {page_num}]**: {', '.join(dict.fromkeys(inv_matches))}"
        )

    if not summary_parts:
        return page_text

    return page_text + "\n\n" + "\n\n".join(summary_parts)


# ---------------------------------------------------------------------------
# Standard Corporate Document Reconstruction (Safe Tables + Reading Order)
# ---------------------------------------------------------------------------


def _reconstruct_standard_document_page(
    page: Any,
    words: list[WordBox],
    visual_lines: list[VisualLine],
    page_width: float,
) -> str:
    """Reconstruct standard corporate PDF pages, converting well-formed tables to Markdown
    while falling back to visual lines if a table slices through text spans.
    """
    safe_tables: list[tuple[float, float, str]] = []  # (y0, y1, markdown_str)

    try:
        if hasattr(page, "find_tables"):
            tabs = page.find_tables()
            for tab in getattr(tabs, "tables", []):
                tb_bbox = tab.bbox  # (x0, y0, x1, y1)
                if not _does_table_slice_words(tab, words):
                    md_table = _format_pymupdf_table_to_markdown(tab)
                    if md_table:
                        safe_tables.append((float(tb_bbox[1]), float(tb_bbox[3]), md_table))
    except Exception as e:
        logger.debug("Table detection skipped on page: %s", str(e))

    if not safe_tables:
        return "\n".join(vl.render(col_gap_pt=18.0) for vl in visual_lines if vl.words)

    # Interleave non-table visual lines and safe Markdown tables by vertical position
    safe_tables.sort(key=lambda t: t[0])
    output_blocks: list[tuple[float, str]] = []

    for ty0, ty1, md in safe_tables:
        output_blocks.append((ty0, md))

    for vl in visual_lines:
        in_table = any((ty0 - 2.0) <= vl.cy <= (ty1 + 2.0) for ty0, ty1, _ in safe_tables)
        if not in_table:
            rendered = vl.render(col_gap_pt=18.0)
            if rendered:
                output_blocks.append((vl.y0, rendered))

    output_blocks.sort(key=lambda b: b[0])
    return "\n".join(text for _, text in output_blocks)


def _does_table_slice_words(tab: Any, words: list[WordBox]) -> bool:
    """Check if vertical cell boundaries of a detected PyMuPDF table cut across horizontal word sequences."""
    try:
        cells = getattr(tab, "cells", []) or []
        if not cells:
            return False
        tx0, ty0, tx1, ty1 = [float(v) for v in tab.bbox]
        # Collect internal vertical cell borders
        v_borders = sorted(
            {
                round(float(c[0]), 1)
                for c in cells
                if tx0 + 8.0 < float(c[0]) < tx1 - 8.0
            }
        )
        if not v_borders:
            return False

        table_words = [w for w in words if ty0 <= w.cy <= ty1 and tx0 <= w.cx <= tx1]
        for w in table_words:
            for vx in v_borders:
                # A word bbox is sliced right through by a vertical cell border
                if (w.x0 + 2.5) < vx < (w.x1 - 2.5):
                    return True
        return False
    except Exception:
        return True


def _format_pymupdf_table_to_markdown(tab: Any) -> str:
    """Convert a PyMuPDF table object into a clean Markdown table."""
    try:
        raw_rows = tab.extract() or []
    except Exception:
        return ""

    cleaned_rows: list[list[str]] = []
    for row in raw_rows:
        if not row:
            continue
        cells: list[str] = []
        last_val = ""
        for cell in row:
            val = " ".join(str(cell or "").split()).replace("|", "\\|").strip()
            cells.append(val)
            if val:
                last_val = val
        if any(cells):
            cleaned_rows.append(cells)

    if len(cleaned_rows) < 2:
        return ""

    width = max(len(r) for r in cleaned_rows)
    padded = [r + [""] * (width - len(r)) for r in cleaned_rows]

    headers = [h if h else f"Cột {i + 1}" for i, h in enumerate(padded[0])]
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "|" + "---|" * width
    body_lines = ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join([header_line, sep_line] + body_lines)


# ---------------------------------------------------------------------------
# Repeating Boilerplate Margin Detection
# ---------------------------------------------------------------------------


def _normalize_margin_text(s: str) -> str:
    """Normalize digits so 'Page 1 of 10' and 'Page 2 of 10' match only if pure page number."""
    return " ".join(s.lower().split()).strip()


def _detect_repeating_margin_lines(doc: Any) -> set[str]:
    """Find margin text strings that repeat identically on >= 50% of pages (min 3 pages)."""
    total_pages = len(doc)
    if total_pages < 3:
        return set()

    counts: Counter[str] = Counter()
    for page_idx in range(total_pages):
        page = doc[page_idx]
        h = float(page.rect.height or 800.0)
        raw_words = page.get_text("words") or []
        margin_words = [
            WordBox(float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]).strip())
            for w in raw_words
            if str(w[4]).strip() and (float(w[1]) < h * 0.055 or float(w[3]) > h * 0.945)
        ]
        margin_lines = _cluster_words_into_visual_lines(margin_words, y_tolerance=3.0)
        page_seen: set[str] = set()
        for ml in margin_lines:
            txt = _normalize_margin_text(ml.render_plain())
            # Never strip shipping keywords, totals, or dates
            if len(txt) < 4 or any(
                kw in txt
                for kw in ("shipper", "consignee", "date", "issue", "board", "total", "invoice", "waybill")
            ):
                continue
            page_seen.add(txt)
        for txt in page_seen:
            counts[txt] += 1

    threshold = max(3, int(total_pages * 0.5))
    return {txt for txt, cnt in counts.items() if cnt >= threshold}


# ---------------------------------------------------------------------------
# Scanned / Image PDF Fallback (Local OCR -> Ollama Multimodal Vision)
# ---------------------------------------------------------------------------


def _extract_scanned_page_text(
    page: Any,
    page_num: int,
    total_pages: int,
    file_path: str,
) -> str:
    """Extract text from a scanned/image PDF page via RapidOCR, Tesseract, or Ollama Vision."""
    try:
        pix = page.get_pixmap(dpi=200, alpha=False)
        png_bytes = pix.tobytes("png")
    except Exception as e:
        logger.warning("Failed to render PDF page %d to image for OCR: %s", page_num, str(e))
        return ""

    # 1. Try RapidOCR if installed
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import]

        ocr_engine = RapidOCR()
        result, _ = ocr_engine(png_bytes)
        if result:
            ocr_words: list[WordBox] = []
            for box, text_val, _score in result:
                xs = [ float(pt[0]) for pt in box ]
                ys = [ float(pt[1]) for pt in box ]
                ocr_words.append(
                    WordBox(min(xs), min(ys), max(xs), max(ys), str(text_val).strip())
                )
            lines = _cluster_words_into_visual_lines(ocr_words, y_tolerance=8.0)
            text_out = "\n".join(vl.render(col_gap_pt=25.0) for vl in lines if vl.words)
            if len(text_out.strip()) >= 30:
                logger.info("Extracted scanned page %d of %s via RapidOCR", page_num, file_path)
                return text_out
    except ImportError:
        pass
    except Exception as e:
        logger.debug("RapidOCR failed on page %d: %s", page_num, str(e))

    # 2. Try pytesseract if installed
    try:
        import io
        from PIL import Image  # type: ignore[import]
        import pytesseract  # type: ignore[import]

        img = Image.open(io.BytesIO(png_bytes))
        text_out = pytesseract.image_to_string(img)
        if text_out and len(text_out.strip()) >= 30:
            logger.info("Extracted scanned page %d of %s via pytesseract", page_num, file_path)
            return text_out.strip()
    except ImportError:
        pass
    except Exception as e:
        logger.debug("pytesseract failed on page %d: %s", page_num, str(e))

    # 3. Fallback to Ollama Multimodal Vision API (e.g., gemma4 supports images natively)
    vision_text = _ollama_vision_transcribe_page(png_bytes, page_num)
    if vision_text:
        logger.info("Extracted scanned page %d of %s via Ollama Vision", page_num, file_path)
        return vision_text

    return ""


def _ollama_vision_transcribe_page(png_bytes: bytes, page_num: int) -> str:
    """Use Ollama multimodal chat API to transcribe a scanned document page verbatim."""
    b64_img = base64.b64encode(png_bytes).decode("ascii")
    configured_host = os.environ.get("OLLAMA_HOST", "http://ollama:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")

    # Try configured host first, then localhost fallback for non-Docker local runs
    hosts_to_try = [configured_host]
    if "ollama:" in configured_host:
        hosts_to_try.append("http://127.0.0.1:11434")

    prompt = (
        "Transcribe ALL text from this document page verbatim and accurately. "
        "Preserve all form box labels and their exact values (e.g. [1. Shipper]: ..., "
        "[2. Consignee]: ..., [Booking No.]: ..., [Sea Waybill No.]: ..., "
        "[Port of Loading]: ..., [Port of Discharge]: ..., [Place of Delivery]: ..., "
        "[Date Laden on Board]: ..., [Date of Issue]: ...). "
        "Preserve all cargo descriptions, HS codes, invoice numbers, and every container line "
        "(Container No., Seal No., Packages, Type, Gross Weight, Measurement) in exact detail. "
        "Do NOT summarize or omit any number or code."
    )

    for host in hosts_to_try:
        try:
            with httpx.Client(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
                resp = client.post(
                    f"{host}/api/chat",
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt,
                                "images": [b64_img],
                            }
                        ],
                        "stream": False,
                        "options": {"temperature": 0.0, "num_predict": 2048},
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = (data.get("message") or {}).get("content", "").strip()
                    if content:
                        return content
        except Exception as e:
            logger.debug("Ollama Vision host %s unreachable for page %d: %s", host, page_num, str(e))

    return ""
