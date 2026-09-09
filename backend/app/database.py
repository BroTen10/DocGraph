"""Postgres 数据库连接与 SQLAlchemy 会话管理。"""

import json
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
        system_setting, # noqa: F401
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
    # 规则流水号回填与存量提示清洗
    _migrate_rule_numbering()
    # 种子数据：内置默认 Skill + 文档类型
    _seed_builtin_skill()
    _seed_doc_types()
    # 批次 11：规范名/别名单源 + 存量清洗（幂等）
    _migrate_canonical_types()


def _run_migrations(engine) -> None:
    """执行增量 DDL 迁移。适用于在已有表上加列等操作。"""
    migrations = [
        "ALTER TABLE rules ADD COLUMN IF NOT EXISTS defects JSONB NOT NULL DEFAULT '[]'::jsonb;",
        "ALTER TABLE rules ADD COLUMN IF NOT EXISTS structure JSONB;",
        # 批次 10：规则自描述（doc_type/check_category 降级为可选派生标签 + scope/intents/provenance）
        "ALTER TABLE rules ALTER COLUMN doc_type DROP NOT NULL;",
        "ALTER TABLE rules ALTER COLUMN check_category DROP NOT NULL;",
        "ALTER TABLE rules ADD COLUMN IF NOT EXISTS scope JSONB;",
        "ALTER TABLE rules ADD COLUMN IF NOT EXISTS intents JSONB NOT NULL DEFAULT '[]'::jsonb;",
        "ALTER TABLE rules ADD COLUMN IF NOT EXISTS provenance JSONB;",
        # 批次 9：结果闭环（问题状态机 + 严重度 + 偏离度 + 图谱实体关联）
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'open';",
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS status_history JSONB NOT NULL DEFAULT '[]'::jsonb;",
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS severity VARCHAR(16);",
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS deviation JSONB;",
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS graph_source VARCHAR(255);",
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS graph_target VARCHAR(255);",
        # 批次 10 Phase C：双引擎审查——结果来源（graph/llm/legacy）与置信度
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS source VARCHAR(16);",
        "ALTER TABLE review_results ADD COLUMN IF NOT EXISTS confidence FLOAT;",
        # 规则集级开关：禁用内置默认 Skill 领域知识（仅保留系统解析契约）
        "ALTER TABLE rule_sets ADD COLUMN IF NOT EXISTS use_default_skill BOOLEAN NOT NULL DEFAULT TRUE;",
        # 存量 pass 结果回填为 closed（无需跟进）
        "UPDATE review_results SET status = 'closed' WHERE result = 'pass' AND status = 'open';",
        # 批次 11：文档类型显式别名（写时归一）
        "ALTER TABLE document_types ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]'::jsonb;",
        "ALTER TABLE document_types ADD COLUMN IF NOT EXISTS field_aliases JSONB NOT NULL DEFAULT '{}'::jsonb;",
        # OCR 文本行坐标：扫描 PDF/图片在原件上的高亮定位
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_layout JSONB NOT NULL DEFAULT '{}'::jsonb;",
        # 规则流水号：规则集内单调递增，对外展示为 R0001/R0002
        "ALTER TABLE rules ADD COLUMN IF NOT EXISTS rule_no INTEGER;",
        "ALTER TABLE rule_sets ADD COLUMN IF NOT EXISTS next_rule_no INTEGER NOT NULL DEFAULT 1;",
        """
        WITH numbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY rule_set_id
                       ORDER BY created_at, id
                   ) AS rn
            FROM rules
            WHERE rule_no IS NULL OR rule_no <= 0
        )
        UPDATE rules AS r
        SET rule_no = numbered.rn
        FROM numbered
        WHERE r.id = numbered.id;
        """,
        "ALTER TABLE rules ALTER COLUMN rule_no SET NOT NULL;",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rules_rule_set_rule_no ON rules (rule_set_id, rule_no);",
        """
        UPDATE rule_sets AS rs
        SET next_rule_no = GREATEST(
            COALESCE(rs.next_rule_no, 1),
            COALESCE((
                SELECT MAX(r.rule_no) + 1
                FROM rules AS r
                WHERE r.rule_set_id = rs.id
            ), 1)
        );
        """,
    ]
    with engine.begin() as conn:
        for sql in migrations:
            conn.execute(text(sql))
    logger.info("增量迁移完成: %d 条", len(migrations))


