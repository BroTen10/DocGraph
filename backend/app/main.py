"""FastAPI 应用入口。

启动时：
1. 初始化 Postgres 表结构（DROP SCHEMA + CREATE SCHEMA + create_all）
2. 注册所有路由（含 rule-sets）
3. 启用 CORS（前端独立部署）

注意：多 RuleSet 改造后，不再自动插入种子规则。
用户需在前端手动创建规则集、添加规则后才能使用审查能力。
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth.rate_limit import client_ip
from .auth.csrf import (
    csrf_header_matches_session,
    is_public_auth,
    is_unsafe,
    origin_matches,
)
from .auth.dependencies import AuthContext, require_permission
from .auth.session_tokens import get_active_session
from .config import settings
from .database import SessionLocal, get_db, init_db
from .observability import (
    RequestContextFilter,
    bind_principal,
    bind_request,
    clear_context,
    normalize_request_id,
)
from .routers import (
    auth,
    contracts,
    doc_types,
    graph,
    ocr,
    platform,
    reviews,
    rule_parse_skills,
    rule_sets,
    rules,
    settings as settings_router,
    tenant_members,
)
from .version import app_version, collect_build_info

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s [%(levelname)s] %(name)s "
        "[request_id=%(request_id)s user_id=%(user_id)s "
        "tenant_id=%(tenant_id)s]: %(message)s"
    ),
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestContextFilter())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化。"""
    logger.info("=== 启动文档审查智能体后端 ===")
    logger.info("版本：%s", collect_build_info()["label"])
    # 1. 创建表（含 rule_sets 表与 rule_set_id 外键）
    #    注意：init_db 会先 DROP SCHEMA public CASCADE，再 CREATE SCHEMA public，再 create_all
    #    用户已确认不迁移旧数据。
    init_db()
    logger.info("Postgres 表已就绪")
    # 多 RuleSet 改造后不再自动插入种子规则。
    # 用户需在前端手动创建规则集（POST /api/rule-sets），
    # 再为规则集添加规则（POST /api/rules?rule_set_id=...）。
    # 2. 确保上传目录存在
    settings.ensure_upload_root()
    # 3. 回收上一个进程遗留的 OCR 任务：OCR 跑在进程内后台线程，进程退出后
    #    任务行会永久停在 running/pending，导致前端一直显示"识别中"并禁用按钮
    from .database import SessionLocal
    from .services.graph_builder_service import backfill_graph_tenant_ownership
    from .services.ocr_task_service import reconcile_stale_tasks, set_process_start
    from .services.review_service import (
        reconcile_stale_review_tasks,
        set_process_start as set_review_process_start,
    )

    _db = SessionLocal()
    try:
        set_process_start(_db)
        set_review_process_start(_db)
        try:
            graph_migration = backfill_graph_tenant_ownership(_db)
            logger.info(
                "Neo4j 旧图谱租户回填完成：%d 节点 / %d 关系，孤儿图谱 %d",
                graph_migration["nodes"],
                graph_migration["relationships"],
                graph_migration["orphan_graphs"],
            )
        except Exception:
            logger.exception("Neo4j 旧图谱租户回填失败，旧图暂不可访问")
        stale = reconcile_stale_tasks(_db, force_all=True)
        if stale:
            logger.warning("启动时回收 %d 个中断的 OCR 任务", stale)
        stale_review = reconcile_stale_review_tasks(_db, force_all=True)
        if stale_review:
            logger.warning("启动时回收 %d 个中断的审查任务", stale_review)
    finally:
        _db.close()
    yield
    logger.info("=== 后端关闭 ===")


