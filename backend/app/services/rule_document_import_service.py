"""规则文档导入服务：从上传的规则描述文档（PDF/EXCEL/WORD/MD）中提取文本，
然后复用 rule_import_service.import_rules_from_text 调用 LLM 解析为结构化规则。

支持格式：
- PDF: pdfplumber 提取文本（与 OCR 服务共用）
- Excel (.xlsx): openpyxl 读取所有 sheet，展开合并区域并保留行级来源
- Word (.docx): python-docx 提取段落 + 表格文本
- Markdown (.md): 直接读取文本
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .rule_import_service import import_rules_with_skills

logger = logging.getLogger(__name__)


# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".docx", ".md", ".txt"}


@dataclass
class ExtractedRuleDocument:
    """规则文档提取结果。

    text 继续作为 LLM 的原始输入；source_rows 保留结构化表格行，供导入后
    做来源追溯与行覆盖率校验。非表格格式的 source_rows 为空。
    """

    text: str
    source_rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _get_file_ext(filename: str) -> str:
    """获取文件扩展名（小写）。"""
    return Path(filename).suffix.lower()


def _validate_file(filename: str) -> str:
    """校验文件类型，返回扩展名。"""
    ext = _get_file_ext(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件类型: {ext}，仅支持 {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    return ext


def _extract_pdf_text(file_path: str) -> str:
    """从 PDF 提取文本（文本型 PDF）。"""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            # 也提取表格文本
            tables = page.extract_tables() or []
            for table in tables:
                for row in table:
                    row_text = " | ".join(str(c) if c else "" for c in row)
                    if row_text.strip(" |"):
                        parts.append(row_text)
            if t.strip():
                parts.append(t)
    return "\n\n".join(parts)


def _cell_text(value: Any) -> str:
    """把 Excel 单元格值转为稳定的文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _row_has_content(row: list[Any]) -> bool:
    return any(_cell_text(cell) for cell in row)


def _expand_sheet_merged_cells(sheet) -> tuple[list[list[Any]], dict[tuple[int, int], str]]:
    """读取 sheet 并把合并区域展开为矩形网格。

    仅填充真实合并区域，不对普通空白做向下填充，避免把业务上的空值误当成继承值。
    返回：(grid, merged_from)；坐标均为 1-based 的 (row, column)。
    """
    from openpyxl.utils import get_column_letter

    max_row = sheet.max_row or 0
    max_column = sheet.max_column or 0
    if max_row <= 0 or max_column <= 0:
        return [], {}

    grid = [
        list(row)
        for row in sheet.iter_rows(
            min_row=1,
            max_row=max_row,
            min_col=1,
            max_col=max_column,
            values_only=True,
        )
    ]
    merged_from: dict[tuple[int, int], str] = {}

    for merged_range in sheet.merged_cells.ranges:
        min_row, max_range_row = merged_range.min_row, merged_range.max_row
        min_col, max_range_col = merged_range.min_col, merged_range.max_col
        if min_row > max_row or min_col > max_column:
            continue
        top_left = grid[min_row - 1][min_col - 1]
        anchor = f"{get_column_letter(min_col)}{min_row}"
        for row_idx in range(min_row, min(max_range_row, max_row) + 1):
            for col_idx in range(min_col, min(max_range_col, max_column) + 1):
                if row_idx != min_row or col_idx != min_col:
                    if grid[row_idx - 1][col_idx - 1] is None:
                        grid[row_idx - 1][col_idx - 1] = top_left
                    merged_from[(row_idx, col_idx)] = anchor

    # 去掉尾部全空行/列，避免格式化区域把 max_row/max_column 撑大。
    last_row = 0
    last_col = 0
    for row_idx, row in enumerate(grid, start=1):
        if _row_has_content(row):
            last_row = row_idx
            for col_idx, cell in enumerate(row, start=1):
                if _cell_text(cell):
                    last_col = max(last_col, col_idx)
    if last_row == 0 or last_col == 0:
        return [], {}

    grid = [row[:last_col] for row in grid[:last_row]]
    merged_from = {
        pos: anchor
        for pos, anchor in merged_from.items()
        if pos[0] <= last_row and pos[1] <= last_col
    }
    return grid, merged_from


