"""High-performance, structure-aware Excel (XLSX) parser and chunker.

Features:
- Uses openpyxl streaming mode (read_only=True, data_only=True) for minimal memory usage.
- Resets bogus sheet dimensions and stops after a long streak of empty rows
  (protects against sheets formatted down to row 1,048,576).
- Detects multiple tables per sheet separated by >= `table_gap_rows` blank rows.
  Tiny blocks (notes, totals, signatures) are attached to the neighbouring table.
- Automatically identifies header rows and handles table titles.
- Drops fully-empty columns and duplicate rows; constant columns are hoisted into
  the chunk context instead of being repeated on every row.
- Dense tables -> compact Markdown tables; sparse tables -> key-value lines with
  only non-empty cells.
- Tiered indexing policy:
    * sheet with <= `full_index_rows` data rows -> row-window chunks (full index)
    * larger sheets -> summary + per-column statistics only ("tạm thời chỉ index summary")
    * per-file chunk budget (`max_chunks_per_file`): largest tables are demoted to
      summary-only until the file fits the budget.
- Handles hidden sheets based on policy (indexes if hidden_sheets < 2).
- Generates clean full-document markdown text for SQLite (Business Compare flow).
  Full text always contains every row, regardless of the vector indexing policy.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _format_cell(val: Any) -> str:
    """Format an Excel cell value into clean, readable text."""
    if val is None:
        return ""
    if isinstance(val, (datetime.datetime, datetime.date)):
        if isinstance(val, datetime.datetime) and (val.hour or val.minute or val.second):
            return val.strftime("%Y-%m-%d %H:%M")
        return val.strftime("%Y-%m-%d")
    if isinstance(val, bool):
        return "Có" if val else "Không"
    if isinstance(val, float):
        if val.is_integer():
            return str(int(val))
        # Round long floating decimals to 4 places, strip trailing zeros
        return f"{val:.4f}".rstrip("0").rstrip(".")
    # String or other types
    s = str(val).strip()
    # Normalize whitespaces and newlines inside cells
    s = " ".join(s.split())
    # Escape pipe characters so markdown table syntax remains valid
    s = s.replace("|", "\\|")
    return s


def _is_row_empty(row: list[str]) -> bool:
    """Check if all cells in a row are empty."""
    return not any(cell for cell in row)


def _md_header(headers: list[str]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "|" + "---|" * len(headers)
    return header_line + "\n" + sep_line


def _md_row(row: list[str]) -> str:
    return "| " + " | ".join(row) + " |"


def _kv_row(headers: list[str], row: list[str]) -> str:
    parts = [f"{h}: {v}" for h, v in zip(headers, row) if v]
    return "- " + "; ".join(parts)


def _build_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """Build a clean Markdown table from headers and row cells."""
    if not headers and not rows:
        return ""
    if not headers and rows:
        headers = [f"Cột {i + 1}" for i in range(len(rows[0]))]
    return "\n".join([_md_header(headers)] + [_md_row(r) for r in rows])


@dataclass
class ExcelTable:
    """A detected table inside a sheet, after cleaning."""

    sheet_name: str
    sheet_idx: int
    table_idx: int
    title: str
    headers: list[str]
    rows: list[list[str]]
    row_numbers: list[int]  # original Excel row numbers, aligned with `rows`
    constants: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sparse: bool = False
    summary_only: bool = False
    data_chunks: list[dict] = field(default_factory=list)


class ExcelParser:
    """Structure-aware parser and chunker for Excel workbooks."""

    def __init__(
        self,
        chunk_chars: int = 4000,
        max_rows_per_sheet: int = 200000,
        max_cols: int = 50,
        full_index_rows: int = 2000,
        max_chunks_per_file: int = 500,
        table_gap_rows: int = 2,
        sparse_threshold: float = 0.4,
        max_empty_streak: int = 1000,
    ):
        self.chunk_chars = chunk_chars
        # Hard scan cap per sheet (rows beyond this are not read at all)
        self.max_rows_per_sheet = max_rows_per_sheet
        self.max_cols = max_cols
        self.full_index_rows = full_index_rows
        self.max_chunks_per_file = max_chunks_per_file
        self.table_gap_rows = max(1, table_gap_rows)
        self.sparse_threshold = sparse_threshold
        self.max_empty_streak = max_empty_streak

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_and_chunk(
        self,
        file_path: str,
        source_filename: str = "",
        start_chunk_index: int = 0,
    ) -> tuple[list[str], list[dict]]:
        """Parse Excel file and return (text_pages, chunks).

        Args:
            file_path: Path to the .xlsx file.
            source_filename: Display name of the file.
            start_chunk_index: Starting global chunk sequence number.

        Returns:
            Tuple of:
            - text_pages: list of cleaned markdown text per sheet (for SQLite full_documents).
            - chunks: list of chunk dicts ready for embeddings and vector storage.
        """
        sheets = self._read_workbook(file_path, source_filename)

        # Phase 1: decide indexing policy per sheet (full vs summary-only)
        for sheet_name, sheet_idx, tables in sheets:
            total_rows = sum(len(t.rows) for t in tables)
            if total_rows > self.full_index_rows:
                logger.info(
                    "Sheet '%s' in %s has %d data rows > %d. Indexing summary only.",
                    sheet_name, source_filename, total_rows, self.full_index_rows,
                )
                for t in tables:
                    t.summary_only = True

        # Phase 2: build data chunks for fully indexed tables
        all_tables = [t for _, _, tables in sheets for t in tables]
        for t in all_tables:
            if not t.summary_only:
                t.data_chunks = self._chunk_table_rows(t, source_filename)

        # Phase 3: enforce per-file chunk budget by demoting the largest tables
        self._enforce_chunk_budget(sheets, source_filename)

        # Phase 4: emit chunks in document order + full text pages
        chunks: list[dict] = []
        text_pages: list[str] = []
        for sheet_name, sheet_idx, tables in sheets:
            if not tables:
                continue
            chunks.append(self._create_sheet_summary_chunk(sheet_name, sheet_idx, tables, source_filename))
            for t in tables:
                if t.summary_only:
                    chunks.append(self._create_table_stats_chunk(t, source_filename))
                else:
                    chunks.extend(t.data_chunks)
            text_pages.append(self._sheet_full_text(sheet_name, tables))

        for i, c in enumerate(chunks):
            c["chunk_index"] = start_chunk_index + i

        logger.info(
            "Excel '%s' parsed into %d sheet pages and %d structure-aware chunks "
            "(%d tables, %d summary-only)",
            source_filename,
            len(text_pages),
            len(chunks),
            len(all_tables),
            sum(1 for t in all_tables if t.summary_only),
        )
        return text_pages, chunks

    # ------------------------------------------------------------------
    # Reading & table detection
    # ------------------------------------------------------------------

    def _read_workbook(self, file_path: str, source_filename: str) -> list[tuple[str, int, list[ExcelTable]]]:
        from openpyxl import load_workbook

        sheets: list[tuple[str, int, list[ExcelTable]]] = []
        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            # Policy: index hidden sheets only if total hidden sheets < 2
            hidden_sheets_count = sum(
                1 for name in wb.sheetnames if getattr(wb[name], "sheet_state", "visible") != "visible"
            )
            allow_hidden = hidden_sheets_count < 2

            for sheet_idx, sheet_name in enumerate(wb.sheetnames, start=1):
                ws = wb[sheet_name]
                if getattr(ws, "sheet_state", "visible") != "visible" and not allow_hidden:
                    logger.info(
                        "Skipping hidden sheet '%s' in %s (total hidden sheets: %d >= 2)",
                        sheet_name, source_filename, hidden_sheets_count,
                    )
                    continue
                tables = self._read_sheet_tables(ws, sheet_name, sheet_idx)
                if tables:
                    sheets.append((sheet_name, sheet_idx, tables))
        finally:
            wb.close()
        return sheets

    def _read_sheet_tables(self, ws: Any, sheet_name: str, sheet_idx: int) -> list[ExcelTable]:
        """Stream rows of a sheet and split them into tables."""
        # Stored dimensions are frequently wrong (e.g. formatted to XFD1048576)
        if hasattr(ws, "reset_dimensions"):
            try:
                ws.reset_dimensions()
            except Exception:  # pragma: no cover - defensive
                pass

        blocks: list[list[tuple[int, list[str]]]] = []
        current: list[tuple[int, list[str]]] = []
        empty_streak = 0

        for row_num, row_tuple in enumerate(ws.iter_rows(values_only=True), start=1):
            if row_num > self.max_rows_per_sheet:
                logger.warning("Sheet '%s' truncated at %d rows (scan cap)", sheet_name, self.max_rows_per_sheet)
                break
            cells = [_format_cell(c) for c in row_tuple[: self.max_cols]]
            # Strip trailing empty cells per row
            while cells and not cells[-1]:
                cells.pop()

            if not cells:
                empty_streak += 1
                if empty_streak >= self.table_gap_rows and current:
                    blocks.append(current)
                    current = []
                if empty_streak >= self.max_empty_streak:
                    break
                continue

            empty_streak = 0
            current.append((row_num, cells))

        if current:
            blocks.append(current)

        tables: list[ExcelTable] = []
        pending_notes: list[str] = []
        for block in blocks:
            if self._is_note_block(block):
                note_lines = [" ".join(c for c in cells if c) for _, cells in block]
                if tables:
                    tables[-1].notes.extend(note_lines)
                else:
                    pending_notes.extend(note_lines)
                continue
            table = self._process_table_block(block, sheet_name, sheet_idx, len(tables) + 1)
            if table:
                if pending_notes:
                    table.notes = pending_notes + table.notes
                    pending_notes = []
                tables.append(table)

        if pending_notes and not tables:
            # Sheet with only short text lines: keep them as a single-column table
            tables.append(
                ExcelTable(
                    sheet_name=sheet_name,
                    sheet_idx=sheet_idx,
                    table_idx=1,
                    title="Ghi chú",
                    headers=["Nội dung"],
                    rows=[[n] for n in pending_notes],
                    row_numbers=list(range(1, len(pending_notes) + 1)),
                    sparse=True,
                )
            )
        return tables

    @staticmethod
    def _is_note_block(block: list[tuple[int, list[str]]]) -> bool:
        """Tiny blocks (<= 2 rows, <= 2 filled cells each) are notes, totals, signatures."""
        if len(block) > 2:
            return False
        return all(sum(1 for c in cells if c) <= 2 for _, cells in block)

    def _process_table_block(
        self,
        block: list[tuple[int, list[str]]],
        sheet_name: str,
        sheet_idx: int,
        table_idx: int,
    ) -> Optional[ExcelTable]:
        """Analyze a contiguous block of rows to extract title, header, and data rows."""
        if not block:
            return None

        title: Optional[str] = None
        start = 0

        # Title banner: first row has a single filled cell
        first_non_empty = [c for c in block[0][1] if c]
        if len(first_non_empty) == 1 and len(block) > 1:
            title = first_non_empty[0]
            start = 1

        body = block[start:]
        if not body:
            return None

        width = max(len(cells) for _, cells in body)
        padded = [(rn, cells + [""] * (width - len(cells))) for rn, cells in body]

        # Detect header row
        # NOTE / TODO: Detailed merged cells layout detection requires read_only=False.
        # In streaming read_only mode, we forward-fill adjacent empty header cells.
        candidate_header = padded[0][1]
        data = padded[1:]
        if not data:
            headers = [f"Cột {i + 1}" for i in range(width)]
            data = padded
        else:
            headers = []
            last_val = ""
            for i, h in enumerate(candidate_header):
                val = h or last_val or f"Cột {i + 1}"
                headers.append(val)
                last_val = val
            headers = self._dedupe_headers(headers)

        # Remove exact duplicate rows (keep first occurrence)
        seen: set[tuple[str, ...]] = set()
        rows: list[list[str]] = []
        row_numbers: list[int] = []
        for rn, cells in data:
            key = tuple(cells)
            if key in seen:
                continue
            seen.add(key)
            rows.append(cells)
            row_numbers.append(rn)

        # Drop columns empty in every data row; hoist constant columns
        keep: list[int] = []
        constants: dict[str, str] = {}
        for ci in range(width):
            col_vals = [r[ci] for r in rows]
            non_empty = [v for v in col_vals if v]
            if not non_empty:
                continue
            if len(rows) >= 3 and len(non_empty) == len(rows) and len(set(non_empty)) == 1:
                constants[headers[ci]] = non_empty[0]
                continue
            keep.append(ci)

        if not keep and not constants:
            return None
        if not keep:
            # Every column is constant: keep them as data so the table is not lost
            keep = [ci for ci in range(width) if headers[ci] in constants]
            constants = {}

        headers = [headers[ci] for ci in keep]
        rows = [[r[ci] for ci in keep] for r in rows]

        total_cells = len(rows) * len(headers)
        filled = sum(1 for r in rows for v in r if v)
        sparse = total_cells > 0 and (filled / total_cells) < self.sparse_threshold

        return ExcelTable(
            sheet_name=sheet_name,
            sheet_idx=sheet_idx,
            table_idx=table_idx,
            title=title or f"Bảng {table_idx}",
            headers=headers,
            rows=rows,
            row_numbers=row_numbers,
            constants=constants,
            sparse=sparse,
        )

    @staticmethod
    def _dedupe_headers(headers: list[str]) -> list[str]:
        counts: Counter[str] = Counter()
        result: list[str] = []
        for h in headers:
            counts[h] += 1
            result.append(h if counts[h] == 1 else f"{h} ({counts[h]})")
        return result

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------

    def _enforce_chunk_budget(self, sheets: list[tuple[str, int, list[ExcelTable]]], source_filename: str) -> None:
        def total_chunks() -> int:
            n = 0
            for _, _, tables in sheets:
                n += 1  # sheet summary
                for t in tables:
                    n += 1 if t.summary_only else len(t.data_chunks)
            return n

        total = total_chunks()
        if total <= self.max_chunks_per_file:
            return

        candidates = sorted(
            (t for _, _, tables in sheets for t in tables if not t.summary_only),
            key=lambda t: len(t.data_chunks),
            reverse=True,
        )
        for t in candidates:
            if total <= self.max_chunks_per_file:
                break
            if len(t.data_chunks) <= 1:
                break  # demoting would not reduce the count
            total -= len(t.data_chunks) - 1
            t.summary_only = True
            t.data_chunks = []
            logger.info(
                "Chunk budget %d exceeded for %s: demoted '%s / %s' (%d rows) to summary-only",
                self.max_chunks_per_file, source_filename, t.sheet_name, t.title, len(t.rows),
            )

        if total > self.max_chunks_per_file:
            logger.warning(
                "Excel %s still has %d chunks after demotion (budget %d)",
                source_filename, total, self.max_chunks_per_file,
            )

    # ------------------------------------------------------------------
    # Chunk builders
    # ------------------------------------------------------------------

    @staticmethod
    def _context_header(source_filename: str, *parts: str) -> str:
        items = [p for p in ([source_filename] if source_filename else []) + list(parts) if p]
        return "[" + " › ".join(items) + "]"

    @staticmethod
    def _constants_line(constants: dict[str, str]) -> str:
        if not constants:
            return ""
        return "Giá trị chung: " + "; ".join(f"{k}: {v}" for k, v in constants.items())

    def _chunk_table_rows(self, t: ExcelTable, source_filename: str) -> list[dict]:
        """Group data rows into chunks within the character budget (never splits a row)."""
        chunks: list[dict] = []
        constants_line = self._constants_line(t.constants)
        md_head = "" if t.sparse else _md_header(t.headers)

        # Fixed overhead: context header (~ estimate with widest row label) + constants + table header
        base_overhead = (
            len(self._context_header(source_filename, t.sheet_name, t.title, "dòng 1000000–1000000"))
            + (len(constants_line) + 1 if constants_line else 0)
            + (len(md_head) + 1 if md_head else 0)
            + 1
        )
        budget = max(self.chunk_chars - base_overhead, 200)

        cur_lines: list[str] = []
        cur_start: Optional[int] = None
        cur_end: Optional[int] = None
        cur_len = 0

        def flush() -> None:
            nonlocal cur_lines, cur_start, cur_end, cur_len
            if cur_lines:
                chunks.append(self._build_data_chunk(t, cur_lines, cur_start, cur_end, source_filename))
            cur_lines, cur_start, cur_end, cur_len = [], None, None, 0

        for row, rn in zip(t.rows, t.row_numbers):
            line = _kv_row(t.headers, row) if t.sparse else _md_row(row)
            line_len = len(line) + 1

            if line_len > budget:
                # Oversized single row: flush, then split the row as key-value pieces
                flush()
                kv = _kv_row(t.headers, row)
                for i in range(0, len(kv), budget):
                    chunks.append(self._build_data_chunk(t, [kv[i : i + budget]], rn, rn, source_filename, force_kv=True))
                continue

            if cur_lines and cur_len + line_len > budget:
                flush()
            if cur_start is None:
                cur_start = rn
            cur_end = rn
            cur_lines.append(line)
            cur_len += line_len

        flush()
        return chunks

    def _build_data_chunk(
        self,
        t: ExcelTable,
        lines: list[str],
        start_row: Optional[int],
        end_row: Optional[int],
        source_filename: str,
        force_kv: bool = False,
    ) -> dict:
        row_label = f"dòng {start_row}–{end_row}" if start_row != end_row else f"dòng {start_row}"
        context_header = self._context_header(source_filename, t.sheet_name, t.title, row_label)

        body_parts: list[str] = []
        constants_line = self._constants_line(t.constants)
        if constants_line:
            body_parts.append(constants_line)
        if t.sparse or force_kv:
            body_parts.extend(lines)
        else:
            body_parts.append(_md_header(t.headers))
            body_parts.extend(lines)
        body = "\n".join(body_parts)

        return {
            "text": f"{context_header}\n{body}".strip(),
            "raw_text": body,
            "chunk_index": 0,  # assigned at emit time
            "page": t.sheet_idx,
            "heading": f"{t.sheet_name} - {t.title} ({row_label})",
            "source": source_filename,
            "context_header": context_header,
            "metadata": {
                "sheet": t.sheet_name,
                "table": t.title,
                "row_start": start_row,
                "row_end": end_row,
                "columns": t.headers,
            },
        }

    def _create_sheet_summary_chunk(
        self,
        sheet_name: str,
        sheet_idx: int,
        tables: list[ExcelTable],
        source_filename: str,
    ) -> dict:
        """One overview chunk per sheet listing all its tables."""
        context_header = self._context_header(source_filename, sheet_name, "Tóm tắt sheet")
        total_rows = sum(len(t.rows) for t in tables)
        lines = [
            f"Sheet: {sheet_name}",
            f"Số bảng: {len(tables)} | Tổng số dòng dữ liệu: {total_rows}",
        ]
        for t in tables:
            mode = "chỉ tóm tắt" if t.summary_only else "đầy đủ"
            lines.append(f"- {t.title}: {len(t.rows)} dòng, {len(t.headers)} cột ({mode})")
            lines.append(f"  Cột: {', '.join(t.headers)}")
            constants_line = self._constants_line(t.constants)
            if constants_line:
                lines.append(f"  {constants_line}")
            for note in t.notes[:5]:
                lines.append(f"  Ghi chú: {note}")

        body = self._truncate("\n".join(lines))
        return {
            "text": f"{context_header}\n{body}".strip(),
            "raw_text": body,
            "chunk_index": 0,
            "page": sheet_idx,
            "heading": f"{sheet_name} (Tóm tắt)",
            "source": source_filename,
            "context_header": context_header,
            "metadata": {
                "sheet": sheet_name,
                "is_summary": True,
                "total_rows": total_rows,
                "tables": [t.title for t in tables],
            },
        }

    def _create_table_stats_chunk(self, t: ExcelTable, source_filename: str) -> dict:
        """Summary + per-column statistics for a table that is not fully indexed."""
        context_header = self._context_header(source_filename, t.sheet_name, t.title, "Thống kê")
        lines = [
            f"Bảng: {t.title}",
            f"Tổng số dòng: {len(t.rows)} | Tổng số cột: {len(t.headers)}",
            "(Bảng lớn: chỉ lưu cấu trúc và thống kê các cột để tra cứu nhanh.)",
        ]
        constants_line = self._constants_line(t.constants)
        if constants_line:
            lines.append(constants_line)
        lines.append("Thống kê các cột:")
        for ci, h in enumerate(t.headers):
            lines.append(f"- {h}: {self._column_stats([r[ci] for r in t.rows])}")

        sample = t.rows[:3]
        if sample:
            lines.append("Dữ liệu mẫu:")
            if t.sparse:
                lines.extend(_kv_row(t.headers, r) for r in sample)
            else:
                lines.append(_build_markdown_table(t.headers, sample))

        body = self._truncate("\n".join(lines))
        return {
            "text": f"{context_header}\n{body}".strip(),
            "raw_text": body,
            "chunk_index": 0,
            "page": t.sheet_idx,
            "heading": f"{t.sheet_name} - {t.title} (Tóm tắt)",
            "source": source_filename,
            "context_header": context_header,
            "metadata": {
                "sheet": t.sheet_name,
                "table": t.title,
                "is_summary": True,
                "total_rows": len(t.rows),
                "columns": t.headers,
            },
        }

    @staticmethod
    def _column_stats(values: list[str]) -> str:
        non_empty = [v for v in values if v]
        if not non_empty:
            return "trống"
        n = len(non_empty)

        nums = [float(v) for v in non_empty if _NUM_RE.match(v)]
        if len(nums) >= 0.8 * n:
            total = sum(nums)
            fmt = lambda x: _format_cell(float(x))  # noqa: E731
            return (
                f"số; min {fmt(min(nums))}, max {fmt(max(nums))}, "
                f"tổng {fmt(total)}, trung bình {fmt(total / len(nums))}"
            )

        dates = sorted(v[:10] for v in non_empty if _DATE_RE.match(v))
        if len(dates) >= 0.8 * n:
            return f"ngày; từ {dates[0]} đến {dates[-1]}"

        counter = Counter(non_empty)
        top = [v if len(v) <= 40 else v[:37] + "..." for v, _ in counter.most_common(10)]
        return f"{len(counter)} giá trị khác nhau; phổ biến: {', '.join(top)}"

    def _truncate(self, text: str) -> str:
        limit = self.chunk_chars
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    # ------------------------------------------------------------------
    # Full text (SQLite)
    # ------------------------------------------------------------------

    @staticmethod
    def _sheet_full_text(sheet_name: str, tables: list[ExcelTable]) -> str:
        parts = [f"## Sheet: {sheet_name}"]
        for t in tables:
            parts.append(f"\n### {t.title}")
            constants_line = ExcelParser._constants_line(t.constants)
            if constants_line:
                parts.append(constants_line)
            parts.append(_build_markdown_table(t.headers, t.rows))
            for note in t.notes:
                parts.append(f"Ghi chú: {note}")
        return "\n\n".join(parts)


def parse_and_chunk_excel(
    file_path: str,
    source_filename: str = "",
    chunk_chars: int = 4000,
    max_rows_per_sheet: int = 200000,
    max_cols: int = 50,
    start_chunk_index: int = 0,
    full_index_rows: int = 2000,
    max_chunks_per_file: int = 500,
    table_gap_rows: int = 2,
    sparse_threshold: float = 0.4,
) -> tuple[list[str], list[dict]]:
    """Helper function to parse and chunk Excel file in one call."""
    parser = ExcelParser(
        chunk_chars=chunk_chars,
        max_rows_per_sheet=max_rows_per_sheet,
        max_cols=max_cols,
        full_index_rows=full_index_rows,
        max_chunks_per_file=max_chunks_per_file,
        table_gap_rows=table_gap_rows,
        sparse_threshold=sparse_threshold,
    )
    return parser.parse_and_chunk(
        file_path=file_path,
        source_filename=source_filename,
        start_chunk_index=start_chunk_index,
    )
