"""规则解析 Skill 管理路由。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.rule_parse_skill import (
    RuleParseSkillCreate,
    RuleParseSkillOut,
    RuleParseSkillUpdate,
    SkillLearnRequest,
)
from ..services import rule_parse_skill_service

router = APIRouter(prefix="/api/rule-sets/{rule_set_id}/skills", tags=["rule-parse-skills"])


@router.get("", response_model=list[RuleParseSkillOut])
def list_skills(
    rule_set_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[RuleParseSkillOut]:
    """获取规则集的所有可用 Skill（含内置默认）。"""
    return rule_parse_skill_service.list_skills(db, rule_set_id=rule_set_id)


@router.post("", response_model=RuleParseSkillOut, status_code=201)
def create_skill(
    rule_set_id: uuid.UUID,
    payload: RuleParseSkillCreate,
    db: Session = Depends(get_db),
) -> RuleParseSkillOut:
    """为规则集创建自定义 Skill（内容用 YAML 文本 content_yaml 提交）。"""
    try:
        return rule_parse_skill_service.create_skill(db, rule_set_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/learn")
def learn_from_correction(
    rule_set_id: uuid.UUID,
    payload: SkillLearnRequest,
    db: Session = Depends(get_db),
) -> dict:
    """将人工修正规则的经验写回 Skill（默认写入『经验修正（自动累积）』）。

    注意：必须注册在 /{skill_id} 之前，否则会被路径参数路由截获。
    """
    try:
        skill, added = rule_parse_skill_service.learn_from_correction(
            db, rule_set_id, payload
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "success": True,
        "skill": skill.model_dump(mode="json"),
        "added_instructions": added,
    }


@router.get("/{skill_id}", response_model=RuleParseSkillOut)
def get_skill(
    rule_set_id: uuid.UUID,
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RuleParseSkillOut:
    skill = rule_parse_skill_service.get_skill(db, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return RuleParseSkillOut.model_validate(skill)


@router.put("/{skill_id}", response_model=RuleParseSkillOut)
def update_skill(
    rule_set_id: uuid.UUID,
    skill_id: uuid.UUID,
    payload: RuleParseSkillUpdate,
    db: Session = Depends(get_db),
) -> RuleParseSkillOut:
    """更新 Skill。

    - 仅启用/停用/优先级：就地更新（内置也允许），不递增版本；
    - 修改内容：自定义就地更新且版本 +1；内置自动创建/更新当前规则集下的副本。
    """
    try:
        result = rule_parse_skill_service.update_skill(
            db, skill_id, payload, rule_set_id=rule_set_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return result


@router.delete("/{skill_id}")
def delete_skill(
    rule_set_id: uuid.UUID,
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """删除自定义 Skill（内置不可删除）。"""
    try:
        ok = rule_parse_skill_service.delete_skill(db, skill_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return {"success": True}
