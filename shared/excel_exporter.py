"""Excel Exporter for Document Comparison Results.

Parses comparison tables (Table 1 - Điểm đúng, Table 2 - Điểm cần xử lý)
and generates a professional 2-sheet Excel file (.xlsx) using openpyxl.
"""

from __future__ import annotations

import io
import re
from typing import Any, List, Tuple
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def reconcile_compare_counts(md: str) -> str:
    """Reconcile table summary counts and ensure strict separation between Table 1 and Table 2."""
    if not md:
        return md

    t1_match = re.search(
        r"(##\s*1\.\s*T[^\n]*\n+)(\|.*?\n\|[-:\s|]+\n)(.*?)(\n+\*\*T[^\n]*?\n|(?=##\s*2|\Z))",
        md,
        re.DOTALL | re.I,
    )
    t2_match = re.search(
        r"(##\s*2\.\s*[^|\n]*\n+)(\|.*?\n\|[-:\s|]+\n)(.*?)(\n+\*\*T[^\n]*?\n|\Z)",
        md,
        re.DOTALL | re.I,
    )

    if not t1_match or not t2_match:
        return md

    t1_head, t1_cols, t1_body, t1_foot = t1_match.groups()
    t2_head, t2_cols, t2_body, t2_foot = t2_match.groups()

    def get_rows(body: str) -> list[str]:
        return [
            l.strip()
            for l in body.splitlines()
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
        if any(
            k in res_col
            for k in [
                "SAI",
                "KHÔNG TÌM THẤY",
                "KHONG TIM THAY",
                "KHÔNG HIỂU",
                "KHONG HIEU",
            ]
        ):
            while len(parts) < 8:
                parts.append("")
            clean_t2.append("| " + " | ".join(parts[1:7]) + " |")
        else:
            clean_t1.append(r)

    # 2. Check Table 2: move any ĐÚNG rows to Table 1
    for r in t2_rows:
        parts = [p.strip() for p in r.split("|")]
        res_col = parts[5].upper() if len(parts) >= 6 else r.upper()
        if ("ĐÚNG" in res_col or "DUNG" in res_col) and not any(
            k in res_col
            for k in [
                "SAI",
                "KHÔNG TÌM THẤY",
                "KHONG TIM THAY",
                "KHÔNG HIỂU",
                "KHONG HIEU",
            ]
        ):
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
    new_t1 = (
        f"{t1_head}{t1_cols}"
        + (nl.join(clean_t1) + nl if clean_t1 else "")
        + f"{nl}**Tổng số điểm đúng: {actual_dung}**{nl}"
    )
    new_t2 = (
        f"{t2_head}{t2_cols}"
        + (nl.join(clean_t2) + nl if clean_t2 else "")
        + f"{nl}**Tổng số điểm SAI: {sai_count} | KHÔNG TÌM THẤY: {miss_count} | KHÔNG HIỂU: {unk_count}**{nl}"
    )

    prefix = md[: t1_match.start(1)]
    middle = md[t1_match.end(0) : t2_match.start(1)]
    suffix = md[t2_match.end(0) :]
    return f"{prefix}{new_t1}{middle}{new_t2}{suffix}"


def _clean_markdown_text(text: str) -> str:
    """Strip bold, italic, inline code backticks, and clean whitespace."""
    if not text:
        return ""
    # Strip markdown bold & italic
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    # Strip backticks
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    # Normalize spaces
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def parse_compare_tables(md: str) -> Tuple[List[List[str]], str, List[List[str]], str]:
    """Parse Markdown comparison tables after strict reconciliation.

    Returns:
        (t1_rows, t1_summary, t2_rows, t2_summary)
    """
    reconciled = reconcile_compare_counts(md)

    t1_match = re.search(
        r"(##\s*1\.\s*T[^\n]*\n+)(\|.*?\n\|[-:\s|]+\n)(.*?)(\n+\*\*T[^\n]*?\n|(?=##\s*2|\Z))",
        reconciled,
        re.DOTALL | re.I,
    )
    t2_match = re.search(
        r"(##\s*2\.\s*[^|\n]*\n+)(\|.*?\n\|[-:\s|]+\n)(.*?)(\n+\*\*T[^\n]*?\n|\Z)",
        reconciled,
        re.DOTALL | re.I,
    )

    t1_rows: List[List[str]] = []
    t1_summary = ""
    t2_rows: List[List[str]] = []
    t2_summary = ""

    def extract_rows(body: str, expected_cols: int) -> List[List[str]]:
        res: List[List[str]] = []
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("|") or re.match(r"^\|\s*[-:]+\s*\|", line):
                continue
            parts = [p.strip() for p in line.split("|")]
            # parts has leading and trailing empty strings due to outer '|'
            row_data = parts[1:-1] if len(parts) >= 3 else parts
            if not row_data or any(re.match(r"^STT$", p, re.I) for p in row_data):
                continue
            cleaned_row = [_clean_markdown_text(cell) for cell in row_data]
            # Pad if needed
            while len(cleaned_row) < expected_cols:
                cleaned_row.append("")
            res.append(cleaned_row[:expected_cols])
        return res

    if t1_match:
        t1_rows = extract_rows(t1_match.group(3), expected_cols=5)
        # Check summary in foot
        t1_foot = t1_match.group(4).strip()
        m_sum1 = re.search(r"\*\*(Tổng số điểm đúng:[^\*]+)\*\*", t1_foot, re.I)
        if m_sum1:
            t1_summary = m_sum1.group(1).strip()
        else:
            t1_summary = f"Tổng số điểm đúng: {len(t1_rows)}"

    if t2_match:
        t2_rows = extract_rows(t2_match.group(3), expected_cols=6)
        t2_foot = t2_match.group(4).strip()
        m_sum2 = re.search(r"\*\*(Tổng số điểm SAI:[^\*]+)\*\*", t2_foot, re.I)
        if m_sum2:
            t2_summary = m_sum2.group(1).strip()
        else:
            t2_summary = f"Tổng số điểm cần xử lý: {len(t2_rows)}"

    return t1_rows, t1_summary, t2_rows, t2_summary


def generate_compare_excel_bytes(md_content: str, title: str = "Báo cáo đối chiếu") -> bytes:
    """Generate Excel binary bytes containing 2 sheets:
    1. Điểm đúng
    2. Điểm sai & Không tìm thấy
    """
    t1_rows, t1_summary, t2_rows, t2_summary = parse_compare_tables(md_content)

    wb = Workbook()

    # Style definitions
    font_family = "Segoe UI"
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name=font_family, size=11, bold=True, color="FFFFFF")

    summary_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    summary_font = Font(name=font_family, size=11, bold=True, color="1F4E78")

    data_font = Font(name=font_family, size=10)
    data_font_bold = Font(name=font_family, size=10, bold=True)

    # Status fills & fonts
    status_dung_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    status_dung_font = Font(name=font_family, size=10, bold=True, color="375623")

    status_sai_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    status_sai_font = Font(name=font_family, size=10, bold=True, color="C00000")

    status_kt_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    status_kt_font = Font(name=font_family, size=10, bold=True, color="B25900")

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    thick_bottom_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="medium", color="1F4E78"),
    )

    align_center = Alignment(horizontal="center", vertical="top", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="top", wrap_text=True)

    # ----------------------------------------------------
    # Sheet 1: Điểm đúng
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "Điểm đúng"
    ws1.views.sheetView[0].showGridLines = True

    # Title row
    ws1.merge_cells("A1:E1")
    cell_t1 = ws1["A1"]
    cell_t1.value = f"BẢNG 1: TỔNG HỢP ĐIỂM ĐÚNG - {title.upper()}"
    cell_t1.font = Font(name=font_family, size=13, bold=True, color="1F4E78")
    cell_t1.alignment = Alignment(horizontal="left", vertical="center")
    ws1.row_dimensions[1].height = 28

    headers1 = ["STT", "Hạng mục check", "Dữ liệu BL Check List", "Dữ liệu BL", "Kết quả"]
    ws1.append(headers1)
    ws1.row_dimensions[2].height = 24

    for col_idx in range(1, len(headers1) + 1):
        cell = ws1.cell(row=2, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border

    # Data rows
    curr_row = 3
    for row in t1_rows:
        ws1.append(row)
        ws1.row_dimensions[curr_row].height = 22

        ws1.cell(row=curr_row, column=1).alignment = align_center  # STT
        ws1.cell(row=curr_row, column=1).font = data_font_bold
        ws1.cell(row=curr_row, column=1).border = thin_border

        ws1.cell(row=curr_row, column=2).alignment = align_left    # Hạng mục
        ws1.cell(row=curr_row, column=2).font = data_font_bold
        ws1.cell(row=curr_row, column=2).border = thin_border

        ws1.cell(row=curr_row, column=3).alignment = align_left    # Dữ liệu CL
        ws1.cell(row=curr_row, column=3).font = data_font
        ws1.cell(row=curr_row, column=3).border = thin_border

        ws1.cell(row=curr_row, column=4).alignment = align_left    # Dữ liệu BL
        ws1.cell(row=curr_row, column=4).font = data_font
        ws1.cell(row=curr_row, column=4).border = thin_border

        res_cell = ws1.cell(row=curr_row, column=5)               # Kết quả
        res_cell.alignment = align_center
        res_cell.fill = status_dung_fill
        res_cell.font = status_dung_font
        res_cell.border = thin_border

        curr_row += 1

    # Summary row Sheet 1
    if not t1_rows:
        ws1.append(["", "Không có hạng mục nào đúng.", "", "", ""])
        for col_idx in range(1, 6):
            ws1.cell(row=curr_row, column=col_idx).border = thin_border
            ws1.cell(row=curr_row, column=col_idx).font = data_font
        curr_row += 1

    ws1.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=5)
    sum_cell1 = ws1.cell(row=curr_row, column=1)
    sum_cell1.value = t1_summary or f"Tổng số điểm đúng: {len(t1_rows)}"
    sum_cell1.fill = summary_fill
    sum_cell1.font = summary_font
    sum_cell1.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[curr_row].height = 25
    for c in range(1, 6):
        ws1.cell(row=curr_row, column=c).border = thick_bottom_border

    # Adjust column widths for Sheet 1
    col_widths1 = {1: 8, 2: 30, 3: 45, 4: 45, 5: 18}
    for col_idx, width in col_widths1.items():
        ws1.column_dimensions[get_column_letter(col_idx)].width = width

    # ----------------------------------------------------
    # Sheet 2: Điểm sai & Không tìm thấy
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="Điểm sai & Không tìm thấy")
    ws2.views.sheetView[0].showGridLines = True

    # Title row
    ws2.merge_cells("A1:F1")
    cell_t2 = ws2["A1"]
    cell_t2.value = f"BẢNG 2: ĐIỂM CẦN XỬ LÝ (SAI / KHÔNG TÌM THẤY / KHÔNG HIỂU) - {title.upper()}"
    cell_t2.font = Font(name=font_family, size=13, bold=True, color="C00000")
    cell_t2.alignment = Alignment(horizontal="left", vertical="center")
    ws2.row_dimensions[1].height = 28

    headers2 = [
        "STT",
        "Hạng mục check",
        "Dữ liệu BL Check List",
        "Dữ liệu BL",
        "Kết quả",
        "Chi tiết khác biệt & Hành động xử lý",
    ]
    ws2.append(headers2)
    ws2.row_dimensions[2].height = 24

    header2_fill = PatternFill(start_color="843C0C", end_color="843C0C", fill_type="solid")
    for col_idx in range(1, len(headers2) + 1):
        cell = ws2.cell(row=2, column=col_idx)
        cell.fill = header2_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border

    curr_row = 3
    for row in t2_rows:
        ws2.append(row)
        ws2.row_dimensions[curr_row].height = 24

        ws2.cell(row=curr_row, column=1).alignment = align_center  # STT
        ws2.cell(row=curr_row, column=1).font = data_font_bold
        ws2.cell(row=curr_row, column=1).border = thin_border

        ws2.cell(row=curr_row, column=2).alignment = align_left    # Hạng mục
        ws2.cell(row=curr_row, column=2).font = data_font_bold
        ws2.cell(row=curr_row, column=2).border = thin_border

        ws2.cell(row=curr_row, column=3).alignment = align_left    # Dữ liệu CL
        ws2.cell(row=curr_row, column=3).font = data_font
        ws2.cell(row=curr_row, column=3).border = thin_border

        ws2.cell(row=curr_row, column=4).alignment = align_left    # Dữ liệu BL
        ws2.cell(row=curr_row, column=4).font = data_font
        ws2.cell(row=curr_row, column=4).border = thin_border

        res_val = str(ws2.cell(row=curr_row, column=5).value or "").upper()
        res_cell = ws2.cell(row=curr_row, column=5)
        res_cell.alignment = align_center
        res_cell.border = thin_border

        if "SAI" in res_val:
            res_cell.fill = status_sai_fill
            res_cell.font = status_sai_font
        else:
            res_cell.fill = status_kt_fill
            res_cell.font = status_kt_font

        ws2.cell(row=curr_row, column=6).alignment = align_left    # Chi tiết
        ws2.cell(row=curr_row, column=6).font = data_font
        ws2.cell(row=curr_row, column=6).border = thin_border

        curr_row += 1

    # Summary row Sheet 2
    if not t2_rows:
        ws2.append(["", "Tuyệt vời! Không có hạng mục nào sai hoặc thiếu sót.", "", "", "", ""])
        for col_idx in range(1, 7):
            ws2.cell(row=curr_row, column=col_idx).border = thin_border
            ws2.cell(row=curr_row, column=col_idx).font = data_font
        curr_row += 1

    ws2.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=6)
    sum_cell2 = ws2.cell(row=curr_row, column=1)
    sum_cell2.value = t2_summary or f"Tổng số điểm cần xử lý: {len(t2_rows)}"
    sum_cell2.fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    sum_cell2.font = Font(name=font_family, size=11, bold=True, color="C00000")
    sum_cell2.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[curr_row].height = 25
    for c in range(1, 7):
        ws2.cell(row=curr_row, column=c).border = thick_bottom_border

    col_widths2 = {1: 8, 2: 28, 3: 40, 4: 40, 5: 20, 6: 45}
    for col_idx, width in col_widths2.items():
        ws2.column_dimensions[get_column_letter(col_idx)].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
