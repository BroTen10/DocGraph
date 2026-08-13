"""规则导入异步任务框架。

把「上传文档 → 提取文本 → 调 LLM 解析 → 入库 → 冲突检测」这条长链路
从同步 HTTP 响应里拆出来，改为：
  1) 接口接收文件后立即落盘并返回 task_id；
  2) 后台线程执行完整流程，进度写入内存任务表；
  3) 前端通过 GET /api/rules/import-tasks/{task_id} 轮询进度。

任务存储在进程内存（单 worker 够用，进程重启会丢进行中的任务，属于可接受范围）。
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ImportProgress:
    """一次导入任务的进度快照。所有字段均为线程安全赋值。"""

    task_id: str
    rule_set_id: str
    status: str = "pending"  # pending|extracting|parsing|importing|conflict|done|error
    message: str = ""
    file_name: str = ""
    # 解析阶段
    total_chunks: int = 0
    parsed_chunks: int = 0
    # 入库阶段
    total_rules: int = 0
    imported_rules: int = 0
    import_errors: int = 0
    # 冲突检测阶段
    conflict_total: int = 0
    conflict_done: int = 0
    conflict_found: int = 0
    # 结果
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "rule_set_id": self.rule_set_id,
            "status": self.status,
            "message": self.message,
            "file_name": self.file_name,
            "total_chunks": self.total_chunks,
            "parsed_chunks": self.parsed_chunks,
            "total_rules": self.total_rules,
            "imported_rules": self.imported_rules,
            "import_errors": self.import_errors,
            "conflict_total": self.conflict_total,
            "conflict_done": self.conflict_done,
            "conflict_found": self.conflict_found,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_TASKS: dict[str, ImportProgress] = {}
_TASKS_LOCK = threading.Lock()


def get_task(task_id: str) -> Optional[ImportProgress]:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def create_task(rule_set_id: str, file_name: str) -> ImportProgress:
    now = datetime.now(timezone.utc).isoformat()
    task = ImportProgress(
        task_id=__import__("uuid").uuid4().hex,
        rule_set_id=str(rule_set_id),
        file_name=file_name,
        created_at=now,
        updated_at=now,
    )
    with _TASKS_LOCK:
        _TASKS[task.task_id] = task
    return task


def update_task(task: ImportProgress, **kwargs: Any) -> None:
    """线程安全地更新任务字段并刷新 updated_at。"""
    for k, v in kwargs.items():
        if hasattr(task, k):
            setattr(task, k, v)
    task.updated_at = datetime.now(timezone.utc).isoformat()


def run_import_task(
    task: ImportProgress,
    file_path: str,
    filename: str,
    skill_ids: list | None = None,
) -> None:
    """后台线程入口：执行完整导入流程并更新任务进度。

    异常会被捕获并写入 task.error，不会导致进程崩溃。
    """
    from ..database import SessionLocal
    from .rule_document_import_service import extract_text_from_file
    from .rule_import_service import import_rules_with_skills

    db = SessionLocal()
    try:
        # 阶段 1：提取文本
        update_task(task, status="extracting", message="正在提取文档文本…")
        text = extract_text_from_file(file_path, filename)
        if not text or not text.strip():
            update_task(task, status="error", error="文件内容为空，无法提取规则文本")
            return

        # 阶段 2+3：解析 → 入库 → 冲突检测（带进度）
        update_task(task, status="parsing", message="正在调用大模型解析规则…")
        result = import_rules_with_skills(
            db, task.rule_set_id, text, skill_ids=skill_ids, progress=task
        )
        update_task(
            task,
            status="done",
            message="导入完成",
            total_rules=result.get("total", 0),
            imported_rules=result.get("imported", 0),
            import_errors=len(result.get("errors", [])),
            result=result,
        )
        logger.info(
            "导入任务 %s 完成: total=%s imported=%s",
            task.task_id,
            result.get("total"),
            result.get("imported"),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("导入任务 %s 失败", task.task_id)
        update_task(task, status="error", error=f"{type(e).__name__}: {e}")
    finally:
        db.close()
        try:
            os.unlink(file_path)
        except OSError:
            pass
