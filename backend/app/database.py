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
    from .models import (
        contract,       # noqa: F401
        document,       # noqa: F401
        document_type,  # noqa: F401
        ocr_task,       # noqa: F401
        review_result,  # noqa: F401
        review_task,    # noqa: F401
        rule,           # noqa: F401
        rule_parse_skill,  # noqa: F401
        rule_set,       # noqa: F401
        rule_snapshot,  # noqa: F401
    )

    if settings.db_reset_on_startup:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE;"))
            conn.execute(text("CREATE SCHEMA public;"))
        logger.warning("DB_RESET_ON_STARTUP=True：已重建 public schema（DROP + CREATE）")
        try:
            from .neo4j_client import get_neo4j_client
            remaining = get_neo4j_client().clear_all_rule_graphs()
            logger.warning("已同步清空 Neo4j 规则图谱（剩余节点=%s）", remaining)
        except Exception:
            logger.exception("清空 Neo4j 规则图谱失败（PG 已重置，图谱可能残留孤儿数据）")

    Base.metadata.create_all(bind=engine)
    logger.info("Postgres 表已就绪（create_all 幂等）")

    # 增量迁移：新增列与表（create_all 不处理已有表的列变更）
    _run_migrations(engine)
    # 种子数据：内置默认 Skill + 文档类型
    _seed_builtin_skill()
    _seed_doc_types()


def _run_migrations(engine) -> None:
    """执行增量 DDL 迁移。适用于在已有表上加列等操作。"""
    migrations = [
        "ALTER TABLE rules ADD COLUMN IF NOT EXISTS defects JSONB NOT NULL DEFAULT '[]'::jsonb;",
    ]
    with engine.begin() as conn:
        for sql in migrations:
            conn.execute(text(sql))
    logger.info("增量迁移完成: %d 条", len(migrations))


def _seed_builtin_skill() -> None:
    """种子数据：如果不存在则创建内置默认 Skill。"""
    from .models import RuleParseSkill
    from sqlalchemy import select

    with SessionLocal() as db:
        existing = db.execute(
            select(RuleParseSkill).where(RuleParseSkill.is_builtin.is_(True))
        ).scalars().first()
        if existing is not None:
            return

        skill = RuleParseSkill(
            rule_set_id=None,
            name="默认规则解析配置",
            description="适用于大多数贸易合同审查场景的基础规则解析配置，开箱即用",
            is_builtin=True,
            enabled=True,
            priority=100,
            version=1,
            content={
                "prompt_instructions": [
                    "rule_text 用简洁中文描述，如'报关单数量应不大于委托单数量'",
                    "将自然语言规则拆分为单条规则时，保留原文的业务含义",
                    "如果原始文档使用英文术语，保留英文术语并在括号内附中文翻译",
                ],
                "field_mappings": {},
                "defaults": {
                    "tolerance": {
                        "amount_percent": 5.0,
                        "weight_kg": 0.5,
                    },
                    "priority": {
                        "齐套性": 10,
                        "基础判断": 20,
                        "信息准确性": 30,
                        "时间逻辑": 40,
                    },
                },
                "validations": [
                    {
                        "field": "tolerance.amount_percent",
                        "rule": "值必须在 0-100 之间",
                        "severity": "error",
                        "message": "金额容差 '{value}' 超出 0-100 范围，请修正",
                    },
                    {
                        "field": "tolerance.weight_kg",
                        "rule": "值必须 >= 0",
                        "severity": "error",
                    },
                ],
                "text_preprocessing": [],
                "term_normalization": {},
                "domain_context": {
                    "glossary": {},
                    "common_patterns": [
                        "金额对比类规则通常涉及报关单金额 vs 委托单金额",
                        "数量对比类规则通常涉及报关单数量 vs 委托单数量",
                        "日期逻辑类规则关注签订日期、报关日期、有效期的先后关系",
                    ],
                },
            },
        )
        db.add(skill)
        db.commit()
        logger.info("已 seed 内置默认 Skill: %s", skill.name)


def _seed_doc_types() -> None:
    """种子数据：将 constants.py 中的硬编码文档类型写入 document_types 表。

    仅在表为空时写入，不覆盖已有数据（用户自定义的类型不会被冲掉）。
    """
    from .models import DocumentType
    from sqlalchemy import select, func

    with SessionLocal() as db:
        count = db.execute(select(func.count(DocumentType.id))).scalar()
        if count and count > 0:
            logger.info("document_types 表已有 %d 条记录，跳过种子", count)
            return

    from .constants import (
        ALL_DOC_TYPES,
        FIELD_TEMPLATES, STAMP_REQUIREMENTS,
    )

    with SessionLocal() as db:
        for idx, name in enumerate(ALL_DOC_TYPES):
            dt = DocumentType(
                name=name,
                key_fields=FIELD_TEMPLATES.get(name, []),
                stamp_required=STAMP_REQUIREMENTS.get(name),
                source="seed",
                status="active",
            )
            db.add(dt)
        db.commit()
        logger.info("已 seed %d 个内置文档类型", len(ALL_DOC_TYPES))
