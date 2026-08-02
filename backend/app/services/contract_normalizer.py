"""合同号识别与归一化。

支持以下写法归一到主号：
- 主号 24HCSP012260253
- 主号+分批号 24HCSP012260253-10 → 归一到主号 24HCSP012260253
- 外贸合同号 24YK097 → 与主号建立映射（同一合同）
- 双号并用 24HCSP012260253-10 24YK097 → 识别为同一合同

归一化策略：
1. 从文件名和文本中提取所有候选合同号
2. 主号优先（HCSP/HCSPxxx 格式），分批号去尾部 -NN 归一
3. 外贸合同号（如 YKxxx）作为别名
4. 同一合同的多种写法归一到同一个主号
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 主合同号格式：2位年份 + HCSP + 9位数字（如 24HCSP012260253）
_MAIN_CONTRACT_PATTERN = re.compile(r"\b(\d{2}HCSP\d{6,12})\b", re.IGNORECASE)
# 主合同号 + 分批号：24HCSP012260253-10
# 注意：分隔符必须显式出现（[-_\s]+），不能用 * —— 否则贪婪回溯会把主号末位数字
# 当成分批号吞掉（24HCSP012260253 → 24HCSP01226025 + 3），产生错误主号。
_MAIN_WITH_BATCH_PATTERN = re.compile(
    r"\b(\d{2}HCSP\d{6,12})[-_\s]+(\d{1,3})\b", re.IGNORECASE
)
# 外贸合同号：2位年份 + 字母 + 数字（如 24YK097）
_FOREIGN_CONTRACT_PATTERN = re.compile(r"\b(\d{2}[A-Z]{2,5}\d{2,6})\b")


def extract_contract_numbers(text: str) -> list[str]:
    """从文本中提取所有候选合同号（去重保序）。"""
    found: list[str] = []
    seen: set[str] = set()

    # 1. 主合同号 + 分批号
    for m in _MAIN_WITH_BATCH_PATTERN.finditer(text):
        no = m.group(1).upper()
        if no not in seen:
            seen.add(no)
            found.append(no)
    # 2. 主合同号（无分批号）
    for m in _MAIN_CONTRACT_PATTERN.finditer(text):
        no = m.group(1).upper()
        if no not in seen:
            seen.add(no)
            found.append(no)
    # 3. 外贸合同号
    for m in _FOREIGN_CONTRACT_PATTERN.finditer(text):
        no = m.group(1).upper()
        # 排除已被主号匹配的（不会重叠，但保险起见）
        if no not in seen:
            seen.add(no)
            found.append(no)

    return found


def normalize_contract_no(
    candidates: list[str],
) -> tuple[Optional[str], list[str]]:
    """将候选合同号归一化为一个主号 + 别名列表。

    Args:
        candidates: 候选合同号列表（已去重）

    Returns:
        (canonical_no, alias_list)
        - canonical_no: 归一化后的主合同号（如 24HCSP012260253）
        - alias_list: 所有候选号（含主号本身，去重）
        若无任何候选，返回 (None, [])
    """
    if not candidates:
        return None, []

    # 大写化
    candidates = [c.upper() for c in candidates]
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    # 找主号：优先 HCSP 格式
    main_nos = [c for c in unique if _MAIN_CONTRACT_PATTERN.fullmatch(c)]
    if main_nos:
        # 主号本身已不含分批号后缀（_MAIN_CONTRACT_PATTERN 不会匹配带 -10 的）。
        # 多候选时优先取最长——更可能是完整主号（如 24HCSP012260253 > 24HCSP01226025）
        canonical = max(main_nos, key=len)
    else:
        # 无主号，取第一个候选作为规范号
        canonical = unique[0]

    # 别名 = 所有候选号（含主号本身），用于 alias_list
    alias_list = unique
    return canonical, alias_list


def normalize_from_filename_and_text(
    filename: str, text: str = ""
) -> tuple[Optional[str], list[str]]:
    """便捷方法：从文件名 + OCR 文本提取并归一化合同号。"""
    candidates = extract_contract_numbers(filename)
    if text:
        candidates.extend(extract_contract_numbers(text))
    return normalize_contract_no(candidates)


def merge_aliases(
    existing_aliases: list[str], new_aliases: list[str]
) -> list[str]:
    """合并已有别名与新别名，去重保序。"""
    seen: set[str] = set()
    merged: list[str] = []
    for a in list(existing_aliases) + list(new_aliases):
        a_up = a.upper()
        if a_up not in seen:
            seen.add(a_up)
            merged.append(a_up)
    return merged
