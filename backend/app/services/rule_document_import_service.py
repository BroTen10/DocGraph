"""规则文档导入服务：从上传的规则描述文档（PDF/EXCEL/WORD/MD）中提取文本，
然后复用 rule_import_service.import_rules_with_skills 调用 LLM 解析为结构化规则。

支持格式：
- PDF: pdfplumber 提取文本（与 OCR 服务共用）
- Excel (.xlsx): openpyxl 读取所有 sheet，展开合并区域并保留行级来源；
  识别「场景与术语」元数据表（声明业务场景 + 术语别名），跳过说明类工作表
- Word (.docx): python-docx 提取段落 + 表格文本
- Markdown (.md): 直接读取文本

文档级声明约定（Excel 用元数据表，文本类文件用声明块）：
    场景与术语
    场景：trade
    场景名称：贸易合同过程文件审查
    术语：出口报关单 = 报关单、报关单证
声明块不会被当作规则解析，只用于选择场景知识包与统一术语。
"""

from __future__ import annotations

import json
import logging
import os
import re
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

# 说明类工作表：不参与规则解析（模板里的「填写说明」等）
_SKIP_SHEET_PREFIXES = ("#", "_")
_SKIP_SHEET_NAMES = {
    "填写说明",
    "说明",
    "模板说明",
    "使用说明",
    "readme",
    "instructions",
}
# 元数据工作表：解析为文档级声明（场景 + 术语），不参与规则解析
_METADATA_SHEET_NAMES = {
    "场景与术语",
    "场景信息",
    "场景声明",
    "场景",
    "声明",
    "元数据",
}
# 文本类文件的声明块起始标记
_DECLARATION_MARKERS = {
    "场景与术语",
    "[场景与术语]",
    "# 场景与术语",
    "== 场景与术语 ==",
    "场景与术语声明",
}
_SCENARIO_KEYS = {"场景", "场景标识", "场景键", "scenario", "场景代码"}
_SCENARIO_LABEL_KEYS = {"场景名称", "业务场景", "场景说明"}
_TERM_KEYS = {"术语", "术语与别名", "别名", "规范名称"}
_NOTE_KEYS = {"适用说明", "备注"}
# 承载“判定内容”的列：这些列有值时才算规则数据行（优先级高于规则编号/检查类别）
_PRIMARY_RULE_HEADER_HINTS = (
    "规则正文",
    "规则描述",
    "规则内容",
    "规则要求",
    "判定内容",
    "检查内容",
    "描述",
    "内容",
    "要求",
)


