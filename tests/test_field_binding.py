# -*- coding: utf-8 -*-
"""字段绑定沉淀（判定架构 P2）测试：审查运行时自己校准两个词表。

验证：
1. 迁移建表（field_bindings）；
2. 绑定记录/累加/冲突；
3. 达到阈值自动升级为该文档类型别名（document_types.field_aliases）；
4. 冲突绑定不自动升级（避免把两个不同字段并成一个）；
5. 学到的签字角色映射可被审查引擎读取；
6. 统一判定落库时把"验证通过的等价映射"沉淀下来（含引用解析发现的等价键）。

运行：需 Postgres（TEST_DATABASE_URL 指向 *_test 库）：pytest tests/test_field_binding.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import inspect, select, text  # noqa: E402

from app.database import engine  # noqa: E402
from app.models import Document, DocumentType, FieldBinding, ReviewResult, Rule  # noqa: E402
from app.services import field_binding_service as svc  # noqa: E402
import app.services.review_judge as judge  # noqa: E402


def _ensure_doc_type(db, tenant_id, name: str) -> DocumentType:
    row = db.execute(
        select(DocumentType).where(
            DocumentType.tenant_id == tenant_id, DocumentType.name == name
        )
    ).scalars().first()
    if row is None:
        row = DocumentType(
            tenant_id=tenant_id,
            name=name,
            category="extra",
            is_required=False,
            key_fields=[],
            source="manual",
            status="active",
        )
        db.add(row)
        db.flush()
    return row


def test_migration_creates_table(migrated_db) -> None:
    assert "field_bindings" in inspect(engine).get_table_names()


def test_record_and_promote(migrated_db, default_tenant_id) -> None:
    db = migrated_db
    _ensure_doc_type(db, default_tenant_id, "报销单")
    for _ in range(3):
        svc.record_bindings(
            db,
            default_tenant_id,
            "报销单",
            [{"rule_field": "报销金额", "doc_key": "报销金额（元）", "kind": "alias"}],
            rule_id="R0016",
        )
    db.flush()
    row = db.execute(
        select(FieldBinding).where(FieldBinding.tenant_id == default_tenant_id)
    ).scalars().one()
    assert row.evidence_count == 3
    assert row.status == "observed"

    promoted = svc.promote_bindings(db, default_tenant_id, threshold=3)
    assert [p["doc_key"] for p in promoted] == ["报销金额（元）"]
    db.refresh(row)
    assert row.status == "confirmed"
    assert row.promoted_at is not None
    dt = db.execute(
        select(DocumentType).where(
            DocumentType.tenant_id == default_tenant_id, DocumentType.name == "报销单"
        )
    ).scalars().one()
    assert (dt.field_aliases or {}).get("报销金额（元）") == "报销金额"

    # 下次写入时该别名即可生效（写时归一）
    from app.services.doc_normalizer import normalize_extracted_keys

    normalized = normalize_extracted_keys(
        db, "报销单", {"报销金额（元）": 4860.0}, tenant_id=default_tenant_id
    )
    assert normalized == {"报销金额": 4860.0}


def test_conflict_not_promoted(migrated_db, default_tenant_id) -> None:
    db = migrated_db
    _ensure_doc_type(db, default_tenant_id, "发票")
    for rule_field in ("金额", "总价"):
        for _ in range(3):
            svc.record_bindings(
                db,
                default_tenant_id,
                "发票",
                [{"rule_field": rule_field, "doc_key": "合计", "kind": "alias"}],
            )
    db.flush()
    conflicts = svc.find_conflicts(db, default_tenant_id)
    assert {(c.doc_key, c.rule_field) for c in conflicts} == {
        ("合计", "金额"),
        ("合计", "总价"),
    }
    assert svc.promote_bindings(db, default_tenant_id, threshold=3) == []
    dt = db.execute(
        select(DocumentType).where(
            DocumentType.tenant_id == default_tenant_id, DocumentType.name == "发票"
        )
    ).scalars().one()
    assert not (dt.field_aliases or {})


def test_learned_role_fields(migrated_db, default_tenant_id) -> None:
    db = migrated_db
    svc.record_bindings(
        db,
        default_tenant_id,
        "报销单",
        [{"rule_field": "部门负责人签字", "doc_key": "审批签字", "kind": "role_signature"}],
    )
    db.flush()
    assert svc.learned_role_fields(db, default_tenant_id, "报销单") == {
        "部门负责人": "审批签字"
    }
    assert svc.binding_metrics(db, default_tenant_id)["role_signature"] == 1


def test_judge_sediments_bindings(migrated_db, default_tenant_id) -> None:
    """统一判定落库时沉淀绑定：模型显式 bindings + 引用解析发现的等价键。"""
    db = migrated_db
    _ensure_doc_type(db, default_tenant_id, "报销单")
    doc = Document(
        doc_type="报销单",
        file_name="BX.docx",
        extracted_fields={"报销金额（元）": 4860.0, "审批签字": "部门负责人 李强"},
        ocr_text="报销金额（元）：4,860.00\n审批签字：部门负责人 李强",
        ocr_confidence=0.98,
    )
    rule = Rule(
        rule_set_id=None,
        rule_no=16,
        rule_text="报销金额达到5000元及以上的报销单，应同时具备部门负责人签字审批。",
        doc_type="报销单",
        check_category="信息准确性",
        enabled=True,
        priority=30,
        status="confirmed",
        structure=None,
        scope={"doc_types": ["报销单"]},
        intents=["信息准确性"],
    )
    graph = {
        "nodes": [],
        "edges": [
            {
                "source": "报销单.报销金额",
                "target": "报销单.部门负责人签字",
                "type": "COMPARE_TO",
                "properties": {"rule_id": "R0016", "operator": "不小于"},
            }
        ],
    }
    prelim = ReviewResult(
        rule_text=rule.rule_text,
        check_category="信息准确性",
        doc_type="报销单",
        result="unverifiable",
        status="open",
        status_history=[],
        source="graph",
        graph_source="报销单.报销金额",
        graph_target="报销单.部门负责人签字",
        issue_desc="字段缺失",
        detail={"rule_id": "R0016", "reason": "field_data_missing"},
    )
    fake = SimpleNamespace(
        chat_json=lambda **kwargs: {
            "results": [
                {
                    "intent_id": "R0016#报销单.报销金额->报销单.部门负责人签字",
                    "rule_id": "R0016",
                    "verdict": "pass",
                    "condition_met": False,
                    "reason": "报销金额 4860 < 5000，条件不成立",
                    "confidence": 0.95,
                    "checks": [
                        {"op": "lt", "left": {"cite": "报销金额"}, "right": 5000, "ok": True}
                    ],
                    # 引用用的是规则侧字段名，证据包里是带单位后缀的键 → 解析发现等价
                    "citations": [{"doc": "BX.docx", "key": "报销金额", "value": 4860.0}],
                    "bindings": [
                        {
                            "rule_field": "部门负责人签字",
                            "doc_key": "审批签字",
                            "kind": "role_signature",
                        }
                    ],
                }
            ]
        }
    )
    original = judge.get_llm_client
    try:
        judge.get_llm_client = lambda: fake  # type: ignore[assignment]
        report = judge.run_unified_judgement(
            db, default_tenant_id, [doc], [rule], [prelim], graph_data=graph
        )
    finally:
        judge.get_llm_client = original  # type: ignore[assignment]
    db.flush()
    assert prelim.result == "pass"
    assert report.bindings_recorded >= 2
    rows = db.execute(
        select(FieldBinding).where(FieldBinding.tenant_id == default_tenant_id)
    ).scalars().all()
    kinds = {(r.rule_field, r.doc_key, r.kind) for r in rows}
    assert ("报销金额", "报销金额（元）", "alias") in kinds  # 引用解析沉淀
    assert ("部门负责人签字", "审批签字", "role_signature") in kinds  # 模型显式绑定
