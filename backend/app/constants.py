"""业务常量：文件类型分类、检查项类别、必备文件清单、字段提取模板。

集中维护，便于规则引擎、文件分类、字段提取共用。
"""

from __future__ import annotations

# ============ 检查项类别 ============
CHECK_COMPLETENESS = "齐套性"
CHECK_STAMP = "基础判断"
CHECK_ACCURACY = "信息准确性"
CHECK_TIME_LOGIC = "时间逻辑"

CHECK_CATEGORIES = [
    CHECK_COMPLETENESS,
    CHECK_STAMP,
    CHECK_ACCURACY,
    CHECK_TIME_LOGIC,
]

# ============ 文件类型 ============
# 必备文件类型（出口代理场景）
DOC_AGENCY_AGREEMENT = "代理协议"
DOC_ENTRUST_CONFIRM = "委托出口确认单"
DOC_CUSTOMS_DECLARATION = "出口报关单"
DOC_WAYBILL = "运单"
DOC_RECEIPT = "签收单"
DOC_RECEIVE_VOUCHER = "收款水单"
DOC_PAY_VOUCHER = "付款水单"

# 非必备文件类型（视场景，未提供不报错）
DOC_WAREHOUSE_INOUT = "出入仓单"
DOC_DISPATCH = "派车单"

# 配套文件（与水单一起出现，归类为非必备）
DOC_FX_CLAIM = "收汇认领"
DOC_PAY_APPLICATION = "付款申请"

# 多余文件（按规则多文件不报错，单独归类）
DOC_SALES_CONTRACT = "销售合同"
DOC_PURCHASE_CONTRACT = "采购合同"
DOC_PACKING_LIST = "装箱单"
DOC_SALES_INVOICE = "销售发票"
DOC_VAT_INVOICE = "增值税发票"
DOC_RELEASE_NOTE = "放行条"
DOC_OTHER = "其他"

# 必备文件类型集合（用于齐套性检查）
REQUIRED_DOC_TYPES = {
    DOC_AGENCY_AGREEMENT,
    DOC_ENTRUST_CONFIRM,
    DOC_CUSTOMS_DECLARATION,
    DOC_WAYBILL,
    DOC_RECEIPT,
    DOC_RECEIVE_VOUCHER,
    DOC_PAY_VOUCHER,
}

# 非必备文件类型集合（缺失不报错）
OPTIONAL_DOC_TYPES = {DOC_WAREHOUSE_INOUT, DOC_DISPATCH}

# 所有支持的业务文件类型（用于分类器选择 + 规则二维表格行）
ALL_DOC_TYPES = [
    DOC_AGENCY_AGREEMENT,
    DOC_ENTRUST_CONFIRM,
    DOC_CUSTOMS_DECLARATION,
    DOC_WAYBILL,
    DOC_RECEIPT,
    DOC_WAREHOUSE_INOUT,
    DOC_DISPATCH,
    DOC_RECEIVE_VOUCHER,
    DOC_PAY_VOUCHER,
    DOC_FX_CLAIM,
    DOC_PAY_APPLICATION,
    DOC_SALES_CONTRACT,
    DOC_PURCHASE_CONTRACT,
    DOC_PACKING_LIST,
    DOC_SALES_INVOICE,
    DOC_VAT_INVOICE,
    DOC_RELEASE_NOTE,
    DOC_OTHER,
]

# ============ 字段提取模板（按文件类型） ============
# 每个文件类型对应需要从 OCR/文本中提取的关键字段名列表
FIELD_TEMPLATES: dict[str, list[str]] = {
    DOC_AGENCY_AGREEMENT: [
        "协议方", "委托方", "协议开始日期", "协议结束日期", "用印方"
    ],
    DOC_ENTRUST_CONFIRM: [
        "合同号", "委托方", "客户", "产品名称", "规格型号", "数量", "单位",
        "单价", "金额", "币别", "结算条款", "签订日期", "用印方"
    ],
    DOC_CUSTOMS_DECLARATION: [
        "预录入编号", "海关编号", "境内发货人", "境外收货人", "生产销售单位",
        "出口日期", "申报日期", "合同协议号", "商品名称", "规格型号",
        "数量", "单位", "单价", "总价", "币别", "毛重", "净重", "件数",
        "成交方式", "申报单位", "用印方"
    ],
    DOC_WAYBILL: [
        "合同号", "运单号", "收货方", "发货方", "件数", "重量", "起运日期",
        "运输方式", "用印方"
    ],
    DOC_RECEIPT: [
        "合同号", "收货方", "签收数量", "签收日期", "用印方"
    ],
    DOC_WAREHOUSE_INOUT: [
        "合同号", "产品名称", "入库数量", "出库数量", "日期", "仓库名称", "用印方"
    ],
    DOC_DISPATCH: [
        "合同号", "司机", "车牌号", "出车时间", "地点", "货物", "用印方"
    ],
    DOC_RECEIVE_VOUCHER: [
        "合同号", "收款方", "付款方", "收款金额", "币别", "收款日期", "银行流水号"
    ],
    DOC_PAY_VOUCHER: [
        "合同号", "付款方", "收款方", "付款金额", "币别", "付款日期", "银行流水号"
    ],
    DOC_FX_CLAIM: [
        "合同号", "认领金额", "认领日期", "认领人"
    ],
    DOC_PAY_APPLICATION: [
        "合同号", "申请金额", "申请日期", "申请人"
    ],
    DOC_VAT_INVOICE: [
        "合同号", "发票号码", "购货方", "销货方", "金额", "税额", "价税合计", "开票日期"
    ],
    DOC_SALES_INVOICE: [
        "合同号", "发票号码", "购货方", "销货方", "金额", "币别", "开票日期"
    ],
    DOC_PACKING_LIST: [
        "合同号", "产品名称", "数量", "毛重", "净重", "件数"
    ],
    DOC_SALES_CONTRACT: [
        "合同号", "卖方", "买方", "产品名称", "数量", "金额", "币别", "签订日期"
    ],
    DOC_RELEASE_NOTE: [
        "合同号", "放行日期", "海关编号"
    ],
}

