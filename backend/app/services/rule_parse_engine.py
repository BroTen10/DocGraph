"""规则解析引擎：将多个 Skill 编译为解析指令，驱动 LLM 解析规则。

流程：
1. 解析本次导入的业务场景（导入参数 > 导入文件声明 > 规则集默认 > 兼容默认）
2. 从数据库加载已启用的 Skill：通用契约包（scenario IS NULL，始终生效）
   + 该场景知识包（scenario = 场景键）+ 该规则集自定义 Skill
3. 按优先级排序，按能力逐 key 合并（自定义覆盖内置，场景包覆盖通用契约）
4. 生成 RuleParseDirective → 注入到 LLM 调用
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RuleParseSkill

logger = logging.getLogger(__name__)


@dataclass
class ValidationRule:
    field: str
    rule: str
    severity: str = "error"
    message: str = ""


@dataclass
class PreprocessStep:
    type: str  # "replace" | "extraction"
    pattern: str = ""
    replacement: str = ""
    extraction: str = ""
    description: str = ""


@dataclass
class RuleParseDirective:
    """从 Skill 集合编译得到的解析指令。"""
    prompt_additions: list[str] = field(default_factory=list)
    field_mappings: dict[str, dict[str, str]] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    validations: list[ValidationRule] = field(default_factory=list)
    text_preprocessing: list[PreprocessStep] = field(default_factory=list)
    term_normalization: dict[str, list[str]] = field(default_factory=dict)
    domain_context: dict[str, Any] = field(default_factory=dict)


def resolve_scenario(
    rule_set: Any,
    scenario: str | None = None,
) -> str:
    """解析本次解析应使用的场景键。

    优先级：调用方显式指定（导入请求/文件声明） > 规则集自身配置 > 兼容默认。
    """
    from ..default_rule_parse_skill import DEFAULT_SCENARIO

    for candidate in (scenario, getattr(rule_set, "scenario", None)):
        value = str(candidate).strip() if candidate not in (None, "") else ""
        if value:
            return value
    return DEFAULT_SCENARIO


def compile_directive(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: Any,
    scenario: str | None = None,
) -> RuleParseDirective:
    """从数据库加载并编译指定规则集的所有已启用 Skill。

    1. 加载内置通用契约包（scenario IS NULL，始终生效）
    2. 加载本次场景的知识包（scenario = 解析出的场景键）
    3. 加载该规则集的自定义 Skill（可覆盖内置）
    4. 按能力逐 key 合并
    """
    directive, _ = compile_directive_with_skills(
        db, tenant_id, rule_set_id, scenario=scenario
    )
    return directive


def compile_directive_with_skills(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: Any,
    scenario: str | None = None,
    *,
    preset_only: bool = False,
) -> tuple[RuleParseDirective, list[RuleParseSkill]]:
    """同 compile_directive，但同时返回参与合并的 Skill（按合并顺序）。

    Args:
        preset_only: 只装配「系统预设」——通用解析契约包（scenario IS NULL）。
            用于「自定义场景」通道：此时**不加载任何场景知识包**，也**不自动带上
            规则集自定义 Skill**（它们由调用方按用户勾选显式叠加），避免出厂领域
            知识与客户自定义场景混用。

    调用方需要知道"哪些 Skill 已经在基线里"时使用（例如导入弹窗额外指定的
    Skill 要叠加在基线之上，而不是重复合并）。
    """
    from .rule_service import get_rule_set

    rs = get_rule_set(db, tenant_id, rule_set_id)
    if rs is None:
        raise ValueError("规则集不存在")
    use_default_skill = rs.use_default_skill
    resolved_scenario = resolve_scenario(rs, scenario)
    overridden_builtin_ids = select(RuleParseSkill.parent_id).where(
        RuleParseSkill.tenant_id == tenant_id,
        RuleParseSkill.rule_set_id == rule_set_id,
        RuleParseSkill.parent_id.is_not(None),
    )

    custom_scoped = (
        (RuleParseSkill.tenant_id == tenant_id)
        & (RuleParseSkill.rule_set_id == rule_set_id)
    )

    conditions = [RuleParseSkill.enabled.is_(True)]
    if preset_only:
        # 系统预设：仅通用契约包；场景包与自定义 Skill 都不自动生效
        conditions.append(
            RuleParseSkill.is_builtin.is_(True)
            & RuleParseSkill.tenant_id.is_(None)
            & RuleParseSkill.id.not_in(overridden_builtin_ids)
            & RuleParseSkill.scenario.is_(None)
        )
    elif use_default_skill:
        # 未被租户副本覆盖的内置模板（通用契约 + 本次场景） + 该规则集自定义 Skill
        conditions.append(
            (
                RuleParseSkill.is_builtin.is_(True)
                & RuleParseSkill.tenant_id.is_(None)
                & RuleParseSkill.id.not_in(overridden_builtin_ids)
                & (
                    RuleParseSkill.scenario.is_(None)
                    | (RuleParseSkill.scenario == resolved_scenario)
                )
            )
            | custom_scoped
        )
    else:
        # 规则集关闭了内置默认 Skill 领域知识：仅使用该规则集自定义 Skill
        conditions.append(custom_scoped)

    # 合并顺序：内置默认先合并，自定义后合并（后写覆盖先写）→ 自定义纠偏生效
    skills = db.execute(
        select(RuleParseSkill).where(*conditions).order_by(
            RuleParseSkill.is_builtin.desc(),
            RuleParseSkill.priority,
            RuleParseSkill.name,
        )
    ).scalars().all()

    if not skills:
        logger.warning("规则集 %s 无可用 Skill，使用空指令", rule_set_id)
        return RuleParseDirective(), []

    directive = RuleParseDirective()
    for skill in skills:
        content = skill.content or {}
        _merge_content(directive, content)

    logger.info(
        "Skill 编译完成（场景=%s）: %d 个 Skill（指令=%d, 映射=%d, 默认值=%d, 校验=%d, 预处理=%d, 术语=%d, 领域=%d）",
        resolved_scenario,
        len(skills),
        len(directive.prompt_additions),
        len(directive.field_mappings),
        len(directive.defaults),
        len(directive.validations),
        len(directive.text_preprocessing),
        len(directive.term_normalization),
        len(directive.domain_context),
    )
    return directive, list(skills)


def directive_to_content(directive: RuleParseDirective) -> dict:
    """把编译后的指令还原为 Skill content 结构（文档镜像、自检与快照使用）。"""
    steps = []
    for step in directive.text_preprocessing:
        item: dict[str, Any] = {"type": step.type}
        if step.pattern:
            item["pattern"] = step.pattern
        if step.replacement:
            item["replacement"] = step.replacement
        if step.extraction:
            item["extraction"] = step.extraction
        if step.description:
            item["description"] = step.description
        steps.append(item)

    validations = []
    for rule in directive.validations:
        item = {"field": rule.field, "rule": rule.rule, "severity": rule.severity}
        if rule.message:
            item["message"] = rule.message
        validations.append(item)

    return {
        "prompt_instructions": list(directive.prompt_additions),
        "field_mappings": copy.deepcopy(directive.field_mappings),
        "defaults": copy.deepcopy(directive.defaults),
        "validations": validations,
        "text_preprocessing": steps,
        "term_normalization": copy.deepcopy(directive.term_normalization),
        "domain_context": copy.deepcopy(directive.domain_context),
    }


def _merge_content(directive: RuleParseDirective, content: dict) -> None:
    """将单个 Skill 的 content 合并到 directive 中。

    合并策略：
    - 列表（prompt_additions / validations）：追加
    - 字典（field_mappings / defaults / term_normalization / domain_context）：深合并，同名 key 覆盖
    """
    # 列表字段：追加
    for instr in (content.get("prompt_instructions") or []):
        if isinstance(instr, str) and instr not in directive.prompt_additions:
            directive.prompt_additions.append(instr)

    for v in (content.get("validations") or []):
        if isinstance(v, dict):
            directive.validations.append(ValidationRule(
                field=v.get("field", ""),
                rule=v.get("rule", ""),
                severity=v.get("severity", "error"),
                message=v.get("message", ""),
            ))

    for step in (content.get("text_preprocessing") or []):
        if isinstance(step, dict):
            directive.text_preprocessing.append(PreprocessStep(
                type=step.get("type", "replace"),
                pattern=step.get("pattern", ""),
                replacement=step.get("replacement", ""),
                extraction=step.get("extraction", ""),
                description=step.get("description", ""),
            ))

    # 字典字段：深合并
    _deep_merge_dict(directive.field_mappings, content.get("field_mappings") or {})
    _deep_merge_dict(directive.defaults, content.get("defaults") or {})
    _deep_merge_dict(directive.term_normalization, content.get("term_normalization") or {})
    _deep_merge_dict(directive.domain_context, content.get("domain_context") or {})


def _deep_merge_dict(base: dict, override: dict) -> None:
    """深度合并两个 dict：override 的同名 key 覆盖 base。"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge_dict(base[k], v)
        else:
            base[k] = copy.deepcopy(v)


