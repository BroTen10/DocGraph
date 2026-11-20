# -*- coding: utf-8 -*-
"""人工修正账本 ORM 模型（规则生命周期闭环）。

记录"人把 LLM 的产出改成了什么"（规则字段、文件类型、检查项、容差、结构……）。
修正本身是**数据**：解析/判定提示词会读到它，模型据此在下次少犯同样的错。
人工只处理 LLM 判不了的部分，账本保证**同一类修正不需要出现第二次**。

设计约束见 docs/项目基线-智能优先原则.md：修正不在代码里加分支，只作为数据回灌给模型。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .mixins import TenantOwnedMixin


class LlmCorrection(TenantOwnedMixin, Base):
    """一条人工修正记录；同类修正按 signature 累加 hit_count。"""

    __tablename__ = "llm_corrections"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_llm_corrections_id_tenant"),
        UniqueConstraint("tenant_id", "signature", name="uq_llm_corrections_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 修正发生的阶段：rule_edit / rule_parse / graph_edit / doc_type / ocr_field / review_status
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 去重签名 sha1(scope|rule_set|field|before|after)：长文本不进唯一索引
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=True
    )
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 业务上下文，帮助模型理解这条修正发生在什么单据/规则上
    doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 被修正的字段名：doc_type / check_category / tolerance / structure / scope / rule_text ...
    field: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    before: Mapped[str] = mapped_column(Text, default="", nullable=False)
    after: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 规则原文 / 来源行等上下文（截断存储）
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 同类修正被人工重复确认的次数：越高越应优先回灌
    hit_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LlmCorrection {self.scope}/{self.field} x{self.hit_count}>"
