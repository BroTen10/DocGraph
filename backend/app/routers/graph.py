"""图谱构建与确认路由。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.graph import (
    GraphConfirmRequest,
    GraphData,
)
from ..schemas.rule import RuleImportResponse
from ..services import (
    graph_builder_service,
    graph_build_progress,
    rule_service,
    rule_import_task,
)
from ..neo4j_client import get_neo4j_client

router = APIRouter(prefix="/api/rules", tags=["graph"])


# ============ 异步图谱构建（带进度追踪） ============


class AsyncBuildResponse(BaseModel):
    """异步构建启动响应。"""
    task_id: str
    message: str = "图谱构建已启动"


class BuildTaskStatus(BaseModel):
    """构建任务状态。"""
    task_id: str
    rule_set_id: str | None = None
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
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    auto_confirm_all: bool = False,
    operator: str = "system",
) -> AsyncBuildResponse:
    """异步启动图谱构建（后台线程执行），返回 task_id 用于轮询进度。

    适合规则较多、LLM 调用耗时较长的场景。
    rule_set_id 用于按规则集隔离规则与快照。
    """
    task_id = graph_build_progress.start_async_build(
        rule_set_id=str(rule_set_id),
        operator=operator,
        auto_confirm_all=auto_confirm_all,
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


class RuleDocumentImportResponse(RuleImportResponse):
    """规则文档导入响应。"""
    extracted_text_preview: str = ""
    extracted_text_length: int = 0
    source_filename: str = ""


@router.post("/import-document")
async def import_rules_from_document(
    rule_set_id: uuid.UUID = Query(..., description="所属规则集 ID"),
    file: UploadFile = File(...),
    skill_ids: str | None = Query(None, description="逗号分隔的 Skill ID 列表"),
    db: Session = Depends(get_db),
) -> dict:
    """从上传的规则描述文档（PDF/EXCEL/WORD/MD）导入规则，归到指定规则集下。

    该流程（提取文本 → LLM 解析 → 入库 → 冲突检测）较长，故改为异步任务：
    立即落盘文件并返回 task_id，前端通过 GET /import-tasks/{task_id} 轮询进度。
    skill_ids 参数传逗号分隔的 Skill UUID，不传则使用该规则集默认配置。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    parsed_skill_ids = None
    if skill_ids:
        try:
            parsed_skill_ids = [uuid.UUID(s.strip()) for s in skill_ids.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="skill_ids 格式错误，应为逗号分隔的 UUID")

    import os
    import tempfile

    # 落盘文件，交由后台线程处理
    suffix = os.path.splitext(file.filename)[1].lower() or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    task = rule_import_task.create_task(str(rule_set_id), file.filename)

    import threading

    t = threading.Thread(
        target=rule_import_task.run_import_task,
        args=(task, tmp_path, file.filename, parsed_skill_ids),
        daemon=True,
    )
    t.start()
    logger = __import__("logging").getLogger(__name__)
    logger.info("已启动导入任务 %s (rule_set=%s, file=%s)", task.task_id, rule_set_id, file.filename)

    return {
        "task_id": task.task_id,
        "rule_set_id": str(rule_set_id),
        "status": task.status,
        "message": "已接收文件，正在后台解析…",
    }


class ImportTaskOut(BaseModel):
    task_id: str
    rule_set_id: str
    status: str
    message: str = ""
    file_name: str = ""
    total_chunks: int = 0
    parsed_chunks: int = 0
    total_rules: int = 0
    imported_rules: int = 0
    import_errors: int = 0
    conflict_total: int = 0
    conflict_done: int = 0
    conflict_found: int = 0
    result: dict | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


@router.get("/import-tasks/{task_id}", response_model=ImportTaskOut)
def get_import_task(task_id: str) -> ImportTaskOut:
    """查询导入任务的进度与结果。前端轮询此接口。"""
    task = rule_import_task.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return ImportTaskOut(**task.to_dict())


# ============ 图谱查询与确认 ============


@router.get("/graph", response_model=GraphData)
def get_latest_graph(
    rule_set_id: uuid.UUID = Query(..., description="规则集 ID"),
    db: Session = Depends(get_db),
) -> GraphData:
    """查看指定规则集的最新规则图谱。"""
    snap = rule_service.get_latest_snapshot(db, rule_set_id)
    if snap is None or not snap.graph_id:
        raise HTTPException(status_code=404, detail="暂无图谱，请先构建")
    return graph_builder_service.get_graph(None, snap.graph_id)


@router.get("/graph/ontology")
def get_graph_ontology(graph_id: str = Query(...)) -> dict:
    """查询图谱本体层（批次 10 Phase D）：文档类型（含字段）、检查意图（含规则数）、规则清单。"""
    return get_neo4j_client().get_ontology(graph_id)


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
