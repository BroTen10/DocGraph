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

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..constants import CHECK_COMPLETENESS, CHECK_STAMP
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
        # 批次 8-4：容差统一挂到 Value 节点（tolerance_params 完整声明 + tolerance 标量），
        # 审查时优先取节点属性，关系属性仅作旧图兜底
        ent.attributes.setdefault("tolerance_params", rule.tolerance or {})
        ent.attributes.setdefault(
            "tolerance",
            (rule.structure or {}).get("assertion", {}).get("tolerance", 0) or 0,
        )
    for rel in rels:
        rel.attributes.setdefault("rule_id", rule_id_str)
        rel.attributes.setdefault("doc_type", rule.doc_type)
        rel.attributes.setdefault("check_category", rule.check_category)
        # 结果解释性：LLM 路径同样注入 rule_text，供审查结果与证据链展示（验收暴露缺失）
        rel.attributes.setdefault("rule_text", rule.rule_text)
        # 1-4：注入规则声明的完整容差参数，供审查时统一取用（amount_percent/weight_kg 等）
        rel.attributes.setdefault("tolerance_params", rule.tolerance or {})
        # 批次 7：注入结构化意图元数据（条件/例外），供审查与可视化使用
        rel.attributes.setdefault("condition", (rule.structure or {}).get("condition"))
        rel.attributes.setdefault("exceptions", (rule.structure or {}).get("exceptions") or [])

    return RuleGraphConvertResult(
        entities=entities,
        relationships=rels,
        confidence=confidence,
        auto_confirmed=False,
    )


# ============ 程序化转换（齐套性 / 印章，不调 LLM） ============
# 这两类规则结构固定，程序化生成更可靠，且能产出 REQUIRED / MUST_STAMP 关系。

# 虚拟节点：表示"齐套性检查"这一动作，作为 REQUIRED 关系的 source
_COMPLETENESS_ROOT = "齐套性检查"
# 虚拟节点：表示"印章要求"这一动作，作为 MUST_STAMP 关系的 target
_STAMP_ROOT = "印章要求"


def _node_name(doc_type: str, field: str, aggregate: Optional[str] = None) -> str:
    """字段节点名。聚合语义以 |SUM|ANY|ALL 后缀显式编码（批次 7-2）。"""
    name = f"{doc_type}.{field}"
    if aggregate and aggregate.upper() in ("SUM", "ANY", "ALL"):
        name += f"|{aggregate.upper()}"
    return name