@dataclass
class ExtractedRuleDocument:
    """规则文档提取结果。

    text 继续作为 LLM 的原始输入；source_rows 保留结构化表格行，供导入后
    做来源追溯与行覆盖率校验。非表格格式的 source_rows 为空。
    metadata 中的 scenario / glossary 来自文档级声明，用于选择场景知识包
    与统一术语，不参与规则文本。
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


# ============ 文档级声明（场景 / 术语与别名）============


def _empty_declaration() -> dict[str, Any]:
    return {"scenario": None, "scene_label": "", "glossary": [], "notes": []}


def _sheet_kind(title: str) -> str:
    """判断工作表用途：skip（说明）/ metadata（声明）/ rules（规则）。"""
    name = (title or "").strip()
    if name.startswith(_SKIP_SHEET_PREFIXES):
        return "skip"
    lowered = name.lower()
    if lowered in _SKIP_SHEET_NAMES or name in _SKIP_SHEET_NAMES:
        return "skip"
    if lowered in _METADATA_SHEET_NAMES or name in _METADATA_SHEET_NAMES:
        return "metadata"
    return "rules"


def _split_aliases(value: str) -> list[str]:
    """拆分别名列表（支持中英文顿号、逗号、分号、斜杠分隔）。"""
    parts = re.split(r"[、,，;；/|]+", value or "")
    return [p.strip() for p in parts if p.strip()]


def _add_glossary(declaration: dict[str, Any], canonical: str, aliases: list[str]) -> None:
    canonical = (canonical or "").strip()
    if not canonical:
        return
    aliases = [a for a in dict.fromkeys(aliases) if a and a != canonical]
    glossary = declaration.setdefault("glossary", [])
    for item in glossary:
        if item["canonical"] == canonical:
            item["aliases"] = list(dict.fromkeys(item["aliases"] + aliases))
            return
    glossary.append({"canonical": canonical, "aliases": aliases})


def _apply_declaration_row(declaration: dict[str, Any], key: str, value: str, extra: list[str] | None = None) -> None:
    """把一行「项目 | 值」声明并入 declaration。"""
    key = (key or "").strip()
    value = (value or "").strip()
    if not key:
        return
    if key.lower() in _SCENARIO_KEYS or key in _SCENARIO_KEYS:
        if value:
            declaration["scenario"] = value.split()[0].strip()
        return
    if key in _SCENARIO_LABEL_KEYS:
        if value:
            declaration["scene_label"] = value
        return
    if key in _TERM_KEYS:
        # 术语行：规范名称 | 别名1、别名2（或 规范名称 = 别名…）
        canonical, _, inline = value.partition("=")
        aliases = _split_aliases(inline) if inline else []
        aliases += [a for a in _split_aliases("、".join(extra or [])) if a]
        _add_glossary(declaration, canonical.strip() or value, aliases)
        return
    if key in _NOTE_KEYS:
        if value:
            declaration["notes"].append(f"{key}：{value}")


def _parse_declaration_rows(rows: list[list[str]], declaration: dict[str, Any]) -> None:
    for row in rows:
        cells = [str(c or "").strip() for c in row]
        if not any(cells):
            continue
        _apply_declaration_row(declaration, cells[0], cells[1] if len(cells) > 1 else "", cells[2:])


def _parse_declaration_block(text: str) -> tuple[str, dict[str, Any]]:
    """从文本类文档中提取并移除「场景与术语」声明块。"""
    declaration = _empty_declaration()
    lines = (text or "").splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() in _DECLARATION_MARKERS:
            start = idx
            break
    if start is None:
        return text, declaration

    end = start + 1
    block: list[str] = []
    while end < len(lines):
        stripped = lines[end].strip()
        if not stripped or stripped.startswith(("===", "---")):
            break
        block.append(stripped)
        end += 1

    for line in block:
        cleaned = line.lstrip("-*# ").strip()
        if not cleaned:
            continue
        key, sep, value = cleaned.partition("：")
        if not sep:
            key, sep, value = cleaned.partition(":")
        if not sep:
            continue
        extra: list[str] = []
        if "|" in value:
            head, _, tail = value.partition("|")
            value, extra = head, [tail]
        _apply_declaration_row(declaration, key, value, extra)

    remaining = lines[:start] + lines[end:]
    return "\n".join(remaining), declaration


def build_import_context(metadata: dict[str, Any]) -> str:
    """把文档级声明渲染成注入用户提示词头部的上下文块。"""
    from ..default_rule_parse_skill import scenario_label

    scenario = metadata.get("scenario")
    # 场景名可留空：自动回落到场景知识包的中文名
    label = metadata.get("scene_label") or scenario_label(scenario)
    glossary = metadata.get("glossary") or []
    notes = metadata.get("notes") or []
    if not any((scenario, label, glossary, notes)):
        return ""

    lines = ["### 本次导入声明（优先遵守）"]
    if scenario or label:
        scene = " / ".join(x for x in (label, f"scenario={scenario}" if scenario else "") if x)
        lines.append(f"- 业务场景：{scene}；本文件全部规则按该场景解析。")
    if glossary:
        lines.append("- 术语与别名（解析时统一使用规范名称）：")
        for item in glossary:
            aliases = "、".join(item.get("aliases") or [])
            lines.append(
                f"  - {item['canonical']}" + (f" ← {aliases}" if aliases else "")
            )
    for note in notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


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

    优先看“规则正文/描述/内容/要求”这类承载判定内容的列：只要表头里存在这类列，
    就要求它非空，避免只填了规则编号或类别的行被当成规则。
    没有明确规则列名时，退化为“任意列非空”，兼容没有规范表头的表。
    """
    primary_headers = [
        header
        for header in headers
        if any(keyword in header for keyword in _PRIMARY_RULE_HEADER_HINTS)
    ]
    if primary_headers:
        return any(_cell_text(values.get(header)) for header in primary_headers)

    rule_headers = [
        header
        for header in headers
        if any(keyword in header for keyword in ("规则", "描述", "要求", "检查", "校验", "审查", "内容"))
    ]
    if rule_headers:
        return any(_cell_text(values.get(header)) for header in rule_headers)
    return any(_cell_text(value) for value in values.values())


