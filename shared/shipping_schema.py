"""Structured 2D spatial schema for shipping documents (B/L Check List & B/L / Sea Waybill).

Shared between the Ingestion Service (`pdf_parser.py`) and RAG Engine (`compare_engine.py`)
so 2D coordinate-extracted fields, tables, containers, and totals pass cleanly without
re-parsing flattened text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Optional

STRUCTURED_DATA_TAG = "### [STRUCTURED_SHIPPING_DATA_JSON]"
STRUCTURED_DATA_END_TAG = "### [/STRUCTURED_SHIPPING_DATA_JSON]"

_STRUCTURED_BLOCK_RE = re.compile(
    r"\n*### \[STRUCTURED_SHIPPING_DATA_JSON\]\s*\n(.*?)\n### \[/STRUCTURED_SHIPPING_DATA_JSON\]\s*",
    re.DOTALL,
)


@dataclass
class BoundingBox:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_dict(cls, raw: Optional[dict[str, Any]]) -> Optional[BoundingBox]:
        if not isinstance(raw, dict):
            return None
        return cls(
            page=int(raw.get("page", 1)),
            x0=float(raw.get("x0", 0.0)),
            y0=float(raw.get("y0", 0.0)),
            x1=float(raw.get("x1", 0.0)),
            y1=float(raw.get("y1", 0.0)),
        )


@dataclass
class ExtractedField:
    value: str = ""
    raw_lines: list[str] = field(default_factory=list)
    source_label: str = ""
    is_empty: bool = False
    bbox: Optional[BoundingBox] = None

    @classmethod
    def from_dict(cls, raw: Optional[dict[str, Any]]) -> Optional[ExtractedField]:
        if not isinstance(raw, dict):
            return None
        return cls(
            value=str(raw.get("value", "") or ""),
            raw_lines=[str(x) for x in (raw.get("raw_lines") or [])],
            source_label=str(raw.get("source_label", "") or ""),
            is_empty=bool(raw.get("is_empty", False)),
            bbox=BoundingBox.from_dict(raw.get("bbox")),
        )


@dataclass
class ExtractedContainer:
    container_no: str
    seal_no: str = "N/A"
    type_raw: str = "N/A"
    type_norm: str = "N/A"
    pkgs_num: int = 0
    pkgs_raw: str = "N/A"
    weight_num: float = 0.0
    weight_raw: str = "N/A"
    cbm_num: float = 0.0
    cbm_raw: str = "N/A"
    movement_raw: str = "N/A"
    movement_norm: str = "N/A"
    sets_raw: str = "N/A"
    goods_descriptions: list[str] = field(default_factory=list)

    def to_compare_dict(self) -> dict[str, Any]:
        """Convert to the container dict shape expected by compare_engine."""
        return {
            "container_no": self.container_no,
            "seal_no": self.seal_no,
            "sets_raw": self.sets_raw,
            "pkgs_num": self.pkgs_num,
            "pkgs_raw": self.pkgs_raw,
            "type": self.type_norm,
            "type_raw": self.type_raw,
            "movement": self.movement_norm,
            "movement_raw": self.movement_raw,
            "weight_num": self.weight_num,
            "weight_raw": self.weight_raw,
            "cbm_num": self.cbm_num,
            "cbm_raw": self.cbm_raw,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExtractedContainer:
        return cls(
            container_no=str(raw.get("container_no", "")),
            seal_no=str(raw.get("seal_no", "N/A")),
            type_raw=str(raw.get("type_raw", "N/A")),
            type_norm=str(raw.get("type_norm", "N/A")),
            pkgs_num=int(raw.get("pkgs_num", 0) or 0),
            pkgs_raw=str(raw.get("pkgs_raw", "N/A")),
            weight_num=float(raw.get("weight_num", 0.0) or 0.0),
            weight_raw=str(raw.get("weight_raw", "N/A")),
            cbm_num=float(raw.get("cbm_num", 0.0) or 0.0),
            cbm_raw=str(raw.get("cbm_raw", "N/A")),
            movement_raw=str(raw.get("movement_raw", "N/A")),
            movement_norm=str(raw.get("movement_norm", "N/A")),
            sets_raw=str(raw.get("sets_raw", "N/A")),
            goods_descriptions=[str(x) for x in (raw.get("goods_descriptions") or [])],
        )


@dataclass
class GrandTotalData:
    sets: int = 0
    pkgs: int = 0
    cartons: int = 0
    weight: float = 0.0
    cbm: float = 0.0

    @classmethod
    def from_dict(cls, raw: Optional[dict[str, Any]]) -> GrandTotalData:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            sets=int(raw.get("sets", 0) or 0),
            pkgs=int(raw.get("pkgs", 0) or 0),
            cartons=int(raw.get("cartons", 0) or 0),
            weight=float(raw.get("weight", 0.0) or 0.0),
            cbm=float(raw.get("cbm", 0.0) or 0.0),
        )


@dataclass
class ShippingDocumentData:
    doc_role: str = "unknown"
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    containers: list[ExtractedContainer] = field(default_factory=list)
    goods_descriptions: list[str] = field(default_factory=list)
    detail_invoices: list[str] = field(default_factory=list)
    gtotal_invoices: list[str] = field(default_factory=list)
    gtotal_raw: str = ""
    grand_total: GrandTotalData = field(default_factory=GrandTotalData)

    def set_field_if_missing(self, key: str, extracted: ExtractedField) -> None:
        """Store field value without letting an empty box overwrite a non-empty value."""
        existing = self.fields.get(key)
        if existing and not existing.is_empty and existing.value.strip():
            return
        if existing and (extracted.is_empty or not extracted.value.strip()):
            return
        self.fields[key] = extracted

    def get_value(self, key: str) -> str:
        f = self.fields.get(key)
        return f.value.strip() if f and not f.is_empty else ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ShippingDocumentData:
        fields: dict[str, ExtractedField] = {}
        for k, v in (raw.get("fields") or {}).items():
            ef = ExtractedField.from_dict(v)
            if ef is not None:
                fields[str(k)] = ef
        return cls(
            doc_role=str(raw.get("doc_role", "unknown")),
            fields=fields,
            containers=[
                ExtractedContainer.from_dict(c)
                for c in (raw.get("containers") or [])
                if isinstance(c, dict)
            ],
            goods_descriptions=[str(x) for x in (raw.get("goods_descriptions") or [])],
            detail_invoices=[str(x) for x in (raw.get("detail_invoices") or [])],
            gtotal_invoices=[str(x) for x in (raw.get("gtotal_invoices") or [])],
            gtotal_raw=str(raw.get("gtotal_raw", "") or ""),
            grand_total=GrandTotalData.from_dict(raw.get("grand_total")),
        )


def serialize_shipping_data_block(data: ShippingDocumentData) -> str:
    """Serialize ShippingDocumentData into a tagged JSON block for full_documents storage."""
    payload = json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"{STRUCTURED_DATA_TAG}\n{payload}\n{STRUCTURED_DATA_END_TAG}"


def extract_shipping_data_block(full_text: str) -> Optional[ShippingDocumentData]:
    """Parse ShippingDocumentData from a stored document's full_text if present."""
    if not full_text or STRUCTURED_DATA_TAG not in full_text:
        return None
    m = _STRUCTURED_BLOCK_RE.search(full_text)
    if not m:
        return None
    try:
        raw = json.loads(m.group(1).strip())
        if isinstance(raw, dict):
            return ShippingDocumentData.from_dict(raw)
    except Exception:
        return None
    return None


def strip_shipping_data_block(full_text: str) -> str:
    """Remove the hidden structured JSON block before sending text to the LLM prompt."""
    if not full_text or STRUCTURED_DATA_TAG not in full_text:
        return full_text
    return _STRUCTURED_BLOCK_RE.sub("\n", full_text).strip()
