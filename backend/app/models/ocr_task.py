"""OCR 任务表 ORM 模型。

支持两种触发范围：
- single_doc: 对单个 Document 启动 OCR
- contract_batch: 对某个 Contract 下所有 ocr_status='pending' 的文档批量启动 OCR
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class OcrTask(Base):
    """一次 OCR 任务（单文档或合同级批量）。"""

    __tablename__ = "ocr_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 所属规则集（命名空间），删除规则集时级联删除任务
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rule_sets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 触发范围: single_doc 或 contract_batch
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    # scope=single_doc 时关联的文档 id
    doc_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # 两种 scope 都存 contract_id，便于按合同查询任务列表
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contracts.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # 任务状态: pending / running / completed / failed
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False, index=True
    )
    # 进度百分比 0-100
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 当前阶段描述（准备中 / 识别中: 文件名 / 完成）
    stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 总数 / 已完成 / 成功 / 失败
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 失败的文档明细: [{"doc_id":..., "file_name":..., "error":...}]
    failures: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 时间戳
    start_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 任务级错误
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OcrTask {self.id} [{self.scope}/{self.status}]>"
