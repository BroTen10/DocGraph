# -*- coding: utf-8 -*-
"""结果元数据：三态之上的问题状态流转（C1）与严重度分级 + 偏离度（C2）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..constants import CHECK_COMPLETENESS

# 问题状态机（C1，Violation Situation Pattern）：open(打开) → confirmed(确认) → fixed(修复) → closed(关闭)
# 任意状态可回退 open（误判纠正）；closed 也可重开。
RESULT_STATUS_FLOW: dict[str, set[str]] = {
    "open": {"confirmed", "fixed", "closed"},
    "confirmed": {"fixed", "closed", "open"},
    "fixed": {"closed", "open"},
    "closed": {"open"},
}
VALID_RESULT_STATUSES: set[str] = set(RESULT_STATUS_FLOW)

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


def default_status(result: str) -> str:
    """结果默认状态：pass 无需处理 → closed；fail/unverifiable → open（待跟进）。"""
    return "closed" if result == "pass" else "open"


def can_transition(current: Optional[str], target: str) -> bool:
    """状态流转合法性：目标合法 且 当前状态允许（同状态幂等、无当前状态视为合法）。"""
    if target not in VALID_RESULT_STATUSES:
        return False
    if current is None or current == target:
        return True
    return target in RESULT_STATUS_FLOW.get(current, set())


def compute_severity(
    result: str,
    check_category: Optional[str],
    detail: Optional[dict] = None,
) -> tuple[Optional[str], Optional[dict]]:
    """严重度分级 + 偏离度（C2）。
    - pass → (None, None)
    - unverifiable → (low, None)：数据缺失类问题，需补档而非违规
    - fail → 按检查项与偏离度分级：
        齐套性缺件 / 币别不一致 → high
        金额/数值偏差 ≥10% 或时间偏差 ≥30 天 → high，否则 medium
        无偏离度的常规 fail → medium
    返回 (severity, deviation)，deviation 形如 {"kind": "percent"|"days", "value": ...}。
    """
    detail = detail or {}
    if result == "pass":
        return None, None
    if result == "unverifiable":
        return SEVERITY_LOW, None
    if check_category == CHECK_COMPLETENESS:
        return SEVERITY_HIGH, None
    if detail.get("required_currency"):
        return SEVERITY_HIGH, None
    deviation = _deviation_from_detail(detail)
    if deviation is not None:
        value = deviation.get("value") or 0
        if deviation.get("kind") == "percent" and value >= 10:
            return SEVERITY_HIGH, deviation
        if deviation.get("kind") == "days" and value >= 30:
            return SEVERITY_HIGH, deviation
        return SEVERITY_MEDIUM, deviation
    return SEVERITY_MEDIUM, None


def _deviation_from_detail(detail: dict) -> Optional[dict]:
    """从比较器 detail 中提取偏离度（金额百分比 / 数值百分比 / 日期天数）。"""
    if detail.get("diff_pct") is not None:
        return {
            "kind": "percent",
            "value": round(float(detail["diff_pct"]), 2),
            "src": detail.get("src_total"),
            "tgt": detail.get("tgt_total"),
        }
    if detail.get("diff") is not None and (detail.get("src") is not None or detail.get("tgt") is not None):
        diff = abs(float(detail["diff"]))
        base = abs(float(detail.get("tgt") or 0))
        pct = diff / base * 100.0 if base > 1e-9 else None
        return {
            "kind": "percent",
            "value": round(pct, 2) if pct is not None else None,
            "abs": round(diff, 4),
            "src": detail.get("src"),
            "tgt": detail.get("tgt"),
        }
    if detail.get("latest_src") and detail.get("earliest_tgt"):
        try:
            d1 = datetime.fromisoformat(str(detail["latest_src"])).date()
            d2 = datetime.fromisoformat(str(detail["earliest_tgt"])).date()
        except Exception:
            return None
        return {
            "kind": "days",
            "value": (d1 - d2).days,
            "src": detail.get("latest_src"),
            "tgt": detail.get("earliest_tgt"),
        }
    return None
