"""图谱构建与确认路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.graph import (
    GraphBuildResponse,
    GraphConfirmRequest,
    GraphData,
)
from ..services import graph_builder_service, rule_service

router = APIRouter(prefix="/api/rules", tags=["graph"])


@router.post("/build-graph", response_model=GraphBuildResponse)
def build_graph(
    auto_confirm_all: bool = False,
    operator: str = "system",
    db: Session = Depends(get_db),
) -> GraphBuildResponse:
    """一键重建图谱（全量替换）。

    Query 参数：
    - auto_confirm_all: 是否一键自动确认全部（忽略置信度）
    - operator: 操作人
    """
    try:
        return graph_builder_service.build_graph(
            db=db, auto_confirm_all=auto_confirm_all, operator=operator
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图谱构建失败: {e}")


@router.get("/graph", response_model=GraphData)
def get_latest_graph(db: Session = Depends(get_db)) -> GraphData:
    """查看最新规则图谱。"""
    snap = rule_service.get_latest_snapshot(db)
    if snap is None or not snap.graph_id:
        raise HTTPException(status_code=404, detail="暂无图谱，请先构建")
    return graph_builder_service.get_graph(None, snap.graph_id)


@router.get("/graph/{graph_id}", response_model=GraphData)
def get_graph(graph_id: str) -> GraphData:
    """查看指定图谱。"""
    return graph_builder_service.get_graph(None, graph_id)


@router.put("/graph/confirm", response_model=GraphData)
def confirm_graph(
    payload: GraphConfirmRequest, db: Session = Depends(get_db)
) -> GraphData:
    """人工确认/编辑图谱后写入生效。"""
    return graph_builder_service.apply_graph_edits(None, payload.graph_id, payload.edits)
