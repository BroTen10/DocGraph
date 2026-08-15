"""字段提取服务：从 OCR 结果中提取并规范化结构化字段。

主要职责：
- 数值字段规范化（金额去除逗号/单位、数量解析为 float）
- 日期字段规范化（多种格式统一为 ISO YYYY-MM-DD）
- 合同号字段统一归一化
- 重量字段四舍五入容差处理
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..constants import (
    FIELD_ALIASES,
    DOC_RECEIVE_VOUCHER,
    DOC_PAY_VOUCHER,
    DOC_VAT_INVOICE,
)
from .contract_normalizer import extract_contract_numbers, normalize_contract_no
from .doc_normalizer import is_currency_field, normalize_currency_value

logger = logging.getLogger(__name__)

# 日期格式正则
_DATE_PATTERNS = [
    # 2024-07-18 / 2024/07/18
    (re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"), lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"),
    # 18/07/2024
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), lambda m: f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"),
    # 2024年7月18日
    (re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"), lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"),
]

# 金额正则（支持 ¥/￥/$/CNY/USD 前缀，逗号分隔）
_AMOUNT_PATTERN = re.compile(
    r"(?:¥|￥|\$|CNY|USD|RMB)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
)


def parse_date(text: str) -> Optional[str]:
    """从文本中提取第一个日期，返回 ISO 格式 YYYY-MM-DD。"""
    if not text:
        return None
    for pattern, formatter in _DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return formatter(m)
            except Exception:
                continue
    return None


def parse_amount(text: str) -> Optional[float]:
    """从文本中提取金额数值（去除货币符号和逗号）。"""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    # 直接是数字
    try:
        return float(s.replace(",", ""))
    except ValueError:
        pass
    m = _AMOUNT_PATTERN.search(s)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def parse_int(text: str) -> Optional[int]:
    """从文本中提取整数。"""
    if text is None:
        return None
    s = str(text)
    m = re.search(r"\d+", s.replace(",", ""))
    return int(m.group()) if m else None


def parse_float(text: str) -> Optional[float]:
    """从文本中提取浮点数。"""
    if text is None:
        return None
    s = str(text)
    m = re.search(r"\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else None


def normalize_fields(
    doc_type: str,
    fields: dict,
    aliases: dict | None = None,
) -> dict:
    """规范化字段：日期/金额/数量/合同号统一格式。

    aliases: 字段键别名表（批次 11 写时归一），传入时按别名把键映射到规范字段名；
    不传则回退 constants.FIELD_ALIASES 内置别名。
    返回规范化后的字段字典（原字段名 + 规范化值）。
    """
    if not fields:
        return {}

    out: dict[str, Any] = dict(fields)

    # 0. 字段键别名归一（批次 11）：OCR 返回的别名键 → 规范字段名
    alias_map = aliases if aliases is not None else FIELD_ALIASES.get(doc_type, {})
    if alias_map:
        renamed: dict[str, Any] = {}
        for k, v in out.items():
            m = alias_map.get(k)
            target = m.get("field") if isinstance(m, dict) else (m or k)
            if target in renamed:
                continue  # 规范键已存在，保留规范值
            renamed[target] = v
        out = renamed

    # 1. 合同号归一化
    contract_no_keys = [k for k in out if "合同" in k and "号" in k]
    for k in contract_no_keys:
        v = out.get(k)
        if v:
            candidates = extract_contract_numbers(str(v))
            if candidates:
                canonical, aliases = normalize_contract_no(candidates)
                out[k] = canonical
                out[f"{k}__aliases"] = aliases

    # 2. 日期字段规范化
    date_keys = [k for k in out if "日期" in k or "时间" in k]
    for k in date_keys:
        v = out.get(k)
        if v:
            d = parse_date(str(v))
            if d:
                out[k] = d

    # 3. 金额字段规范化
    amount_keys = [k for k in out if "金额" in k or "总价" in k or "价税合计" in k or "税额" in k]
    for k in amount_keys:
        v = out.get(k)
        if v is not None:
            amt = parse_amount(str(v))
            if amt is not None:
                out[k] = amt

    # 3.5 币别值归一（批次 12）：美元/USD/美金 等写法统一为规范中文币名，
    # 多行分号拼接展开去重（如 币别="美元;美元" → "美元"）
    currency_keys = [k for k in out if is_currency_field(k)]
    for k in currency_keys:
        v = out.get(k)
        if v is not None:
            out[k] = normalize_currency_value(v)

    # 4. 数量字段规范化
    qty_keys = [k for k in out if "数量" in k or "件数" in k]
    for k in qty_keys:
        v = out.get(k)
        if v is not None:
            n = parse_float(str(v))
            if n is not None:
                out[k] = n

    # 5. 重量字段规范化
    weight_keys = [k for k in out if "重" in k]
    for k in weight_keys:
        v = out.get(k)
        if v is not None:
            n = parse_float(str(v))
            if n is not None:
                out[k] = n

    return out


def cross_validate_contract_no(file_name: str, fields: dict) -> dict:
    """文件名 ground truth 与 OCR 提取的合同号交叉校验（批次 2-2）。

    验收发现：OCR 对长数字串（如 24HCSP012260253）末位误识，产生"合同号不一致"假阳性。
    文件名由用户命名，含合同号时可信度更高：
    - 文件名能提取到合同号 → 作为 ground truth
    - OCR 合同号归一化后与文件名不一致 → 用文件名覆盖，并记录 OCR 原始值供追溯
    - 文件名无合同号 → 原样返回
    """
    fn_candidates = extract_contract_numbers(file_name)
    if not fn_candidates:
        return fields
    fn_canonical, fn_aliases = normalize_contract_no(fn_candidates)
    if not fn_canonical:
        return fields

    out: dict[str, Any] = dict(fields)
    contract_keys = [k for k in out if "合同" in k and "号" in k]
    if not contract_keys:
        return out

    for k in contract_keys:
        v = out.get(k)
        ocr_candidates = extract_contract_numbers(str(v)) if v else []
        ocr_canonical, _ = (
            normalize_contract_no(ocr_candidates) if ocr_candidates else (None, [])
        )
        if ocr_canonical and ocr_canonical not in fn_aliases:
            # OCR 合同号与文件名不一致 → 以文件名为准（防末位误识）
            out[k] = fn_canonical
            out[f"{k}__ocr_raw"] = v
            out[f"{k}__source"] = "filename_override"
    return out


def aggregate_amount(doc_type: str, docs: list[dict]) -> Optional[float]:
    """聚合同类型多张单据的金额（一对多总额比对用）。

    Args:
        doc_type: 文件类型
        docs: [{fields: {...}}, ...]

    Returns:
        总额（float），无任何金额返回 None
    """
    amount_key = None
    if doc_type in (DOC_RECEIVE_VOUCHER, DOC_PAY_VOUCHER):
        amount_key = "收款金额" if doc_type == DOC_RECEIVE_VOUCHER else "付款金额"
    elif doc_type == DOC_VAT_INVOICE:
        amount_key = "价税合计"
    else:
        return None

    total = 0.0
    found = False
    for d in docs:
        fields = d.get("fields", {}) or {}
        v = fields.get(amount_key)
        if v is not None:
            amt = parse_amount(str(v)) if isinstance(v, str) else float(v)
            if amt is not None:
                total += amt
                found = True
    return total if found else None
