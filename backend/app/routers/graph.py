"""图谱构建与确认路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.graph import (
    GraphBuildResponse,
    GraphConfirmRequest,
    GraphData,
)
from ..services import (
    graph_builder_service,
    graph_build_progress,
    rule_document_import_service,
    rule_service,
)

router = APIRouter(prefix="/api/rules", tags=["graph"])


@router.post("/build-graph", response_model=GraphBuildResponse)
def build_graph(
    auto_confirm_all: bool = False,
    operator: str = "system",
    db: Session = Depends(get_db),
) -> GraphBuildResponse:
    """一键重建图谱（全量替换，同步）。

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


# ============ 异步图谱构建（带进度追踪） ============


class AsyncBuildResponse(BaseModel):
    """异步构建启动响应。"""
    task_id: str
    message: str = "图谱构建已启动"


class BuildTaskStatus(BaseModel):
    """构建任务状态。"""
    task_id: str
    status: str  # running / completed / failed
    progress: int
    stage: str
    operator: str
    auto_confirm_all: bool
    started_at: str
    completed_at: str | None = None
    error: str | None = None
    messages: list[dict[str, Any]] = []
    snapshot_id: str | None = None
    graph_id: str | None = None
    node_count: int = 0
    edge_count: int = 0
    rule_count: int = 0
    auto_confirmed_count: int = 0
    manual_pending_count: int = 0


@router.post("/build-graph-async", response_model=AsyncBuildResponse)
def build_graph_async(
    auto_confirm_all: bool = False,
    operator: str = "system",
) -> AsyncBuildResponse:
    """异步启动图谱构建（后台线程执行），返回 task_id 用于轮询进度。

    适合规则较多、LLM 调用耗时较长的场景。
    """
    task_id = graph_build_progress.start_async_build(
        operator=operator, auto_confirm_all=auto_confirm_all
    )
    return AsyncBuildResponse(task_id=task_id, message="图谱构建已启动，请通过 task_id 轮询进度")


@router.get("/build-graph-status/{task_id}", response_model=BuildTaskStatus)
def get_build_status(task_id: str) -> BuildTaskStatus:
    """查询异步图谱构建任务进度。"""
    state = graph_build_progress.get_build_task(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="构建任务不存在")
    return BuildTaskStatus(**state.to_dict())


@router.get("/build-graph-tasks", response_model=list[BuildTaskStatus])
def list_build_tasks(limit: int = Query(default=20, ge=1, le=100)) -> list[BuildTaskStatus]:
    """列出最近的图谱构建任务。"""
    tasks = graph_build_progress.list_build_tasks(limit=limit)
    return [BuildTaskStatus(**t) for t in tasks]


# ============ 规则文档导入 ============


class RuleDocumentImportResponse(BaseModel):
    """规则文档导入响应。"""
    total: int
    imported: int
    skipped: int
    rules: list[dict[str, Any]] = []
    errors: list[str] = []
    extracted_text_preview: str = ""
    extracted_text_length: int = 0
    source_filename: str = ""


@router.post("/import-document", response_model=RuleDocumentImportResponse)
async def import_rules_from_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RuleDocumentImportResponse:
    """从上传的规则描述文档（PDF/EXCEL/WORD/MD）导入规则。

    自动提取文本 → 调用 LLM 解析为结构化规则 → 入库。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    try:
        content = await file.read()
        result = rule_document_import_service.import_rules_from_document(
            db=db,
            file_content=content,
            filename=file.filename,
        )
        return RuleDocumentImportResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")


# ============ 图谱查询与确认 ============


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
