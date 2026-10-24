"""Neo4j 客户端封装。

单例驱动、参数化查询；graph_id 隔离不同规则集版本，写操作走事务，
只读查询先过 cypher_guard 校验。
"""

from __future__ import annotations

import logging
import threading
import json
import uuid
from typing import Optional

from neo4j import Driver, GraphDatabase, ManagedTransaction

from .config import settings
from .utils.cypher_guard import is_safe_query, validate_cypher_params

logger = logging.getLogger(__name__)

_global_driver: Optional["Neo4jClient"] = None
_global_lock = threading.Lock()

_RESERVED_GRAPH_IDENTITY_PROPERTIES = frozenset(
    {"name", "graph_id", "tenant_id", "source", "target", "type", "id"}
)


class Neo4jClient:
    """Neo4j 连接池管理与查询执行。"""

    def __init__(
        self,
        uri: Optional[str] = None,
        auth: Optional[tuple[str, str]] = None,
    ) -> None:
        self.uri = uri or settings.neo4j_uri
        self.auth = auth or settings.neo4j_auth
        self._driver: Driver = GraphDatabase.driver(self.uri, auth=self.auth)
        # 启动时验证连接
        try:
            self._driver.verify_connectivity()
            logger.info("Neo4j 连接成功: %s", self.uri)
        except Exception as e:  # pragma: no cover
            logger.error("Neo4j 连接失败: %s", e)
            raise

    @property
    def driver(self) -> Driver:
        return self._driver

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:  # pragma: no cover
            logger.debug("关闭 Neo4j 驱动时出错", exc_info=True)

    # ---------- 通用查询 ----------
    def execute_read(self, query: str, params: Optional[dict] = None) -> list[dict]:
        """执行只读查询。对写操作关键字做防护。"""
        safe, reason = is_safe_query(query)
        if not safe:
            raise ValueError(f"拒绝执行非只读查询: {reason}")
        params = params or {}
        valid, err = validate_cypher_params(params)
        if not valid:
            raise ValueError(f"Cypher 参数校验失败: {err}")
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [r.data() for r in result]

    def execute_write(
        self,
        query: str,
        params: Optional[dict] = None,
        check_string_values: bool = True,
    ) -> list[dict]:
        """执行写查询（CREATE/MERGE/DELETE/SET）。

        check_string_values=False 用于业务内容（规则文本/描述）写入图谱属性：
        查询全部参数化，值内容不构成注入，宽松校验避免误伤合法业务数据。
        """
        params = params or {}
        valid, err = validate_cypher_params(params, check_string_values=check_string_values)
        if not valid:
            raise ValueError(f"Cypher 参数校验失败: {err}")

        def _tx_fn(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(query, **params)
            return [r.data() for r in result]

        with self._driver.session() as session:
            return session.execute_write(_tx_fn)

    # ---------- 规则图谱专用 ----------
    @staticmethod
    def _sanitize_props(props: dict) -> dict:
        """Neo4j 属性仅支持标量/数组：嵌套 dict/list 序列化为 JSON 字符串。
        （批次 8 验收暴露：tolerance_params/condition/exceptions 等嵌套结构直接写入会报
        'Property values can only be of primitive types'，读取侧在 graph_review_service 反序列化。）
        """
        out: dict = {}
        for k, v in props.items():
            if isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    def clear_graph(self, graph_id: str, tenant_id: uuid.UUID | str) -> int:
        """清除指定租户和 graph_id 的所有节点与关系。返回剩余节点数。"""
        tenant = str(tenant_id)
        # 先删关系再删节点
        self.execute_write(
            "MATCH (n) WHERE n.graph_id = $graph_id AND n.tenant_id = $tenant_id "
            "DETACH DELETE n",
            {"graph_id": graph_id, "tenant_id": tenant},
        )
        # 统计当前剩余（应为 0）
        records = self.execute_read(
            "MATCH (n) WHERE n.graph_id = $graph_id "
            "AND n.tenant_id = $tenant_id RETURN count(n) AS cnt",
            {"graph_id": graph_id, "tenant_id": tenant},
        )
        return records[0]["cnt"] if records else 0

    def clear_all_rule_graphs(self) -> int:
        """清除所有规则图谱节点（跨 graph_id）。用于数据库重置时与 Postgres 同步。返回剩余节点数（应为 0）。"""
        self.execute_write("MATCH (n:RuleEntity) DETACH DELETE n")
        records = self.execute_read("MATCH (n:RuleEntity) RETURN count(n) AS cnt")
        return records[0]["cnt"] if records else 0

    def write_rule_graph(
        self,
        graph_id: str,
        tenant_id: uuid.UUID | str,
        entities: list[dict],
        relationships: list[dict],
    ) -> dict:
        """幂等写入规则图谱（MERGE）。

        entities: [{"name": "代理协议.协议方", "type": "Field", "attributes": {...}}]
        relationships: [{"source": "...", "target": "...", "type": "COMPARE_TO|REQUIRED|MUST_STAMP",
                         "attributes": {"operator": "等于", "tolerance": 0, "rule_id": "R001"}}]
        """
        tenant = str(tenant_id)
        # 写节点
        for ent in entities:
            attrs = ent.get("attributes", {}) or {}
            attrs = {
                **attrs,
                "graph_id": graph_id,
                "tenant_id": tenant,
                "name": ent["name"],
            }
            self.execute_write(
                "MERGE (n:RuleEntity {name: $name, graph_id: $graph_id, tenant_id: $tenant_id}) "
                "SET n.type = $type, n += $attrs",
                {
                    "name": ent["name"],
                    "graph_id": graph_id,
                    "tenant_id": tenant,
                    "type": ent.get("type", "Field"),
                    "attrs": self._sanitize_props(
                        {
                            k: v
                            for k, v in attrs.items()
                            if k not in ("name", "graph_id", "tenant_id")
                        }
                    ),
                },
                check_string_values=False,
            )
        # 写关系（尊重 rel.type，不再硬编码 COMPARE_TO）
        # 批次 10 Phase D：本体层边（APPLIES_TO/CHECKS/INVOLVES/HAS_FIELD）加入白名单
        _ALLOWED_REL_TYPES = (
            "COMPARE_TO",
            "REQUIRED",
            "MUST_STAMP",
            "APPLIES_TO",
            "CHECKS",
            "INVOLVES",
            "HAS_FIELD",
        )
        for rel in relationships:
            attrs = rel.get("attributes", {}) or {}
            attrs = {**attrs, "graph_id": graph_id, "tenant_id": tenant}
            rel_type = rel.get("type", "COMPARE_TO")
            # 白名单校验，防止注入
            if rel_type not in _ALLOWED_REL_TYPES:
                rel_type = "COMPARE_TO"
            # 带 rule_id 的关系按 (起点, 类型, 终点, rule_id) 幂等：不同规则即使
            # 引用同一字段对（如 物流/承运人 与 代收方（C/O）都归一为 代收方），
            # 也各自保留一条边，避免 MERGE 把多条规则折叠成一条。
            if attrs.get("rule_id"):
                cypher = (
                    "MATCH (a:RuleEntity {name: $src, graph_id: $graph_id, tenant_id: $tenant_id}), "
                    "(b:RuleEntity {name: $tgt, graph_id: $graph_id, tenant_id: $tenant_id}) "
                    f"MERGE (a)-[r:{rel_type} {{rule_id: $rule_id}}]->(b) "
                    "SET r += $attrs"
                )
            else:
                cypher = (
                    "MATCH (a:RuleEntity {name: $src, graph_id: $graph_id, tenant_id: $tenant_id}), "
                    "(b:RuleEntity {name: $tgt, graph_id: $graph_id, tenant_id: $tenant_id}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) "
                    "SET r += $attrs"
                )
            self.execute_write(
                cypher,
                {
                    "src": rel["source"],
                    "tgt": rel["target"],
                    "graph_id": graph_id,
                    "tenant_id": tenant,
                    "rule_id": attrs.get("rule_id"),
                    "attrs": self._sanitize_props(attrs),
                },
                check_string_values=False,
            )
        return {"nodes": len(entities), "relationships": len(relationships)}

    def get_ontology(self, graph_id: str, tenant_id: uuid.UUID | str) -> dict:
        """查询图谱本体层（批次 10 Phase D）：
        文档类型（含 HAS_FIELD 字段清单）、检查意图（含关联规则数）、规则清单。
        """
        params = {"graph_id": graph_id, "tenant_id": str(tenant_id)}
        doc_types = self.execute_read(
            "MATCH (n:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id, type: 'DocumentType'}) "
            "OPTIONAL MATCH (n)-[h:HAS_FIELD]->"
            "(f:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id, type: 'Field'}) "
            "WHERE h.tenant_id = $tenant_id "
            "RETURN n.name AS name, properties(n) AS props, collect(DISTINCT f.name) AS fields",
            params,
        )
        check_intents = self.execute_read(
            "MATCH (n:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id, type: 'CheckIntent'}) "
            "OPTIONAL MATCH (n)<-[c:CHECKS]-"
            "(r:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id, type: 'Rule'}) "
            "WHERE c.tenant_id = $tenant_id "
            "RETURN n.name AS name, properties(n) AS props, count(DISTINCT r.name) AS rule_count",
            params,
        )
        rules = self.execute_read(
            "MATCH (n:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id, type: 'Rule'}) "
            "RETURN n.name AS name, properties(n) AS props",
            params,
        )
        return {
            "graph_id": graph_id,
            "doc_types": doc_types,
            "check_intents": check_intents,
            "rules": rules,
        }

    # ---------- 图谱驱动审查查询 ----------

    def get_required_docs(
        self, graph_id: str, tenant_id: uuid.UUID | str
    ) -> list[dict]:
        """查所有齐套性要求（REQUIRED 关系）。

        返回: [{"doc_type": "委托出口确认单", "rule_id": "R001", "rule_text": "...", ...}]
        """
        records = self.execute_read(
            "MATCH (a:RuleEntity)-[r:REQUIRED]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id AND b.graph_id = $graph_id "
            "AND a.tenant_id = $tenant_id AND b.tenant_id = $tenant_id "
            "AND r.tenant_id = $tenant_id "
            "RETURN a.name AS source, b.name AS target, "
            "a.type AS source_type, b.type AS target_type, "
            "properties(r) AS rel_props, properties(a) AS source_props, "
            "properties(b) AS target_props",
            {"graph_id": graph_id, "tenant_id": str(tenant_id)},
        )
        return records

    def get_stamp_requirements(
        self, graph_id: str, tenant_id: uuid.UUID | str
    ) -> list[dict]:
        """查所有印章要求（MUST_STAMP 关系）。

        返回: [{"source": "代理协议", "rule_id": "R001", ...}]
        """
        records = self.execute_read(
            "MATCH (a:RuleEntity)-[r:MUST_STAMP]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id AND b.graph_id = $graph_id "
            "AND a.tenant_id = $tenant_id AND b.tenant_id = $tenant_id "
            "AND r.tenant_id = $tenant_id "
            "RETURN a.name AS source, b.name AS target, "
            "a.type AS source_type, b.type AS target_type, "
            "properties(r) AS rel_props, properties(a) AS source_props, "
            "properties(b) AS target_props",
            {"graph_id": graph_id, "tenant_id": str(tenant_id)},
        )
        return records

    def get_compare_relationships(
        self, graph_id: str, tenant_id: uuid.UUID | str
    ) -> list[dict]:
        """查所有字段比对要求（COMPARE_TO 关系）。

        返回: [{"source": "代理协议.协议方", "target": "委托单.委托方",
               "operator": "等于", "tolerance": 0, "rule_id": "R001", ...}]
        """
        records = self.execute_read(
            "MATCH (a:RuleEntity)-[r:COMPARE_TO]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id AND b.graph_id = $graph_id "
            "AND a.tenant_id = $tenant_id AND b.tenant_id = $tenant_id "
            "AND r.tenant_id = $tenant_id "
            "RETURN a.name AS source, b.name AS target, "
            "a.type AS source_type, b.type AS target_type, "
            "properties(r) AS rel_props, properties(a) AS source_props, "
            "properties(b) AS target_props",
            {"graph_id": graph_id, "tenant_id": str(tenant_id)},
        )
        return records

    def get_graph_data(
        self, graph_id: str, tenant_id: uuid.UUID | str
    ) -> dict:
        """获取指定图谱的节点和边，供前端力导向图渲染。"""
        params = {"graph_id": graph_id, "tenant_id": str(tenant_id)}
        nodes = self.execute_read(
            "MATCH (n:RuleEntity) WHERE n.graph_id = $graph_id "
            "AND n.tenant_id = $tenant_id "
            "RETURN elementId(n) AS id, n.name AS name, n.type AS type, "
            "properties(n) AS properties",
            params,
        )
        edges = self.execute_read(
            "MATCH (a:RuleEntity)-[r]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id AND b.graph_id = $graph_id "
            "AND a.tenant_id = $tenant_id AND b.tenant_id = $tenant_id "
            "AND r.tenant_id = $tenant_id "
            "RETURN elementId(r) AS id, a.name AS source, b.name AS target, "
            "type(r) AS type, properties(r) AS properties",
            params,
        )
        return {"graph_id": graph_id, "nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}

    def update_node_properties(
        self,
        graph_id: str,
        tenant_id: uuid.UUID | str,
        node_name: str,
        properties: dict,
    ) -> None:
        safe_properties = {
            key: value
            for key, value in properties.items()
            if key not in _RESERVED_GRAPH_IDENTITY_PROPERTIES
        }
        if not safe_properties:
            return
        self.execute_write(
            "MATCH (n:RuleEntity {name: $name, graph_id: $graph_id, tenant_id: $tenant_id}) "
            "SET n += $props",
            {
                "name": node_name,
                "graph_id": graph_id,
                "tenant_id": str(tenant_id),
                "props": self._sanitize_props(safe_properties),
            },
        )

    def update_edge_properties(
        self,
        graph_id: str,
        tenant_id: uuid.UUID | str,
        source: str,
        target: str,
        properties: dict,
    ) -> None:
        safe_properties = {
            key: value
            for key, value in properties.items()
            if key not in _RESERVED_GRAPH_IDENTITY_PROPERTIES
        }
        if not safe_properties:
            return
        self.execute_write(
            "MATCH (a:RuleEntity {name: $src, graph_id: $graph_id, tenant_id: $tenant_id})"
            "-[r]->(b:RuleEntity {name: $tgt, graph_id: $graph_id, tenant_id: $tenant_id}) "
            "WHERE r.tenant_id = $tenant_id "
            "SET r += $props",
            {
                "src": source,
                "tgt": target,
                "graph_id": graph_id,
                "tenant_id": str(tenant_id),
                "props": self._sanitize_props(safe_properties),
            },
        )

    def delete_node(
        self, graph_id: str, tenant_id: uuid.UUID | str, node_name: str
    ) -> None:
        self.execute_write(
            "MATCH (n:RuleEntity {name: $name, graph_id: $graph_id, tenant_id: $tenant_id}) "
            "DETACH DELETE n",
            {"name": node_name, "graph_id": graph_id, "tenant_id": str(tenant_id)},
        )

    def delete_edge(
        self,
        graph_id: str,
        tenant_id: uuid.UUID | str,
        source: str,
        target: str,
    ) -> None:
        self.execute_write(
            "MATCH (a:RuleEntity {name: $src, graph_id: $graph_id, tenant_id: $tenant_id})"
            "-[r]->(b:RuleEntity {name: $tgt, graph_id: $graph_id, tenant_id: $tenant_id}) "
            "WHERE r.tenant_id = $tenant_id "
            "DELETE r",
            {
                "src": source,
                "tgt": target,
                "graph_id": graph_id,
                "tenant_id": str(tenant_id),
            },
        )

    def remove_nodes_by_rule_id(
        self,
        graph_id: str,
        tenant_id: uuid.UUID | str,
        rule_id: str,
    ) -> int:
        """从图谱中移除指定 rule_id 对应的所有节点和关系。

        通过节点属性 rule_id 匹配（图谱构建时为每个节点/关系注入 rule_id）。
        返回删除的节点数。
        """
        # 先删与该 rule_id 相关的关系
        self.execute_write(
            "MATCH (a:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id})"
            "-[r]->(b:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id}) "
            "WHERE r.rule_id = $rule_id AND r.tenant_id = $tenant_id "
            "DELETE r",
            {
                "graph_id": graph_id,
                "tenant_id": str(tenant_id),
                "rule_id": rule_id,
            },
        )
        # 再删节点
        result = self.execute_write(
            "MATCH (n:RuleEntity {graph_id: $graph_id, tenant_id: $tenant_id}) "
            "WHERE n.rule_id = $rule_id "
            "DETACH DELETE n "
            "RETURN count(n) AS cnt",
            {
                "graph_id": graph_id,
                "tenant_id": str(tenant_id),
                "rule_id": rule_id,
            },
        )
        count = result[0]["cnt"] if result else 0
        logger.info("从图谱 %s 中移除 rule_id=%s 的 %d 个节点", graph_id, rule_id, count)
        return count

    def backfill_graph_tenant(
        self,
        graph_id: str,
        tenant_id: uuid.UUID | str,
    ) -> dict[str, int]:
        """只给旧图中尚无租户属性的节点/关系补租户归属。"""
        tenant = str(tenant_id)
        conflicts = self.execute_read(
            "MATCH (n:RuleEntity {graph_id: $graph_id}) "
            "WHERE n.tenant_id IS NOT NULL AND n.tenant_id <> $tenant_id "
            "RETURN count(DISTINCT n.tenant_id) AS cnt",
            {"graph_id": graph_id, "tenant_id": tenant},
        )
        if conflicts and int(conflicts[0]["cnt"]) > 0:
            logger.warning(
                "图谱 %s 存在与快照租户冲突的节点归属，冲突租户数=%s",
                graph_id,
                conflicts[0]["cnt"],
            )
        node_rows = self.execute_write(
            "MATCH (n:RuleEntity {graph_id: $graph_id}) "
            "WHERE n.tenant_id IS NULL "
            "SET n.tenant_id = $tenant_id "
            "RETURN count(n) AS cnt",
            {"graph_id": graph_id, "tenant_id": tenant},
        )
        rel_rows = self.execute_write(
            "MATCH (a:RuleEntity)-[r]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id AND b.graph_id = $graph_id "
            "AND r.tenant_id IS NULL "
            "SET r.tenant_id = $tenant_id "
            "RETURN count(r) AS cnt",
            {"graph_id": graph_id, "tenant_id": tenant},
        )
        return {
            "nodes": int(node_rows[0]["cnt"]) if node_rows else 0,
            "relationships": int(rel_rows[0]["cnt"]) if rel_rows else 0,
        }

    def list_unowned_graph_ids(self) -> list[str]:
        """列出仍无法映射到租户的旧图谱，仅用于启动时告警。"""
        rows = self.execute_read(
            "MATCH (n:RuleEntity) WHERE n.tenant_id IS NULL "
            "RETURN DISTINCT n.graph_id AS graph_id "
            "ORDER BY graph_id",
        )
        return [str(row["graph_id"]) for row in rows if row.get("graph_id")]


def get_neo4j_client() -> Neo4jClient:
    """全局单例 Neo4j 客户端。"""
    global _global_driver
    if _global_driver is not None:
        return _global_driver
    with _global_lock:
        if _global_driver is None:
            _global_driver = Neo4jClient()
        return _global_driver
