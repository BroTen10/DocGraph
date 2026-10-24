from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import Contract, Document, OcrTask, ReviewResult, ReviewTask, RuleSet
from app.services import ocr_task_service, review_service

from conftest import authenticate


@pytest.fixture
def review_objects(migrated_db, two_tenants):
    tenant_a, tenant_b = two_tenants
    now = datetime.now()

    rule_set_a = RuleSet(tenant_id=tenant_a.id, name="租户 A 规则集")
    rule_set_b = RuleSet(tenant_id=tenant_b.id, name="租户 B 规则集")
    migrated_db.add_all([rule_set_a, rule_set_b])
    migrated_db.flush()

    contract_a = Contract(
        tenant_id=tenant_a.id,
        rule_set_id=rule_set_a.id,
        contract_no="A-001",
        status="uploaded",
    )
    contract_b = Contract(
        tenant_id=tenant_b.id,
        rule_set_id=rule_set_b.id,
        contract_no="B-001",
        status="uploaded",
    )
    migrated_db.add_all([contract_a, contract_b])
    migrated_db.flush()

    doc_a = Document(
        tenant_id=tenant_a.id,
        contract_id=contract_a.id,
        file_name="a.pdf",
        file_path="/tmp/a.pdf",
        file_type="pdf",
        doc_type="代理协议",
    )
    doc_b = Document(
        tenant_id=tenant_b.id,
        contract_id=contract_b.id,
        file_name="b.pdf",
        file_path="/tmp/b.pdf",
        file_type="pdf",
        doc_type="代理协议",
    )
    migrated_db.add_all([doc_a, doc_b])
    migrated_db.flush()

    review_task_a = ReviewTask(
        tenant_id=tenant_a.id,
        contract_id=contract_a.id,
        status="completed",
        progress=100,
        start_time=now,
        summary={},
    )
    review_task_b = ReviewTask(
        tenant_id=tenant_b.id,
        contract_id=contract_b.id,
        status="completed",
        progress=100,
        start_time=now,
        summary={},
    )
    migrated_db.add_all([review_task_a, review_task_b])
    migrated_db.flush()

    ocr_task_a = OcrTask(
        tenant_id=tenant_a.id,
        rule_set_id=rule_set_a.id,
        contract_id=contract_a.id,
        doc_id=doc_a.id,
        scope="single_doc",
        status="completed",
        progress=100,
        total_count=1,
        done_count=1,
        start_time=now - timedelta(minutes=1),
        end_time=now,
    )
    ocr_task_b = OcrTask(
        tenant_id=tenant_b.id,
        rule_set_id=rule_set_b.id,
        contract_id=contract_b.id,
        doc_id=doc_b.id,
        scope="single_doc",
        status="completed",
        progress=100,
        total_count=1,
        done_count=1,
        start_time=now - timedelta(minutes=1),
        end_time=now,
    )
    migrated_db.add_all([ocr_task_a, ocr_task_b])
    migrated_db.flush()

    review_result_a = ReviewResult(
        tenant_id=tenant_a.id,
        task_id=review_task_a.id,
        rule_id=None,
        result="fail",
        status="open",
        status_history=[],
        detail={},
    )
    review_result_b = ReviewResult(
        tenant_id=tenant_b.id,
        task_id=review_task_b.id,
        rule_id=None,
        result="fail",
        status="open",
        status_history=[],
        detail={},
    )
    migrated_db.add_all([review_result_a, review_result_b])
    migrated_db.commit()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "rule_set_a": rule_set_a,
        "rule_set_b": rule_set_b,
        "contract_a": contract_a,
        "contract_b": contract_b,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "review_task_a": review_task_a,
        "review_task_b": review_task_b,
        "ocr_task_a": ocr_task_a,
        "ocr_task_b": ocr_task_b,
        "review_result_a": review_result_a,
        "review_result_b": review_result_b,
    }