# ============ 文件类型用印要求（用于基础判断规则） ============
# value: 谁应该用印（None 表示该文件不要求用印）
STAMP_REQUIREMENTS: dict[str, str | None] = {
    DOC_AGENCY_AGREEMENT: "双方回签用印",
    DOC_ENTRUST_CONFIRM: "委托方用印",
    DOC_CUSTOMS_DECLARATION: "报关行用印",
    DOC_WAYBILL: "正本用印",
    DOC_RECEIPT: "正本用印",
    DOC_WAREHOUSE_INOUT: "仓库用印",
    DOC_DISPATCH: "物流公司用印",
    DOC_RECEIVE_VOUCHER: None,
    DOC_PAY_VOUCHER: None,
}


# ============ 类型/字段显式别名（批次 11：写时归一） ============
# 原则：
# 1. 只登记业务上确定同义的说法，不做模糊/近似猜测，避免过拟合；
# 2. 别名在"写入那一刻"翻译成规范名（规则解析落库、文档分类落库、OCR 提取落库），
#    审查匹配仍用精确等值，行为可预期、可审计；
# 3. 未命中规范名/别名的新名称不硬猜，走新类型检测 → pending_review 人工确认。
# 用户可在"文档类型"页维护各类型的 aliases / field_aliases，注册表优先于此处常量。
DOC_TYPE_ALIASES: dict[str, list[str]] = {
    # 内置规范名 → 常见同义叫法
    DOC_CUSTOMS_DECLARATION: ["报关单", "出口货物报关单", "海关报关单"],
    DOC_PACKING_LIST: ["装箱清单", "装箱明细"],
}

# 字段别名：{规范文档类型: {字段别名: 规范字段名 或 {"field": ..., "aggregate": ...}}}
# aggregate 用于表达"总数量 = 数量求和"这类聚合语义，而非普通同义词。
FIELD_ALIASES: dict[str, dict[str, object]] = {
    DOC_CUSTOMS_DECLARATION: {
        "币种": "币别",
        "币制": "币别",
        "货币": "币别",
        "金额": "总价",
        "总金额": "总价",
        "货值": "总价",
        "报关金额": "总价",
    },
    DOC_PACKING_LIST: {
        "总数量": {"field": "数量", "aggregate": "SUM"},
        "数量合计": {"field": "数量", "aggregate": "SUM"},
        "总计数量": {"field": "数量", "aggregate": "SUM"},
        "总件数": {"field": "件数", "aggregate": "SUM"},
        "件数合计": {"field": "件数", "aggregate": "SUM"},
    },
    DOC_WAYBILL: {
        "总件数": {"field": "件数", "aggregate": "SUM"},
        "总重量": {"field": "重量", "aggregate": "SUM"},
    },
}

# ============ 币别值别名（批次 12：写时归一 + 读时兜底） ============
# 规范值为中文币名（与规则条件/OCR 提取的中文表述一致），值为该币种的常见写法
# （ISO 代码 / 货币符号 / 中文变体），统一大写比较。
# 原则与字段别名一致：只登记业务上确定同义的写法，不做模糊猜测。
CURRENCY_VALUE_ALIASES: dict[str, list[str]] = {
    "美元": ["USD", "US$", "US DOLLAR", "美金", "美圆", "美元"],
    "人民币": ["CNY", "RMB", "人民币元", "人民币"],
    "港币": ["HKD", "港元", "港币"],
    "欧元": ["EUR", "欧元"],
    "日元": ["JPY", "日圆", "日元"],
    "英镑": ["GBP", "英镑"],
    "澳大利亚元": ["AUD", "澳元", "澳大利亚元"],
    "加拿大元": ["CAD", "加元", "加拿大元"],
    "新加坡元": ["SGD", "新加坡元"],
    "新台币": ["TWD", "台币", "新台币"],
    "韩元": ["KRW", "韩圆", "韩元"],
}
