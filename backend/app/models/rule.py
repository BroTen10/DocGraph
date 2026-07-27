"""规则表 ORM 模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Rule(Base):
    """审查规则：按"文件类型 × 检查项"二维组织，自然语言描述 + 容差参数。"""

    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 所属规则集（命名空间），删除规则集时级联删除规则
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rule_sets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 文件类型：代理协议 / 委托出口确认单 / 出口报关单 / 运单 / 签收单 /
    # 出入仓单 / 派车单 / 收款水单 / 付款水单
    doc_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # 检查项类别：齐套性 / 基础判断 / 信息准确性 / 时间逻辑
    check_category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    # 自然语言规则文本
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 容差参数：{"amount_percent":5,"weight_kg":0.5,"time_days":0,"allow_same_day":true}
    tolerance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # 启用状态
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 优先级（数字越小越先执行）
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    # ----- LLM 置信度与确认状态 -----
    # LLM 解析该规则时的置信度 (0-1)，null 表示旧数据未评估
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # 确认状态：'pending' 待确认 / 'confirmed' 已确认
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # ----- -----
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # 反向关系
    rule_set: Mapped["RuleSet"] = relationship("RuleSet", back_populates="rules")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Rule [{self.doc_type}/{self.check_category}] status={self.status}>"
