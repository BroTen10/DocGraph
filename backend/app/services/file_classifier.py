"""文件业务类型分类器。

基于文件名关键词 + 扩展名启发式规则，将上传文件归类到预定义的 doc_type。
分类准确率要求 ≥ 90%（PRD AC1），分类结果可由用户修正。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..constants import (
    DOC_AGENCY_AGREEMENT,
    DOC_CUSTOMS_DECLARATION,
    DOC_DISPATCH,
    DOC_ENTRUST_CONFIRM,
    DOC_FX_CLAIM,
    DOC_OTHER,
    DOC_PACKING_LIST,
    DOC_PAY_APPLICATION,
    DOC_PAY_VOUCHER,
    DOC_RECEIPT,
    DOC_RECEIVE_VOUCHER,
    DOC_RELEASE_NOTE,
    DOC_SALES_CONTRACT,
    DOC_SALES_INVOICE,
    DOC_VAT_INVOICE,
    DOC_WAREHOUSE_INOUT,
    DOC_WAYBILL,
    OPTIONAL_DOC_TYPES,
    REQUIRED_DOC_TYPES,
)

logger = logging.getLogger(__name__)

# 文件名关键词 → 业务类型映射（按优先级排序，越具体越靠前）
# 使用 (关键词列表, 业务类型) 元组
_KEYWORD_RULES: list[tuple[list[str], str]] = [
    # 代理协议（最优先，避免被"协议"误判）
    (["代理协议", "出口代理协议"], DOC_AGENCY_AGREEMENT),
    # 委托出口确认单
    (["委托出口确认单", "委托单", "委托确认"], DOC_ENTRUST_CONFIRM),
    # 报关单
    (["报关单"], DOC_CUSTOMS_DECLARATION),
    # 提单 / 运单 / 载货清单
    (["提单", "运单", "载货清单", "cargo receipt"], DOC_WAYBILL),
    # 签收单
    (["签收单", "签收"], DOC_RECEIPT),
    # 出入仓单
    (["出库单", "入库单", "入仓单", "出仓单", "出入仓单"], DOC_WAREHOUSE_INOUT),
    # 派车单
    (["派车单", "派车"], DOC_DISPATCH),
    # 收汇认领（必须在收款水单之前匹配，因为收汇认领也含"收汇"）
    (["收汇认领", "认领"], DOC_FX_CLAIM),
    # 收款水单 / 收汇水单 / 收汇截图
    (["收款水单", "收汇水单", "收汇截图", "收水单"], DOC_RECEIVE_VOUCHER),
    # 付款申请（必须在付款水单之前匹配）
    (["付款申请"], DOC_PAY_APPLICATION),
    # 付款水单 / 银行水单（含 CNPAY 通常是付款）
    (["付款水单", "付水单"], DOC_PAY_VOUCHER),
    # 增值税发票
    (["增值税发票", "增值税"], DOC_VAT_INVOICE),
    # 销售发票
    (["销售发票"], DOC_SALES_INVOICE),
    # 装箱单
    (["装箱单"], DOC_PACKING_LIST),
    # 销售合同（在采购合同之后，因为"合同"是通用词）
    (["销售合同"], DOC_SALES_CONTRACT),
    # 放行条
    (["放行条", "放行"], DOC_RELEASE_NOTE),
]

# 银行水单的额外判定：含 CNPAY 视为付款水单，含 SH 视为收款水单
_BANK_VOUCHER_PAY_PATTERN = re.compile(r"CNPAY\d+", re.IGNORECASE)
_BANK_VOUCHER_RECEIVE_PATTERN = re.compile(r"SH\d+", re.IGNORECASE)

# 文件扩展名 → file_type
_EXT_FILE_TYPE = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".docx": "docx",
}


def get_file_type(filename: str) -> str:
    """根据扩展名返回 file_type。未知扩展名返回 'other'。"""
    ext = Path(filename).suffix.lower()
    return _EXT_FILE_TYPE.get(ext, "other")


def classify_file(
    filename: str,
    registry: dict[str, bool] | None = None,
) -> tuple[str, bool]:
    """根据文件名归类业务类型。

    Args:
        filename: 原始文件名
        registry: 文档类型注册表（name → is_required），来自 DocumentType 动态清单
                  （批次 10 Phase B：新类型无需改代码即可被识别）

    Returns:
        (doc_type, is_required)
    """
    name = filename.lower()
    # 中文关键词不区分大小写匹配，直接用原始文件名
    original = filename

    # 1. 先按关键词匹配
    for keywords, doc_type in _KEYWORD_RULES:
        for kw in keywords:
            if kw in original:
                # 注册表优先（动态维护的必备标记），常量兜底
                if registry is not None and doc_type in registry:
                    return doc_type, registry[doc_type]
                return doc_type, doc_type in REQUIRED_DOC_TYPES

    # 2. 银行水单模式：含 CNPAY → 付款水单；含 SH → 收款水单
    if "水单" in original or "银行" in original:
        if _BANK_VOUCHER_PAY_PATTERN.search(original):
            return DOC_PAY_VOUCHER, True
        if _BANK_VOUCHER_RECEIVE_PATTERN.search(original):
            return DOC_RECEIVE_VOUCHER, True
        # 默认水单归为收款水单（实际由用户修正）
        return DOC_RECEIVE_VOUCHER, True

    # 3. 含"合同"但未匹配到具体合同类型 → 默认销售合同（非必备）
    if "合同" in original:
        return DOC_SALES_CONTRACT, False

    # 4. 含"发票"但未匹配到具体发票类型 → 销售发票（非必备）
    if "发票" in original:
        return DOC_SALES_INVOICE, False

    # 5. 动态注册表匹配：文件名包含已注册类型名（含新发现的 pending_review 类型）
    if registry:
        for name, is_required in registry.items():
            if name and name in original:
                logger.debug("文件 %s 按动态注册表归类为 %s", filename, name)
                return name, is_required

    # 6. 未识别
    logger.debug("文件 %s 未匹配到任何业务类型，归为其他", filename)
    return DOC_OTHER, False


def classify_files(
    filenames: list[str],
    registry: dict[str, bool] | None = None,
) -> list[dict]:
    """批量分类文件。返回 [{file_name, doc_type, is_required, file_type}, ...]。"""
    results = []
    for fname in filenames:
        doc_type, is_required = classify_file(fname, registry=registry)
        results.append(
            {
                "file_name": fname,
                "doc_type": doc_type,
                "is_required": is_required,
                "file_type": get_file_type(fname),
            }
        )
    return results
