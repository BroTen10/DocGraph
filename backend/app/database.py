"""Postgres 数据库连接与 SQLAlchemy 会话管理。"""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


engine = create_engine(
    settings.pg_dsn,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求一个数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """初始化数据库表结构（开发期使用；生产建议用 alembic 迁移）。

    默认行为（settings.db_reset_on_startup=False）：
        仅执行幂等的 create_all，只创建缺失的表，不触碰已有数据。
        重启不再丢失合同/规则/审查结果。

    重置行为（settings.db_reset_on_startup=True）：
        先 DROP SCHEMA public CASCADE 再 CREATE SCHEMA public 重建 Postgres，
        并同步清空 Neo4j 的所有规则图谱，避免 PG 与 Neo4j 失联产生孤儿图谱。
        仅在 schema 破坏性变更或需要干净环境时临时开启。
    """
    # 导入所有模型以触发注册
    from .models import (  # noqa: F401
        contract,
        document,
        ocr_task,
        review_result,
        review_task,
        rule,
        rule_set,
        rule_snapshot,
    )

    if settings.db_reset_on_startup:
        # 破坏性重置：清空 Postgres（会丢失所有表与数据）
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE;"))
            conn.execute(text("CREATE SCHEMA public;"))
        logger.warning("DB_RESET_ON_STARTUP=True：已重建 public schema（DROP + CREATE）")
        # 同步清空 Neo4j，避免 PG 与图谱失联
        try:
            from .neo4j_client import get_neo4j_client

            remaining = get_neo4j_client().clear_all_rule_graphs()
            logger.warning("已同步清空 Neo4j 规则图谱（剩余节点=%s）", remaining)
        except Exception:
            logger.exception("清空 Neo4j 规则图谱失败（PG 已重置，图谱可能残留孤儿数据）")

    Base.metadata.create_all(bind=engine)
    logger.info("Postgres 表已就绪（create_all 幂等）")
