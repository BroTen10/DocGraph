"""规则快照表 ORM 模型。每次重建图谱保留一份规则集快照。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class RuleSnapshot(Base):
    """规则集快照：每次"一键重建图谱"时保存当前启用规则集。"""

    __tablename__ = "rule_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    snapshot_time: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    rule_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 完整规则集（含已停用规则，便于追溯）
    rules_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 对应写入 Neo4j 的 graph_id（与 Neo4j 节点 graph_id 一致）
    graph_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 节点数 / 关系数（图谱构建完成后回填）
    node_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edge_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
