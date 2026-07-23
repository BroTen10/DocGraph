"""规则转图谱服务。

链路：
1. 读取所有启用规则
2. LLM 解析每条规则为 {entities, relationships}（带 confidence）
3. 三档确认：
   - confidence >= 阈值 → 自动确认
   - confidence < 阈值 → 进入人工确认队列
   - 一键自动 → 全部确认
4. 写入 Neo4j（graph_id 隔离，MERGE 幂等）
5. 保存规则快照到 Postgres

输出契约（参考 MiroFish-Explorer）：
{
  "entities": [{"name": "代理协议.协议方", "type": "Field", "attributes": {...}}],
  "relationships": [{"source": "...", "target": "...", "type": "COMPARE_TO",
                     "attributes": {"operator": "等于", "tolerance": 0}}]
}
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..llm_client import LLMError, get_llm_client
from ..models import Rule, RuleSnapshot
from ..neo4j_client import Neo4jClient, get_neo4j_client
from ..schemas.graph import (
    EdgeData,
    EntityData,
    GraphBuildResponse,
    GraphData,
    GraphEditOp,
    RuleGraphConvertResult,
)
from .rule_service import get_enabled_rules_for_snapshot

logger = logging.getLogger(__name__)


# ============ LLM 提示词 ============
_SYSTEM_PROMPT = """你是规则图谱构建助手。任务：把自然语言审查规则转换为知识图谱的结构化表示。

输出契约（严格 JSON）：
{
  "entities": [
    {"name": "文件类型.字段名", "type": "Field", "attributes": {"description": "字段说明"}}
  ],
  "relationships": [
    {"source": "文件类型.字段名", "target": "文件类型.字段名", "type": "COMPARE_TO",
     "attributes": {"operator": "等于|不大于|不小于|时间早于|时间不晚于|总额等于|包含于", "tolerance": 0, "rule_id": "R001"}}
  ],
  "confidence": 0.0-1.0
}

规则：
1. 实体名使用"文件类型.字段名"格式，如"代理协议.协议方"、"委托单.委托方"
2. 关系类型固定为 COMPARE_TO（比对关系）
3. operator 必须是上述枚举之一
4. tolerance 为数值容差（百分比、千克、天数等，0 表示严格相等）
5. rule_id 用规则在规则集中的序号（如 R001、R002）
6. 一条规则可拆出多个实体和关系
7. confidence 反映你对规则理解的确信度（0-1）"""

_USER_PROMPT_TEMPLATE = """请将以下规则转换为图谱结构：

规则编号: {rule_id}
文件类型: {doc_type}
检查项: {check_category}
规则文本: {rule_text}
容差参数: {tolerance_json}

