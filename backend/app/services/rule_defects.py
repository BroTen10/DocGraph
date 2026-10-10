"""规则缺陷语义：未处理/已处理判定、规则健康度归桶、缺陷消解。

方案 A（用户认可即视为缺陷已处理）的单一事实来源：

1. 每条缺陷可带 ``resolution`` 字段：
   - ``"resolved"``：用户（或系统自动确认）已认可/已处理；
   - 缺失或 ``"open"``：未处理。
   已处理缺陷仍保留在 ``defects`` 列表中，用于审计与展示，不做物理删除。

2. 规则按"最严重的未处理缺陷"归入唯一页签（互斥且穷尽）：
   冲突 > 错误 > 警告 > 提示 > 正常规则。
   因此 ``全部 = 正常 + 冲突 + 错误 + 警告 + 提示``，各页签计数都是**规则条数**。

3. "确认并启用规则"即用户认可该规则及其全部缺陷，此时把未处理缺陷批量标记为已处理，
   规则随即进入「正常规则」。冲突检测重新发现缺陷时会重新打开（reopen），
   避免已处理的旧缺陷掩盖新出现的问题。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

# 冲突类缺陷（规则间矛盾/边界重叠/冗余），优先级最高
CONFLICT_TYPES = frozenset({"logical_contradiction", "boundary_overlap", "redundant"})
# 实质缺陷：影响规则能否参与图谱构建与文档审查
BLOCKING_SEVERITIES = ("error", "warning")
# 归桶顺序（互斥）
BUCKETS = ("conflict", "error", "warning", "info", "healthy")
SEVERITIES = ("error", "warning", "info")

RESOLVED = "resolved"
OPEN = "open"

# 消解来源：resolved_by 记录"谁认可了这条缺陷"
RESOLVED_BY_CONFIRM = "user"  # 交互式确认（默认操作人）
RESOLVED_BY_IMPORT = "system:import"  # 导入时无实质缺陷，系统自动确认
RESOLVED_BY_CONFLICT_CLEARED = "system:conflict-cleared"  # 冲突检测确认冲突已消除
RESOLVED_BY_MIGRATION = "system:migration"  # 存量回填

CONFIRM_NOTE = "确认规则时视为已认可该缺陷"
AUTO_CONFIRM_NOTE = "导入时无实质缺陷，自动确认并接受该提示"
CONFLICT_CLEARED_NOTE = "冲突检测确认该冲突已消除"
MIGRATION_NOTE = "历史数据：规则此前已确认启用，视为用户已认可"


def _get(defect: Any, key: str, default: Any = None) -> Any:
    """兼容 dict（数据库 JSONB）与 Pydantic/ORM 对象（schema 输出）。"""
    if isinstance(defect, dict):
        return defect.get(key, default)
    return getattr(defect, key, default)


def is_resolved(defect: Any) -> bool:
    """缺陷是否已被认可/处理。"""
    return str(_get(defect, "resolution") or "").strip().lower() == RESOLVED


def is_open(defect: Any) -> bool:
    """缺陷是否仍未处理。"""
    return not is_resolved(defect)


def is_conflict_defect(defect: Any) -> bool:
    """是否为冲突类缺陷。"""
    return str(_get(defect, "type") or "") in CONFLICT_TYPES


def severity_of(defect: Any) -> str:
    """缺陷严重度（缺省按 info 处理）。"""
    severity = str(_get(defect, "severity") or "info").strip().lower()
    return severity if severity in SEVERITIES else "info"


def open_defects(defects: Iterable[Any] | None) -> list[Any]:
    """未处理缺陷列表。"""
    return [d for d in (defects or []) if is_open(d)]


def resolved_defects(defects: Iterable[Any] | None) -> list[Any]:
    """已处理缺陷列表。"""
    return [d for d in (defects or []) if is_resolved(d)]


def classify(defects: Iterable[Any] | None) -> str:
    """把规则归入唯一页签：conflict / error / warning / info / healthy。"""
    pending = open_defects(defects)
    if any(is_conflict_defect(d) for d in pending):
        return "conflict"
    for severity in SEVERITIES:
        if any(severity_of(d) == severity for d in pending):
            return severity
    return "healthy"


def matches_bucket(defects: Iterable[Any] | None, bucket: str) -> bool:
    """规则是否属于指定页签（``none`` 与 ``healthy`` 等价）。"""
    key = "healthy" if bucket == "none" else bucket
    return classify(defects) == key


def is_healthy(defects: Iterable[Any] | None) -> bool:
    """正常规则：无任何未处理缺陷。"""
    return classify(defects) == "healthy"


def has_open_blocking_defect(defects: Iterable[Any] | None) -> bool:
    """是否存在未处理的实质缺陷（冲突 / error / warning）。"""
    return any(
        is_conflict_defect(d) or severity_of(d) in BLOCKING_SEVERITIES
        for d in open_defects(defects)
    )


def open_severity_counts(defects: Iterable[Any] | None) -> dict[str, int]:
    """未处理缺陷按严重度计数（用于缺陷弹层等逐条展示场景）。"""
    counts = {severity: 0 for severity in SEVERITIES}
    for defect in open_defects(defects):
        counts[severity_of(defect)] += 1
    return counts


def _stamp(at: datetime | None) -> str:
    return (at or datetime.now()).isoformat(timespec="seconds")


def resolve_defects(
    defects: Iterable[Any] | None,
    *,
    by: str = RESOLVED_BY_CONFIRM,
    note: str | None = CONFIRM_NOTE,
    at: datetime | None = None,
) -> tuple[list[dict], int]:
    """把未处理缺陷标记为已处理。

    Returns:
        (缺陷列表, 本次消解条数)。已处理的条目保持原样（幂等）。
    """
    stamp = _stamp(at)
    result: list[dict] = []
    resolved_count = 0
    for raw in defects or []:
        if not isinstance(raw, dict):
            result.append(raw)
            continue
        defect = dict(raw)
        if is_open(defect):
            defect["resolution"] = RESOLVED
            defect["resolved_at"] = stamp
            defect["resolved_by"] = by
            if note:
                defect["resolution_note"] = note
            resolved_count += 1
        result.append(defect)
    return result, resolved_count


def reopen_defects(
    defects: Iterable[Any] | None,
    *,
    types: Iterable[str] | None = None,
) -> tuple[list[dict], int]:
    """重新打开已处理的缺陷（同一问题再次出现时使用）。

    Args:
        types: 仅重开这些缺陷类型；None 表示全部类型。
    """
    wanted = set(types) if types else None
    result: list[dict] = []
    reopened = 0
    for raw in defects or []:
        if not isinstance(raw, dict):
            result.append(raw)
            continue
        defect = dict(raw)
        if is_resolved(defect) and (wanted is None or str(defect.get("type") or "") in wanted):
            for key in ("resolution", "resolved_at", "resolved_by", "resolution_note"):
                defect.pop(key, None)
            reopened += 1
        result.append(defect)
    return result, reopened