def _migrate_rule_numbering() -> None:
    """回填规则流水号相关提示，修复历史"规则0/规则1"式歧义。

    - 为存量缺陷补充所属规则流水号；
    - 为冲突缺陷补充关联规则流水号；
    - 将描述中的数组下标改写为可定位的规则流水号。
    """
    import re

    with SessionLocal() as db:
        rules = list(db.execute(text("SELECT id, rule_set_id, rule_no FROM rules")).mappings())
        code_by_id = {
            str(row["id"]): f"R{int(row['rule_no']):04d}"
            for row in rules
        }
        changed = 0

        for row in db.execute(
            text("SELECT id, defects FROM rules WHERE defects IS NOT NULL AND defects != '[]'::jsonb")
        ).mappings():
            rule_id = str(row["id"])
            own_code = code_by_id.get(rule_id)
            defects = list(row["defects"] or [])
            next_defects: list[dict] = []
            dirty = False
            for raw in defects:
                defect = dict(raw) if isinstance(raw, dict) else {}
                if not defect:
                    next_defects.append(raw)
                    continue

                related_ids = [
                    str(x)
                    for x in (defect.get("related_rule_ids") or [])
                    if str(x) in code_by_id
                ]
                related_codes = sorted(
                    {code_by_id[x] for x in related_ids},
                    key=lambda code: int(code[1:]),
                )
                if own_code and defect.get("rule_code") != own_code:
                    defect["rule_code"] = own_code
                    dirty = True
                if related_codes and defect.get("related_rule_codes") != related_codes:
                    defect["related_rule_codes"] = related_codes
                    dirty = True

                description = str(defect.get("description") or "")
                has_ambiguous_ref = bool(
                    re.search(r"规则\s*\d+", description)
                    or re.search(r"相关规则(?:\s*[、,，和及]\s*\d+)+", description)
                    or re.search(r"[（(]\s*索引\s*\d+\s*[)）]", description)
                )
                if has_ambiguous_ref:
                    referenced_codes = sorted(
                        {
                            code_by_id[rule_id],
                            *(code_by_id[x] for x in related_ids),
                        },
                        key=lambda code: int(code[1:]),
                    )
                    # 历史描述里的序号是分组内下标，无法稳定还原到具体规则；
                    # 统一去掉歧义序号，改为显式列出涉及规则流水号。
                    cleaned = re.sub(
                        r"规则\s*\d+(?:\s*[、,，和及]\s*\d+)*"
                        r"(?:\s*[（(]\s*索引\s*\d+\s*[)）])?",
                        "相关规则",
                        description,
                    )
                    cleaned = re.sub(
                        r"相关规则(?:\s*[、,，和及]\s*\d+)+",
                        "相关规则",
                        cleaned,
                    )
                    cleaned = re.sub(
                        r"[（(]\s*索引\s*\d+\s*[)）]",
                        "",
                        cleaned,
                    )
                    if re.search(r"涉及规则\s+R\d{4}", cleaned):
                        defect["description"] = cleaned
                    else:
                        defect["description"] = (
                            f"涉及规则 {'、'.join(referenced_codes)}：{cleaned}"
                        )
                    dirty = True
                next_defects.append(defect)

            if dirty:
                db.execute(
                    text(
                        "UPDATE rules SET defects = CAST(:defects AS jsonb) "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"defects": json.dumps(next_defects, ensure_ascii=False), "id": rule_id},
                )
                changed += 1

        if changed:
            db.commit()
            logger.info("规则流水号提示迁移完成: 已更新 %d 条规则", changed)


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
                    "输入行带 [SOURCE_ROW=...] 时逐行解析并回填 source_ref；禁止因描述相似跨行合并",
                    "表格的文件类型列写入 scope，业务条件列写入 structure.condition；合并单元格值已展开到每个数据行",
                    "描述相似但 scope 或 condition 不同时必须拆成独立规则，禁止合并",
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
        DOC_FX_CLAIM,
        DOC_OTHER,
        DOC_PAY_APPLICATION,
        OPTIONAL_DOC_TYPES,
        REQUIRED_DOC_TYPES,
    )

    def _seed_category(name: str) -> tuple[str, bool]:
        """与文件分类器/齐套性语义对齐的类别推导（批次 10 Phase B）。"""
        if name in REQUIRED_DOC_TYPES:
            return "required", True
        if name in OPTIONAL_DOC_TYPES:
            return "optional", False
        if name in (DOC_FX_CLAIM, DOC_PAY_APPLICATION):
            return "supporting", False
        if name == DOC_OTHER:
            return "other", False
        return "extra", False

    with SessionLocal() as db:
        for idx, name in enumerate(ALL_DOC_TYPES):
            category, is_required = _seed_category(name)
            dt = DocumentType(
                name=name,
                category=category,
                is_required=is_required,
                key_fields=FIELD_TEMPLATES.get(name, []),
                stamp_required=STAMP_REQUIREMENTS.get(name),
                source="seed",
                status="active",
            )
            db.add(dt)
        db.commit()
        logger.info("已 seed %d 个内置文档类型", len(ALL_DOC_TYPES))


