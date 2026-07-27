"""规则集 ORM 模型。

RuleSet 作为"命名空间"，让系统可以存放多套审查规则，
每套规则对应自己的合同、文档、图谱、审查任务，完全隔离。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class RuleSet(Base):
    """规则集：一套独立的审查规则命名空间。

    一个 RuleSet 下挂载多个 Rule、RuleSnapshot、Contract，
    通过 rule_set_id 外键实现完全隔离。
    """

    __tablename__ = "rule_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 规则集名称（唯一）
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # 规则集描述
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 适用文件类型清单，如 ["代理协议","委托单","报关单"]
    doc_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 是否默认规则集（同一时刻只能有一个默认）
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 反向关系
    rules: Mapped[list["Rule"]] = relationship(
        "Rule", back_populates="rule_set", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["RuleSnapshot"]] = relationship(
        "RuleSnapshot", back_populates="rule_set", cascade="all, delete-orphan"
    )
    contracts: Mapped[list["Contract"]] = relationship(
        "Contract", back_populates="rule_set", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RuleSet {self.name}>"