def _detect_header_rows(sheet, grid: list[list[Any]]) -> list[int]:
    """识别表头行（0-based）。

    首行非空行视为表头；若首行存在横向合并且下一行非空，则把下一行视为
    多级表头的子表头。规则表绝大多数是单行表头，这里是保守启发式。
    """
    non_empty_rows = [idx for idx, row in enumerate(grid) if _row_has_content(row)]
    if not non_empty_rows:
        return []

    header_rows = [non_empty_rows[0]]
    first_row = non_empty_rows[0]
    for merged_range in sheet.merged_cells.ranges:
        if (
            merged_range.min_row - 1 == first_row
            and merged_range.max_row - 1 == first_row
            and merged_range.max_col > merged_range.min_col
        ):
            next_row = first_row + 1
            if next_row < len(grid) and _row_has_content(grid[next_row]):
                header_rows.append(next_row)
            break
    return header_rows


def _build_headers(grid: list[list[Any]], header_rows: list[int]) -> list[str]:
    """把单行/多级表头合并成唯一列名。"""
    width = max((len(row) for row in grid), default=0)
    headers: list[str] = []
    seen: dict[str, int] = {}

    for col_idx in range(width):
        parts: list[str] = []
        for row_idx in header_rows:
            row = grid[row_idx]
            value = _cell_text(row[col_idx]) if col_idx < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        name = "/".join(parts) or f"列{col_idx + 1}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name}_{seen[name]}"
        headers.append(name)
    return headers


def _is_rule_candidate_row(headers: list[str], values: dict[str, str]) -> bool:
    """判断一行是否可能是规则数据行。

    若表头中存在“规则/描述/要求/检查/校验/审查/内容”等列，则这些列至少一个非空；
    否则退化为“任意列非空”，兼容没有明确规则列名的表。
    """
    rule_headers = [
        header
        for header in headers
        if any(keyword in header for keyword in ("规则", "描述", "要求", "检查", "校验", "审查", "内容"))
    ]
    if rule_headers:
        return any(_cell_text(values.get(header)) for header in rule_headers)
    return any(_cell_text(value) for value in values.values())


def _extract_excel_document(file_path: str) -> ExtractedRuleDocument:
    """从 Excel 提取结构化规则行，并展开合并单元格。"""
    from openpyxl import load_workbook

    # 必须使用普通模式：ReadOnlyWorksheet 不暴露 merged_cells。
    wb = load_workbook(file_path, data_only=True, read_only=False)
    parts: list[str] = []
    source_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    sheets_meta: list[dict[str, Any]] = []

    try:
        for sheet in wb.worksheets:
            grid, merged_from = _expand_sheet_merged_cells(sheet)
            if not grid:
                continue

            header_rows = _detect_header_rows(sheet, grid)
            if not header_rows:
                continue
            headers = _build_headers(grid, header_rows)
            data_start = max(header_rows) + 1
            data_count = 0

            parts.append(f"=== Sheet: {sheet.title} ===")
            for row_idx in range(data_start, len(grid)):
                row = grid[row_idx]
                if not _row_has_content(row):
                    continue

                values: dict[str, str] = {}
                row_merged_from: dict[str, str] = {}
                for col_idx, header in enumerate(headers):
                    value = _cell_text(row[col_idx]) if col_idx < len(row) else ""
                    values[header] = value
                    anchor = merged_from.get((row_idx + 1, col_idx + 1))
                    if anchor:
                        row_merged_from[header] = anchor

                if not _is_rule_candidate_row(headers, values):
                    continue

                source_ref = f"{sheet.title}!{row_idx + 1}"
                source_rows.append({
                    "source_ref": source_ref,
                    "sheet": sheet.title,
                    "row": row_idx + 1,
                    "values": values,
                    "merged_from": row_merged_from,
                })
                parts.append(
                    f"[SOURCE_ROW={source_ref}] "
                    + json.dumps(values, ensure_ascii=False, separators=(",", ":"))
                )
                data_count += 1

            sheets_meta.append({
                "sheet": sheet.title,
                "header_rows": [row + 1 for row in header_rows],
                "headers": headers,
                "source_row_count": data_count,
            })
    finally:
        wb.close()

    if not source_rows:
        warnings.append("Excel 未识别到规则数据行；请确认表头后至少存在一行规则")

    return ExtractedRuleDocument(
        text="\n".join(parts),
        source_rows=source_rows,
        warnings=warnings,
        metadata={"file_type": "excel", "sheets": sheets_meta},
    )