def _migrate_canonical_types() -> None:
    """批次 11：规范名/别名单源 + 存量清洗（幂等，可重复执行）。

    1. 补齐内置规范类型（含此前缺失的"出口报关单"），写入内置 aliases/field_aliases 默认值；
    2. 把"别名即类型"的重复条目（如规则导入自动发现的"报关单"）置为 rejected，
       避免同一业务含义在注册表里双轨繁殖；
    3. 存量文档/规则/规则集归一到规范名；存量 extracted_fields 字段键按别名归一。
    """
    from sqlalchemy import select
    from .models import Document, DocumentType, Rule, RuleSet
    from . import constants as C
    from .services.doc_normalizer import (
        build_doc_type_alias_index,
        normalize_doc_type,
        normalize_extracted_keys,
        normalize_scope,
        normalize_structure,
    )

    with SessionLocal() as db:
        # ---- 1) 补齐内置规范类型 + 别名默认值 ----
        existing = {dt.name: dt for dt in db.execute(select(DocumentType)).scalars()}
        changed = False
        for name in C.ALL_DOC_TYPES:
            dt = existing.get(name)
            if dt is None:
                if name in C.REQUIRED_DOC_TYPES:
                    category, is_required = "required", True
                elif name in C.OPTIONAL_DOC_TYPES:
                    category, is_required = "optional", False
                elif name in (C.DOC_FX_CLAIM, C.DOC_PAY_APPLICATION):
                    category, is_required = "supporting", False
                elif name == C.DOC_OTHER:
                    category, is_required = "other", False
                else:
                    category, is_required = "extra", False
                dt = DocumentType(
                    name=name,
                    category=category,
                    is_required=is_required,
                    key_fields=C.FIELD_TEMPLATES.get(name, []),
                    stamp_required=C.STAMP_REQUIREMENTS.get(name),
                    source="seed",
                    status="active",
                )
                db.add(dt)
                existing[name] = dt
                changed = True
            if not dt.aliases and C.DOC_TYPE_ALIASES.get(name):
                dt.aliases = C.DOC_TYPE_ALIASES.get(name)
                changed = True
            if not dt.field_aliases and C.FIELD_ALIASES.get(name):
                dt.field_aliases = C.FIELD_ALIASES.get(name)
                changed = True
        if changed:
            db.commit()

        # ---- 2) 别名条目置为 rejected（如 rule_import 的"报关单"）----
        alias_index = build_doc_type_alias_index(db)
        for alias, canonical in alias_index.items():
            row = existing.get(alias)
            if row is not None and row.name != canonical and row.status == "active":
                row.status = "rejected"
                row.is_required = False
                db.commit()
                logger.info("文档类型「%s」已并入「%s」的别名（置为 rejected）", alias, canonical)

        # ---- 3) 存量数据归一：文档 ----
        for d in list(db.execute(select(Document)).scalars()):
            dirty = False
            nt = normalize_doc_type(db, d.doc_type)
            if nt and nt != d.doc_type:
                d.doc_type = nt
                dirty = True
            if d.extracted_fields:
                nf = normalize_extracted_keys(db, d.doc_type, d.extracted_fields)
                if nf != d.extracted_fields:
                    d.extracted_fields = nf
                    dirty = True
            if dirty:
                db.commit()

        # ---- 3) 存量数据归一：规则 ----
        for r in list(db.execute(select(Rule)).scalars()):
            dirty = False
            if r.doc_type:
                nt = normalize_doc_type(db, r.doc_type)
                if nt and nt != r.doc_type:
                    r.doc_type = nt
                    dirty = True
            if r.structure:
                ns = normalize_structure(db, r.structure, r.doc_type)
                if ns != r.structure:
                    r.structure = ns
                    dirty = True
            if r.scope:
                ns = normalize_scope(db, r.scope)
                if ns != r.scope:
                    r.scope = ns
                    dirty = True
            if dirty:
                db.commit()

        # ---- 3) 存量数据归一：规则集 doc_types ----
        for rs in list(db.execute(select(RuleSet)).scalars()):
            nd = sorted(
                {
                    normalize_doc_type(db, x) or str(x)
                    for x in (rs.doc_types or [])
                    if x
                }
            )
            if nd != rs.doc_types:
                rs.doc_types = nd
                db.commit()
