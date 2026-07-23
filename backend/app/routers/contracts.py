"""合同与文档管理路由。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.contract import (
    ContractAliasUpdate,
    ContractBrief,
    ContractDetail,
    ContractUploadResponse,
    DocTypeUpdate,
)
from ..services import contract_service

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


@router.post("/upload", response_model=ContractUploadResponse)
async def upload_contract_folder(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> ContractUploadResponse:
    """上传合同文件夹（一个合同 = 一组文件）。"""
    try:
        return await contract_service.upload_contract_folder(db, files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


@router.get("", response_model=list[ContractBrief])
def list_contracts(db: Session = Depends(get_db)) -> list[ContractBrief]:
    """合同列表。"""
    return contract_service.list_contracts(db)


@router.get("/{contract_id}", response_model=ContractDetail)
def get_contract(contract_id: uuid.UUID, db: Session = Depends(get_db)) -> ContractDetail:
    """合同详情（含文件清单）。"""
    detail = contract_service.get_contract_detail(db, contract_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="合同不存在")
    return detail


@router.delete("/{contract_id}")
def delete_contract(contract_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """删除合同及其文件。"""
    if not contract_service.delete_contract(db, contract_id):
        raise HTTPException(status_code=404, detail="合同不存在")
    return {"success": True, "message": "合同已删除"}


@router.put("/{contract_id}/aliases", response_model=ContractBrief)
def update_contract_aliases(
    contract_id: uuid.UUID,
    payload: ContractAliasUpdate,
    db: Session = Depends(get_db),
) -> ContractBrief:
    """修正合同号归一化结果。"""
    contract = contract_service.update_contract_aliases(
        db, contract_id, payload.contract_no, payload.alias_list
    )
    if contract is None:
        raise HTTPException(status_code=404, detail="合同不存在")
    return ContractBrief.model_validate(contract)


@router.put("/documents/{doc_id}/doc-type")
def update_doc_type(
    doc_id: uuid.UUID,
    payload: DocTypeUpdate,
    db: Session = Depends(get_db),
) -> dict:
    """修正单个文件的业务类型。"""
    doc = contract_service.update_doc_type(db, doc_id, payload.doc_type)
    if doc is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {
        "success": True,
        "doc_id": str(doc.id),
        "doc_type": doc.doc_type,
        "is_required": doc.is_required,
    }
