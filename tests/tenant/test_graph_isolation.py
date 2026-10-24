from __future__ import annotations

import uuid

from app.models import RuleSet, RuleSnapshot
from app.neo4j_client import get_neo4j_client
from app.services import graph_builder_service, graph_build_progress
from app.services.graph_builder_service import backfill_graph_tenant_ownership

from conftest import authenticate


def _create_snapshot(db, tenant_id, *, graph_id: str | None = None):
    rule_set = RuleSet(
        tenant_id=tenant_id,
        name=f"图谱隔离-{uuid.uuid4().hex[:8]}",
        description="tenant graph isolation",
        doc_types=[],
        check_categories=[],
        is_default=False,
    )
    db.add(rule_set)
    db.flush()
    snapshot = RuleSnapshot(
        tenant_id=tenant_id,
        rule_set_id=rule_set.id,
        rule_count=1,
        rules_json=[],
        graph_id=graph_id or f"graph_test_{uuid.uuid4().hex}",
        node_count=2,
        edge_count=1,
        operator="pytest",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return rule_set, snapshot


def _write_graph(db, snapshot, tenant_id):
    neo4j = get_neo4j_client()
    neo4j.write_rule_graph(
        snapshot.graph_id,
        tenant_id,
        [
            {"name": "测试合同号", "type": "Field", "attributes": {"layer": "ontology"}},
            {"name": "测试发票号", "type": "Field", "attributes": {"layer": "ontology"}},
        ],
        [
            {
                "source": "测试合同号",
                "target": "测试发票号",
                "type": "COMPARE_TO",
                "attributes": {
                    "rule_id": "R0001",
                    "rule_text": "测试字段一致",
                    "operator": "等于",
                    "layer": "execution",
                },
            }
        ],
    )
    return neo4j


def test_graph_access_resolves_tenant_from_snapshot(
    client,
    migrated_db,
    admin_auth,
    admin_b,
):
    _, snapshot = _create_snapshot(migrated_db, admin_auth.tenant_id)
    neo4j = _write_graph(migrated_db, snapshot, admin_auth.tenant_id)
    try:
        authenticate(client, admin_auth)
        own = client.get(f"/api/rules/graph/{snapshot.graph_id}")
        assert own.status_code == 200
        assert own.json()["node_count"] == 2

        authenticate(client, admin_b)
        assert client.get(f"/api/rules/graph/{snapshot.graph_id}").status_code == 404
        ontology = client.get(
            "/api/rules/graph/ontology",
            params={"graph_id": snapshot.graph_id},
        )
        assert ontology.status_code == 404

        headers = authenticate(client, admin_b)
        confirm = client.put(
            "/api/rules/graph/confirm",
            json={"graph_id": snapshot.graph_id, "edits": []},
            headers=headers,
        )
        assert confirm.status_code == 404
    finally:
        neo4j.clear_graph(snapshot.graph_id, admin_auth.tenant_id)


def test_build_task_list_and_status_are_tenant_scoped():
    graph_build_progress._build_tasks.clear()
    state_a = graph_build_progress.create_state_for_test(
        tenant_id="tenant-a",
        user_id="user-a",
    )
    graph_build_progress.create_state_for_test(
        tenant_id="tenant-b",
        user_id="user-b",
    )
    assert graph_build_progress.get_build_task(state_a.task_id, "tenant-b") is None
    assert [
        item["task_id"]
        for item in graph_build_progress.list_build_tasks("tenant-a", limit=20)
    ] == [state_a.task_id]


def test_foreign_ruleset_cannot_start_graph_build(
    client,
    migrated_db,
    admin_auth,
    admin_b,
):
    rule_set, _ = _create_snapshot(migrated_db, admin_auth.tenant_id)
    headers = authenticate(client, admin_b)
    response = client.post(
        "/api/rules/build-graph-async",
        params={"rule_set_id": str(rule_set.id), "auto_confirm_all": True},
        headers=headers,
    )
    assert response.status_code == 404


def test_neo4j_same_graph_id_is_isolated_and_legacy_node_can_be_claimed():
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    graph_id = f"graph_shared_{uuid.uuid4().hex}"
    neo4j = get_neo4j_client()
    try:
        neo4j.write_rule_graph(
            graph_id,
            tenant_a,
            [{"name": "A节点", "type": "Field", "attributes": {}}],
            [],
        )
        neo4j.write_rule_graph(
            graph_id,
            tenant_b,
            [{"name": "B节点", "type": "Field", "attributes": {}}],
            [],
        )
        assert [
            node["name"] for node in neo4j.get_graph_data(graph_id, tenant_a)["nodes"]
        ] == ["A节点"]
        assert [
            node["name"] for node in neo4j.get_graph_data(graph_id, tenant_b)["nodes"]
        ] == ["B节点"]

        legacy_graph_id = f"graph_legacy_{uuid.uuid4().hex}"
        neo4j.execute_write(
            "CREATE (:RuleEntity {name: '旧节点', graph_id: $graph_id, type: 'Field'})",
            {"graph_id": legacy_graph_id},
        )
        claimed = neo4j.backfill_graph_tenant(legacy_graph_id, tenant_a)
        assert claimed["nodes"] == 1
        assert [
            node["name"]
            for node in neo4j.get_graph_data(legacy_graph_id, tenant_a)["nodes"]
        ] == ["旧节点"]
    finally:
        neo4j.clear_graph(graph_id, tenant_a)
        neo4j.clear_graph(graph_id, tenant_b)
        neo4j.clear_graph(locals().get("legacy_graph_id", graph_id), tenant_a)


def test_startup_ownership_backfill_uses_snapshot_tenant(
    migrated_db,
    admin_auth,
):
    _, snapshot = _create_snapshot(migrated_db, admin_auth.tenant_id)
    neo4j = get_neo4j_client()
    try:
        neo4j.execute_write(
            "CREATE (:RuleEntity {name: '迁移节点', graph_id: $graph_id, type: 'Field'})",
            {"graph_id": snapshot.graph_id},
        )
        result = backfill_graph_tenant_ownership(migrated_db, neo4j)
        assert result["nodes"] >= 1
        assert [
            node["name"]
            for node in neo4j.get_graph_data(
                snapshot.graph_id,
                admin_auth.tenant_id,
            )["nodes"]
        ] == ["迁移节点"]
    finally:
        neo4j.clear_graph(snapshot.graph_id, admin_auth.tenant_id)


def test_graph_edit_rejects_identity_and_unknown_properties(
    client,
    migrated_db,
    admin_auth,
):
    _, snapshot = _create_snapshot(migrated_db, admin_auth.tenant_id)
    neo4j = _write_graph(migrated_db, snapshot, admin_auth.tenant_id)
    headers = authenticate(client, admin_auth)
    try:
        identity_edit = client.put(
            "/api/rules/graph/confirm",
            json={
                "graph_id": snapshot.graph_id,
                "edits": [
                    {
                        "op": "update_node",
                        "node_name": "测试合同号",
                        "properties": {"tenant_id": str(uuid.uuid4())},
                    }
                ],
            },
            headers=headers,
        )
        assert identity_edit.status_code == 400

        unknown_edit = client.put(
            "/api/rules/graph/confirm",
            json={
                "graph_id": snapshot.graph_id,
                "edits": [
                    {
                        "op": "update_edge",
                        "source": "测试合同号",
                        "target": "测试发票号",
                        "properties": {"arbitrary_identity": "other"},
                    }
                ],
            },
            headers=headers,
        )
        assert unknown_edit.status_code == 400

        allowed_edit = client.put(
            "/api/rules/graph/confirm",
            json={
                "graph_id": snapshot.graph_id,
                "edits": [
                    {
                        "op": "update_node",
                        "node_name": "测试合同号",
                        "properties": {"description": "人工确认说明"},
                    }
                ],
            },
            headers=headers,
        )
        assert allowed_edit.status_code == 200
        node = next(
            item
            for item in allowed_edit.json()["nodes"]
            if item["name"] == "测试合同号"
        )
        assert node["properties"]["description"] == "人工确认说明"
        assert node["properties"]["tenant_id"] == str(admin_auth.tenant_id)
        assert node["properties"]["graph_id"] == snapshot.graph_id
    finally:
        neo4j.clear_graph(snapshot.graph_id, admin_auth.tenant_id)


def test_neo4j_property_update_strips_identity_fields():
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    graph_id = f"graph_identity_{uuid.uuid4().hex}"
    neo4j = get_neo4j_client()
    try:
        neo4j.write_rule_graph(
            graph_id,
            tenant_a,
            [
                {"name": "保护节点", "type": "Field", "attributes": {}},
                {"name": "目标节点", "type": "Field", "attributes": {}},
            ],
            [
                {
                    "source": "保护节点",
                    "target": "目标节点",
                    "type": "COMPARE_TO",
                    "attributes": {"rule_id": "R0001", "operator": "等于"},
                }
            ],
        )
        neo4j.update_node_properties(
            graph_id,
            tenant_a,
            "保护节点",
            {
                "tenant_id": str(tenant_b),
                "graph_id": "other_graph",
                "name": "renamed",
                "description": "允许说明",
            },
        )
        neo4j.update_edge_properties(
            graph_id,
            tenant_a,
            "保护节点",
            "目标节点",
            {
                "tenant_id": str(tenant_b),
                "graph_id": "other_graph",
                "rule_text": "允许更新说明",
            },
        )

        tenant_a_graph = neo4j.get_graph_data(graph_id, tenant_a)
        tenant_b_graph = neo4j.get_graph_data(graph_id, tenant_b)
        node = next(
            item for item in tenant_a_graph["nodes"] if item["name"] == "保护节点"
        )
        assert node["properties"]["tenant_id"] == str(tenant_a)
        assert node["properties"]["graph_id"] == graph_id
        assert node["properties"]["description"] == "允许说明"
        assert tenant_b_graph["nodes"] == []
        assert tenant_a_graph["edges"][0]["properties"]["tenant_id"] == str(tenant_a)
        assert tenant_a_graph["edges"][0]["properties"]["graph_id"] == graph_id
        assert (
            tenant_a_graph["edges"][0]["properties"]["rule_text"]
            == "允许更新说明"
        )
    finally:
        neo4j.clear_graph(graph_id, tenant_a)
        neo4j.clear_graph(graph_id, tenant_b)


def test_apply_graph_edits_rejects_reserved_properties_directly():
    class StubNeo4j:
        def __init__(self):
            self.updated = False

        def update_node_properties(self, *_args, **_kwargs):
            self.updated = True

        def get_graph_data(self, *_args, **_kwargs):
            return {
                "graph_id": "g1",
                "nodes": [],
                "edges": [],
                "node_count": 0,
                "edge_count": 0,
            }

    stub = StubNeo4j()
    edit = graph_builder_service.GraphEditOp(
        op="update_node",
        node_name="节点",
        properties={"graph_id": "attacker"},
    )
    try:
        graph_builder_service.apply_graph_edits(
            stub,
            uuid.uuid4(),
            "g1",
            [edit],
        )
    except ValueError as exc:
        assert "graph_id" in str(exc)
    else:
        raise AssertionError("reserved graph identity must be rejected")
    assert stub.updated is False