def _convert_structured_rule(rule: Rule, rule_index: int) -> RuleGraphConvertResult:
    """结构化规则程序化转换（批次 7）：从 rule.structure.assertion 生成图谱。

    不再依赖 LLM 转图谱：断言结构（源/算子/目标/聚合/角色/币别/单位）由规则导入时
    LLM 解析产出，此处确定性映射为 Field 节点 + COMPARE_TO 边，置信度恒 1.0。
    无 structure 的旧规则仍走 LLM 转换路径。
    """
    rule_id_str = f"R{rule_index:03d}"
    structure = rule.structure or {}
    assertion = structure.get("assertion") or {}
    source = assertion.get("source") or {}
    target = assertion.get("target") or {}

    src_doc_type = source.get("doc_type") or rule.doc_type
    tgt_doc_type = target.get("doc_type") or rule.doc_type
    src_field = (source.get("field") or "").strip()
    tgt_field = (target.get("field") or "").strip()
    if not src_field or not tgt_field:
        raise ValueError(f"规则 {rule_id_str} structure.assertion 缺少 source/target 字段")

    operator = str(assertion.get("operator") or "等于").strip()
    # aggregate 允许声明在 assertion 顶层或 source/target 内（两处均可）
    aggregate = str(
        assertion.get("aggregate") or source.get("aggregate") or target.get("aggregate") or ""
    ).strip().upper() or None
    currency = assertion.get("currency") or None
    unit = assertion.get("unit") or None
    src_role = source.get("role") or None
    tgt_role = target.get("role") or None

    src_name = _node_name(src_doc_type, src_field, aggregate)
    tgt_name = _node_name(tgt_doc_type, tgt_field, aggregate)

    src_attrs: dict = {
        "doc_type": src_doc_type, "field": src_field, "rule_id": rule_id_str,
    }
    tgt_attrs: dict = {
        "doc_type": tgt_doc_type, "field": tgt_field, "rule_id": rule_id_str,
    }
    if aggregate:
        src_attrs["aggregate"] = aggregate
        tgt_attrs["aggregate"] = aggregate
    if src_role:
        src_attrs["role"] = src_role
    if tgt_role:
        tgt_attrs["role"] = tgt_role
    if currency:
        src_attrs["currency"] = currency
        tgt_attrs["currency"] = currency
    if unit:
        src_attrs["unit"] = unit
        tgt_attrs["unit"] = unit
    # 批次 8-4：容差挂到 Value 节点（完整参数 + 标量），审查时节点优先、关系兜底
    src_attrs["tolerance_params"] = rule.tolerance or {}
    tgt_attrs["tolerance_params"] = rule.tolerance or {}
    src_attrs["tolerance"] = assertion.get("tolerance", 0) or 0
    tgt_attrs["tolerance"] = assertion.get("tolerance", 0) or 0

    rel_attrs: dict = {
        "rule_id": rule_id_str,
        "rule_text": rule.rule_text,
        "doc_type": rule.doc_type,
        "check_category": rule.check_category,
        "operator": operator,
        "tolerance": assertion.get("tolerance", 0) or 0,
        "tolerance_params": rule.tolerance or {},
        "condition": structure.get("condition") or None,
        "exceptions": structure.get("exceptions") or [],
    }
    if aggregate:
        rel_attrs["aggregate"] = aggregate
    if currency:
        rel_attrs["currency"] = currency
    if unit:
        rel_attrs["unit"] = unit

    return RuleGraphConvertResult(
        entities=[
            EntityData(name=src_name, type="Field", attributes=src_attrs),
            EntityData(name=tgt_name, type="Field", attributes=tgt_attrs),
        ],
        relationships=[
            EdgeData(source=src_name, target=tgt_name, type="COMPARE_TO", attributes=rel_attrs),
        ],
        confidence=1.0,  # 程序化生成，置信度满分
        auto_confirmed=False,
    )


def _convert_completeness_rule(rule: Rule, rule_index: int) -> RuleGraphConvertResult:
    """齐套性规则程序化转换：生成 RequiredDoc 节点 + REQUIRED 关系。

    节点: {name: doc_type, type: "RequiredDoc"}
    关系: {source: "齐套性检查", target: doc_type, type: "REQUIRED"}
    """
    rule_id_str = f"R{rule_index:03d}"
    doc_type = rule.doc_type
    # 根节点
    root_ent = EntityData(
        name=_COMPLETENESS_ROOT,
        type="CheckRoot",
        attributes={"description": "齐套性检查入口", "rule_id": rule_id_str},
    )
    # 文件节点
    doc_ent = EntityData(
        name=doc_type,
        type="RequiredDoc",
        attributes={
            "doc_type": doc_type,
            "rule_id": rule_id_str,
            "rule_text": rule.rule_text,
            "check_category": rule.check_category,
        },
    )
    rel = EdgeData(
        source=_COMPLETENESS_ROOT,
        target=doc_type,
        type="REQUIRED",
        attributes={
            "rule_id": rule_id_str,
            "rule_text": rule.rule_text,
            "doc_type": doc_type,
            "check_category": rule.check_category,
        },
    )
    return RuleGraphConvertResult(
        entities=[root_ent, doc_ent],
        relationships=[rel],
        confidence=1.0,  # 程序化生成，置信度满分
        auto_confirmed=False,
    )


def _convert_stamp_rule(rule: Rule, rule_index: int) -> RuleGraphConvertResult:
    """印章规则程序化转换：生成 StampRequirement 节点 + MUST_STAMP 关系。

    节点: {name: doc_type, type: "StampRequirement"}
    关系: {source: doc_type, target: "印章要求", type: "MUST_STAMP"}
    """
    rule_id_str = f"R{rule_index:03d}"
    doc_type = rule.doc_type
    doc_ent = EntityData(
        name=doc_type,
        type="StampRequirement",
        attributes={
            "doc_type": doc_type,
            "rule_id": rule_id_str,
            "rule_text": rule.rule_text,
            "check_category": rule.check_category,
        },
    )
    root_ent = EntityData(
        name=_STAMP_ROOT,
        type="CheckRoot",
        attributes={"description": "印章要求检查入口", "rule_id": rule_id_str},
    )
    rel = EdgeData(
        source=doc_type,
        target=_STAMP_ROOT,
        type="MUST_STAMP",
        attributes={
            "rule_id": rule_id_str,
            "rule_text": rule.rule_text,
            "doc_type": doc_type,
            "check_category": rule.check_category,
        },
    )
    return RuleGraphConvertResult(
        entities=[doc_ent, root_ent],
        relationships=[rel],
        confidence=1.0,
        auto_confirmed=False,
    )


