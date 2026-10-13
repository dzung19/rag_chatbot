"""High-performance, structure-aware Excel (XLSX) parser and chunker.

Features:
- Uses openpyxl streaming mode (read_only=True, data_only=True) for minimal memory usage.
- Trims trailing empty rows and columns (prevents empty '| | |' explosions).
- Detects multiple tables per sheet separated by blank rows.
- Automatically identifies header rows and handles table titles.
- Handles hidden sheets based on policy (indexes if hidden_sheets < 2).
- Large sheet protection: if sheet > max_rows_per_sheet (default 20,000), indexes summary only.
- Row-window chunking: produces compact Markdown tables with repeated headers, never cuts rows in half.
- Produces a summary chunk per sheet/table for high-level retrieval.
- Generates clean full-document markdown text for SQLite (Business Compare flow).
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)


def _format_cell(val: Any) -> str:
    """Format an Excel cell value into clean, readable text."""
    if val is None:
        return ""
    if isinstance(val, (datetime.datetime, datetime.date)):
        if isinstance(val, datetime.datetime) and (val.hour or val.minute or val.second):
            return val.strftime("%Y-%m-%d %H:%M")
        return val.strftime("%Y-%m-%d")
    if isinstance(val, float):
        if val.is_integer():
            return str(int(val))
        # Round long floating decimals to 4 places, strip trailing zeros
        return f"{val:.4f}".rstrip("0").rstrip(".")
    if isinstance(val, bool):
        return "Có" if val else "Không"
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


def _build_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """Build a clean Markdown table from headers and row cells."""
    if not headers and not rows:
        return ""
    if not headers and rows:
        headers = [f"Cột {i + 1}" for i in range(len(rows[0]))]

    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    data_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, sep_line] + data_lines)


class ExcelParser:
    """Structure-aware parser and chunker for Excel workbooks."""

    def __init__(
        self,
        chunk_chars: int = 1500,
        max_rows_per_sheet: int = 20000,
        max_cols: int = 50,
    ):
        self.chunk_chars = chunk_chars
        self.max_rows_per_sheet = max_rows_per_sheet
        self.max_cols = max_cols

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
        from openpyxl import load_workbook

        text_pages: list[str] = []
        chunks: list[dict] = []
        current_chunk_idx = start_chunk_index

        wb = load_workbook(file_path, read_only=True, data_only=True)

        try:
            # 1. Determine sheet visibility policy
            # Policy: index hidden sheets only if total hidden sheets < 2
            hidden_sheets_count = sum(
                1 for name in wb.sheetnames if getattr(wb[name], "sheet_state", "visible") != "visible"
            )
            allow_hidden = hidden_sheets_count < 2

            for sheet_idx, sheet_name in enumerate(wb.sheetnames, start=1):
                ws = wb[sheet_name]
                sheet_state = getattr(ws, "sheet_state", "visible")
                if sheet_state != "visible" and not allow_hidden:
                    logger.info(
                        "Skipping hidden sheet '%s' in %s (total hidden sheets: %d >= 2)",
                        sheet_name,
                        source_filename,
                        hidden_sheets_count,
                    )
                    continue

                # 2. Extract and sanitize raw rows
                raw_rows: list[list[str]] = []
                for row_tuple in ws.iter_rows(values_only=True):
                    row_strs = [_format_cell(c) for c in row_tuple]
                    raw_rows.append(row_strs)

                if not raw_rows:
                    continue

                # 3. Detect maximum non-empty column index to strip trailing empty columns
                max_active_col = 0
                for r in raw_rows:
                    for c_idx in range(len(r) - 1, -1, -1):
                        if r[c_idx]:
                            if c_idx + 1 > max_active_col:
                                max_active_col = c_idx + 1
                            break

                if max_active_col == 0:
                    continue  # Entire sheet is empty

                # Cap max columns to prevent runaway horizontal width
                active_col_count = min(max_active_col, self.max_cols)

                # Trim rows
                trimmed_rows: list[list[str]] = []
                for r in raw_rows:
                    trimmed = r[:active_col_count]
                    if len(trimmed) < active_col_count:
                        trimmed.extend([""] * (active_col_count - len(trimmed)))
                    trimmed_rows.append(trimmed)

                # 4. Partition rows into tables separated by empty rows
                tables = self._extract_tables_from_rows(trimmed_rows)
                if not tables:
                    continue

                sheet_markdown_parts: list[str] = [f"## Sheet: {sheet_name}"]

                # 5. Process each table in the sheet
                for table_idx, table_data in enumerate(tables, start=1):
                    table_title = table_data["title"] or f"Bảng {table_idx}"
                    headers = table_data["headers"]
                    data_rows = table_data["data_rows"]
                    total_table_rows = len(data_rows)

                    sheet_markdown_parts.append(f"\n### {table_title}")
                    sheet_markdown_parts.append(_build_markdown_table(headers, data_rows))

                    # Check for oversized sheet/table limit (e.g. > 20,000 rows)
                    # Policy: "tạm thời chỉ index summary"
                    if total_table_rows > self.max_rows_per_sheet:
                        logger.warning(
                            "Sheet '%s' table '%s' has %d rows exceeding limit %d. Indexing summary only.",
                            sheet_name,
                            table_title,
                            total_table_rows,
                            self.max_rows_per_sheet,
                        )
                        summary_chunk = self._create_summary_chunk(
                            sheet_name=sheet_name,
                            sheet_idx=sheet_idx,
                            table_title=table_title,
                            headers=headers,
                            total_rows=total_table_rows,
                            sample_rows=data_rows[:20],
                            source_filename=source_filename,
                            chunk_index=current_chunk_idx,
                            is_oversized=True,
                        )
                        chunks.append(summary_chunk)
                        current_chunk_idx += 1
                        continue

                    # Standard table: Always emit 1 Summary Chunk for high-level retrieval
                    summary_chunk = self._create_summary_chunk(
                        sheet_name=sheet_name,
                        sheet_idx=sheet_idx,
                        table_title=table_title,
                        headers=headers,
                        total_rows=total_table_rows,
                        sample_rows=data_rows[:5],
                        source_filename=source_filename,
                        chunk_index=current_chunk_idx,
                        is_oversized=False,
                    )
                    chunks.append(summary_chunk)
                    current_chunk_idx += 1

                    # Row-window chunking into Markdown tables
                    table_chunks = self._chunk_table_rows(
                        sheet_name=sheet_name,
                        sheet_idx=sheet_idx,
                        table_title=table_title,
                        headers=headers,
                        data_rows=data_rows,
                        source_filename=source_filename,
                        start_chunk_index=current_chunk_idx,
                    )
                    chunks.extend(table_chunks)
                    current_chunk_idx += len(table_chunks)

                text_pages.append("\n\n".join(sheet_markdown_parts))

        finally:
            wb.close()

        logger.info(
            "Excel '%s' parsed into %d sheet pages and %d structure-aware chunks",
            source_filename,
            len(text_pages),
            len(chunks),
        )
        return text_pages, chunks

    def _extract_tables_from_rows(self, rows: list[list[str]]) -> list[dict]:
        """Split a list of sheet rows into distinct tables separated by blank rows."""
        tables: list[dict] = []
        current_block: list[list[str]] = []

        for row in rows:
            if _is_row_empty(row):
                if current_block:
                    table = self._process_table_block(current_block)
                    if table:
                        tables.append(table)
                    current_block = []
            else:
                current_block.append(row)

        if current_block:
            table = self._process_table_block(current_block)
            if table:
                tables.append(table)

        return tables

    def _process_table_block(self, block: list[list[str]]) -> Optional[dict]:
        """Analyze a contiguous block of rows to extract title, header, and data rows."""
        if not block:
            return None

        title: Optional[str] = None
        start_idx = 0

        # Check if first row is a title banner (e.g. only 1 or 2 cells filled among many columns)
        first_row_non_empty = [c for c in block[0] if c]
        if len(first_row_non_empty) == 1 and len(block) > 1:
            title = first_row_non_empty[0]
            start_idx = 1

        if start_idx >= len(block):
            return None

        # Detect header row
        candidate_header = block[start_idx]
        # Fill-forward horizontally for blank header cells (common in merged cell reports)
        # NOTE / TODO: Detailed merged cells layout detection requires read_only=False.
        # In streaming read_only mode, we forward-fill adjacent empty header cells.
        filled_header: list[str] = []
        last_val = ""
        for i, h in enumerate(candidate_header):
            val = h if h else last_val
            if not val:
                val = f"Cột {i + 1}"
            filled_header.append(val)
            last_val = val

        data_rows = block[start_idx + 1 :]
        if not data_rows:
            # Table has only 1 row, treat it as data
            headers = [f"Cột {i + 1}" for i in range(len(candidate_header))]
            data_rows = [candidate_header]
        else:
            headers = filled_header

        return {
            "title": title,
            "headers": headers,
            "data_rows": data_rows,
        }

    def _create_summary_chunk(
        self,
        sheet_name: str,
        sheet_idx: int,
        table_title: str,
        headers: list[str],
        total_rows: int,
        sample_rows: list[list[str]],
        source_filename: str,
        chunk_index: int,
        is_oversized: bool = False,
    ) -> dict:
        """Create a high-level summary chunk for the sheet/table."""
        header_parts = []
        if source_filename:
            header_parts.append(f"Nguồn: {source_filename}")
        header_parts.append(f"Sheet: {sheet_name}")
        header_parts.append(f"Tóm tắt: {table_title}")
        context_header = f"[{' | '.join(header_parts)}]\n"

        lines = [
            f"Bảng: {table_title}",
            f"Tổng số dòng: {total_rows} dòng",
            f"Tổng số cột: {len(headers)} cột",
            f"Danh sách các cột: {', '.join(headers)}",
        ]

        if is_oversized:
            lines.append(
                f"(Lưu ý: Bảng này có kích thước lớn với {total_rows} dòng. "
                "Hệ thống tóm tắt cấu trúc và các dòng mẫu dưới đây để tra cứu nhanh)."
            )

        if sample_rows:
            lines.append("\nDữ liệu mẫu:")
            lines.append(_build_markdown_table(headers, sample_rows))

        body = "\n".join(lines)
        full_text = (context_header + body).strip()

        return {
            "text": full_text,
            "raw_text": body,
            "chunk_index": chunk_index,
            "page": sheet_idx,
            "heading": f"{sheet_name} - {table_title} (Tóm tắt)",
            "source": source_filename,
            "context_header": context_header.strip(),
            "metadata": {
                "sheet": sheet_name,
                "table": table_title,
                "is_summary": True,
                "total_rows": total_rows,
                "columns": headers,
            },
        }

    def _chunk_table_rows(
        self,
        sheet_name: str,
        sheet_idx: int,
        table_title: str,
        headers: list[str],
        data_rows: list[list[str]],
        source_filename: str,
        start_chunk_index: int,
    ) -> list[dict]:
        """Group data rows into Markdown table chunks within character limit."""
        chunks: list[dict] = []
        chunk_idx = start_chunk_index

        header_line = "| " + " | ".join(headers) + " |"
        sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
        table_boilerplate_len = len(header_line) + len(sep_line) + 2

        current_rows: list[list[str]] = []
        current_len = table_boilerplate_len
        current_start_row = 1

        for row_num, row_cells in enumerate(data_rows, start=1):
            row_str = "| " + " | ".join(row_cells) + " |"
            row_len = len(row_str) + 1

            # If adding this row exceeds chunk size budget (and we already have at least 1 row)
            if current_rows and (current_len + row_len > self.chunk_chars):
                end_row = row_num - 1
                chunk = self._build_data_chunk(
                    sheet_name=sheet_name,
                    sheet_idx=sheet_idx,
                    table_title=table_title,
                    headers=headers,
                    rows=current_rows,
                    start_row=current_start_row,
                    end_row=end_row,
                    source_filename=source_filename,
                    chunk_index=chunk_idx,
                )
                chunks.append(chunk)
                chunk_idx += 1

                current_rows = [row_cells]
                current_len = table_boilerplate_len + row_len
                current_start_row = row_num
            else:
                current_rows.append(row_cells)
                current_len += row_len

        # Flush remaining rows
        if current_rows:
            end_row = len(data_rows)
            chunk = self._build_data_chunk(
                sheet_name=sheet_name,
                sheet_idx=sheet_idx,
                table_title=table_title,
                headers=headers,
                rows=current_rows,
                start_row=current_start_row,
                end_row=end_row,
                source_filename=source_filename,
                chunk_index=chunk_idx,
            )
            chunks.append(chunk)

        return chunks

    def _build_data_chunk(
        self,
        sheet_name: str,
        sheet_idx: int,
        table_title: str,
        headers: list[str],
        rows: list[list[str]],
        start_row: int,
        end_row: int,
        source_filename: str,
        chunk_index: int,
    ) -> dict:
        """Construct a single Markdown table data chunk."""
        header_parts = []
        if source_filename:
            header_parts.append(f"Nguồn: {source_filename}")
        header_parts.append(f"Sheet: {sheet_name}")
        header_parts.append(f"Bảng: {table_title}")
        header_parts.append(f"Dòng {start_row}–{end_row}")
        context_header = f"[{' | '.join(header_parts)}]\n"

        md_table = _build_markdown_table(headers, rows)
        full_text = (context_header + md_table).strip()

        return {
            "text": full_text,
            "raw_text": md_table,
            "chunk_index": chunk_index,
            "page": sheet_idx,
            "heading": f"{sheet_name} - {table_title} (Dòng {start_row}–{end_row})",
            "source": source_filename,
            "context_header": context_header.strip(),
            "metadata": {
                "sheet": sheet_name,
                "table": table_title,
                "row_start": start_row,
                "row_end": end_row,
                "columns": headers,
            },
        }


def parse_and_chunk_excel(
    file_path: str,
    source_filename: str = "",
    chunk_chars: int = 1500,
    max_rows_per_sheet: int = 20000,
    max_cols: int = 50,
    start_chunk_index: int = 0,
) -> tuple[list[str], list[dict]]:
    """Helper function to parse and chunk Excel file in one call."""
    parser = ExcelParser(
        chunk_chars=chunk_chars,
        max_rows_per_sheet=max_rows_per_sheet,
        max_cols=max_cols,
    )
    return parser.parse_and_chunk(
        file_path=file_path,
        source_filename=source_filename,
        start_chunk_index=start_chunk_index,
    )
