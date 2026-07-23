"""规则表 ORM 模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Rule(Base):
    """审查规则：按"文件类型 × 检查项"二维组织，自然语言描述 + 容差参数。"""

    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Rule [{self.doc_type}/{self.check_category}]>"
