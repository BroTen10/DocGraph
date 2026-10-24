from __future__ import annotations

from conftest import authenticate
import pytest
from sqlalchemy import func, select

from app.models import DocumentType, SystemSetting
from app.services.settings_service import get_all_settings, get_setting, seed_tenant_defaults


def test_same_doc_type_name_allowed_and_settings_isolated(
    client,
    admin_auth,
    admin_b,
):
    headers_a = authenticate(client, admin_auth)
    assert client.post(
        "/api/doc-types",
        json={"name": "自定义发票"},
        headers=headers_a,
    ).status_code == 201
    updated = client.put(
        "/api/settings",
        json={
            "items": [
                {"key": "review.amount_tolerance_percent", "value": 7}
            ]
        },
        headers=headers_a,
    )
    assert updated.status_code == 200

    assert client.get("/api/doc-types").json()["total"] == 1
    assert {
        item["name"] for item in client.get("/api/doc-types").json()["doc_types"]
    } == {"自定义发票"}

    authenticate(client, admin_b)
    headers_b = {"X-CSRF-Token": admin_b.csrf_token}
    assert client.post(
        "/api/doc-types",
        json={"name": "自定义发票"},
        headers=headers_b,
    ).status_code == 201
    assert client.get("/api/doc-types").json()["total"] == 1

    settings = client.get("/api/settings").json()["settings"]
    tolerance = next(
        item
        for item in settings
        if item["key"] == "review.amount_tolerance_percent"
    )
    assert tolerance["value"] != 7


def test_document_type_and_setting_object_ids_are_hidden_across_tenants(
    client,
    migrated_db,
    admin_auth,
    admin_b,
):
    assert admin_auth.tenant_id is not None
    assert admin_b.tenant_id is not None
    document_type_a = DocumentType(
        tenant_id=admin_auth.tenant_id,
        name="甲租户专属类型",
        status="active",
    )
    migrated_db.add(document_type_a)
    migrated_db.add(
        SystemSetting(
            tenant_id=admin_auth.tenant_id,
            key="review.amount_tolerance_percent",
            value=8,
            group="运行参数",
            label="金额容差",
            kind="number",
        )
    )
    migrated_db.commit()

    authenticate(client, admin_b)
    assert client.get(f"/api/doc-types/{document_type_a.id}").status_code == 404
    assert get_setting(
        migrated_db,
        admin_b.tenant_id,
        "review.amount_tolerance_percent",
    ) != 8


def test_tenant_default_seed_is_idempotent(migrated_db, two_tenants):
    tenant_id = two_tenants[0].id
    seed_tenant_defaults(migrated_db, tenant_id)
    first_count = migrated_db.execute(
        select(func.count(DocumentType.id)).where(
            DocumentType.tenant_id == tenant_id
        )
    ).scalar_one()

    seed_tenant_defaults(migrated_db, tenant_id)
    second_count = migrated_db.execute(
        select(func.count(DocumentType.id)).where(
            DocumentType.tenant_id == tenant_id
        )
    ).scalar_one()

    assert first_count > 0
    assert second_count == first_count


def test_settings_list_only_returns_current_tenant_overrides(
    migrated_db,
    admin_auth,
    admin_b,
):
    assert admin_auth.tenant_id is not None
    assert admin_b.tenant_id is not None
    migrated_db.add(
        SystemSetting(
            tenant_id=admin_auth.tenant_id,
            key="review.amount_tolerance_percent",
            value=9,
            group="运行参数",
            label="金额容差",
            kind="number",
        )
    )
    migrated_db.commit()

    values_a = {
        item["key"]: item["value"]
        for item in get_all_settings(migrated_db, admin_auth.tenant_id)
    }
    values_b = {
        item["key"]: item["value"]
        for item in get_all_settings(migrated_db, admin_b.tenant_id)
    }
    assert values_a["review.amount_tolerance_percent"] == 9
    assert values_b["review.amount_tolerance_percent"] != 9


@pytest.mark.parametrize("filename", ["../../escape.txt", r"..\..\escape.txt"])
def test_sample_analysis_never_writes_outside_tenant_directory(
    client,
    admin_auth,
    monkeypatch,
    tmp_path,
    filename,
):
    from app.config import settings

    class FakeLlm:
        def chat_json(self, **kwargs):
            return {
                "detected_name": "测试类型",
                "description": "测试",
                "key_fields": [],
                "stamp_required": None,
                "business_meaning": "测试",
            }

    monkeypatch.setattr(settings, "upload_root", str(tmp_path))
    monkeypatch.setattr("app.llm_client.get_llm_client", lambda: FakeLlm())
    headers = authenticate(client, admin_auth)

    response = client.post(
        "/api/doc-types/analyze-sample",
        files={"file": (filename, b"safe sample content", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 200
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_setting_read_requires_tenant_identity_when_db_is_present(
    migrated_db,
):
    with pytest.raises(ValueError, match="tenant_id"):
        get_setting(
            migrated_db,
            None,
            "review.amount_tolerance_percent",
        )
