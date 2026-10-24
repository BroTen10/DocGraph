from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    Contract,
    Document,
    DocumentType,
    OcrTask,
    ReviewResult,
    ReviewTask,
    Rule,
    RuleSet,
    RuleSnapshot,
    RuleParseSkill,
    SystemSetting,
)
from app.models.mixins import TenantOwnedMixin


TENANT_OWNED_MODELS = [
    RuleSet,
    Rule,
    RuleSnapshot,
    Contract,
    Document,
    OcrTask,
    ReviewTask,
    ReviewResult,
    DocumentType,
]


def _default_tenant_id(db):
    from app.migrations.security_v1 import default_tenant_id

    return default_tenant_id(db)


def test_all_business_models_are_tenant_owned(migrated_db):
    tenant_id = _default_tenant_id(migrated_db)
    assert tenant_id is not None
    for model in TENANT_OWNED_MODELS:
        assert issubclass(model, TenantOwnedMixin), model.__name__
        assert model.tenant_id.property.columns[0].nullable is False
        null_count = migrated_db.execute(
            select(func.count()).select_from(model).where(model.tenant_id.is_(None))
        ).scalar_one()
        assert null_count == 0, model.__name__

    assert migrated_db.execute(select(func.count(DocumentType.id))).scalar_one() > 0


