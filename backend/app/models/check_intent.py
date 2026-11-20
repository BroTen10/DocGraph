"""检查项（检查意图）注册表 ORM 模型。

替代「rule_sets.check_categories 字符串数组 + 常量清单」的隐式约定，
让检查项与文档类型一样成为**可登记、可审核、可改名/合并**的一等维度。

检查项来源：
1. 内置种子（constants.CHECK_CATEGORIES，初始化时写入）
2. 规则导入时 LLM 新发现的意图（status=pending_review，人工确认后生效）
3. 用户手动创建
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .mixins import TenantOwnedMixin


class CheckIntent(TenantOwnedMixin, Base):
    """检查项定义（租户内唯一名称）。"""

    __tablename__ = "check_intents"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_check_intents_id_tenant"),
        UniqueConstraint("tenant_id", "name", name="uq_check_intents_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 规范名称（租户内唯一），如"齐套性"、"金额一致性"
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 简短说明（展示用）
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 显式别名：写时归一用，如 金额一致性 aliases=["金额校验", "金额核对"]
    aliases: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 展示与执行顺序（数字小靠前；内置四类按 10/20/30/40）
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    # 来源：seed（内置）/ rule_import（规则导入发现）/ manual（人工创建）
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    # 状态：active（已确认，参与矩阵分列与执行分派）/ pending_review（待确认）/ rejected
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CheckIntent {self.name} [{self.status}]>"
