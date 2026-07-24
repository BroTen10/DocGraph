"""异步图谱构建服务：在后台线程中构建图谱，并追踪进度。

设计：
- 使用模块级 dict 存储构建任务进度（内存中，进程重启后丢失）
- 与审查任务类似，在后台线程中执行 LLM 调用（耗时长）
- 前端通过 task_id 轮询进度
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..database import SessionLocal
from . import graph_builder_service

logger = logging.getLogger(__name__)


# ============ 内存进度追踪 ============
# key: task_id, value: GraphBuildTaskState
_build_tasks: dict[str, "GraphBuildTaskState"] = {}
_build_tasks_lock = threading.Lock()


class GraphBuildTaskState:
    """图谱构建任务的内存状态。"""

    def __init__(self, task_id: str, operator: str, auto_confirm_all: bool):
        self.task_id = task_id
        self.status: str = "running"  # running / completed / failed
        self.progress: int = 0  # 0-100
        self.stage: str = "初始化"
        self.operator = operator
        self.auto_confirm_all = auto_confirm_all
        self.started_at: str = datetime.now().isoformat()
        self.completed_at: Optional[str] = None
        self.error: Optional[str] = None
        # 构建日志：每条规则的处理状态
        self.messages: list[dict[str, Any]] = []
        # 最终结果
        self.snapshot_id: Optional[str] = None
        self.graph_id: Optional[str] = None
        self.node_count: int = 0
        self.edge_count: int = 0
        self.rule_count: int = 0
        self.auto_confirmed_count: int = 0
        self.manual_pending_count: int = 0

    def add_message(self, level: str, stage: str, message: str) -> None:
        """添加一条进度日志。"""
        self.messages.append(
            {
                "time": datetime.now().isoformat(),
                "level": level,  # info / success / warning / error
                "stage": stage,
                "message": message,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为可返回前端的字典。"""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "operator": self.operator,
            "auto_confirm_all": self.auto_confirm_all,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "messages": self.messages,
            "snapshot_id": self.snapshot_id,
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "rule_count": self.rule_count,
            "auto_confirmed_count": self.auto_confirmed_count,
            "manual_pending_count": self.manual_pending_count,
        }


def get_build_task(task_id: str) -> Optional[GraphBuildTaskState]:
    """查询构建任务进度。"""
    with _build_tasks_lock:
        return _build_tasks.get(task_id)


def list_build_tasks(limit: int = 20) -> list[dict[str, Any]]:
    """列出最近的构建任务。"""
    with _build_tasks_lock:
        tasks = sorted(
            _build_tasks.values(),
            key=lambda t: t.started_at,
            reverse=True,
        )[:limit]
        return [t.to_dict() for t in tasks]


def start_async_build(
    operator: str = "system",
    auto_confirm_all: bool = False,
) -> str:
    """启动异步图谱构建，返回 task_id。

    在后台线程中执行 build_graph，通过状态对象追踪进度。
    """
    task_id = str(uuid.uuid4())
    state = GraphBuildTaskState(task_id, operator, auto_confirm_all)
    with _build_tasks_lock:
        _build_tasks[task_id] = state

    # 清理过旧的任务（保留最近 50 个）
    _cleanup_old_tasks()

    thread = threading.Thread(
        target=_run_async_build,
        args=(task_id, operator, auto_confirm_all),
        name=f"graph-build-{task_id}",
        daemon=True,
    )
    thread.start()

    return task_id


def _cleanup_old_tasks(max_keep: int = 50) -> None:
    """清理过旧的构建任务记录（保留最近完成的 + 正在运行的）。"""
    with _build_tasks_lock:
        if len(_build_tasks) <= max_keep:
            return
        # 按 started_at 排序，保留最新的 max_keep 个
        sorted_tasks = sorted(
            _build_tasks.items(),
            key=lambda x: x[1].started_at,
            reverse=True,
        )
        to_remove = sorted_tasks[max_keep:]
        for task_id, _ in to_remove:
            # 不删除正在运行的任务
            if _build_tasks[task_id].status == "running":
                continue
            del _build_tasks[task_id]


def _run_async_build(task_id: str, operator: str, auto_confirm_all: bool) -> None:
    """后台线程执行图谱构建。"""
    state = get_build_task(task_id)
    if state is None:
        logger.error("构建任务 %s 不存在", task_id)
        return

    db: Session = SessionLocal()
    try:
        state.progress = 3
        state.stage = "初始化"
        state.add_message("info", "开始", "开始图谱构建任务")

        # 进度回调：由 build_graph 内部调用，追踪每条规则
        def _progress_callback(stage: str, progress: int, message: str) -> None:
            state.stage = stage
            state.progress = progress
            # 只记录关键阶段消息，避免日志过多
            if (
                stage != state.messages[-1].get("stage")
                if state.messages
                else True
            ) or progress % 10 == 0 or "失败" in message:
                level = "info"
                if "完成" in message or "成功" in message:
                    level = "success"
                elif "失败" in message or "异常" in message:
                    level = "error"
                state.add_message(level, stage, message)

        # 调用 build_graph（在后台线程中执行，传入进度回调）
        result = graph_builder_service.build_graph(
            db=db,
            auto_confirm_all=auto_confirm_all,
            operator=operator,
            progress_callback=_progress_callback,
        )

        # 更新最终状态
        state.progress = 100
        state.status = "completed"
        state.stage = "完成"
        state.completed_at = datetime.now().isoformat()
        state.snapshot_id = str(result.snapshot_id)
        state.graph_id = result.graph_id
        state.node_count = result.node_count
        state.edge_count = result.edge_count
        state.rule_count = result.rule_count
        state.auto_confirmed_count = result.auto_confirmed_count
        state.manual_pending_count = result.manual_pending_count

        state.add_message(
            "success",
            "完成",
            f"图谱构建完成：{result.node_count} 节点 / {result.edge_count} 关系 "
            f"（自动确认 {result.auto_confirmed_count}，待确认 {result.manual_pending_count}）",
        )

        logger.info("异步图谱构建任务 %s 完成", task_id)

    except ValueError as e:
        state.status = "failed"
        state.error = str(e)
        state.completed_at = datetime.now().isoformat()
        state.stage = "失败"
        state.add_message("error", "失败", str(e))
        logger.warning("异步图谱构建任务 %s 失败: %s", task_id, e)
    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        state.completed_at = datetime.now().isoformat()
        state.stage = "失败"
        state.add_message("error", "失败", f"构建异常: {e}")
        logger.error(
            "异步图谱构建任务 %s 异常: %s", task_id, e, exc_info=True
        )
    finally:
        db.close()