def apply_text_preprocessing(text: str, steps: list[PreprocessStep]) -> str:
    """对原始规则文本执行预处理步骤。"""
    for step in steps:
        if step.type == "replace" and step.pattern:
            try:
                text = re.sub(step.pattern, step.replacement, text)
            except re.error as e:
                logger.warning("文本预处理正则错误: %s → %s", step.pattern, e)
    return text


def apply_field_mappings(rules: list[dict], mappings: dict[str, dict[str, str]]) -> list[dict]:
    """对解析后的规则逐条应用字段映射。"""
    if not mappings:
        return rules

    for rule in rules:
        for field_name, mapping in mappings.items():
            if field_name in rule and isinstance(rule[field_name], str):
                rule[field_name] = mapping.get(rule[field_name], rule[field_name])
    return rules


def apply_defaults(rules: list[dict], defaults: dict) -> list[dict]:
    """对解析后的规则应用默认值（仅在字段缺失或 null 时填充）。"""
    if not defaults:
        return rules

    for rule in rules:
        # defaults.tolerance
        tol_defaults = defaults.get("tolerance") or {}
        tol = rule.get("tolerance") or {}
        if not isinstance(tol, dict):
            tol = {}
        for k, v in tol_defaults.items():
            if k not in tol or tol[k] is None:
                tol[k] = v
        if tol_defaults:
            rule["tolerance"] = tol

        # defaults.priority（按 check_category）
        pri_defaults = defaults.get("priority") or {}
        cc = rule.get("check_category", "")
        if cc in pri_defaults and ("priority" not in rule or rule.get("priority") is None):
            rule["priority"] = pri_defaults[cc]

    return rules


def apply_term_normalization(rules: list[dict], normalization: dict[str, list[str]]) -> list[dict]:
    """对规则文本中的术语做归一化替换。"""
    if not normalization:
        return rules

    for rule in rules:
        text = rule.get("rule_text", "")
        if not text:
            continue
        for canonical, variants in normalization.items():
            for v in variants:
                text = text.replace(v, canonical)
        rule["rule_text"] = text

    return rules