def test_ocr_and_review_ids_from_other_tenant_return_404(
    client,
    admin_b,
    review_objects,
):
    headers = authenticate(client, admin_b)
    doc_a = review_objects["doc_a"]
    rule_set_a = review_objects["rule_set_a"]
    review_task_a = review_objects["review_task_a"]

    assert client.post(
        f"/api/ocr/documents/{doc_a.id}",
        params={"rule_set_id": rule_set_a.id},
        headers=headers,
    ).status_code == 404
    assert client.get(f"/api/reviews/{review_task_a.id}").status_code == 404
    assert client.get(f"/api/reviews/{review_task_a.id}/by-rule").status_code == 404
    assert client.get(f"/api/reviews/{review_task_a.id}/by-doc").status_code == 404


def test_task_lists_and_details_use_tenant(client, admin_auth, review_objects):
    authenticate(client, admin_auth)
    review_ids = {
        item["id"] for item in client.get("/api/reviews").json()
    }
    ocr_ids = {
        item["id"]
        for item in client.get(
            "/api/ocr/tasks",
            params={"rule_set_id": review_objects["rule_set_a"].id},
        ).json()
    }

    assert str(review_objects["review_task_a"].id) in review_ids
    assert str(review_objects["review_task_b"].id) not in review_ids
    assert str(review_objects["ocr_task_a"].id) in ocr_ids
    assert str(review_objects["ocr_task_b"].id) not in ocr_ids


def test_task_reconciliation_can_be_scoped_to_one_tenant(
    migrated_db,
    review_objects,
):
    now = datetime.now()
    tenant_a = review_objects["tenant_a"]
    tenant_b = review_objects["tenant_b"]

    review_a = ReviewTask(
        tenant_id=tenant_a.id,
        contract_id=review_objects["contract_a"].id,
        status="running",
        progress=5,
        start_time=now - timedelta(hours=2),
        summary={},
    )
    review_b = ReviewTask(
        tenant_id=tenant_b.id,
        contract_id=review_objects["contract_b"].id,
        status="running",
        progress=5,
        start_time=now - timedelta(hours=2),
        summary={},
    )
    ocr_a = OcrTask(
        tenant_id=tenant_a.id,
        rule_set_id=review_objects["rule_set_a"].id,
        contract_id=review_objects["contract_a"].id,
        scope="contract_batch",
        status="running",
        progress=5,
        total_count=1,
        start_time=now - timedelta(hours=2),
    )
    ocr_b = OcrTask(
        tenant_id=tenant_b.id,
        rule_set_id=review_objects["rule_set_b"].id,
        contract_id=review_objects["contract_b"].id,
        scope="contract_batch",
        status="running",
        progress=5,
        total_count=1,
        start_time=now - timedelta(hours=2),
    )
    migrated_db.add_all([review_a, review_b, ocr_a, ocr_b])
    migrated_db.commit()

    review_service.reconcile_stale_review_tasks(migrated_db, tenant_id=tenant_a.id)
    ocr_task_service.reconcile_stale_tasks(migrated_db, tenant_id=tenant_a.id)

    migrated_db.refresh(review_a)
    migrated_db.refresh(review_b)
    migrated_db.refresh(ocr_a)
    migrated_db.refresh(ocr_b)
    assert review_a.status == "failed"
    assert review_b.status == "running"
    assert ocr_a.status == "failed"
    assert ocr_b.status == "running"


def test_result_status_history_uses_authenticated_user(
    client,
    admin_auth,
    review_objects,
):
    headers = authenticate(client, admin_auth)
    result_a = review_objects["review_result_a"]

    response = client.patch(
        f"/api/reviews/results/{result_a.id}/status",
        json={"status": "confirmed", "note": "已核对原件"},
        headers=headers,
    )
    assert response.status_code == 200
    history = response.json()["status_history"]
    assert history[-1]["by"] == str(admin_auth.user_id)
    assert history[-1]["by_name"] == "tenant_admin测试用户"
    assert history[-1]["note"] == "已核对原件"
