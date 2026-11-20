# -*- coding: utf-8 -*-
"""生成费用报销端到端测试素材（规则描述文件 + 模拟单据）。

产物：
  samples/费用报销测试套件/
    费用报销规则描述文档.xlsx          ← 规则导入用（符合 templates/规则描述文档模板.xlsx 规范）
    01-合规报销_BX-2026-0312/*.docx    ← 应当基本全部通过
    02-问题报销_BX-2026-0318/*.docx    ← 应当触发多条不通过

运行：backend/.venv/bin/python scripts/make_expense_test_kit.py
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.shared import Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "费用报销测试套件"
COMPANY = "深圳市远航进出口有限公司"
APPLICANT = "张伟"

RULE_HEADERS = [
    "规则正文（判定内容）（必填）",
    "适用单据/对象（建议填）",
    "触发条件/业务场景（建议填）",
    "例外（建议填）",
    "检查类别（可留空）",
    "复核策略（可留空）",
    "比较关系（可留空）",
    "容差（可留空）",
    "依据来源（可留空）",
]

# 与下方单据严格对应：合规单据应全部通过，问题单据应命中第 1/3/4/5/6/7 条
RULES: list[list[str]] = [
    [
        "报销单的报销金额应等于所附全部增值税发票价税合计之和。",
        "报销单、增值税发票",
        "",
        "",
        "信息准确性",
        "硬性不符",
        "报销单.报销金额 总额等于 增值税发票.价税合计",
        "",
        "费用报销管理办法 第12条",
    ],
    [
        "增值税发票的购货方应与报销单的所属公司名称一致。",
        "增值税发票、报销单",
        "",
        "",
        "信息准确性",
        "硬性不符",
        "",
        "",
        "费用报销管理办法 第9条",
    ],
    [
        "增值税发票的开票日期应不晚于报销单的提交日期。",
        "增值税发票、报销单",
        "",
        "电子发票同样适用",
        "时间逻辑",
        "硬性不符",
        "",
        "时间容差0天",
        "费用报销管理办法 第11条",
    ],
    [
        "报销单的提交日期与费用发生日期的间隔不得超过30日。",
        "报销单",
        "",
        "跨年度报销另行审批",
        "时间逻辑",
        "提示",
        "",
        "时间容差30天（允许同日）",
        "费用报销管理办法 第7条",
    ],
    [
        "费用报销应齐套：报销单、增值税发票、行程单。",
        "报销单",
        "",
        "",
        "齐套性",
        "硬性不符",
        "",
        "",
        "差旅费管理办法 第5条",
    ],
    [
        "报销金额达到5000元及以上的报销单，应同时具备部门负责人与财务负责人签字审批。",
        "报销单",
        "报销金额≥5000元",
        "",
        "信息准确性",
        "硬性不符",
        "",
        "",
        "费用报销管理办法 第18条",
    ],
    [
        "增值税发票应加盖销售方发票专用章。",
        "增值税发票",
        "",
        "电子发票除外",
        "基础判断",
        "硬性不符",
        "",
        "",
        "发票管理规定 第6条",
    ],
    [
        "报销单的申请人应与费用发生人一致。",
        "报销单",
        "",
        "代垫费用需附说明",
        "信息准确性",
        "提示",
        "",
        "",
        "费用报销管理办法 第8条",
    ],
    [
        "行程单的乘机人应与报销单的申请人一致。",
        "行程单、报销单",
        "",
        "",
        "信息准确性",
        "硬性不符",
        "",
        "",
        "差旅费管理办法 第9条",
    ],
    [
        "增值税发票的价税合计应等于该发票的金额与税额之和。",
        "增值税发票",
        "",
        "",
        "信息准确性",
        "提示",
        "",
        "金额容差0.01元",
        "发票管理规定 第4条",
    ],
]


def build_rule_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "填写说明"
    for line in [
        "费用报销测试规则集 · 填写说明",
        "",
        "本文件用于 DocGraph 规则导入的端到端测试，配合 samples/费用报销测试套件/01-合规报销_BX-2026-0312 与 02-问题报销_BX-2026-0318 两份单据使用。",
        "场景与术语：场景=expense，导入后应生效「通用规则解析契约 + 场景包：费用报销」。",
        "规则清单：10 条规则，覆盖金额一致性、时间逻辑、齐套性、审批层级、形式审查与信息一致性六类判定。",
        "预期：01 合规单据全部通过；02 问题单据命中「金额不一致 / 开票日期晚于提交日期 / 提交超30日 / 缺行程单 / 审批缺双签 / 缺发票专用章」。",
    ]:
        ws.append([line])
    ws.column_dimensions["A"].width = 110

    meta = wb.create_sheet("场景与术语")
    meta.append(["项目", "值", "说明（请勿删除本表）"])
    meta.append(["场景", "expense", "费用报销场景"])
    meta.append(["场景名称", "", "留空：系统自动使用「费用报销」场景包的中文名"])
    meta.append(["适用说明", "员工差旅与日常费用报销的齐套性、形式、金额一致性与时间逻辑审查", ""])
    meta.append(["术语", "报销单", "费用报销单、报销凭单"])
    meta.append(["术语", "增值税发票", "发票、增值税专用发票、电子发票"])
    meta.append(["术语", "行程单", "航空运输电子客票行程单、差旅行程单"])
    for col, width in (("A", 12), ("B", 22), ("C", 52)):
        meta.column_dimensions[col].width = width

    rules = wb.create_sheet("规则清单")
    rules.append(RULE_HEADERS)
    for row in RULES:
        rules.append(row)
    header_fill = PatternFill("solid", fgColor="DCE6F1")
    for idx, _ in enumerate(RULE_HEADERS, start=1):
        cell = rules.cell(row=1, column=idx)
        cell.font = Font(bold=True, color="C00000" if idx == 1 else "000000")
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    widths = [46, 20, 18, 16, 12, 12, 30, 20, 22]
    for idx, width in enumerate(widths, start=1):
        rules.column_dimensions[rules.cell(row=1, column=idx).column_letter].width = width
    for row in rules.iter_rows(min_row=2, max_row=rules.max_row):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def build_doc(path: Path, title: str, lines: list[str]) -> None:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "PingFang SC"
    style.font.size = Pt(11)
    heading = doc.add_paragraph()
    run = heading.add_run(title)
    run.bold = True
    run.font.size = Pt(15)
    heading.alignment = 1
    for line in lines:
        doc.add_paragraph(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def reimbursement_form(no: str, amount: str, submit: str, approvers: list[str]) -> list[str]:
    return [
        f"报销单号：{no}",
        f"所属公司：{COMPANY}",
        f"申请人：{APPLICANT}",
        "所属部门：销售部",
        f"费用发生人：{APPLICANT}",
        "费用类型：差旅费",
        "费用事由：上海客户拜访差旅费",
        "费用发生日期：2026-03-05",
        "费用发生年度：2026",
        f"提交日期：{submit}",
        f"报销金额（元）：{amount}",
        "报销年度：2026",
        "币别：人民币",
        "收款账户：招商银行 6225 **** **** 1234",
        "审批签字：" + "；".join(approvers),
        "备注：本报销单为 DocGraph 端到端测试用模拟单据。",
    ]


def vat_invoice(
    number: str,
    issue_date: str,
    seller: str,
    item: str,
    amount: str,
    tax: str,
    total: str,
    stamp: bool,
) -> list[str]:
    lines = [
        "增值税电子普通发票",
        f"发票号码：{number}",
        f"开票日期：{issue_date}",
        f"购货方：{COMPANY}",
        "购货方纳税人识别号：91440300MA5XXXXX7L",
        f"销货方：{seller}",
        f"货物或应税劳务名称：{item}",
        f"金额：{amount}",
        "税率：6%",
        f"税额：{tax}",
        f"价税合计（小写）：¥{total}",
    ]
    if stamp:
        lines.append(f"发票专用章：{seller}发票专用章")
    else:
        lines.append("发票专用章：（无，本张发票未加盖销售方发票专用章）")
    lines.append("备注：本发票为 DocGraph 端到端测试用模拟单据，不具备任何票据效力。")
    return lines


def itinerary() -> list[str]:
    return [
        "航空运输电子客票行程单",
        f"乘机人：{APPLICANT}",
        "身份证号码：4403**********1234",
        "承运人：中国东方航空股份有限公司",
        "航班号：MU5331",
        "行程：深圳宝安国际机场 → 上海虹桥国际机场",
        "乘机日期：2026-03-05",
        "航班号：MU5332",
        "行程：上海虹桥国际机场 → 深圳宝安国际机场",
        "乘机日期：2026-03-07",
        "票价（元）：3,200.00",
        "民航发展基金（元）：100.00",
        "燃油附加费（元）：0.00",
        "合计金额（元）：3,200.00",
        "填开单位：中国东方航空股份有限公司",
        "备注：本行程单为 DocGraph 端到端测试用模拟单据。",
    ]


def main() -> None:
    generated: list[Path] = []

    rule_file = OUT / "费用报销规则描述文档.xlsx"
    build_rule_workbook(rule_file)
    generated.append(rule_file)

    ok_dir = OUT / "01-合规报销_BX-2026-0312"
    docs = [
        (
            f"BX-2026-0312_费用报销单.docx",
            "费用报销单",
            reimbursement_form(
                "BX-2026-0312", "4,860.00", "2026-03-09", ["部门负责人 李强"]
            ),
        ),
        (
            "BX-2026-0312_增值税发票_1_航空客票.docx",
            "增值税电子普通发票",
            vat_invoice(
                "04412000001234567890",
                "2026-03-06",
                "中国东方航空股份有限公司",
                "国内旅客运输服务",
                "3,036.70",
                "163.30",
                "3,200.00",
                True,
            ),
        ),
        (
            "BX-2026-0312_增值税发票_2_住宿费.docx",
            "增值税电子普通发票",
            vat_invoice(
                "04412000001234567891",
                "2026-03-08",
                "上海锦江国际酒店管理有限公司",
                "住宿服务",
                "1,566.04",
                "93.96",
                "1,660.00",
                True,
            ),
        ),
        ("BX-2026-0312_行程单.docx", "航空运输电子客票行程单", itinerary()),
    ]
    for filename, title, lines in docs:
        path = ok_dir / filename
        build_doc(path, title, lines)
        generated.append(path)

    bad_dir = OUT / "02-问题报销_BX-2026-0318"
    docs = [
        (
            "BX-2026-0318_费用报销单.docx",
            "费用报销单",
            reimbursement_form(
                "BX-2026-0318",
                "5,860.00",
                "2026-04-20",
                ["部门负责人 李强", "财务负责人 （未签字）"],
            ),
        ),
        (
            "BX-2026-0318_增值税发票_1_住宿费.docx",
            "增值税电子普通发票",
            vat_invoice(
                "04412000001239999999",
                "2026-04-25",
                "上海浦东香格里拉大酒店有限公司",
                "住宿服务",
                "4,716.98",
                "283.02",
                "5,000.00",
                False,
            ),
        ),
    ]
    for filename, title, lines in docs:
        path = bad_dir / filename
        build_doc(path, title, lines)
        generated.append(path)

    for path in generated:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
