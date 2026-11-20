# -*- coding: utf-8 -*-
"""规则导入运行记录 ORM 模型（规则生命周期闭环：LLM 判不了的部分要看得见）。

背景：导入任务进度、解析错误此前只存在进程内存（rule_import_task._TASKS），
服务重启就没了。于是"LLM 哪些没解析出来"无人可见，人工也就无从修正——
闭环里"人工只修 LLM 判不了的"这一环缺了入口。

本表是**持久记录**：内存任务表继续负责实时进度轮询，DB 负责留档与复盘。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .mixins import TenantOwnedMixin


class RuleImportRun(TenantOwnedMixin, Base):
    """一次规则导入的持久记录（状态、计数、解析错误与警告）。"""

    __tablename__ = "rule_import_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rule_sets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 内存任务 id（前端轮询用）；同一导入的持久行按它去重更新
    task_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    file_name: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # pending|extracting|parsing|importing|conflict|done|error（与内存任务一致）
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parsed_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_rules: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported_rules: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    import_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflict_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # LLM 判不了的部分：[{"index": 3, "reason": "第 3 条：规则文本与结构化断言均为空，跳过"}]
    errors: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 导入警告（跳过的工作表、场景不一致等）
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 整次任务级失败原因（提取为空、解析异常等）
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RuleImportRun {self.task_id} [{self.status}]>"
