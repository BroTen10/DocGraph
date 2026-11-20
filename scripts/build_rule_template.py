#!/usr/bin/env python3
"""生成「规则描述文档模板」Excel 文件。

产物：
    templates/规则描述文档模板.xlsx                空白模板（填写说明 + 场景与术语 + 规则清单）
    templates/规则描述文档模板-示例-费用报销.xlsx    已填写示例（演示非贸易场景与"留空由 AI 推断"）
    templates/规则描述文档模板.md                    Markdown/Word 场景的替代模板
    templates/规则描述文档模板-填写说明.md            发给客户的填写规范（与 Excel 内说明同源）

设计原则：**AI 多干活、人少干活**。
- 必填只有 1 列：规则正文。其余列留空由系统从正文推断，且留空不会被判定为信息缺失；
- 列按「必填 → 建议填 → 可留空」从左到右排列，表头直接带档位标注，单元格批注给填写示例；
- 只有"正文说不清、或需要显式放宽"的少数规则才需要填后面的列；
- 场景与术语表整表可留空（留空按规则集默认场景）。

用法：
    backend/.venv/bin/python scripts/build_rule_template.py
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT_DIR = _REPO_ROOT / "templates"

_TITLE_FONT = Font(name="微软雅黑", size=14, bold=True)
_SECTION_FONT = Font(name="微软雅黑", size=11, bold=True, color="1F4E79")
_BODY_FONT = Font(name="微软雅黑", size=10)
_HEADER_FONT = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
_FILL_REQUIRED = PatternFill("solid", fgColor="C00000")   # 深红：必填
_FILL_SUGGESTED = PatternFill("solid", fgColor="2F5597")  # 蓝：建议填
_FILL_OPTIONAL = PatternFill("solid", fgColor="808080")   # 灰：可留空
_HINT_FILL = PatternFill("solid", fgColor="FFF2CC")
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP = Alignment(vertical="top", wrap_text=True)

# (表头, 档位, 宽度, 批注)
RULE_COLUMNS: list[tuple[str, str, int, str]] = [
    (
        "规则正文（判定内容）（必填）",
        "required",
        58,
        "唯一必填列。写一句完整、可判定的中文：谁、和谁、比什么、什么方向。\n"
        "示例：发票价税合计应与报销单报销金额一致。\n"
        "对象请写全名（不要写“上述单据”），这样系统能自动推断适用范围。\n"
        "一句话里不要塞多个判定；多个字段/多个关系请拆成多行。",
    ),
    (
        "适用单据/对象（建议填）",
        "suggested",
        18,
        "这条规则管哪些单据，多个用「、」分隔，例：发票、报销单。\n"
        "规则正文里已写清对象全名时可以留空，系统会自动推断。\n"
        "连续多行相同时，可以纵向合并单元格，系统会把值继承到每一行。",
    ),
    (
        "触发条件/业务场景（建议填）",
        "suggested",
        22,
        "只有满足条件时才生效的规则建议填写，例：差旅费 / 金额≥5000元 / 增值税专用发票。\n"
        "留空表示无条件适用。条件写在正文里也可以，但写在此列可避免被误判为重复规则。",
    ),
    (
        "例外（建议填）",
        "suggested",
        20,
        "「除…外」的条件写在这里，例：电子发票除外 / 金额小于5000元时除外。\n"
        "例外必须和主规则写在同一行，不要单独成行。\n"
        "写在规则正文里也可以，系统会尝试提取。",
    ),
    (
        "检查类别（可留空）",
        "optional",
        14,
        "齐套性（是否齐全）/ 基础判断（印章、版本、形式）/ 信息准确性（字段比对）/ 时间逻辑（日期先后）。\n"
        "留空由系统按规则意图归类；若贵司规则集登记了其他检查项，以规则集为准。",
    ),
    (
        "复核策略（可留空）",
        "optional",
        12,
        "硬性不符 / 人工确认 / 仅提示。\n"
        "写「人工确认」「仅提示」的规则即使不满足也不会直接判失败。\n"
        "留空默认按硬性不符处理。",
    ),
    (
        "比较关系（可留空）",
        "optional",
        30,
        "仅当正文说不清、或涉及多张票据汇总时才需要填。\n"
        "格式：源对象.字段 运算符 目标对象.字段\n"
        "运算符：等于 / 不大于 / 不小于 / 时间早于 / 时间不早于 / 时间晚于 / 时间不晚于 / 总额等于 / 包含于\n"
        "示例：发票.价税合计 总额等于 报销单.报销金额",
    ),
    (
        "容差（可留空）",
        "optional",
        12,
        "只在需要放宽时才填，例：金额 0% / 时间 30 天 / 允许同日 / 重量 0.5。\n"
        "留空按严格（0）执行，系统不会擅自放宽；正文里写明阈值时系统也会自动填。",
    ),
    (
        "依据来源（可留空）",
        "optional",
        20,
        "制度、合同或条款号，例：费用报销管理办法 第12条。\n"
        "仅用于审查结果追溯，不参与规则判定。",
    ),
]
RULE_HEADERS = [c[0] for c in RULE_COLUMNS]
RULE_WIDTHS = [c[2] for c in RULE_COLUMNS]

_CHECK_CATEGORIES = "齐套性,基础判断,信息准确性,时间逻辑"
_REVIEW_POLICIES = "硬性不符,人工确认,仅提示"

_INSTRUCTIONS: list[tuple[str, list[str]]] = [
    (
        "一、先看这里：最少只填 1 列",
        [
            "「规则清单」表里唯一必填的是红色表头的【规则正文】。只想快点用起来，就只填这一列，"
            "其他列留空——系统会从正文里自动推断适用范围、触发条件、检查类别、比较关系与容差。",
            "留空不等于信息缺失：留空的列不会让规则变成「待确认」，也不会被判为缺陷。",
            "唯一的要求是：正文写清对象全名、比较方向和条件，别用「上述单据」「相关日期」这类模糊指代。",
        ],
    ),
    (
        "二、三档填写深度（表头已标注）",
        [
            "【必填】规则正文（判定内容）：一句话一条规则，写清谁、和谁、比什么、什么方向。",
            "【建议填】适用单据/对象、触发条件/业务场景、例外：填了更准、更快，也更容易被人类复核；"
            "对同一句话可能被理解成两种意思的规则，强烈建议填。",
            "【可留空】检查类别、复核策略、比较关系、容差、依据来源：系统能推断的都不必填。"
            "只有两种情况值得填——正文说不清比较关系（多字段/多票据汇总），或需要显式放宽容差。",
        ],
    ),
    (
        "三、AI 会自动做什么（所以你可以少填）",
        [
            "拆原子判定：一句话里多个字段分别比较时自动拆成多条；组合齐套（任一/二选一/至少其一）会保留成一条。",
            "推断适用范围：从正文里的对象全名推断 scope；推断触发条件并保留到 condition。",
            "推断例外的归属：正文里的「除…外」会挂到主规则上，不会单独成条。",
            "推断检查类别与优先级，并据此组织冲突检测分组。",
            "提取比较关系与方向：数量/金额/日期分别识别，日期会按「一对时间锚点」拆分。",
            "容差：正文写明阈值/天数/百分比时自动填；没写就按严格 0，不会擅自放宽。",
            "质量检查：模糊指代、缺比较对象、自相矛盾会被标成缺陷，红色项需要人工处理后才会启用。",
        ],
    ),
    (
        "四、怎么写好「规则正文」",
        [
            "写全对象：发票价税合计应与报销单报销金额一致。（不要写：金额应一致）",
            "写清方向：写「不大于/不小于/不晚于/不早于」，不要只写「一致」了事——确需相等就写「应等于/应一致」。",
            "一行一件事：产品名称、规格型号一致，数量、金额不大于对方 → 拆成 4 行。",
            "条件随行：只有特定场景才生效的要求，把条件写进「触发条件」列或正文开头（例：差旅费报销应…）。",
            "例外同行：电子发票除外 → 写在「例外」列，或紧跟主句写在同一行。",
            "组合别拆：任一运输单据/二选一/至少一项 必须写在一行里，拆成多行会被当成多条强制要求。",
            "不要写规划：过程说明、采集方式、后续计划、内部备注不会变成规则，写进来只会产生噪声。",
        ],
    ),
    (
        "五、常见错误与系统行为",
        [
            "用「相关日期」「有关部门」这类模糊指代 → 标记 ambiguous_reference 缺陷，规则进入待确认。",
            "只写「金额应一致」没写和谁比 → 标记 incomplete_condition，规则默认禁用，等人工补全。",
            "需要容差却没写 → 按严格 0 执行；确需放宽请在「容差」列写清。",
            "两条规则文本相似但适用对象/条件/例外不同 → 分别在两行写清楚；系统按「适用范围+条件+例外+比较字段」"
            "判断是否重复，不会把不同场景的规则合并。",
            "直接粘贴大段制度原文 → 非判定性内容会被过滤，但仍建议只保留可判定的要求。",
        ],
    ),
    (
        "六、导入后你会看到什么",
        [
            "1) 解析结果：每条规则的正文、适用范围、来源行（Sheet!行号），可逐行核对。",
            "2) 来源行覆盖率：Excel 每行都会生成来源行标识；某行没解析出规则会提示「源表有 N 行未解析出规则」。",
            "3) 缺陷页签：冲突 / 错误 / 警告 / 提示 / 正常规则，红色项处理后才启用。",
            "4) 冲突报告：同一适用范围下互相矛盾的规则会被检出并引用规则编号，便于定位。",
            "5) 场景：本次实际使用的业务场景会随导入结果返回，可在导入结果中确认。",
        ],
    ),
    (
        "七、「场景与术语」表怎么填（整表可留空）",
        [
            "场景：决定加载哪套领域解析知识。trade=贸易合同与过程单证，expense=费用报销，supplier=供应商审查；"
            "留空则按规则集默认场景（存量规则集为贸易单证）。",
            "适用说明：一句话说明本文件覆盖范围，仅作为解析上下文，可留空。",
            "术语：把同一对象的多种叫法收敛到一个规范名称，避免同一单据被登记成多个类型、产生重复规则。"
            "格式：第一列写「术语」，第二列写规范名称，第三列写别名（多个用「、」分隔）。",
        ],
    ),
    (
        "八、最省事的填写示例",
        [
            "只填必填列（推荐起步做法）：",
            "  规则正文 = 发票开票日期应不晚于报销单提交日期。",
            "  其余 8 列全部留空 → 系统推断：适用对象=发票、报销单；检查类别=时间逻辑；严格 0 天。",
            "正文说不清时才补可选列：",
            "  规则正文 = 发票价税合计应与报销单报销金额一致。｜比较关系 = 发票.价税合计 总额等于 报销单.报销金额",
            "  规则正文 = 发票应加盖销售方发票专用章。｜例外 = 电子发票除外 ｜复核策略 = 硬性不符",
            "完整示例见同目录《规则描述文档模板-示例-费用报销.xlsx》。",
        ],
    ),
]

_SCENE_ROWS = [
    ["项目", "值", "说明（请勿删除本表）"],
    ["场景", "", "可留空：留空按规则集默认场景。可选 trade=贸易合同与过程单证 / expense=费用报销 / supplier=供应商审查；其他场景由管理员新增场景知识包"],
    ["场景名称", "", "可留空：留空自动使用场景知识包的中文名"],
    ["适用说明", "", "可留空：一句话说明本文件的适用范围，仅作为解析上下文"],
    ["术语", "", "建议填：第二列=规范名称，第三列=别名（多个用「、」分隔）；可续行，例：术语 | 报销单 | 费用报销单、报销凭单"],
]

_DEMO_SCENE_ROWS = [
    ["项目", "值", "说明（请勿删除本表）"],
    ["场景", "expense", "费用报销场景（非贸易场景，用于验证场景知识包机制）"],
    ["场景名称", "", "留空：系统自动使用「费用报销」场景包的中文名"],
    ["适用说明", "覆盖差旅与日常费用报销的齐套性、形式、金额一致性与时间逻辑检查"],
    ["术语", "报销单", "费用报销单、报销凭单"],
    ["术语", "发票", "增值税发票、电子发票"],
    ["术语", "出差审批单", "出差申请单、行程审批单"],
]

# 演示：前 3 行只填必填列（含合并单元格继承），后面按需补建议/可选列
_DEMO_RULES = [
    ["发票价税合计应与报销单报销金额一致。", "发票、报销单", "", "", "", "", "发票.价税合计 总额等于 报销单.报销金额", "", "费用报销管理办法 第12条"],
    ["发票购买方名称应与报销单所属公司名称一致。", "发票", "", "", "", "", "", "", "费用报销管理办法 第9条"],
    ["发票开票日期应不晚于报销单提交日期。", "发票", "", "", "", "", "", "", "费用报销管理办法 第11条"],
    ["发票应加盖销售方发票专用章。", "发票", "", "电子发票除外", "基础判断", "硬性不符", "", "", "发票管理规定"],
    ["差旅报销应齐套：报销单、发票，以及行程单或出差审批单（任一）。", "报销单", "差旅费", "", "齐套性", "硬性不符", "", "", "差旅费管理办法 第5条"],
    ["金额达到审批权限的报销单应具备部门负责人及财务负责人双签。", "", "金额≥5000元", "金额小于5000元时除外", "基础判断", "硬性不符", "", "", "授权审批制度 第3条"],
    ["跨年度报销应附财务负责人审批说明。", "", "跨年度报销", "", "齐套性", "人工确认", "", "", "费用报销管理办法 第18条"],
    ["报销单申请人应为费用实际发生人。", "", "", "", "", "人工确认", "报销单.申请人 等于 报销单.费用发生人", "", "费用报销管理办法 第7条"],
    ["同一发票号码不得在已报销记录中重复使用。", "", "", "", "信息准确性", "硬性不符", "", "", "发票管理规定 第6条"],
    ["报销单提交日期应不晚于费用发生日期后30日。", "", "", "", "", "仅提示", "", "时间 30 天", "费用报销管理办法 第15条"],
]


def _write_instructions(ws) -> None:
    ws.column_dimensions["A"].width = 120
    ws["A1"] = "规则描述文档模板 · 填写说明（先看第一节，最少只填 1 列）"
    ws["A1"].font = _TITLE_FONT
    row = 3
    for title, lines in _INSTRUCTIONS:
        ws.cell(row=row, column=1, value=title).font = _SECTION_FONT
        row += 1
        for line in lines:
            cell = ws.cell(row=row, column=1, value=line)
            cell.font = _BODY_FONT
            cell.alignment = _WRAP
            row += 1
        row += 1
    ws.sheet_view.showGridLines = False


def _write_scene_sheet(ws, rows: list[list[str]]) -> None:
    for col, width in enumerate([14, 18, 78], start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = _HEADER_FONT if r == 1 else _BODY_FONT
            cell.alignment = _WRAP
            cell.border = _BORDER
            if r == 1:
                cell.fill = _FILL_SUGGESTED
            elif c == 2:
                cell.fill = _HINT_FILL
    dv = DataValidation(
        type="list",
        formula1='"trade,expense,supplier"',
        allow_blank=True,
        showDropDown=False,
    )
    dv.prompt = "选择业务场景键；可留空（留空按规则集默认场景）"
    dv.promptTitle = "业务场景"
    ws.add_data_validation(dv)
    dv.add("B2")
    ws.freeze_panes = "A2"


def _write_rules_sheet(ws, rules: list[list[str]] | None) -> None:
    for col, (_, _, width, _) in enumerate(RULE_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width

    fills = {
        "required": _FILL_REQUIRED,
        "suggested": _FILL_SUGGESTED,
        "optional": _FILL_OPTIONAL,
    }
    for col, (header, tier, _, note) in enumerate(RULE_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = _HEADER_FONT
        cell.fill = fills[tier]
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = _BORDER
        comment = Comment(note, "DocGraph", width=380, height=170)
        comment.fill = _HINT_FILL
        cell.comment = comment
    ws.row_dimensions[1].height = 34

    for r, row in enumerate(rules or [], start=2):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = _BODY_FONT
            cell.alignment = _WRAP
            cell.border = _BORDER

    category_dv = DataValidation(
        type="list", formula1=f'"{_CHECK_CATEGORIES}"', allow_blank=True, showDropDown=False
    )
    policy_dv = DataValidation(
        type="list", formula1=f'"{_REVIEW_POLICIES}"', allow_blank=True, showDropDown=False
    )
    ws.add_data_validation(category_dv)
    ws.add_data_validation(policy_dv)
    last_row = max(len(rules or []) + 200, 200)
    category_dv.add(f"E2:E{last_row}")
    policy_dv.add(f"F2:F{last_row}")
    ws.freeze_panes = "B2"


def _build(path: Path, scene_rows: list[list[str]], rules: list[list[str]] | None, merges=None) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_instructions(wb.create_sheet("填写说明"))
    _write_scene_sheet(wb.create_sheet("场景与术语"), scene_rows)
    _write_rules_sheet(wb.create_sheet("规则清单"), rules)
    if merges:
        ws = wb["规则清单"]
        for cell_range in merges:
            ws.merge_cells(cell_range)
    wb.save(path)
    wb.close()


def _markdown_guide() -> str:
    lines = ["# 规则描述文档模板 · 填写说明", "", "> 核心原则：**必填只有 1 列（规则正文），其余留空由 AI 推断，留空不算信息缺失。**", ""]
    for title, items in _INSTRUCTIONS:
        lines.append(f"## {title}")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("## 列档位一览")
    lines.append("")
    lines.append("| 列 | 档位 | 说明 |")
    lines.append("| --- | --- | --- |")
    for header, tier, _, note in RULE_COLUMNS:
        tier_label = {"required": "必填", "suggested": "建议填", "optional": "可留空"}[tier]
        lines.append(f"| {header} | {tier_label} | {note.splitlines()[0]} |")
    lines.append("")
    return "\n".join(lines)


def _markdown_template() -> str:
    header = " | ".join(RULE_HEADERS)
    divider = " | ".join(["---"] * len(RULE_HEADERS))
    empty = " | ".join([""] * len(RULE_HEADERS))
    example = "| 发票价税合计应与报销单报销金额一致。 | 发票、报销单 | 一般报销 | | 信息准确性 | 硬性不符 | | 金额 0% | 费用报销管理办法 第12条 |"
    return f"""# （请替换为规则文档标题，例：XX公司费用报销审查规则）

