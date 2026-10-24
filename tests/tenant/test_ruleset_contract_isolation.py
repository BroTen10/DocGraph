from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.models import Contract, Document, RuleSet


def authenticate(client, auth):
    client.cookies.clear()
    client.cookies.set("docgraph_session", auth.session_token)
    return {"X-CSRF-Token": auth.csrf_token}


@pytest.fixture
def contract_a(migrated_db, admin_auth):
    tenant_id = admin_auth.tenant_id
    assert tenant_id is not None
    rule_set = RuleSet(
        tenant_id=tenant_id,
        name=f"合同隔离规则集-{uuid.uuid4().hex[:8]}",
        is_default=True,
    )
    migrated_db.add(rule_set)
    migrated_db.flush()
    contract = Contract(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        contract_no=f"TENANT-A-{uuid.uuid4().hex[:8]}",
    )
    migrated_db.add(contract)
    migrated_db.flush()
    directory = (
        settings.ensure_upload_root()
        / str(tenant_id)
        / str(rule_set.id)
        / str(contract.id)
    )
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.4 tenant-a")
    document = Document(
        tenant_id=tenant_id,
        contract_id=contract.id,
        file_name="sample.pdf",
        file_path=str(file_path),
        file_type="pdf",
        doc_type="其他",
        ocr_status="done",
    )
    migrated_db.add(document)
    migrated_db.commit()
    return contract, document


def test_rulesets_and_files_are_isolated(
    client,
    admin_auth,
    admin_b,
    contract_a,
):
    contract, document = contract_a
    authenticate(client, admin_auth)
    own = client.get("/api/rule-sets")
    assert own.status_code == 200
    assert {item["id"] for item in own.json()} == {str(contract.rule_set_id)}
    assert client.get(f"/api/contracts/{contract.id}").status_code == 200
    own_file = client.get(f"/api/contracts/documents/{document.id}/file")
    assert own_file.status_code == 200
    assert own_file.content == b"%PDF-1.4 tenant-a"

    headers_b = authenticate(client, admin_b)
    assert client.get("/api/contracts").json() == []
    assert client.get(f"/api/contracts/{contract.id}").status_code == 404
    assert (
        client.get(f"/api/contracts/documents/{document.id}/file").status_code
        == 404
    )
    assert client.get(f"/api/contracts/documents/{document.id}/ocr").status_code == 404
    assert client.get(f"/api/rule-sets/{contract.rule_set_id}").status_code == 404
    assert (
        client.delete(
            f"/api/contracts/{contract.id}",
            headers=headers_b,
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/rule-sets/{contract.rule_set_id}",
            json={"name": "不应成功"},
            headers=headers_b,
        ).status_code
        == 404
    )


def test_same_ruleset_name_is_allowed_across_tenants(
    client,
    admin_auth,
    admin_b,
):
    name = "跨租户同名规则集"
    headers_a = authenticate(client, admin_auth)
    created_a = client.post("/api/rule-sets", json={"name": name}, headers=headers_a)
    assert created_a.status_code == 201

    headers_b = authenticate(client, admin_b)
    created_b = client.post("/api/rule-sets", json={"name": name}, headers=headers_b)
    assert created_b.status_code == 201
    assert created_a.json()["id"] != created_b.json()["id"]
