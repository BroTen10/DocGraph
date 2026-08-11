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
    """删除规则集并清空其全部数据（级联删除关联的规则/快照/合同/文档/审查结果，
    并清理 Neo4j 图谱节点与磁盘上该规则集的上传文件）。此操作不可恢复。"""
    import logging
    import shutil
    from pathlib import Path

    from ..config import settings
    from ..models import Contract, Document, RuleSnapshot
    from ..neo4j_client import get_neo4j_client

    logger = logging.getLogger(__name__)

    rs = db.get(RuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(status_code=404, detail="规则集不存在")

    # 1) 收集该规则集全部图谱 graph_id（当前快照 + 历史快照）
    graph_ids = list(
        db.execute(
            select(RuleSnapshot.graph_id)
            .where(
                RuleSnapshot.rule_set_id == rs.id,
                RuleSnapshot.graph_id.isnot(None),
            )
        ).scalars().all()
    )

    # 2) 收集该规则集上传文件所在目录（按 Document.file_path 父目录去重）
    upload_root = Path(settings.upload_root).resolve()
    file_dirs = {
        str(Path(p).parent.resolve())
        for p in db.execute(
            select(Document.file_path)
            .join(Contract, Contract.id == Document.contract_id)
            .where(Contract.rule_set_id == rs.id)
        ).scalars().all()
        if p
    }

    # 3) 清理 Neo4j 图谱节点（尽力而为：Neo4j 不可用时仍完成删除）
    graphs_cleared = 0
    try:
        neo4j = get_neo4j_client()
        for gid in graph_ids:
            try:
                neo4j.clear_graph(gid)
                graphs_cleared += 1
            except Exception:
                logger.exception("清理规则集 %s 的图谱 %s 失败", rs.id, gid)
    except Exception:
        logger.exception("Neo4j 不可用，跳过规则集 %s 的图谱清理", rs.id)

    # 4) 清理磁盘上传文件（仅删除上传根目录内的目录，防止误删其他路径）
    file_dirs_removed = 0
    for d in file_dirs:
        p = Path(d)
        try:
            if p.is_relative_to(upload_root) and p.exists():
                shutil.rmtree(p)
                file_dirs_removed += 1
        except Exception:
            logger.exception("清理规则集 %s 的上传目录 %s 失败", rs.id, d)

    # 5) 删除规则集（Postgres 级联清理规则/快照/合同/文档/审查任务与结果等）
    db.delete(rs)
    db.commit()

    return {
        "success": True,
        "message": "规则集已删除，全部关联数据（规则/快照/文档/审查结果/图谱/上传文件）已清空",
        "graphs_cleared": graphs_cleared,
        "file_dirs_removed": file_dirs_removed,
    }


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
