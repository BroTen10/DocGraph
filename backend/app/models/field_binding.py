# -*- coding: utf-8 -*-
"""字段绑定沉淀表 ORM 模型。

记录"规则侧字段名 ↔ 单据侧字段名"的历史绑定（别名 / 签字角色），
支撑审查运行时的本体自校准（见 services/field_binding_service.py）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .mixins import TenantOwnedMixin


class FieldBinding(TenantOwnedMixin, Base):
    """一条字段绑定知识。"""

    __tablename__ = "field_bindings"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_field_bindings_id_tenant"),
        UniqueConstraint(
            "tenant_id",
            "doc_type",
            "rule_field",
            "doc_key",
            "kind",
            name="uq_field_bindings_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 文档类型（规范名）
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 规则侧字段名（规范名），如 "报销金额"、"部门负责人签字"
    rule_field: Mapped[str] = mapped_column(String(128), nullable=False)
    # 单据侧字段名（OCR 实际键），如 "报销金额（元）"、"审批签字"
    doc_key: Mapped[str] = mapped_column(String(128), nullable=False)
    # alias（字段别名）/ role_signature（签字角色映射）
    kind: Mapped[str] = mapped_column(String(24), default="alias", nullable=False)
    # 被验证过的命中次数
    evidence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # observed（已观察）/ confirmed（已升级为别名或人工确认）/ rejected（人工否决）
    status: Mapped[str] = mapped_column(String(16), default="observed", nullable=False, index=True)
    # 命中来源样例（便于人工核对）
    sample_doc: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sample_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 触发过该绑定的规则流水号
    rule_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FieldBinding {self.doc_type}: {self.rule_field} ← {self.doc_key} "
            f"[{self.kind}/{self.status} x{self.evidence_count}]>"
        )
