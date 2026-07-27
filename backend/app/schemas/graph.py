"""图谱相关 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EntityData(BaseModel):
    """图谱节点（实体/字段）。"""
    name: str
    type: str = "Field"
    attributes: dict[str, Any] = Field(default_factory=dict)


class EdgeData(BaseModel):
    """图谱边（比对关系）。"""
    source: str
    target: str
    type: str = "COMPARE_TO"
    attributes: dict[str, Any] = Field(default_factory=dict)


class RuleGraphConvertResult(BaseModel):
    """LLM 规则转图谱的输出契约。"""
    entities: list[EntityData]
    relationships: list[EdgeData]
    confidence: float = 0.0
    auto_confirmed: bool = False
    low_confidence_items: list[dict] = Field(default_factory=list)


class GraphData(BaseModel):
    """图谱可视化数据。"""
    graph_id: str
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0


class GraphBuildResponse(BaseModel):
    """一键重建图谱响应。"""
    snapshot_id: UUID
    graph_id: str
    node_count: int
    edge_count: int
    rule_count: int
    auto_confirmed_count: int = 0
    manual_pending_count: int = 0
    message: str = "图谱构建完成"


class GraphEditOp(BaseModel):
    """单个人工编辑操作。"""
    op: str  # update_node | update_edge | delete_node | delete_edge
    node_name: str | None = None
    source: str | None = None
    target: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphConfirmRequest(BaseModel):
    """人工确认/编辑图谱。"""
    graph_id: str
    edits: list[GraphEditOp] = Field(default_factory=list)


class GraphSnapshotOut(BaseModel):
    """规则快照。"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_set_id: UUID
    snapshot_time: datetime
    rule_count: int
    graph_id: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    operator: str | None = None
    note: str | None = None