_docs_enabled = settings.app_debug or settings.api_docs_enabled
app = FastAPI(
    title="基于知识图谱的自动文档审查智能体 API",
    version=app_version(),
    description="出口代理贸易单证自动审查 - MVP（多规则集）",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# CORS：白名单来源（批次 6-2 收紧，不再放开 *）；同源部署可留空
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    """Validate Origin and CSRF for state-changing requests."""
    if not is_unsafe(request):
        return await call_next(request)

    if not origin_matches(request):
        return JSONResponse(
            status_code=403,
            content={"code": "ORIGIN_INVALID", "message": "请求来源不合法"},
        )

    if not is_public_auth(request):
        db = SessionLocal()
        try:
            raw_session = request.cookies.get("docgraph_session")
            session = get_active_session(db, raw_session)
            if session is None:
                return JSONResponse(
                    status_code=401,
                    content={"code": "UNAUTHENTICATED", "message": "未登录或会话已失效"},
                )
            if not csrf_header_matches_session(request, session.csrf_hash):
                return JSONResponse(
                    status_code=403,
                    content={"code": "CSRF_INVALID", "message": "CSRF 校验失败"},
                )
        finally:
            db.close()

    return await call_next(request)


@app.middleware("http")
async def request_correlation(request: Request, call_next):
    """Attach a safe request ID and record authenticated access denials."""
    supplied = normalize_request_id(request.headers.get("x-request-id"))
    request_id = supplied or uuid.uuid4().hex
    bind_request(request_id, client_ip(request))
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        if response.status_code in {403, 404} and request.url.path.startswith("/api/"):
            from .services import audit_service

            tenant = getattr(request.state, "tenant_id", None)
            user = getattr(request.state, "actor_user_id", None)
            if user:
                route = request.scope.get("route")
                route_path = getattr(route, "path", request.url.path)
                db = SessionLocal()
                try:
                    audit_service.record_and_commit(
                        db,
                        uuid.UUID(tenant) if tenant else None,
                        uuid.UUID(user),
                        "security.access.denied",
                        target_type="api_route",
                        result="denied",
                        metadata={
                            "method": request.method,
                            "path": route_path,
                            "status": response.status_code,
                        },
                    )
                except Exception:
                    logger.exception("记录访问拒绝审计失败")
                finally:
                    db.close()

        bind_principal(
            getattr(request.state, "actor_user_id", None),
            getattr(request.state, "tenant_id", None),
        )
        logger.info(
            "request completed method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response
    finally:
        clear_context()


# 注册路由
app.include_router(auth.router)
app.include_router(tenant_members.router)
app.include_router(platform.router)
app.include_router(rule_sets.router)
app.include_router(contracts.router)
app.include_router(rules.router)
app.include_router(graph.router)
app.include_router(reviews.router)
app.include_router(ocr.router)
app.include_router(rule_parse_skills.router)
app.include_router(doc_types.router)
app.include_router(settings_router.router)


@app.get("/api/health")
def health() -> dict:
    """健康检查：带版本信息，便于一条命令确认某环境跑的是哪次构建。"""
    info = collect_build_info()
    return {
        "status": "ok",
        "version": info["version"],
        "label": info["label"],
        "commit": info["commit"],
        "dirty": info["dirty"],
        "build_env": info["build_env"],
    }


@app.get("/api/version")
def version_info() -> dict:
    """完整版本信息（版本号 + git 定位 + 构建环境）。

    本机开发环境与服务器正式环境用同一结构返回，可直接对比：
    本机 ``http://localhost:8800/api/version`` vs
    服务器 ``https://<域名>/api/version``。
    """
    return collect_build_info()


@app.get("/api/constants/doc-types")
def list_doc_types(
    db=Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.view")),
) -> dict:
    """返回支持的文件类型与检查项类别，供前端二维表格使用。

    必备/非必备不再由文档类型自身定义，而是由齐套性规则决定。
    从 document_types 表读取，替代旧的 constants.py 硬编码。
    """
    from .models import DocumentType
    from .constants import CHECK_CATEGORIES, CHECK_COMPLETENESS
    from sqlalchemy import select

    assert ctx.tenant is not None
    rows = db.execute(
        select(DocumentType)
        .where(
            DocumentType.tenant_id == ctx.tenant.id,
            DocumentType.status == "active",
        )
        .order_by(DocumentType.name)
    ).scalars().all()

    return {
        "doc_types": [
            {
                "name": r.name,
                "stamp_required": r.stamp_required,
                "key_fields": r.key_fields or [],
                "aliases": r.aliases or [],
                "business_meaning": r.business_meaning,
                "has_sample": r.has_sample if hasattr(r, "has_sample") else False,
            }
            for r in rows
        ],
        "check_categories": CHECK_CATEGORIES,
        # 批次 3-5：齐套性检查项名称由后端单源下发，前端不再硬编码 '齐套性'
        "completeness_category": CHECK_COMPLETENESS,
    }
