"""Tenant ownership migration for existing DocGraph business data.

The migration is deliberately additive: it creates the default tenant, adds
tenant columns, backfills them, and only then installs the non-null/unique/FK
constraints. It never drops business tables or existing business rows.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_TENANT_CODE = "default"
DEFAULT_TENANT_NAME = "默认租户"
DEFAULT_RULE_SET_NAME = "默认规则集"

TENANT_OWNED_TABLES = (
    "rule_sets",
    "rules",
    "rule_snapshots",
    "contracts",
    "documents",
    "ocr_tasks",
    "review_tasks",
    "review_results",
    "document_types",
)

PARENT_CHILD_BACKFILLS = (
    (
        "rules",
        "rule_sets",
        "rule_set_id",
        "id",
    ),
    (
        "rule_snapshots",
        "rule_sets",
        "rule_set_id",
        "id",
    ),
    (
        "contracts",
        "rule_sets",
        "rule_set_id",
        "id",
    ),
    (
        "documents",
        "contracts",
        "contract_id",
        "id",
    ),
    (
        "ocr_tasks",
        "rule_sets",
        "rule_set_id",
        "id",
    ),
    (
        "ocr_tasks",
        "contracts",
        "contract_id",
        "id",
    ),
    (
        "ocr_tasks",
        "documents",
        "doc_id",
        "id",
    ),
    (
        "review_tasks",
        "contracts",
        "contract_id",
        "id",
    ),
    (
        "review_tasks",
        "rule_snapshots",
        "snapshot_id",
        "id",
    ),
    (
        "review_results",
        "review_tasks",
        "task_id",
        "id",
    ),
    (
        "review_results",
        "documents",
        "doc_id",
        "id",
    ),
    (
        "rule_parse_skills",
        "rule_sets",
        "rule_set_id",
        "id",
    ),
)

COMPOSITE_FOREIGN_KEYS = (
    ("rules", "fk_rules_rule_set_tenant", ("rule_set_id", "tenant_id"), "rule_sets", ("id", "tenant_id"), "CASCADE"),
    ("rule_snapshots", "fk_rule_snapshots_rule_set_tenant", ("rule_set_id", "tenant_id"), "rule_sets", ("id", "tenant_id"), "CASCADE"),
    ("contracts", "fk_contracts_rule_set_tenant", ("rule_set_id", "tenant_id"), "rule_sets", ("id", "tenant_id"), "CASCADE"),
    ("documents", "fk_documents_contract_tenant", ("contract_id", "tenant_id"), "contracts", ("id", "tenant_id"), "CASCADE"),
    ("ocr_tasks", "fk_ocr_tasks_rule_set_tenant", ("rule_set_id", "tenant_id"), "rule_sets", ("id", "tenant_id"), "CASCADE"),
    ("ocr_tasks", "fk_ocr_tasks_contract_tenant", ("contract_id", "tenant_id"), "contracts", ("id", "tenant_id"), "NO ACTION"),
    ("ocr_tasks", "fk_ocr_tasks_doc_tenant", ("doc_id", "tenant_id"), "documents", ("id", "tenant_id"), "NO ACTION"),
    ("review_tasks", "fk_review_tasks_contract_tenant", ("contract_id", "tenant_id"), "contracts", ("id", "tenant_id"), "CASCADE"),
    ("review_tasks", "fk_review_tasks_snapshot_tenant", ("snapshot_id", "tenant_id"), "rule_snapshots", ("id", "tenant_id"), "NO ACTION"),
    ("review_results", "fk_review_results_task_tenant", ("task_id", "tenant_id"), "review_tasks", ("id", "tenant_id"), "CASCADE"),
    ("review_results", "fk_review_results_doc_tenant", ("doc_id", "tenant_id"), "documents", ("id", "tenant_id"), "NO ACTION"),
)

LEGACY_SINGLE_FOREIGN_KEYS = (
    ("rules", "rule_set_id", "rule_sets", "CASCADE"),
    ("rule_snapshots", "rule_set_id", "rule_sets", "CASCADE"),
    ("contracts", "rule_set_id", "rule_sets", "CASCADE"),
    ("documents", "contract_id", "contracts", "CASCADE"),
    ("ocr_tasks", "rule_set_id", "rule_sets", "CASCADE"),
    ("ocr_tasks", "contract_id", "contracts", "SET NULL"),
    ("ocr_tasks", "doc_id", "documents", "SET NULL"),
    ("review_tasks", "contract_id", "contracts", "CASCADE"),
    ("review_tasks", "snapshot_id", "rule_snapshots", "SET NULL"),
    ("review_results", "task_id", "review_tasks", "CASCADE"),
    ("review_results", "doc_id", "documents", "SET NULL"),
)

COMPOSITE_UNIQUE_INDEXES = (
    ("uq_rule_sets_id_tenant", "rule_sets", ("id", "tenant_id")),
    ("uq_rules_id_tenant", "rules", ("id", "tenant_id")),
    ("uq_rule_snapshots_id_tenant", "rule_snapshots", ("id", "tenant_id")),
    ("uq_contracts_id_tenant", "contracts", ("id", "tenant_id")),
    ("uq_documents_id_tenant", "documents", ("id", "tenant_id")),
    ("uq_ocr_tasks_id_tenant", "ocr_tasks", ("id", "tenant_id")),
    ("uq_review_tasks_id_tenant", "review_tasks", ("id", "tenant_id")),
    ("uq_review_results_id_tenant", "review_results", ("id", "tenant_id")),
    ("uq_document_types_id_tenant", "document_types", ("id", "tenant_id")),
)


def _constraint_exists(conn: Connection, table: str, name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = CAST(:table_name AS regclass)
                  AND conname = :constraint_name
                """
            ),
            {"table_name": table, "constraint_name": name},
        ).scalar()
    )


