"""Cypher 查询安全防护。

参考 MiroFish-Explorer 的 cypher_guard.py，重新实现一个精简版本：
- 参数名必须是合法标识符
- 参数值（字符串）中检测危险 Cypher 关键字
- 只读查询白名单校验（防止误执行写操作）
"""

from __future__ import annotations

import re
from typing import Optional

# 安全标识符正则（参数名、节点变量名）
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Cypher 危险关键字（用于只读/写判定）
_WRITE_KEYWORDS = [
    "CREATE", "DELETE", "MERGE", "SET", "REMOVE",
    "DROP", "ALTER", "GRANT", "REVOKE", "DETACH",
]
_WRITE_RE = re.compile(
    r"\b(" + "|".join(_WRITE_KEYWORDS) + r")\b", re.IGNORECASE
)

# 危险子句（用于参数值检测，防止注入）
_DANGEROUS_PARAM_PATTERNS = [
    r"\bMATCH\b", r"\bCREATE\b", r"\bDELETE\b", r"\bMERGE\b",
    r"\bRETURN\b", r"\bUNION\b", r"\bCALL\b", r"\bYIELD\b",
    r"apoc\.", r"\bLOAD\s+CSV\b",
]
_DANGEROUS_PARAM_RE = re.compile("|".join(_DANGEROUS_PARAM_PATTERNS), re.IGNORECASE)


def validate_cypher_params(
    params: dict,
    check_string_values: bool = True,
) -> tuple[bool, Optional[str]]:
    """校验 Cypher 参数字典。

    - 键必须是合法标识符
    - 字符串值中不能包含危险 Cypher 关键字
    - 嵌套 dict / list 递归校验

    check_string_values=False 时仅校验键名/结构，不扫描字符串值内容——
    用于业务内容（规则文本、描述等）作为图属性写入的场景：查询语句本身
    全部硬编码参数化，参数值中的危险词不构成注入；误报会阻止合法业务数据落库。
    """
    if not isinstance(params, dict):
        return False, "参数必须是字典类型"
    return _validate_value(params, "", check_string_values)


def _validate_value(value, path: str, check_string_values: bool) -> tuple[bool, Optional[str]]:
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or not _SAFE_IDENTIFIER.match(k):
                return False, f"参数名格式无效: {path}{k}"
            ok, err = _validate_value(v, f"{path}{k}.", check_string_values)
            if not ok:
                return False, err
    elif isinstance(value, list):
        for i, v in enumerate(value):
            ok, err = _validate_value(v, f"{path}[{i}].", check_string_values)
            if not ok:
                return False, err
    elif isinstance(value, str) and check_string_values:
        if _DANGEROUS_PARAM_RE.search(value):
            return False, f"参数值包含可疑 Cypher 关键字: {path}"
    return True, None


def is_safe_query(query: str) -> tuple[bool, Optional[str]]:
    """判断是否为只读查询（不含写操作关键字）。"""
    if not isinstance(query, str) or not query.strip():
        return False, "查询必须是非空字符串"
    if _WRITE_RE.search(query):
        return False, "查询包含写操作关键字"
    return True, None


def is_write_query(query: str) -> tuple[bool, Optional[str]]:
    """判断是否为合法写查询（必须包含写操作关键字）。"""
    if not isinstance(query, str) or not query.strip():
        return False, "查询必须是非空字符串"
    if not _WRITE_RE.search(query):
        return False, "写查询必须包含写操作关键字"
    return True, None
