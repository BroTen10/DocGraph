"""FastAPI 应用入口。

启动时：
1. 初始化 Postgres 表结构
2. 插入种子规则（若 rules 表为空）
3. 注册所有路由
4. 启用 CORS（前端独立部署）
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import SessionLocal, init_db
from .routers import contracts, graph, reviews, rules
from .services.seed_rules import init_seed_rules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化。"""
    logger.info("=== 启动文档审查智能体后端 ===")
    # 1. 创建表
    init_db()
    logger.info("Postgres 表已就绪")
    # 2. 插入种子规则
    with SessionLocal() as db:
        n = init_seed_rules(db)
        if n:
            logger.info("已插入 %d 条种子规则", n)
        else:
            logger.info("种子规则已存在，跳过")
    # 3. 确保上传目录存在
    settings.ensure_upload_root()
    yield
    logger.info("=== 后端关闭 ===")


app = FastAPI(
    title="基于知识图谱的自动文档审查智能体 API",
    version="1.0.0",
    description="出口代理贸易单证自动审查 - MVP",
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
app.include_router(contracts.router)
app.include_router(rules.router)
app.include_router(graph.router)
app.include_router(reviews.router)


@app.get("/api/health")
def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/constants/doc-types")
def list_doc_types() -> dict:
    """返回支持的文件类型与必备性，供前端二维表格使用。"""
    from .constants import (
        ALL_DOC_TYPES,
        CHECK_CATEGORIES,
        OPTIONAL_DOC_TYPES,
        REQUIRED_DOC_TYPES,
        STAMP_REQUIREMENTS,
    )

    return {
        "doc_types": [
            {
                "name": t,
                "is_required": t in REQUIRED_DOC_TYPES,
                "is_optional": t in OPTIONAL_DOC_TYPES,
                "stamp_required": STAMP_REQUIREMENTS.get(t),
            }
            for t in ALL_DOC_TYPES
        ],
        "check_categories": CHECK_CATEGORIES,
    }
