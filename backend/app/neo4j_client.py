"""Neo4j 客户端封装。

参考 MiroFish-Explorer 的设计：单例驱动、参数化查询、graph_id 隔离不同规则集版本。
本模块重新实现，去除对原项目 services 包的耦合。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from neo4j import Driver, GraphDatabase, ManagedTransaction

from .config import settings
from .utils.cypher_guard import is_safe_query, validate_cypher_params

logger = logging.getLogger(__name__)

_global_driver: Optional["Neo4jClient"] = None
_global_lock = threading.Lock()


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

    def execute_write(self, query: str, params: Optional[dict] = None) -> list[dict]:
        """执行写查询（CREATE/MERGE/DELETE/SET）。"""
        params = params or {}
        valid, err = validate_cypher_params(params)
        if not valid:
            raise ValueError(f"Cypher 参数校验失败: {err}")

        def _tx_fn(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(query, **params)
            return [r.data() for r in result]

        with self._driver.session() as session:
            return session.execute_write(_tx_fn)

    # ---------- 规则图谱专用 ----------
    def clear_graph(self, graph_id: str) -> int:
        """全量清除指定 graph_id 的所有节点和关系。返回删除节点数。"""
        # 先删关系再删节点
        self.execute_write(
            "MATCH (n) WHERE n.graph_id = $graph_id "
            "DETACH DELETE n",
            {"graph_id": graph_id},
        )
        # 统计当前剩余（应为 0）
        records = self.execute_read(
            "MATCH (n) WHERE n.graph_id = $graph_id RETURN count(n) AS cnt",
            {"graph_id": graph_id},
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
        entities: list[dict],
        relationships: list[dict],
    ) -> dict:
        """幂等写入规则图谱（MERGE）。

        entities: [{"name": "代理协议.协议方", "type": "Field", "attributes": {...}}]
        relationships: [{"source": "...", "target": "...", "type": "COMPARE_TO|REQUIRED|MUST_STAMP",
                         "attributes": {"operator": "等于", "tolerance": 0, "rule_id": "R001"}}]
        """
        # 写节点
        for ent in entities:
            attrs = ent.get("attributes", {}) or {}
            attrs = {**attrs, "graph_id": graph_id, "name": ent["name"]}
            self.execute_write(
                "MERGE (n:RuleEntity {name: $name, graph_id: $graph_id}) "
                "SET n.type = $type, n += $attrs",
                {
                    "name": ent["name"],
                    "graph_id": graph_id,
                    "type": ent.get("type", "Field"),
                    "attrs": {k: v for k, v in attrs.items() if k not in ("name", "graph_id")},
                },
            )
        # 写关系（尊重 rel.type，不再硬编码 COMPARE_TO）
        for rel in relationships:
            attrs = rel.get("attributes", {}) or {}
            attrs = {**attrs, "graph_id": graph_id}
            rel_type = rel.get("type", "COMPARE_TO")
            # 白名单校验，防止注入
            if rel_type not in ("COMPARE_TO", "REQUIRED", "MUST_STAMP"):
                rel_type = "COMPARE_TO"
            cypher = (
                "MATCH (a:RuleEntity {name: $src, graph_id: $graph_id}), "
                "(b:RuleEntity {name: $tgt, graph_id: $graph_id}) "
                f"MERGE (a)-[r:{rel_type}]->(b) "
                "SET r += $attrs"
            )
            self.execute_write(
                cypher,
                {
                    "src": rel["source"],
                    "tgt": rel["target"],
                    "graph_id": graph_id,
                    "attrs": attrs,
                },
            )
        return {"nodes": len(entities), "relationships": len(relationships)}

    # ---------- 图谱驱动审查查询 ----------

    def get_required_docs(self, graph_id: str) -> list[dict]:
        """查所有齐套性要求（REQUIRED 关系）。

        返回: [{"doc_type": "委托出口确认单", "rule_id": "R001", "rule_text": "...", ...}]
        """
        records = self.execute_read(
            "MATCH (a:RuleEntity)-[r:REQUIRED]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id "
            "RETURN a.name AS source, b.name AS target, "
            "a.type AS source_type, b.type AS target_type, "
            "properties(r) AS rel_props, properties(a) AS source_props, "
            "properties(b) AS target_props",
            {"graph_id": graph_id},
        )
        return records

    def get_stamp_requirements(self, graph_id: str) -> list[dict]:
        """查所有印章要求（MUST_STAMP 关系）。

        返回: [{"source": "代理协议", "rule_id": "R001", ...}]
        """
        records = self.execute_read(
            "MATCH (a:RuleEntity)-[r:MUST_STAMP]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id "
            "RETURN a.name AS source, b.name AS target, "
            "a.type AS source_type, b.type AS target_type, "
            "properties(r) AS rel_props, properties(a) AS source_props, "
            "properties(b) AS target_props",
            {"graph_id": graph_id},
        )
        return records

    def get_compare_relationships(self, graph_id: str) -> list[dict]:
        """查所有字段比对要求（COMPARE_TO 关系）。

        返回: [{"source": "代理协议.协议方", "target": "委托单.委托方",
               "operator": "等于", "tolerance": 0, "rule_id": "R001", ...}]
        """
        records = self.execute_read(
            "MATCH (a:RuleEntity)-[r:COMPARE_TO]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id "
            "RETURN a.name AS source, b.name AS target, "
            "a.type AS source_type, b.type AS target_type, "
            "properties(r) AS rel_props, properties(a) AS source_props, "
            "properties(b) AS target_props",
            {"graph_id": graph_id},
        )
        return records

    def get_graph_data(self, graph_id: str) -> dict:
        """获取指定图谱的节点和边，供前端力导向图渲染。"""
        nodes = self.execute_read(
            "MATCH (n:RuleEntity) WHERE n.graph_id = $graph_id "
            "RETURN id(n) AS id, n.name AS name, n.type AS type, "
            "properties(n) AS properties",
            {"graph_id": graph_id},
        )
        edges = self.execute_read(
            "MATCH (a:RuleEntity)-[r]->(b:RuleEntity) "
            "WHERE a.graph_id = $graph_id "
            "RETURN id(r) AS id, a.name AS source, b.name AS target, "
            "type(r) AS type, properties(r) AS properties",
            {"graph_id": graph_id},
        )
        return {"graph_id": graph_id, "nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}

    def update_node_properties(self, graph_id: str, node_name: str, properties: dict) -> None:
        self.execute_write(
            "MATCH (n:RuleEntity {name: $name, graph_id: $graph_id}) "
            "SET n += $props",
            {"name": node_name, "graph_id": graph_id, "props": properties},
        )

    def update_edge_properties(
        self, graph_id: str, source: str, target: str, properties: dict
    ) -> None:
        self.execute_write(
            "MATCH (a:RuleEntity {name: $src, graph_id: $graph_id})"
            "-[r]->(b:RuleEntity {name: $tgt, graph_id: $graph_id}) "
            "SET r += $props",
            {"src": source, "tgt": target, "graph_id": graph_id, "props": properties},
        )

    def delete_node(self, graph_id: str, node_name: str) -> None:
        self.execute_write(
            "MATCH (n:RuleEntity {name: $name, graph_id: $graph_id}) "
            "DETACH DELETE n",
            {"name": node_name, "graph_id": graph_id},
        )

    def delete_edge(self, graph_id: str, source: str, target: str) -> None:
        self.execute_write(
            "MATCH (a:RuleEntity {name: $src, graph_id: $graph_id})"
            "-[r]->(b:RuleEntity {name: $tgt, graph_id: $graph_id}) "
            "DELETE r",
            {"src": source, "tgt": target, "graph_id": graph_id},
        )

    def remove_nodes_by_rule_id(self, graph_id: str, rule_id: str) -> int:
        """从图谱中移除指定 rule_id 对应的所有节点和关系。

        通过节点属性 rule_id 匹配（图谱构建时为每个节点/关系注入 rule_id）。
        返回删除的节点数。
        """
        # 先删与该 rule_id 相关的关系
        self.execute_write(
            "MATCH (a:RuleEntity {graph_id: $graph_id})-[r]->(b:RuleEntity {graph_id: $graph_id}) "
            "WHERE r.rule_id = $rule_id "
            "DELETE r",
            {"graph_id": graph_id, "rule_id": rule_id},
        )
        # 再删节点
        result = self.execute_write(
            "MATCH (n:RuleEntity {graph_id: $graph_id}) "
            "WHERE n.rule_id = $rule_id "
            "DETACH DELETE n "
            "RETURN count(n) AS cnt",
            {"graph_id": graph_id, "rule_id": rule_id},
        )
        count = result[0]["cnt"] if result else 0
        logger.info("从图谱 %s 中移除 rule_id=%s 的 %d 个节点", graph_id, rule_id, count)
        return count


def get_neo4j_client() -> Neo4jClient:
    """全局单例 Neo4j 客户端。"""
    global _global_driver
    if _global_driver is not None:
        return _global_driver
    with _global_lock:
        if _global_driver is None:
            _global_driver = Neo4jClient()
        return _global_driver