def _single_column_fk_exists(
    conn: Connection,
    table: str,
    column: str,
    parent: str,
) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM pg_constraint AS c
                JOIN pg_attribute AS a
                  ON a.attrelid = c.conrelid
                 AND a.attnum = c.conkey[1]
                WHERE c.contype = 'f'
                  AND c.conrelid = CAST(:table_name AS regclass)
                  AND c.confrelid = CAST(:parent_name AS regclass)
                  AND cardinality(c.conkey) = 1
                  AND a.attname = :column_name
                """
            ),
            {
                "table_name": table,
                "parent_name": parent,
                "column_name": column,
            },
        ).scalar()
    )


def _add_foreign_key(
    conn: Connection,
    table: str,
    name: str,
    columns: Iterable[str],
    parent: str,
    parent_columns: Iterable[str],
    ondelete: str,
    deferred: bool = False,
) -> None:
    if _constraint_exists(conn, table, name):
        if deferred:
            conn.execute(
                text(
                    f"ALTER TABLE {table} "
                    f"ALTER CONSTRAINT {name} DEFERRABLE INITIALLY DEFERRED"
                )
            )
        return
    column_sql = ", ".join(columns)
    parent_sql = ", ".join(parent_columns)
    deferrable_sql = "DEFERRABLE INITIALLY DEFERRED" if deferred else ""
    conn.execute(
        text(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT {name}
            FOREIGN KEY ({column_sql})
            REFERENCES {parent} ({parent_sql})
            ON DELETE {ondelete}
            {deferrable_sql}
            NOT VALID
            """
        )
    )
    conn.execute(text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}"))


def _add_tenant_fk(conn: Connection, table: str, ondelete: str = "RESTRICT") -> None:
    if _single_column_fk_exists(conn, table, "tenant_id", "tenants"):
        return
    _add_foreign_key(
        conn,
        table,
        f"fk_{table}_tenant_id",
        ("tenant_id",),
        "tenants",
        ("id",),
        ondelete,
    )