请输出 JSON。"""


def _convert_one_rule(
    rule: Rule,
    rule_index: int,
) -> RuleGraphConvertResult:
    """调用 LLM 将单条规则转换为图谱结构。"""
    llm = get_llm_client()
    rule_id_str = f"R{rule_index:03d}"
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        rule_id=rule_id_str,
        doc_type=rule.doc_type,
        check_category=rule.check_category,
        rule_text=rule.rule_text,
        tolerance_json=json.dumps(rule.tolerance or {}, ensure_ascii=False),
    )
    try:
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
    except LLMError as e:
        logger.warning("规则 %s 转换失败: %s", rule_id_str, e)
        return RuleGraphConvertResult(
            entities=[], relationships=[], confidence=0.0, auto_confirmed=False
        )

    entities = [EntityData(**e) for e in resp.get("entities", [])]
    rels = [EdgeData(**r) for r in resp.get("relationships", [])]
    confidence = float(resp.get("confidence", 0.0))

    # 给每个实体的 attributes 注入 rule_id 和 doc_type
    for ent in entities:
        ent.attributes.setdefault("rule_id", rule_id_str)
        ent.attributes.setdefault("doc_type", rule.doc_type)
        ent.attributes.setdefault("check_category", rule.check_category)
    for rel in rels:
        rel.attributes.setdefault("rule_id", rule_id_str)
        rel.attributes.setdefault("doc_type", rule.doc_type)
        rel.attributes.setdefault("check_category", rule.check_category)

    return RuleGraphConvertResult(
        entities=entities,
        relationships=rels,
        confidence=confidence,
        auto_confirmed=False,
    )


def build_graph(
    db: Session,
    neo4j: Optional[Neo4jClient] = None,
    auto_confirm_all: bool = False,
    operator: str = "system",
) -> GraphBuildResponse:
    """一键重建图谱：全量替换。

    Args:
        db: 数据库会话
        neo4j: Neo4j 客户端（不传则用全局单例）
        auto_confirm_all: 是否一键自动确认全部（忽略置信度）
        operator: 操作人
    """
    neo4j = neo4j or get_neo4j_client()

    # 1. 读取启用规则
    rules = get_enabled_rules_for_snapshot(db)
    if not rules:
        raise ValueError("无启用的规则，请先添加规则")

    threshold = settings.llm_confidence_threshold

    # 2. 逐条转换
    all_entities: list[EntityData] = []
    all_relationships: list[EdgeData] = []
    auto_confirmed_count = 0
    manual_pending_count = 0

    for idx, rule in enumerate(rules, start=1):
        result = _convert_one_rule(rule, idx)
        if auto_confirm_all or result.confidence >= threshold:
            result.auto_confirmed = True
            auto_confirmed_count += 1
            all_entities.extend(result.entities)
            all_relationships.extend(result.relationships)
        else:
            # 低置信度仍写入图谱，但标记 low_confidence（供前端高亮）
            manual_pending_count += 1
            for ent in result.entities:
                ent.attributes["low_confidence"] = True
                ent.attributes["confidence"] = result.confidence
            for rel in result.relationships:
                rel.attributes["low_confidence"] = True
                rel.attributes["confidence"] = result.confidence
            all_entities.extend(result.entities)
            all_relationships.extend(result.relationships)

    # 3. 生成 graph_id（与快照绑定）
    graph_id = f"graph_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # 4. 全量替换：先清除旧图谱，再写入新图谱
    # 注：每次重建生成新 graph_id，旧 graph_id 的节点自然保留为历史快照
    neo4j.write_rule_graph(
        graph_id=graph_id,
        entities=[e.model_dump() for e in all_entities],
        relationships=[r.model_dump() for r in all_relationships],
    )

    # 5. 统计节点/关系数
    graph_data = neo4j.get_graph_data(graph_id)

    # 6. 保存规则快照
    snapshot = RuleSnapshot(
        snapshot_time=datetime.now(),
        rule_count=len(rules),
        rules_json=[
            {
                "id": str(r.id),
                "doc_type": r.doc_type,
                "check_category": r.check_category,
                "rule_text": r.rule_text,
                "tolerance": r.tolerance,
                "enabled": r.enabled,
                "priority": r.priority,
            }
            for r in rules
        ],
        graph_id=graph_id,
        node_count=graph_data["node_count"],
        edge_count=graph_data["edge_count"],
        operator=operator,
        note=f"auto_confirm_all={auto_confirm_all}, threshold={threshold}",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return GraphBuildResponse(
        snapshot_id=snapshot.id,
        graph_id=graph_id,
        node_count=graph_data["node_count"],
        edge_count=graph_data["edge_count"],
        rule_count=len(rules),
        auto_confirmed_count=auto_confirmed_count,
        manual_pending_count=manual_pending_count,
        message=f"图谱构建完成：{graph_data['node_count']} 节点 / {graph_data['edge_count']} 关系",
    )


def get_graph(neo4j: Optional[Neo4jClient], graph_id: str) -> GraphData:
    """获取图谱可视化数据。"""
    neo4j = neo4j or get_neo4j_client()
    data = neo4j.get_graph_data(graph_id)
    return GraphData(**data)


def apply_graph_edits(
    neo4j: Optional[Neo4jClient],
    graph_id: str,
    edits: list[GraphEditOp],
) -> GraphData:
    """应用人工编辑（确认生效）。"""
    neo4j = neo4j or get_neo4j_client()
    for op in edits:
        if op.op == "update_node" and op.node_name:
            neo4j.update_node_properties(graph_id, op.node_name, op.properties)
        elif op.op == "update_edge" and op.source and op.target:
            neo4j.update_edge_properties(graph_id, op.source, op.target, op.properties)
        elif op.op == "delete_node" and op.node_name:
            neo4j.delete_node(graph_id, op.node_name)
        elif op.op == "delete_edge" and op.source and op.target:
            neo4j.delete_edge(graph_id, op.source, op.target)
    return get_graph(neo4j, graph_id)
