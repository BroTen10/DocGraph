"""Security acceptance tests for all externally reachable API surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import uuid
from uuid import uuid4

from conftest import authenticate

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
    SystemSetting,
)
from app.neo4j_client import get_neo4j_client


TENANT_GETS = [
    "/api/constants/doc-types",
    "/api/rule-sets",
    "/api/contracts",
    "/api/doc-types",
    "/api/settings",
    "/api/reviews",
    f"/api/ocr/tasks?rule_set_id={uuid4()}",
    f"/api/rules?rule_set_id={uuid4()}",
    f"/api/rules/defect-summary?rule_set_id={uuid4()}",
    f"/api/rules/snapshots?rule_set_id={uuid4()}",
    "/api/rules/build-graph-tasks",
    f"/api/rules/graph?rule_set_id={uuid4()}",
    "/api/tenant/members",
]

PLATFORM_GETS = [
    "/api/platform/registrations",
    "/api/platform/tenants",
]


def test_all_business_endpoints_require_login(client):
    for path in [*TENANT_GETS, *PLATFORM_GETS]:
        response = client.get(path)
        assert response.status_code == 401, (path, response.status_code)


def test_unauthenticated_writes_return_401_before_csrf(client):
    responses = [
        client.post(
            "/api/reviews/start",
            json={"contract_id": str(uuid4()), "snapshot_id": None},
        ),
        client.put("/api/settings", json={"items": []}),
        client.delete(f"/api/contracts/{uuid4()}"),
    ]
    assert [response.status_code for response in responses] == [401, 401, 401]
    assert all("CSRF" not in response.text for response in responses)


def test_public_health_and_version_remain_available(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/version").status_code == 200


def test_development_api_docs_are_available(client):
    assert client.app.docs_url == "/docs"
    assert client.app.openapi_url == "/openapi.json"


def test_viewer_has_read_access_but_no_write_access(client, viewer_auth):
    authenticate(client, viewer_auth)
    assert client.get("/api/contracts").status_code == 200
    assert client.get("/api/doc-types").status_code == 200
    assert client.get("/api/settings").status_code == 403

    headers = authenticate(client, viewer_auth)
    rule_set_id = uuid4()
    blocked = client.post(
        "/api/contracts/upload",
        params={"rule_set_id": str(rule_set_id)},
        files=[("files", ("sample.pdf", b"%PDF-1.4", "application/pdf"))],
        headers=headers,
    )
    assert blocked.status_code == 403


def test_reviewer_can_read_workflow_but_not_admin_surfaces(client, reviewer_auth):
    authenticate(client, reviewer_auth)
    assert client.get("/api/contracts").status_code == 200
    assert client.get("/api/settings").status_code == 403
    assert client.get("/api/tenant/members").status_code == 403
    assert client.get("/api/platform/registrations").status_code == 403


def test_tenant_admin_can_read_settings_but_not_platform_admin_surfaces(
    client,
    admin_auth,
):
    authenticate(client, admin_auth)
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/tenant/members").status_code == 200
    assert client.get("/api/platform/registrations").status_code == 403
    assert client.get("/api/platform/tenants").status_code == 403


def test_platform_admin_cannot_access_tenant_business_data(
    client,
    platform_auth,
):
    authenticate(client, platform_auth)
    for path in TENANT_GETS:
        response = client.get(path)
        assert response.status_code == 403, (path, response.status_code)


def test_role_denied_write_is_not_mistaken_for_csrf_failure(
    client,
    viewer_auth,
):
    headers = authenticate(client, viewer_auth)
    response = client.put(
        "/api/settings",
        json={"items": []},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json().get("detail") != "CSRF 校验失败"


SHARED_RESOURCE_NAME = "最终验收同名资源"
SETTING_KEY = "review.amount_tolerance_percent"


@dataclass(frozen=True)
class TenantBundle:
    tenant_id: uuid.UUID
    rule_set: RuleSet
    contract: Contract
    document: Document
    doc_type: DocumentType
    rule: Rule
    snapshot: RuleSnapshot
    ocr_task: OcrTask
    review_task: ReviewTask
    review_result: ReviewResult


def _create_tenant_bundle(
    db,
    *,
    tenant_id: uuid.UUID,
    label: str,
    temp_root: Path,
    tolerance: float,
) -> TenantBundle:
    now = datetime.now()
    rule_set = RuleSet(
        tenant_id=tenant_id,
        name=SHARED_RESOURCE_NAME,
        is_default=True,
    )
    doc_type = DocumentType(
        tenant_id=tenant_id,
        name=SHARED_RESOURCE_NAME,
        category="other",
        key_fields=["合同号"],
        aliases=[],
        field_aliases={},
        ocr_extra_fields=[],
        source="manual",
        status="active",
    )
    db.add_all([rule_set, doc_type])
    db.flush()

    contract = Contract(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        contract_no=f"ACCEPT-{label}-{uuid4().hex[:8]}",
        status="reviewed",
    )
    db.add(contract)
    db.flush()

    directory = temp_root / str(tenant_id) / str(rule_set.id) / str(contract.id)
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / f"{label}.pdf"
    file_content = f"%PDF-1.4 tenant-{label}".encode()
    file_path.write_bytes(file_content)
    document = Document(
        tenant_id=tenant_id,
        contract_id=contract.id,
        file_name=f"{label}.pdf",
        file_path=str(file_path),
        file_type="pdf",
        doc_type=SHARED_RESOURCE_NAME,
        ocr_status="done",
        ocr_text=f"tenant {label}",
        extracted_fields={"合同号": contract.contract_no},
    )
    db.add(document)
    db.flush()

    rule = Rule(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        rule_no=1,
        doc_type=SHARED_RESOURCE_NAME,
        check_category="信息准确性",
        rule_text=f"{label} 租户验收规则",
        status="confirmed",
        enabled=True,
    )
    db.add(rule)
    db.flush()

    graph_id = f"graph_accept_{label}_{uuid4().hex}"
    snapshot = RuleSnapshot(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        rule_count=1,
        rules_json=[
            {
                "id": str(rule.id),
                "rule_no": 1,
                "rule_text": rule.rule_text,
            }
        ],
        graph_id=graph_id,
        node_count=1,
        edge_count=0,
        operator="acceptance",
    )
    db.add(snapshot)
    db.flush()

    ocr_task = OcrTask(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        scope="single_doc",
        doc_id=document.id,
        contract_id=contract.id,
        status="completed",
        progress=100,
        total_count=1,
        done_count=1,
        success_count=1,
        start_time=now - timedelta(minutes=1),
        end_time=now,
    )
    db.add(ocr_task)
    db.flush()

    review_task = ReviewTask(
        tenant_id=tenant_id,
        contract_id=contract.id,
        snapshot_id=snapshot.id,
        status="completed",
        progress=100,
        stage="完成",
        summary={"total": 1, "fail": 1},
        start_time=now,
        end_time=now,
    )
    db.add(review_task)
    db.flush()

    review_result = ReviewResult(
        tenant_id=tenant_id,
        task_id=review_task.id,
        rule_id=rule.id,
        rule_text=rule.rule_text,
        doc_type=rule.doc_type,
        check_category=rule.check_category,
        doc_id=document.id,
        result="fail",
        status="open",
        status_history=[],
        issue_desc=f"{label} 租户问题",
        detail={"field": "合同号"},
        suggestion="人工复核",
    )
    db.add(review_result)
    db.add(
        SystemSetting(
            tenant_id=tenant_id,
            key=SETTING_KEY,
            value=tolerance,
            group="审查与建议",
            label="金额容差百分比",
            kind="number",
        )
    )
    db.commit()
    return TenantBundle(
        tenant_id=tenant_id,
        rule_set=rule_set,
        contract=contract,
        document=document,
        doc_type=doc_type,
        rule=rule,
        snapshot=snapshot,
        ocr_task=ocr_task,
        review_task=review_task,
        review_result=review_result,
    )


def test_tenant_ab_full_resource_isolation(
    client,
    migrated_db,
    admin_auth,
    admin_b,
    tmp_path,
):
    assert admin_auth.tenant_id is not None
    assert admin_b.tenant_id is not None

    bundle_a = _create_tenant_bundle(
        migrated_db,
        tenant_id=admin_auth.tenant_id,
        label="a",
        temp_root=tmp_path,
        tolerance=2.5,
    )
    bundle_b = _create_tenant_bundle(
        migrated_db,
        tenant_id=admin_b.tenant_id,
        label="b",
        temp_root=tmp_path,
        tolerance=7.5,
    )

    neo4j = get_neo4j_client()
    graph_ids = [bundle_a.snapshot.graph_id, bundle_b.snapshot.graph_id]
    try:
        neo4j.write_rule_graph(
            bundle_a.snapshot.graph_id,
            bundle_a.tenant_id,
            [{"name": "A最终验收节点", "type": "Field", "attributes": {}}],
            [],
        )
        neo4j.write_rule_graph(
            bundle_b.snapshot.graph_id,
            bundle_b.tenant_id,
            [{"name": "B最终验收节点", "type": "Field", "attributes": {}}],
            [],
        )

        headers_a = authenticate(client, admin_auth)
        rule_sets = client.get("/api/rule-sets")
        assert rule_sets.status_code == 200
        assert {item["id"] for item in rule_sets.json()} == {
            str(bundle_a.rule_set.id)
        }
        assert client.get(f"/api/contracts/{bundle_a.contract.id}").status_code == 200
        assert client.get(f"/api/doc-types/{bundle_a.doc_type.id}").status_code == 200
        assert client.get(f"/api/rules/snapshots/{bundle_a.snapshot.id}").status_code == 200
        assert client.get(f"/api/ocr/tasks/{bundle_a.ocr_task.id}").status_code == 200
        assert client.get(f"/api/reviews/{bundle_a.review_task.id}").status_code == 200
        own_file = client.get(
            f"/api/contracts/documents/{bundle_a.document.id}/file"
        )
        assert own_file.status_code == 200
        assert own_file.content == b"%PDF-1.4 tenant-a"
        assert client.get(
            f"/api/rules/graph/{bundle_a.snapshot.graph_id}"
        ).status_code == 200

        settings_a = client.get("/api/settings").json()["settings"]
        value_a = next(
            item["value"] for item in settings_a if item["key"] == SETTING_KEY
        )
        assert value_a == 2.5

        foreign_paths = [
            f"/api/rule-sets/{bundle_b.rule_set.id}",
            f"/api/contracts/{bundle_b.contract.id}",
            f"/api/contracts/documents/{bundle_b.document.id}/file",
            f"/api/contracts/documents/{bundle_b.document.id}/ocr",
            f"/api/doc-types/{bundle_b.doc_type.id}",
            f"/api/rules/snapshots/{bundle_b.snapshot.id}",
            f"/api/ocr/tasks/{bundle_b.ocr_task.id}",
            f"/api/reviews/{bundle_b.review_task.id}",
            f"/api/reviews/{bundle_b.review_task.id}/by-rule",
            f"/api/reviews/{bundle_b.review_task.id}/by-doc",
            f"/api/rules/graph/{bundle_b.snapshot.graph_id}",
        ]
        for path in foreign_paths:
            assert client.get(path).status_code == 404, path

        foreign_rule_set_queries = [
            f"/api/rules?rule_set_id={bundle_b.rule_set.id}",
            f"/api/rules/snapshots?rule_set_id={bundle_b.rule_set.id}",
            f"/api/ocr/tasks?rule_set_id={bundle_b.rule_set.id}",
            f"/api/rules/graph?rule_set_id={bundle_b.rule_set.id}",
        ]
        for path in foreign_rule_set_queries:
            assert client.get(path).status_code == 404, path

        assert client.patch(
            f"/api/reviews/results/{bundle_b.review_result.id}/status",
            json={"status": "confirmed", "note": "不得跨租户"},
            headers=headers_a,
        ).status_code == 404
        assert client.patch(
            f"/api/tenant/members/{admin_b.membership_id}",
            json={"role": "viewer"},
            headers=headers_a,
        ).status_code == 404

        headers_b = authenticate(client, admin_b)
        rule_sets_b = client.get("/api/rule-sets")
        assert rule_sets_b.status_code == 200
        assert {item["id"] for item in rule_sets_b.json()} == {
            str(bundle_b.rule_set.id)
        }
        assert client.get(f"/api/contracts/{bundle_b.contract.id}").status_code == 200
        assert client.get(f"/api/doc-types/{bundle_b.doc_type.id}").status_code == 200
        assert client.get(f"/api/rules/snapshots/{bundle_b.snapshot.id}").status_code == 200
        assert client.get(f"/api/ocr/tasks/{bundle_b.ocr_task.id}").status_code == 200
        assert client.get(f"/api/reviews/{bundle_b.review_task.id}").status_code == 200
        own_file_b = client.get(
            f"/api/contracts/documents/{bundle_b.document.id}/file"
        )
        assert own_file_b.status_code == 200
        assert own_file_b.content == b"%PDF-1.4 tenant-b"
        assert client.get(
            f"/api/rules/graph/{bundle_b.snapshot.graph_id}"
        ).status_code == 200

        settings_b = client.get("/api/settings").json()["settings"]
        value_b = next(
            item["value"] for item in settings_b if item["key"] == SETTING_KEY
        )
        assert value_b == 7.5
        assert client.get(f"/api/contracts/{bundle_a.contract.id}").status_code == 404
        assert client.get(f"/api/rules/graph/{bundle_a.snapshot.graph_id}").status_code == 404
        assert client.patch(
            f"/api/tenant/members/{admin_auth.membership_id}",
            json={"role": "viewer"},
            headers=headers_b,
        ).status_code == 404
    finally:
        for graph_id in graph_ids:
            neo4j.clear_graph(graph_id, bundle_a.tenant_id)
            neo4j.clear_graph(graph_id, bundle_b.tenant_id)


def test_disabled_member_existing_session_is_revoked(
    client,
    migrated_db,
    admin_auth,
    reviewer_auth,
):
    assert admin_auth.tenant_id == reviewer_auth.tenant_id
    assert reviewer_auth.membership_id is not None
    authenticate(client, reviewer_auth)
    assert client.get("/api/contracts").status_code == 200

    headers = authenticate(client, admin_auth)
    response = client.patch(
        f"/api/tenant/members/{reviewer_auth.membership_id}",
        json={"status": "disabled"},
        headers=headers,
    )
    assert response.status_code == 200

    authenticate(client, reviewer_auth)
    assert client.get("/api/contracts").status_code == 401
