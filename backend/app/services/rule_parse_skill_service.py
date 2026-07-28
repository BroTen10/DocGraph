"""RuleParseSkill CRUD 服务。"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RuleParseSkill
from ..schemas.rule_parse_skill import (
    RuleParseSkillCreate,
    RuleParseSkillOut,
    RuleParseSkillUpdate,
)


def list_skills(
    db: Session,
    rule_set_id: uuid.UUID | None = None,
    include_builtin: bool = True,
) -> list[RuleParseSkillOut]:
    """获取 Skill 列表。

    Args:
        rule_set_id: 指定规则集的 Skill（含全局内置）
        include_builtin: 是否包含内置默认 Skill（is_builtin=True）
    """
    stmt = select(RuleParseSkill).order_by(
        RuleParseSkill.is_builtin.desc(),
        RuleParseSkill.priority,
        RuleParseSkill.name,
    )
    if rule_set_id:
        # 该规则集的 Skill + 全局内置
        stmt = stmt.where(
            (RuleParseSkill.rule_set_id == rule_set_id) |
            (RuleParseSkill.is_builtin.is_(True))
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
    """为指定规则集创建自定义 Skill。"""
    skill = RuleParseSkill(
        rule_set_id=rule_set_id,
        name=payload.name,
        description=payload.description,
        is_builtin=False,
        enabled=payload.enabled,
        priority=payload.priority,
        content=payload.content.model_dump(mode="json"),
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

    特殊逻辑：如果编辑的是内置默认 Skill（is_builtin=True），
    自动创建一个副本（is_builtin=False）归到当前规则集下，
    原始内置不动。
    """
    skill = db.get(RuleParseSkill, skill_id)
    if skill is None:
        return None

    data = payload.model_dump(exclude_unset=True)

    # 如果编辑的是内置默认 → 自动创建该规则集下的副本
    if skill.is_builtin and rule_set_id is not None:
        # 检查是否已有该规则集下的副本（基于 parent_id）
        existing_copy = db.execute(
            select(RuleParseSkill).where(
                RuleParseSkill.parent_id == skill_id,
                RuleParseSkill.rule_set_id == rule_set_id,
            )
        ).scalars().first()

        if existing_copy:
            # 更新已有副本
            copy = existing_copy
            for k, v in data.items():
                setattr(copy, k, v)
            copy.version += 1
        else:
            # 创建新副本
            copy_content = dict(skill.content)
            if "content" in data:
                copy_content.update(data.pop("content"))
            copy = RuleParseSkill(
                rule_set_id=rule_set_id,
                parent_id=skill.id,
                name=data.get("name", skill.name),
                description=data.get("description", skill.description),
                is_builtin=False,
                enabled=data.get("enabled", skill.enabled),
                priority=data.get("priority", skill.priority),
                content=copy_content,
                version=skill.version + 1,
            )
            db.add(copy)

        db.commit()
        db.refresh(copy)
        return RuleParseSkillOut.model_validate(copy)

    # 普通自定义 Skill 直接更新
    if "content" in data and isinstance(data["content"], dict):
        data["content"] = data["content"]  # already dict from pydantic
    for k, v in data.items():
        setattr(skill, k, v)
    skill.version += 1
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
