"""审查任务表 ORM 模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class ReviewTask(Base):
    """一次审查任务（一个合同触发一次）。"""

    __tablename__ = "review_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), index=True
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 任务状态：pending / running / completed / failed
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # 进度百分比 0-100
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 当前阶段描述（OCR中 / 字段提取中 / 规则比对中 / 生成报告中）
    stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 使用的规则快照 ID
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    # 汇总统计
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract: Mapped["Contract"] = relationship("Contract", back_populates="review_tasks")
    results: Mapped[list["ReviewResult"]] = relationship(
        "ReviewResult", back_populates="task", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ReviewTask {self.id} [{self.status}]>"
