"""审查结果表 ORM 模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class ReviewResult(Base):
    """单条规则的审查结果。三态：pass / fail / unverifiable。"""

    __tablename__ = "review_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_tasks.id", ondelete="CASCADE"), index=True
    )
    # 关联规则（不强制外键，因为规则可能被删除/快照保留）
    rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # 冗余字段：规则文本与文件类型，便于历史查看
    rule_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 关联文档（可空，齐套性等规则可能不绑定单个文档）
    doc_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    # 结果：pass / fail / unverifiable
    result: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 问题描述
    issue_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 详细差异（字段、期望值、实际值）
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # 修正建议
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    task: Mapped["ReviewTask"] = relationship("ReviewTask", back_populates="results")
    document: Mapped["Document | None"] = relationship("Document", back_populates="review_results")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ReviewResult [{self.result}]>"
