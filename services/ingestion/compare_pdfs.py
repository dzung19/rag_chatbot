from __future__ import annotations
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import pymupdf
@dataclass(frozen=True)
class Record:
    key: str
    value: str
    page: int
    source: str
def norm(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())
def extract(path: Path, key_column: int = 0, value_column: int = 1):
    records = defaultdict(list); warnings = []
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, 1):
            if not page.get_text().strip():
                warnings.append(f"Page {number}: no extractable text; OCR required"); continue
            tables = page.find_tables().tables
            boxes = [pymupdf.Rect(table.bbox) for table in tables]
            for ti, table in enumerate(tables, 1):
                for ri, row in enumerate(table.extract(), 1):
                    if len(row) <= max(key_column, value_column): continue
                    key, value = row[key_column], row[value_column]
                    if key and value and norm(key) not in {"parameter", "chỉ tiêu", "item", "項目"}:
                        records[norm(key)].append(Record(str(key).strip(), str(value).strip(), number, f"table {ti} row {ri}"))
            for bi, block in enumerate(page.get_text("dict", sort=True)["blocks"], 1):
                if block.get("type") != 0: continue
                for li, line in enumerate(block.get("lines", []), 1):
                    if any(pymupdf.Rect(line["bbox"]).intersects(box) for box in boxes): continue
                    text = "".join(span["text"] for span in line.get("spans", []))
                    match = re.fullmatch(r"(.{2,100}?)\s*[:：]\s*(\S.*)", text.strip())
                    if match:
                        key, value = match.groups()
                        records[norm(key)].append(Record(key.strip(), value.strip(), number, f"text block {bi} line {li}"))
    return records, warnings
def numeric(raw):
    m = re.fullmatch(r"([+-]?\d+(?:[.,]\d+)?)\s*(\S*)", raw.strip())
    if not m: return None
    value, unit = m.groups()
    if re.search(r"[.,]\d{3}$", value) and not value.startswith(("0.", "0,")): return None
    try: return Decimal(value.replace(",", ".")), norm(unit)
    except InvalidOperation: return None
def compare(left, right):
    output = []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key, []), right.get(key, [])
        if len(a) > 1 or len(b) > 1: status = "ambiguous_duplicate"
        elif not a: status = "missing_in_left"
        elif not b: status = "missing_in_right"
        elif norm(a[0].value) == norm(b[0].value): status = "equal"
        else:
            x, y = numeric(a[0].value), numeric(b[0].value)
            status = ("different_unit" if x[1] != y[1] else "equal" if x[0] == y[0] else "different_value") if x and y else "needs_review"
        output.append({"key": key, "status": status, "left": [asdict(i) for i in a], "right": [asdict(i) for i in b]})
    return output