<!-- ============ 使用说明（本段注释在导入时会被自动忽略） ============
本文档可直接上传到 DocGraph「规则管理 → 批量导入规则 → 文件导入」。
- 必填只有 1 列：规则正文。其余列留空由系统从正文推断，留空不会被判为信息缺失；
- HTML 注释段与下面的「场景与术语」声明块在导入时会被剥离，不会被当成规则；
- 推荐优先使用 Excel 模板：Excel 能保留行级来源（Sheet!行号）、合并单元格继承与列档位提示，
  导入后可校验「源表有 N 行未解析出规则」；Markdown/Word 只能整篇解析，没有行级覆盖校验；
- 每行 = 一条规则；比较关系统一写成「对象.字段 运算符 对象.字段」；
  运算符：等于 / 不大于 / 不小于 / 时间早于 / 时间不早于 / 时间晚于 / 时间不晚于 / 总额等于 / 包含于；
- 详细填写规范见同目录《规则描述文档模板-填写说明.md》。

下面是一行填写示例（只填了必填列 + 少量建议列，其余留空）：
{example}
============ 使用说明结束 ============ -->

场景与术语
场景：
场景名称：
适用说明：
术语： | 、
术语： | 、

# 规则清单

{header}
{divider}
{empty}

# 补充说明（可选，本节内容不是审查规则）

- 本节只放名词解释、制度背景等说明性内容；导入前建议删除本节。
"""


def main() -> int:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    blank = _OUT_DIR / "规则描述文档模板.xlsx"
    demo = _OUT_DIR / "规则描述文档模板-示例-费用报销.xlsx"
    guide = _OUT_DIR / "规则描述文档模板-填写说明.md"
    md_template = _OUT_DIR / "规则描述文档模板.md"

    _build(blank, _SCENE_ROWS, None)
    # 示例文件演示：连续 3 行「发票」纵向合并适用单据列（合并值会继承到每一行）
    _build(demo, _DEMO_SCENE_ROWS, _DEMO_RULES, merges=["B3:B5"])
    guide.write_text(_markdown_guide(), encoding="utf-8")
    md_template.write_text(_markdown_template(), encoding="utf-8")

    for path in (blank, demo, guide, md_template):
        print(f"已生成 {path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
