"""Security acceptance tests for all externally reachable API surfaces."""

from __future__ import annotations

from uuid import uuid4

from conftest import authenticate


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
