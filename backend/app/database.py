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
    """创建所有表（开发期使用；生产建议用 alembic 迁移）。

    多 RuleSet 改造后，rules / rule_snapshots / contracts 等表新增了
    nullable=False 的 rule_set_id 外键。旧表里如果有数据，
    直接 create_all 会因约束冲突建表失败。
    因此采用"先 DROP SCHEMA public CASCADE 再 CREATE SCHEMA public"的策略
    （用户已确认不迁移旧数据）。
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

    # 注意：会清空 public schema 下所有表与数据
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE;"))
        conn.execute(text("CREATE SCHEMA public;"))
    logger.info("已重建 public schema（DROP + CREATE）")

    Base.metadata.create_all(bind=engine)
    logger.info("Postgres 表已创建")
