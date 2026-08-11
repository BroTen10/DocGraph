"""规则解析引擎：将多个 Skill 编译为解析指令，驱动 LLM 解析规则。

流程：
1. 从数据库加载已启用的 Skill（内置默认 + 自定义）
2. 按优先级排序，按能力逐 key 合并（自定义覆盖内置）
3. 生成 RuleParseDirective → 注入到 LLM 调用
"""

from __future__ import annotations

import copy
import logging
import re
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


def compile_directive(db: Session, rule_set_id: Any) -> RuleParseDirective:
    """从数据库加载并编译指定规则集的所有已启用 Skill。

    1. 加载内置默认 Skill（始终生效）
    2. 加载该规则集的自定义 Skill（可覆盖内置）
    3. 按能力逐 key 合并
    """
    from ..models import RuleSet

    rs = db.get(RuleSet, rule_set_id)
    use_default_skill = rs is None or rs.use_default_skill

    conditions = [RuleParseSkill.enabled.is_(True)]
    if use_default_skill:
        # 内置默认 Skill（全局常驻）+ 该规则集自定义 Skill
        conditions.append(
            (RuleParseSkill.is_builtin.is_(True))
            | (RuleParseSkill.rule_set_id == rule_set_id)
        )
    else:
        # 规则集关闭了内置默认 Skill 领域知识：仅使用该规则集自定义 Skill
        conditions.append(RuleParseSkill.rule_set_id == rule_set_id)

    # 合并顺序：内置默认先合并，自定义后合并（后写覆盖先写）→ 自定义纠偏生效
    skills = db.execute(
        select(RuleParseSkill).where(*conditions).order_by(
            RuleParseSkill.is_builtin.desc(),
            RuleParseSkill.priority,
        )
    ).scalars().all()

    if not skills:
        logger.warning("规则集 %s 无可用 Skill，使用空指令", rule_set_id)
        return RuleParseDirective()

    directive = RuleParseDirective()
    for skill in skills:
        content = skill.content or {}
        _merge_content(directive, content)

    logger.info(
        "Skill 编译完成: %d 个 Skill（指令=%d, 映射=%d, 默认值=%d, 校验=%d, 预处理=%d, 术语=%d, 领域=%d）",
        len(skills),
        len(directive.prompt_additions),
        len(directive.field_mappings),
        len(directive.defaults),
        len(directive.validations),
        len(directive.text_preprocessing),
        len(directive.term_normalization),
        len(directive.domain_context),
    )
    return directive


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
        for field, mapping in mappings.items():
            if field in rule and isinstance(rule[field], str):
                rule[field] = mapping.get(rule[field], rule[field])
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
