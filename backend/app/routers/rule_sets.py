"""规则集管理路由。

RuleSet 作为"命名空间"，让系统可以存放多套审查规则，
每套规则对应自己的合同、文档、图谱、审查任务，完全隔离。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import RuleSet
from ..schemas.rule_set import RuleSetCreate, RuleSetOut, RuleSetUpdate

router = APIRouter(prefix="/api/rule-sets", tags=["rule-sets"])


@router.get("", response_model=list[RuleSetOut])
def list_rule_sets(db: Session = Depends(get_db)) -> list[RuleSetOut]:
    """规则集列表（按创建时间倒序，默认规则集置顶）。"""
    stmt = select(RuleSet).order_by(
        RuleSet.is_default.desc(),
        RuleSet.created_at.desc(),
    )
    rows = db.execute(stmt).scalars().all()
    return [RuleSetOut.model_validate(r) for r in rows]


@router.post("", response_model=RuleSetOut, status_code=201)
def create_rule_set(
    payload: RuleSetCreate, db: Session = Depends(get_db)
) -> RuleSetOut:
    """创建规则集。"""
    # 名称唯一性校验
    existing = db.execute(
        select(RuleSet).where(RuleSet.name == payload.name)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=400, detail=f"规则集名称已存在: {payload.name}")

    # 如果请求设为默认，先把其他默认置为 False
    if payload.is_default:
        db.execute(update(RuleSet).where(RuleSet.is_default.is_(True)).values(is_default=False))

    rs = RuleSet(
        name=payload.name,
        description=payload.description,
        doc_types=payload.doc_types,
        check_categories=payload.check_categories,
        is_default=payload.is_default,
    )
    db.add(rs)
    db.commit()
    db.refresh(rs)
    return RuleSetOut.model_validate(rs)


@router.get("/{rule_set_id}", response_model=RuleSetOut)
def get_rule_set(rule_set_id: uuid.UUID, db: Session = Depends(get_db)) -> RuleSetOut:
    """获取规则集详情。"""
    rs = db.get(RuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="规则集不存在")
    return RuleSetOut.model_validate(rs)


@router.put("/{rule_set_id}", response_model=RuleSetOut)
def update_rule_set(
    rule_set_id: uuid.UUID,
    payload: RuleSetUpdate,
    db: Session = Depends(get_db),
) -> RuleSetOut:
    """更新规则集。"""
    rs = db.get(RuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="规则集不存在")

    data = payload.model_dump(exclude_unset=True)

    # 名称唯一性校验
    if "name" in data and data["name"] != rs.name:
        existing = db.execute(
            select(RuleSet).where(RuleSet.name == data["name"])
        ).scalars().first()
        if existing is not None:
            raise HTTPException(status_code=400, detail=f"规则集名称已存在: {data['name']}")

    # 设为默认时，先把其他默认置为 False
    if data.get("is_default") is True and not rs.is_default:
        db.execute(update(RuleSet).where(RuleSet.is_default.is_(True)).values(is_default=False))

    for k, v in data.items():
        setattr(rs, k, v)
    db.commit()
    db.refresh(rs)
    return RuleSetOut.model_validate(rs)


@router.delete("/{rule_set_id}")
def delete_rule_set(rule_set_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """删除规则集（级联删除关联的规则、快照、合同）。

    注意：Neo4j 中的图谱节点不在 Postgres 的事务范围之内，
    删除规则集不会清除 Neo4j 中已写入的图节点；如需清理请通过 graph_id 单独操作。
    """
    rs = db.get(RuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="规则集不存在")
    db.delete(rs)
    db.commit()
    return {"success": True, "message": "规则集已删除（关联数据已级联清理）"}


@router.post("/{rule_set_id}/set-default", response_model=RuleSetOut)
def set_default(rule_set_id: uuid.UUID, db: Session = Depends(get_db)) -> RuleSetOut:
    """将指定规则集设为默认（同一时刻只能有一个默认）。"""
    rs = db.get(RuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="规则集不存在")
    if not rs.is_default:
        # 先把其他默认置为 False
        db.execute(update(RuleSet).where(RuleSet.is_default.is_(True)).values(is_default=False))
        rs.is_default = True
        db.commit()
        db.refresh(rs)
    return RuleSetOut.model_validate(rs)