def test_rule_set_name_is_unique_within_a_tenant_only(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    migrated_db.add_all(
        [
            RuleSet(tenant_id=tenant_a.id, name="跨租户同名规则集"),
            RuleSet(tenant_id=tenant_b.id, name="跨租户同名规则集"),
        ]
    )
    migrated_db.commit()

    migrated_db.add(RuleSet(tenant_id=tenant_a.id, name="跨租户同名规则集"))
    with pytest.raises(IntegrityError):
        migrated_db.commit()
    migrated_db.rollback()


def test_only_one_default_rule_set_per_tenant(migrated_db, two_tenants):
    tenant_a, _ = two_tenants
    migrated_db.add_all(
        [
            RuleSet(tenant_id=tenant_a.id, name="默认一", is_default=True),
            RuleSet(tenant_id=tenant_a.id, name="默认二", is_default=True),
        ]
    )
    with pytest.raises(IntegrityError):
        migrated_db.commit()
    migrated_db.rollback()


def test_settings_and_skills_use_explicit_nullability(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    migrated_db.add_all(
        [
            SystemSetting(tenant_id=tenant_a.id, key="test.setting", value=1),
            SystemSetting(tenant_id=tenant_b.id, key="test.setting", value=2),
            RuleParseSkill(
                tenant_id=None,
                name="系统模板",
                is_builtin=True,
                content={},
            ),
            RuleParseSkill(
                tenant_id=tenant_a.id,
                name="租户自定义",
                is_builtin=False,
                content={},
            ),
        ]
    )
    migrated_db.commit()

    values = migrated_db.execute(
        select(SystemSetting.tenant_id, SystemSetting.value).where(
            SystemSetting.key == "test.setting"
        )
    ).all()
    assert {row.tenant_id for row in values} == {tenant_a.id, tenant_b.id}
    builtin = migrated_db.execute(
        select(RuleParseSkill).where(RuleParseSkill.name == "系统模板")
    ).scalar_one()
    assert builtin.tenant_id is None


def test_migration_is_idempotent(migrated_db):
    from app.database import engine
    from app.migrations.security_v1 import apply_security_v1_migration

    first_tenant = _default_tenant_id(migrated_db)
    first_count = migrated_db.execute(
        select(func.count(RuleSet.id)).where(RuleSet.tenant_id == first_tenant)
    ).scalar_one()
    migrated_db.rollback()
    apply_security_v1_migration(engine)
    migrated_db.expire_all()
    assert _default_tenant_id(migrated_db) == first_tenant
    assert migrated_db.execute(
        select(func.count(RuleSet.id)).where(RuleSet.tenant_id == first_tenant)
    ).scalar_one() == first_count


def test_legacy_rows_backfill_tenant_from_parent_relationships(migrated_db):
    from app.database import engine
    from app.migrations.security_v1 import apply_security_v1_migration

    migrated_db.rollback()
    rule_set_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    document_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    ocr_id = uuid.uuid4()
    review_id = uuid.uuid4()
    result_id = uuid.uuid4()

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE rule_sets CASCADE"))
        for table in (
            "rule_sets",
            "rules",
            "rule_snapshots",
            "contracts",
            "documents",
            "ocr_tasks",
            "review_tasks",
            "review_results",
        ):
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP NOT NULL"))

        conn.execute(
            text(
                """
                INSERT INTO rule_sets
                    (id, name, doc_types, check_categories, use_default_skill,
                     is_default, next_rule_no, tenant_id)
                VALUES
                    (:id, '旧规则集', '[]'::jsonb, '[]'::jsonb, true,
                     true, 2, NULL)
                """
            ),
            {"id": rule_set_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO contracts
                    (id, rule_set_id, contract_no, alias_list, status, tenant_id)
                VALUES
                    (:id, :rule_set_id, 'LEGACY-001', '[]'::jsonb, 'uploaded', NULL)
                """
            ),
            {"id": contract_id, "rule_set_id": rule_set_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO documents
                    (id, contract_id, file_name, file_path, file_type, doc_type,
                     is_required, ocr_status, extracted_fields, ocr_layout, tenant_id)
                VALUES
                    (:id, :contract_id, '旧单证.pdf', '/tmp/旧单证.pdf', 'pdf', '其他',
                     false, 'done', '{}'::jsonb, '{}'::jsonb, NULL)
                """
            ),
            {"id": document_id, "contract_id": contract_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO rule_snapshots
                    (id, rule_set_id, rule_count, rules_json, tenant_id)
                VALUES (:id, :rule_set_id, 1, '[]'::jsonb, NULL)
                """
            ),
            {"id": snapshot_id, "rule_set_id": rule_set_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO rules
                    (id, rule_set_id, rule_no, rule_text, tolerance, enabled,
                     priority, status, defects, intents, tenant_id)
                VALUES
                    (:id, :rule_set_id, 1, '旧规则', '{}'::jsonb, true,
                     100, 'confirmed', '[]'::jsonb, '[]'::jsonb, NULL)
                """
            ),
            {"id": rule_id, "rule_set_id": rule_set_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO ocr_tasks
                    (id, rule_set_id, scope, contract_id, status, progress,
                     total_count, done_count, success_count, failed_count,
                     failures, applied_config, tenant_id)
                VALUES
                    (:id, :rule_set_id, 'contract_batch', :contract_id, 'completed',
                     100, 1, 1, 1, 0, '[]'::jsonb, '{}'::jsonb, NULL)
                """
            ),
            {
                "id": ocr_id,
                "rule_set_id": rule_set_id,
                "contract_id": contract_id,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO review_tasks
                    (id, contract_id, status, progress, summary, tenant_id)
                VALUES
                    (:id, :contract_id, 'completed', 100, '{}'::jsonb, NULL)
                """
            ),
            {"id": review_id, "contract_id": contract_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO review_results
                    (id, task_id, result, status, status_history, detail, tenant_id)
                VALUES
                    (:id, :task_id, 'pass', 'closed', '[]'::jsonb, '{}'::jsonb, NULL)
                """
            ),
            {"id": result_id, "task_id": review_id},
        )

    apply_security_v1_migration(engine)

    with engine.connect() as conn:
        tenant_id = conn.execute(
            text("SELECT id FROM tenants WHERE code = 'default'")
        ).scalar_one()
        for table, row_id in (
            ("rule_sets", rule_set_id),
            ("rules", rule_id),
            ("rule_snapshots", snapshot_id),
            ("contracts", contract_id),
            ("documents", document_id),
            ("ocr_tasks", ocr_id),
            ("review_tasks", review_id),
            ("review_results", result_id),
        ):
            actual = conn.execute(
                text(f"SELECT tenant_id FROM {table} WHERE id = :id"),
                {"id": row_id},
            ).scalar_one()
            assert actual == tenant_id, table