def _extract_excel_document(file_path: str) -> ExtractedRuleDocument:
    """从 Excel 提取结构化规则行，并展开合并单元格。

    - 说明类工作表（填写说明/README/# 开头）不参与解析；
    - 元数据工作表（场景与术语）解析为文档级声明；
    - 其余工作表按规则表处理，逐行输出 [SOURCE_ROW=...]。
    """
    from openpyxl import load_workbook

    # 必须使用普通模式：ReadOnlyWorksheet 不暴露 merged_cells。
    wb = load_workbook(file_path, data_only=True, read_only=False)
    parts: list[str] = []
    source_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    sheets_meta: list[dict[str, Any]] = []
    declaration = _empty_declaration()
    skipped_sheets: list[str] = []

    try:
        for sheet in wb.worksheets:
            kind = _sheet_kind(sheet.title)
            if kind == "skip":
                skipped_sheets.append(sheet.title)
                continue
            if kind == "metadata":
                rows = [
                    [_cell_text(cell) for cell in row]
                    for row in sheet.iter_rows(values_only=True)
                ]
                _parse_declaration_rows(rows, declaration)
                continue

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
        warnings.append(
            "Excel 未识别到规则数据行；请确认「规则清单」表头完整，且至少有一行的"
            "“规则正文/规则描述”列已填写"
        )
    if skipped_sheets:
        logger.info("已跳过非规则工作表: %s", "、".join(skipped_sheets))

    return ExtractedRuleDocument(
        text="\n".join(parts),
        source_rows=source_rows,
        warnings=warnings,
        metadata={
            "file_type": "excel",
            "sheets": sheets_meta,
            "skipped_sheets": skipped_sheets,
            "scenario": declaration.get("scenario"),
            "scene_label": declaration.get("scene_label") or "",
            "glossary": declaration.get("glossary") or [],
            "notes": declaration.get("notes") or [],
        },
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


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _text_document(text: str, file_type: str) -> ExtractedRuleDocument:
    """文本类文档：剥离编写说明注释与「场景与术语」声明块，并把声明写入元数据。"""
    cleaned = _HTML_COMMENT_RE.sub("", text or "")
    cleaned, declaration = _parse_declaration_block(cleaned)
    return ExtractedRuleDocument(
        text=cleaned,
        metadata={
            "file_type": file_type,
            "scenario": declaration.get("scenario"),
            "scene_label": declaration.get("scene_label") or "",
            "glossary": declaration.get("glossary") or [],
            "notes": declaration.get("notes") or [],
        },
    )


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
        return _text_document(text, "pdf")

    if ext == ".xlsx":
        return _extract_excel_document(file_path)

    if ext == ".xls":
        raise ValueError(
            "暂不支持老式 .xls 文件，请用 Excel 另存为 .xlsx 后再导入"
        )

    if ext == ".docx":
        return _text_document(_extract_docx_text(file_path), "docx")

    if ext in (".md", ".txt"):
        return _text_document(_extract_markdown_text(file_path), ext.lstrip("."))

    raise ValueError(f"不支持的文件类型: {ext}")


def extract_text_from_file(file_path: str, filename: str) -> str:
    """兼容旧调用：只返回提取文本。"""
    return extract_document_from_file(file_path, filename).text


def import_rules_from_document(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    file_content: bytes,
    filename: str,
    skill_ids: list[uuid.UUID] | None = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """从上传的规则描述文档中提取文本，并调用 LLM 解析为结构化规则。

    场景优先级：显式 scenario 参数 > 文档内声明 > 规则集默认场景。

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID（导入规则归到该规则集下）
        file_content: 文件二进制内容
        filename: 原始文件名
        skill_ids: 额外叠加的 Skill ID（合并到「通用契约 + 场景包 + 自定义 Skill」
            基线之上）；不传则只使用基线
        scenario: 显式指定的业务场景键（覆盖文档声明）

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
            detail = "；".join(document.warnings) if document.warnings else ""
            raise ValueError(
                "文件内容为空，无法提取规则文本" + (f"（{detail}）" if detail else "")
            )

        logger.info("文件 %s 提取到 %d 字符文本", filename, len(text))

        metadata = document.metadata or {}
        declared_scenario = metadata.get("scenario")
        effective_scenario = scenario or declared_scenario
        import_context = build_import_context(metadata)
        if import_context:
            logger.info("文件 %s 携带导入声明: 场景=%s", filename, effective_scenario)

        # 复用带 Skill 的导入逻辑
        result = import_rules_with_skills(
            db,
            tenant_id,
            rule_set_id,
            text,
            skill_ids=skill_ids,
            source_rows=document.source_rows,
            source_meta={"filename": filename, **metadata},
            scenario=effective_scenario,
            import_context=import_context,
        )
        result["extracted_text_preview"] = text[:500]  # 预览前 500 字符
        result["extracted_text_length"] = len(text)
        result["source_filename"] = filename
        if not skill_ids:
            # 自定义场景通道下没有加载场景包，不要用文档声明把场景回显出来
            result["scenario"] = result.get("scenario") or effective_scenario
        warnings = list(result.get("import_warnings") or [])
        if declared_scenario and scenario and str(declared_scenario) != str(scenario):
            warnings.append(
                f"文档声明的场景 {declared_scenario} 与本次指定的场景 {scenario} 不一致，"
                f"已按 {scenario} 处理"
            )
        if metadata.get("skipped_sheets"):
            warnings.append(
                "已跳过非规则工作表：" + "、".join(metadata["skipped_sheets"])
            )
        result["import_warnings"] = list(dict.fromkeys(warnings + document.warnings))
        return result

    finally:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