def _extract_excel_text(file_path: str) -> str:
    """兼容旧调用：只返回 Excel 提取文本。"""
    return _extract_excel_document(file_path).text


def _extract_docx_text(file_path: str) -> str:
    """从 Word 文档提取文本（段落 + 表格）。"""
    from docx import Document as DocxDocument

    doc = DocxDocument(file_path)
    parts: list[str] = []

    # 段落
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)

    # 表格
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells)
            if row_text.strip(" |"):
                parts.append(row_text)

    return "\n".join(parts)


def _extract_markdown_text(file_path: str) -> str:
    """Markdown 直接读取文本。"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def extract_document_from_file(file_path: str, filename: str) -> ExtractedRuleDocument:
    """根据文件类型提取文本与结构化来源行。

    Args:
        file_path: 临时文件路径
        filename: 原始文件名（用于判断扩展名）

    Returns:
        提取结果（文本 + 表格来源行 + 元数据）
    """
    ext = _validate_file(filename)

    if ext == ".pdf":
        text = _extract_pdf_text(file_path)
        if len(text.strip()) < 50:
            # 可能是扫描型 PDF，用 PyMuPDF 尝试提取
            import fitz

            doc = fitz.open(file_path)
            parts = []
            for page in doc:
                parts.append(page.get_text() or "")
            doc.close()
            text = "\n".join(parts)
        return ExtractedRuleDocument(
            text=text,
            metadata={"file_type": "pdf"},
        )

    if ext == ".xlsx":
        return _extract_excel_document(file_path)

    if ext == ".xls":
        raise ValueError(
            "暂不支持老式 .xls 文件，请用 Excel 另存为 .xlsx 后再导入"
        )

    if ext == ".docx":
        return ExtractedRuleDocument(
            text=_extract_docx_text(file_path),
            metadata={"file_type": "docx"},
        )

    if ext in (".md", ".txt"):
        return ExtractedRuleDocument(
            text=_extract_markdown_text(file_path),
            metadata={"file_type": ext.lstrip(".")},
        )

    raise ValueError(f"不支持的文件类型: {ext}")


def extract_text_from_file(file_path: str, filename: str) -> str:
    """兼容旧调用：只返回提取文本。"""
    return extract_document_from_file(file_path, filename).text


def import_rules_from_document(
    db: Session,
    rule_set_id: uuid.UUID,
    file_content: bytes,
    filename: str,
    skill_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """从上传的规则描述文档中提取文本，并调用 LLM 解析为结构化规则。

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID（导入规则归到该规则集下）
        file_content: 文件二进制内容
        filename: 原始文件名
        skill_ids: 指定应用的 Skill ID，不传则使用默认

    Returns:
        导入结果（与 import_rules_from_text 相同结构，额外含 extracted_text_preview）
    """
    ext = _validate_file(filename)

    # 写入临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    try:
        # 提取文本与结构化表格来源行
        logger.info("开始从文件 %s 提取规则文本", filename)
        document = extract_document_from_file(tmp_path, filename)
        text = document.text

        if not text or not text.strip():
            raise ValueError("文件内容为空，无法提取规则文本")

        logger.info("文件 %s 提取到 %d 字符文本", filename, len(text))

        # 复用带 Skill 的导入逻辑
        result = import_rules_with_skills(
            db,
            rule_set_id,
            text,
            skill_ids=skill_ids,
            source_rows=document.source_rows,
            source_meta={"filename": filename, **document.metadata},
        )
        result["extracted_text_preview"] = text[:500]  # 预览前 500 字符
        result["extracted_text_length"] = len(text)
        result["source_filename"] = filename
        if document.warnings:
            result["import_warnings"] = list(dict.fromkeys(
                list(result.get("import_warnings") or []) + document.warnings
            ))
        return result

    finally:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
