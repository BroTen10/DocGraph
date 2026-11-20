# -*- coding: utf-8 -*-
"""规则导入运行记录：把"LLM 判不了的部分"留档，供人工修正与复盘。

内存任务表（rule_import_task._TASKS）负责实时进度轮询；本服务负责**持久留档**：
状态变化与终态时写一次 DB（导入过程中状态变化只有几次，写入成本可忽略），
失败绝不影响导入本身。

为什么需要它：解析失败此前重启即丢，人工看不到"哪些规则没解析出来"，
闭环里"人工只修 LLM 判不了的"这一环就断了入口。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RuleImportRun

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"done", "error"})
# 单次导入最多留档的解析错误条数（避免一次导入写爆 JSONB）
MAX_STORED_ERRORS = 200
MAX_STORED_WARNINGS = 50


def _errors_from_result(result: Any) -> list[dict]:
    """把导入结果里的 errors（字符串列表）转成带序号的留档结构。"""
    if not isinstance(result, dict):
        return []
    out: list[dict] = []
    for idx, item in enumerate(result.get("errors") or [], start=1):
        text = str(item or "").strip()
        if not text:
            continue
        out.append({"index": idx, "reason": text[:1000]})
        if len(out) >= MAX_STORED_ERRORS:
            break
    return out


def _warnings_from_result(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    seen: list[str] = []
    for item in result.get("import_warnings") or []:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text[:1000])
        if len(seen) >= MAX_STORED_WARNINGS:
            break
    return seen


def _apply_task(run: RuleImportRun, task: Any) -> None:
    """把内存任务快照同步到持久行（只取需要留档的字段）。"""
    run.status = str(getattr(task, "status", run.status) or run.status)[:16]
    run.message = str(getattr(task, "message", "") or "")[:255]
    run.total_chunks = int(getattr(task, "total_chunks", 0) or 0)
    run.parsed_chunks = int(getattr(task, "parsed_chunks", 0) or 0)
    run.total_rules = int(getattr(task, "total_rules", 0) or 0)
    run.imported_rules = int(getattr(task, "imported_rules", 0) or 0)
    run.import_errors = int(getattr(task, "import_errors", 0) or 0)
    run.conflict_found = int(getattr(task, "conflict_found", 0) or 0)
    task_error = getattr(task, "error", None)
    if task_error:
        run.error = str(task_error)[:4000]
    result = getattr(task, "result", None)
    errors = _errors_from_result(result)
    if errors:
        run.errors = errors
        run.import_errors = max(run.import_errors, len(errors))
    warnings = _warnings_from_result(result)
    if warnings:
        run.warnings = warnings
    if run.status in TERMINAL_STATUSES and run.finished_at is None:
        run.finished_at = datetime.now()


def persist_task(task: Any, *, force: bool = False) -> None:
    """把一次导入任务写进持久表（best-effort：失败只记日志，绝不影响导入）。"""
    from ..database import SessionLocal, bind_tenant

    task_id = str(getattr(task, "task_id", "") or "")
    if not task_id:
        return
    try:
        tenant_id = uuid.UUID(str(getattr(task, "tenant_id")))
    except (TypeError, ValueError):
        return

    db = SessionLocal()
    try:
        bind_tenant(db, tenant_id)
        run = db.execute(
            select(RuleImportRun).where(
                RuleImportRun.tenant_id == tenant_id,
                RuleImportRun.task_id == task_id,
            )
        ).scalars().first()
        if run is None:
            rule_set_id = getattr(task, "rule_set_id", None)
            if not rule_set_id:
                return
            run = RuleImportRun(
                tenant_id=tenant_id,
                rule_set_id=uuid.UUID(str(rule_set_id)),
                task_id=task_id,
                file_name=str(getattr(task, "file_name", "") or "")[:512],
                status="pending",
            )
            try:
                run.user_id = uuid.UUID(str(getattr(task, "user_id")))
            except (TypeError, ValueError):
                run.user_id = None
            db.add(run)
        _apply_task(run, task)
        db.commit()
    except Exception:  # pragma: no cover - 留档失败不影响导入
        logger.warning("导入运行记录写入失败（不影响导入）: task=%s", task_id, exc_info=True)
        db.rollback()
    finally:
        db.close()


def list_runs(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    *,
    limit: int = 20,
) -> list[RuleImportRun]:
    """按时间倒序列出规则集的导入记录（供"导入记录"面板复盘 LLM 判不了的部分）。"""
    rows = db.execute(
        select(RuleImportRun)
        .where(
            RuleImportRun.tenant_id == tenant_id,
            RuleImportRun.rule_set_id == rule_set_id,
        )
        .order_by(RuleImportRun.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).scalars().all()
    return list(rows)


def get_run(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> Optional[RuleImportRun]:
    return db.execute(
        select(RuleImportRun).where(
            RuleImportRun.tenant_id == tenant_id,
            RuleImportRun.id == run_id,
        )
    ).scalars().first()