def build_graph(
    db: Session,
    rule_set_id: uuid.UUID,
    neo4j: Optional[Neo4jClient] = None,
    auto_confirm_all: bool = False,
    operator: str = "system",
    progress_callback: Optional[callable] = None,
) -> GraphBuildResponse:
    """一键重建图谱：全量替换。

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID（按规则集查规则、写快照，命名空间隔离）
        neo4j: Neo4j 客户端（不传则用全局单例）
        auto_confirm_all: 是否一键自动确认全部（忽略置信度）
        operator: 操作人
        progress_callback: 可选的进度回调函数 (stage: str, progress: int, message: str) -> None
    """
    neo4j = neo4j or get_neo4j_client()

    def _report(stage: str, progress: int, message: str) -> None:
        if progress_callback:
            try:
                progress_callback(stage, progress, message)
            except Exception:
                pass  # 进度回调不应影响主流程

    # 1. 读取启用规则（按 rule_set_id 过滤）
    _report("读取启用规则", 5, "正在读取启用的规则")
    rules = get_enabled_rules_for_snapshot(db, rule_set_id)
    if not rules:
        raise ValueError("无已确认且已启用的规则，请先在规则管理中确认并启用规则")

    threshold = settings.llm_confidence_threshold
    _report("读取启用规则", 10, f"共 {len(rules)} 条启用规则")

    # 2. 逐条转换
    all_entities: list[EntityData] = []
    all_relationships: list[EdgeData] = []
    auto_confirmed_count = 0
    manual_pending_count = 0
    total = len(rules)

    for idx, rule in enumerate(rules, start=1):
        # 按 check_category 分派：齐套性/印章程序化，其余走 LLM
        if rule.check_category == CHECK_COMPLETENESS:
            result = _convert_completeness_rule(rule, idx)
        elif rule.check_category == CHECK_STAMP:
            result = _convert_stamp_rule(rule, idx)
        elif (rule.structure or {}).get("assertion"):
            # 批次 7：有结构化断言的规则程序化转图谱（确定性，不调 LLM）
            _report(
                "结构化规则转换",
                10 + int((idx - 1) / total * 70),
                f"正在转换规则 {idx}/{total}：[{rule.doc_type}] {rule.rule_text[:40]}...",
            )
            result = _convert_structured_rule(rule, idx)
        else:
            _report(
                "LLM 转换规则",
                10 + int((idx - 1) / total * 70),
                f"正在转换规则 {idx}/{total}：[{rule.doc_type}] {rule.rule_text[:40]}...",
            )
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
    _report(
        "写入 Neo4j",
        85,
        f"正在写入 Neo4j：{len(all_entities)} 实体 / {len(all_relationships)} 关系",
    )
    neo4j.write_rule_graph(
        graph_id=graph_id,
        entities=[e.model_dump() for e in all_entities],
        relationships=[r.model_dump() for r in all_relationships],
    )

    # 5. 统计节点/关系数
    graph_data = neo4j.get_graph_data(graph_id)

    # 6. 保存规则快照（带 rule_set_id）
    _report("保存规则快照", 95, "正在保存规则快照")
    snapshot = RuleSnapshot(
        rule_set_id=rule_set_id,
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

    # 1-1：构建前全清——写入成功并保存快照后，清理该规则集的历史图谱。
    # 先写后清：若新图写入失败，旧图仍可用（可回滚），避免构建中途失败丢数据。
    old_graph_ids = [
        gid
        for gid in db.execute(
            select(RuleSnapshot.graph_id)
            .where(
                RuleSnapshot.rule_set_id == rule_set_id,
                RuleSnapshot.graph_id.isnot(None),
            )
        ).scalars().all()
        if gid and gid != graph_id
    ]
    for old_gid in old_graph_ids:
        try:
            removed = neo4j.clear_graph(old_gid)
            logger.info("已清理旧图谱 %s（%d 节点）", old_gid, removed)
        except Exception as e:
            logger.warning("清理旧图谱 %s 失败: %s", old_gid, e)

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
