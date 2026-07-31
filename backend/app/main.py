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
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import get_db, init_db
from .routers import contracts, doc_types, graph, ocr, reviews, rule_parse_skills, rule_sets, rules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化。"""
    logger.info("=== 启动文档审查智能体后端 ===")
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
    yield
    logger.info("=== 后端关闭 ===")


app = FastAPI(
    title="基于知识图谱的自动文档审查智能体 API",
    version="1.0.0",
    description="出口代理贸易单证自动审查 - MVP（多规则集）",
    lifespan=lifespan,
)

# CORS：允许前端独立部署跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP 阶段放开，生产应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(rule_sets.router)
app.include_router(contracts.router)
app.include_router(rules.router)
app.include_router(graph.router)
app.include_router(reviews.router)
app.include_router(ocr.router)
app.include_router(rule_parse_skills.router)
app.include_router(doc_types.router)


@app.get("/api/health")
def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/constants/doc-types")
def list_doc_types(db=Depends(get_db)) -> dict:
    """返回支持的文件类型与检查项类别，供前端二维表格使用。

    必备/非必备不再由文档类型自身定义，而是由齐套性规则决定。
    从 document_types 表读取，替代旧的 constants.py 硬编码。
    """
    from .models import DocumentType
    from .constants import CHECK_CATEGORIES
    from sqlalchemy import select

    rows = db.execute(
        select(DocumentType).where(DocumentType.status == "active").order_by(DocumentType.name)
    ).scalars().all()

    return {
        "doc_types": [
            {
                "name": r.name,
                "stamp_required": r.stamp_required,
                "key_fields": r.key_fields or [],
                "business_meaning": r.business_meaning,
                "has_sample": r.has_sample if hasattr(r, "has_sample") else False,
            }
            for r in rows
        ],
        "check_categories": CHECK_CATEGORIES,
    }
