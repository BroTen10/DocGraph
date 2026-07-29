"""RuleParseSkill CRUD 服务。

版本策略：
- 仅当 content（Skill 内容）发生变化时 version + 1；
- 启用/停用、改优先级、改名等元数据操作不递增版本。

内置 Skill 策略：
- 启用/停用/优先级：直接改内置本体（全局生效，所有规则集共享）；
- 修改内容/名称/描述：不动内置本体，自动在当前规则集下创建/更新自定义副本。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RuleParseSkill
from ..schemas.rule_parse_skill import (
    RuleParseSkillCreate,
    RuleParseSkillOut,
    RuleParseSkillUpdate,
    SkillLearnRequest,
)

# 元数据字段：更新它们不产生新版本、不触发内置副本
_META_FIELDS = {"enabled", "priority"}

LEARN_SKILL_NAME = "经验修正（自动累积）"


def list_skills(
    db: Session,
    rule_set_id: uuid.UUID | None = None,
    include_builtin: bool = True,
) -> list[RuleParseSkillOut]:
    """获取 Skill 列表。"""
    stmt = select(RuleParseSkill).order_by(
        RuleParseSkill.is_builtin.desc(),
        RuleParseSkill.priority,
        RuleParseSkill.name,
    )
    if rule_set_id:
        stmt = stmt.where(
            (RuleParseSkill.rule_set_id == rule_set_id)
            | (RuleParseSkill.is_builtin.is_(True))
        )
    else:
        stmt = stmt.where(RuleParseSkill.rule_set_id.is_(None))

    rows = db.execute(stmt).scalars().all()
    return [RuleParseSkillOut.model_validate(r) for r in rows]


def get_skill(db: Session, skill_id: uuid.UUID) -> Optional[RuleParseSkill]:
    return db.get(RuleParseSkill, skill_id)


def create_skill(
    db: Session,
    rule_set_id: uuid.UUID,
    payload: RuleParseSkillCreate,
) -> RuleParseSkillOut:
    """为指定规则集创建自定义 Skill。

    Raises:
        ValueError: content_yaml 解析/校验失败。
    """
    content = payload.resolved_content()
    skill = RuleParseSkill(
        rule_set_id=rule_set_id,
        name=payload.name,
        description=payload.description,
        is_builtin=False,
        enabled=payload.enabled,
        priority=payload.priority,
        content=content.model_dump(mode="json"),
        version=1,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return RuleParseSkillOut.model_validate(skill)


def update_skill(
    db: Session,
    skill_id: uuid.UUID,
    payload: RuleParseSkillUpdate,
    rule_set_id: uuid.UUID | None = None,
) -> Optional[RuleParseSkillOut]:
    """更新 Skill。

    Raises:
        ValueError: content_yaml 解析/校验失败。
    """
    skill = db.get(RuleParseSkill, skill_id)
    if skill is None:
        return None

    data = payload.model_dump(exclude_unset=True, exclude={"content", "content_yaml"})
    new_content = payload.resolved_content()  # None = 本次不改内容

    # ---------- 内置 Skill ----------
    if skill.is_builtin:
        substantive = new_content is not None or any(
            k not in _META_FIELDS for k in data
        )
        if not substantive:
            # 仅启用/停用/优先级 → 直接改内置本体，全局生效，不建副本、不加版本
            for k, v in data.items():
                setattr(skill, k, v)
            db.commit()
            db.refresh(skill)
            return RuleParseSkillOut.model_validate(skill)

        if rule_set_id is None:
            raise ValueError("修改内置 Skill 内容必须在规则集上下文中进行")

        # 内容级修改 → 创建/更新该规则集下的副本
        existing_copy = db.execute(
            select(RuleParseSkill).where(
                RuleParseSkill.parent_id == skill_id,
                RuleParseSkill.rule_set_id == rule_set_id,
            )
        ).scalars().first()

        if existing_copy:
            copy = existing_copy
            for k, v in data.items():
                setattr(copy, k, v)
            if new_content is not None:
                copy.content = new_content.model_dump(mode="json")
                copy.version += 1
        else:
            copy = RuleParseSkill(
                rule_set_id=rule_set_id,
                parent_id=skill.id,
                name=data.get("name", f"{skill.name}（自定义副本）"),
                description=data.get("description", skill.description),
                is_builtin=False,
                enabled=data.get("enabled", skill.enabled),
                priority=data.get("priority", skill.priority),
                content=(
                    new_content.model_dump(mode="json")
                    if new_content is not None
                    else dict(skill.content)
                ),
                version=1,
            )
            db.add(copy)

        db.commit()
        db.refresh(copy)
        return RuleParseSkillOut.model_validate(copy)

    # ---------- 自定义 Skill：就地更新 ----------
    for k, v in data.items():
        setattr(skill, k, v)
    if new_content is not None:
        skill.content = new_content.model_dump(mode="json")
        skill.version += 1  # 只有内容变化才递增版本
    db.commit()
    db.refresh(skill)
    return RuleParseSkillOut.model_validate(skill)


def delete_skill(db: Session, skill_id: uuid.UUID) -> bool:
    """删除 Skill。内置不可删除。"""
    skill = db.get(RuleParseSkill, skill_id)
    if skill is None:
        return False
    if skill.is_builtin:
        raise ValueError("内置默认 Skill 不可删除")
    db.delete(skill)
    db.commit()
    return True


# ============ 修正经验反哺 ============

_LEARNABLE_FIELDS = {
    "rule_text": "规则文本",
    "check_category": "检查类别",
    "doc_type": "适用单据",
    "severity": "严重级别",
}


def build_experience_instructions(req: SkillLearnRequest) -> list[str]:
    """根据修正前后差异，生成可注入 LLM 提示词的经验指令。"""
    instructions: list[str] = []
    before, after = req.before or {}, req.after or {}
    stamp = datetime.now().strftime("%Y-%m-%d")

    b_text = str(before.get("rule_text") or "").strip()
    a_text = str(after.get("rule_text") or "").strip()

    for field_name, label in _LEARNABLE_FIELDS.items():
        b = before.get(field_name)
        a = after.get(field_name)
        if b is None and a is None:
            continue
        if str(b or "").strip() == str(a or "").strip():
            continue
        if field_name == "rule_text":
            instructions.append(
                f"[经验修正 {stamp}] 规则文本『{b}』曾被解析错误，正确表述应为『{a}』；"
                f"遇到同类原文时按后者语义解析"
            )
        else:
            anchor = a_text or b_text or "该类规则"
            instructions.append(
                f"[经验修正 {stamp}] 类似『{anchor}』的规则，其{label}应为"
                f"『{a}』而非『{b}』"
            )

    if req.note:
        instructions.append(f"[经验修正 {stamp}] 用户补充：{req.note.strip()}")
    return instructions


def learn_from_correction(
    db: Session,
    rule_set_id: uuid.UUID,
    req: SkillLearnRequest,
) -> tuple[RuleParseSkillOut, list[str]]:
    """将人工修正经验写回 Skill。

    优先写入 req.skill_id 指定的自定义 Skill；
    否则写入规则集下的『经验修正（自动累积）』Skill（不存在则创建）。

    Returns:
        (更新后的 Skill, 实际新增的指令列表)

    Raises:
        ValueError: 目标 Skill 是内置、不存在，或本次修正无可学习差异。
    """
    instructions = build_experience_instructions(req)
    if not instructions:
        raise ValueError("修正前后无可学习的差异")

    target: RuleParseSkill | None = None
    if req.skill_id is not None:
        target = db.get(RuleParseSkill, req.skill_id)
        if target is None:
            raise ValueError("目标 Skill 不存在")
        if target.is_builtin:
            raise ValueError("经验不能写入内置 Skill，请选择自定义 Skill")
    else:
        target = db.execute(
            select(RuleParseSkill).where(
                RuleParseSkill.rule_set_id == rule_set_id,
                RuleParseSkill.name == LEARN_SKILL_NAME,
            )
        ).scalars().first()

    if target is None:
        target = RuleParseSkill(
            rule_set_id=rule_set_id,
            name=LEARN_SKILL_NAME,
            description="由人工修正解析错误自动累积的经验指令，导入规则时自动注入解析提示词",
            is_builtin=False,
            enabled=True,
            priority=50,
            content={
                "prompt_instructions": [],
                "field_mappings": {},
                "defaults": {},
                "validations": [],
                "text_preprocessing": [],
                "term_normalization": {},
                "domain_context": {},
            },
            version=1,
        )
        db.add(target)
        db.flush()

    content = dict(target.content or {})
    existing = list(content.get("prompt_instructions") or [])
    added = [i for i in instructions if i not in existing]
    if not added:
        raise ValueError("该修正经验已存在于 Skill 中，未重复添加")

    content["prompt_instructions"] = existing + added
    target.content = content
    target.version += 1
    db.commit()
    db.refresh(target)
    return RuleParseSkillOut.model_validate(target), added
