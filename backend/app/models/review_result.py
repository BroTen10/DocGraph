"""审查结果表 ORM 模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
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
    # ----- 批次 9（C1/C2）：问题闭环与严重度 -----
    # 问题状态机：open / confirmed / fixed / closed（pass 默认 closed，fail/unverifiable 默认 open）
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    # 状态流转审计历史：[{"status": "...", "at": "...", "by": "...", "note": "..."}]
    status_history: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 严重度分级：high / medium / low（pass 为 null）
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 偏离度：{"kind": "percent"|"days", "value": ..., "src": ..., "tgt": ...}
    deviation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 关联图谱实体（COMPARE_TO 的 source/target 节点名；旧逻辑/齐套性等可为 null）
    graph_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    graph_target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ----- 批次 10 Phase C：结果来源与置信度（双引擎审查）-----
    # 来源：graph（图谱确定性引擎）/ llm（LLM 语义审查）/ legacy（旧逻辑 fallback）；旧数据为 null
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # LLM 语义审查的置信度 (0-1)；确定性结果与旧数据为 null
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ----- -----
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