def _ensure_default_tenant(conn: Connection) -> uuid.UUID:
    tenant_id = conn.execute(
        text("SELECT id FROM tenants WHERE code = :code"),
        {"code": DEFAULT_TENANT_CODE},
    ).scalar_one_or_none()
    if tenant_id is not None:
        return tenant_id

    tenant_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO tenants (id, name, code, status)
            VALUES (:id, :name, :code, 'active')
            """
        ),
        {"id": tenant_id, "name": DEFAULT_TENANT_NAME, "code": DEFAULT_TENANT_CODE},
    )
    logger.info("已创建默认租户 %s", tenant_id)
    return tenant_id


def default_tenant_id(db: Session | Connection) -> uuid.UUID:
    value = db.execute(
        text("SELECT id FROM tenants WHERE code = :code"),
        {"code": DEFAULT_TENANT_CODE},
    ).scalar_one_or_none()
    if value is None:
        raise RuntimeError("默认租户不存在，租户迁移尚未完成")
    return value


def _backfill_tenant_columns(conn: Connection, default_tenant: uuid.UUID) -> None:
    for table in TENANT_OWNED_TABLES:
        conn.execute(
            text(
                f"ALTER TABLE {table} "
                "ADD COLUMN IF NOT EXISTS tenant_id UUID"
            )
        )
    conn.execute(
        text(
            "ALTER TABLE rule_parse_skills "
            "ADD COLUMN IF NOT EXISTS tenant_id UUID"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE system_settings "
            "ADD COLUMN IF NOT EXISTS tenant_id UUID"
        )
    )

    conn.execute(
        text("UPDATE rule_sets SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
        {"tenant_id": default_tenant},
    )
    conn.execute(
        text("UPDATE document_types SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
        {"tenant_id": default_tenant},
    )
    conn.execute(
        text("UPDATE system_settings SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
        {"tenant_id": default_tenant},
    )
    conn.execute(
        text(
            "UPDATE rule_parse_skills "
            "SET tenant_id = :tenant_id "
            "WHERE tenant_id IS NULL AND is_builtin = false"
        ),
        {"tenant_id": default_tenant},
    )

    for child, parent, child_key, parent_key in PARENT_CHILD_BACKFILLS:
        conn.execute(
            text(
                f"""
                UPDATE {child} AS child
                SET tenant_id = parent.tenant_id
                FROM {parent} AS parent
                WHERE child.{child_key} = parent.{parent_key}
                  AND child.tenant_id IS NULL
                """
            )
        )

    remaining = {}
    for table in TENANT_OWNED_TABLES:
        count = conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
        ).scalar_one()
        if count:
            remaining[table] = count
    if remaining:
        raise RuntimeError(f"租户迁移存在无法回填的孤儿数据: {remaining}")


def _install_tenant_constraints(conn: Connection) -> None:
    for table in TENANT_OWNED_TABLES:
        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN tenant_id SET NOT NULL"))
        _add_tenant_fk(conn, table)

    conn.execute(
        text(
            "ALTER TABLE rule_parse_skills "
            "ALTER COLUMN tenant_id DROP NOT NULL"
        )
    )
    _add_tenant_fk(conn, "rule_parse_skills", ondelete="CASCADE")

    conn.execute(
        text("ALTER TABLE system_settings ALTER COLUMN tenant_id SET NOT NULL")
    )
    conn.execute(
        text(
            """
            DO $$
            DECLARE
                pk_definition text;
            BEGIN
                SELECT pg_get_constraintdef(c.oid)
                INTO pk_definition
                FROM pg_constraint c
                WHERE c.conrelid = 'system_settings'::regclass
                  AND c.contype = 'p';

                IF pk_definition IS NULL OR pk_definition NOT LIKE '%tenant_id%' THEN
                    ALTER TABLE system_settings DROP CONSTRAINT IF EXISTS system_settings_pkey;
                    ALTER TABLE system_settings
                        ADD CONSTRAINT system_settings_pkey
                        PRIMARY KEY (tenant_id, key);
                END IF;
            END $$;
            """
        )
    )
    _add_tenant_fk(conn, "system_settings", ondelete="CASCADE")

    for table in ("rule_sets", "document_types"):
        conn.execute(
            text(
                f"ALTER TABLE {table} "
                f"DROP CONSTRAINT IF EXISTS {table}_name_key"
            )
        )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_sets_tenant_name "
            "ON rule_sets (tenant_id, name)"
        )
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_document_types_tenant_name "
            "ON document_types (tenant_id, name)"
        )
    )


def _install_composite_constraints(conn: Connection) -> None:
    for name, table, columns in COMPOSITE_UNIQUE_INDEXES:
        column_sql = ", ".join(columns)
        conn.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {name} "
                f"ON {table} ({column_sql})"
            )
        )

    for table, name, columns, parent, parent_columns, ondelete in COMPOSITE_FOREIGN_KEYS:
        _add_foreign_key(
            conn,
            table,
            name,
            columns,
            parent,
            parent_columns,
            ondelete,
            deferred=True,
        )


def _ensure_legacy_single_column_fks(conn: Connection) -> None:
    """Keep the original column-scoped delete behavior alongside tenant integrity FKs."""
    for table, column, parent, ondelete in LEGACY_SINGLE_FOREIGN_KEYS:
        if _single_column_fk_exists(conn, table, column, parent):
            continue
        _add_foreign_key(
            conn,
            table,
            f"fk_{table}_{column}",
            (column,),
            parent,
            ("id",),
            ondelete,
        )


def _ensure_one_default_per_tenant(conn: Connection, default_tenant: uuid.UUID) -> None:
    conn.execute(
        text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY tenant_id
                           ORDER BY created_at, id
                       ) AS rn
                FROM rule_sets
                WHERE is_default = true
            )
            UPDATE rule_sets AS rs
            SET is_default = false
            FROM ranked
            WHERE rs.id = ranked.id
              AND ranked.rn > 1
            """
        )
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_rule_sets_one_default_per_tenant "
            "ON rule_sets (tenant_id) WHERE is_default"
        )
    )
    existing = conn.execute(
        text(
            "SELECT id FROM rule_sets "
            "WHERE tenant_id = :tenant_id "
            "ORDER BY is_default DESC, created_at, id LIMIT 1"
        ),
        {"tenant_id": default_tenant},
    ).scalar_one_or_none()
    if existing is None:
        conn.execute(
            text(
                """
                INSERT INTO rule_sets
                    (id, tenant_id, name, description, doc_types,
                     check_categories, use_default_skill, is_default,
                     next_rule_no)
                VALUES
                    (:id, :tenant_id, :name, :description,
                     CAST(:doc_types AS jsonb), CAST(:check_categories AS jsonb),
                     true, true, 1)
                """
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": default_tenant,
                "name": DEFAULT_RULE_SET_NAME,
                "description": "存量数据迁移时创建的默认规则集",
                "doc_types": json.dumps([]),
                "check_categories": json.dumps(
                    ["齐套性", "基础判断", "信息准确性", "时间逻辑"],
                    ensure_ascii=False,
                ),
            },
        )


def apply_security_v1_migration(engine: Engine) -> uuid.UUID:
    """Apply the tenant ownership migration and return the default tenant ID."""
    with engine.begin() as conn:
        default_tenant = _ensure_default_tenant(conn)
        _backfill_tenant_columns(conn, default_tenant)
        _install_tenant_constraints(conn)
        _ensure_legacy_single_column_fks(conn)
        _install_composite_constraints(conn)
        _ensure_one_default_per_tenant(conn, default_tenant)

    logger.info("安全多租户迁移 v1 完成，默认租户=%s", default_tenant)
    return default_tenant
