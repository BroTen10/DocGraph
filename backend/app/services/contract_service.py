"""合同与文档管理服务。"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..constants import REQUIRED_DOC_TYPES
from ..models import Contract, Document, DocumentType
from ..schemas.contract import (
    ContractBrief,
    ContractDetail,
    ContractUploadResponse,
    DocumentBrief,
)
from .contract_normalizer import (
    extract_contract_numbers,
    merge_aliases,
    normalize_contract_no,
)
from .file_classifier import classify_file, get_file_type

logger = logging.getLogger(__name__)


def _safe_filename(filename: str) -> str:
    """清洗文件名，保留中文但移除路径分隔符。"""
    # 移除路径分隔符与危险字符
    bad = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    out = "".join("_" if c in bad else c for c in filename)
    return out.strip() or f"file_{uuid.uuid4().hex[:8]}"


def _load_doc_type_registry(db: Session) -> dict[str, bool]:
    """从 DocumentType 动态清单构建 name → is_required 注册表（active + pending_review）。
    批次 10 Phase B：新发现的类型无需改代码即可参与文件分类。"""
    rows = db.execute(
        select(DocumentType.name, DocumentType.is_required).where(
            DocumentType.status.in_(("active", "pending_review"))
        )
    ).all()
    return {name: bool(is_required) for name, is_required in rows}


def _contract_upload_dir(contract_no: str) -> Path:
    """合同文件存储目录：uploads/{contract_no}/"""
    root = settings.ensure_upload_root()
    safe = _safe_filename(contract_no)
    d = root / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


async def upload_contract_folder(
    db: Session,
    rule_set_id: uuid.UUID,
    files: list[UploadFile],
) -> ContractUploadResponse:
    """上传合同文件夹：保存所有文件、分类、识别合同号、归一化、写库。

    一个合同文件夹 = 一个合同 = 一条 Contract 记录 + N 条 Document 记录。
    所有文件归属到指定 rule_set_id 命名空间下。
    """
    if not files:
        raise ValueError("未接收到任何文件")

    # 动态文档类型注册表（批次 10 Phase B）
    registry = _load_doc_type_registry(db)

    # 1. 第一遍：保存文件 + 收集文件名用于合同号识别
    saved: list[tuple[str, Path, str, bool]] = []  # (orig_name, path, doc_type, is_required)
    contract_candidates: list[str] = []
    for f in files:
        orig_name = f.filename or f"file_{uuid.uuid4().hex[:8]}"
        safe_name = _safe_filename(orig_name)
        # 先存到临时目录，确定合同号后再移动
        tmp_dir = settings.ensure_upload_root() / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{uuid.uuid4().hex}_{safe_name}"
        content = await f.read()
        tmp_path.write_bytes(content)
        doc_type, is_required = classify_file(orig_name, registry=registry)
        # 从文件名提取合同号
        contract_candidates.extend(extract_contract_numbers(orig_name))
        saved.append((orig_name, tmp_path, doc_type, is_required))

    # 2. 归一化合同号
    canonical, alias_list = normalize_contract_no(contract_candidates)
    if not canonical:
        # 文件名中无合同号，生成一个临时合同号
        canonical = f"UNKNOWN-{uuid.uuid4().hex[:8]}"
        alias_list = []

    # 3. 创建 Contract 记录（带 rule_set_id）
    contract = Contract(
        rule_set_id=rule_set_id,
        contract_no=canonical,
        alias_list=alias_list,
        status="uploaded",
    )
    db.add(contract)
    db.flush()  # 拿到 contract.id

    # 4. 把文件移动到合同目录，创建 Document 记录
    target_dir = _contract_upload_dir(canonical)
    classified_list: list[dict] = []
    for orig_name, tmp_path, doc_type, is_required in saved:
        safe_name = _safe_filename(orig_name)
        final_path = target_dir / safe_name
        # 重名追加序号
        if final_path.exists():
            stem = final_path.stem
            suffix = final_path.suffix
            final_path = target_dir / f"{stem}_{uuid.uuid4().hex[:4]}{suffix}"
        shutil.move(str(tmp_path), str(final_path))

        doc = Document(
            contract_id=contract.id,
            file_name=orig_name,
            file_path=str(final_path),
            file_type=get_file_type(orig_name),
            doc_type=doc_type,
            is_required=is_required,
            ocr_status="pending",
        )
        db.add(doc)
        classified_list.append(
            {
                "file_name": orig_name,
                "doc_type": doc_type,
                "is_required": is_required,
                "file_type": doc.file_type,
            }
        )

    db.commit()
    db.refresh(contract)

    return ContractUploadResponse(
        contract_id=contract.id,
        contract_no=contract.contract_no,
        alias_list=contract.alias_list,
        file_count=len(saved),
        classified=classified_list,
        message=f"成功上传 {len(saved)} 个文件，合同号 {contract.contract_no}",
    )


def list_contracts(
    db: Session, rule_set_id: Optional[uuid.UUID] = None
) -> list[ContractBrief]:
    """合同列表（含文件数），可按规则集过滤。"""
    stmt = (
        select(
            Contract,
            func.count(Document.id).label("file_count"),
        )
        .outerjoin(Document, Document.contract_id == Contract.id)
        .group_by(Contract.id)
        .order_by(Contract.upload_time.desc())
    )
    if rule_set_id is not None:
        stmt = stmt.where(Contract.rule_set_id == rule_set_id)
    rows = db.execute(stmt).all()
    out: list[ContractBrief] = []
    for contract, file_count in rows:
        b = ContractBrief.model_validate(contract)
        b.file_count = file_count or 0
        out.append(b)
    return out


def get_contract_detail(db: Session, contract_id: uuid.UUID) -> Optional[ContractDetail]:
    """合同详情（含文件清单）。"""
    contract = db.get(Contract, contract_id)
    if contract is None:
        return None
    docs = [
        DocumentBrief.model_validate(d) for d in sorted(contract.documents, key=lambda x: x.file_name)
    ]
    detail = ContractDetail.model_validate(contract)
    detail.documents = docs
    detail.file_count = len(docs)
    return detail


def delete_contract(db: Session, contract_id: uuid.UUID) -> bool:
    """删除合同及其文件。"""
    contract = db.get(Contract, contract_id)
    if contract is None:
        return False
    contract_no = contract.contract_no
    # 删除文件目录
    target_dir = settings.ensure_upload_root() / _safe_filename(contract_no)
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    db.delete(contract)
    db.commit()
    return True


def update_doc_type(
    db: Session, doc_id: uuid.UUID, doc_type: str
) -> Optional[Document]:
    """修正单个文件的业务类型。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        return None
    doc.doc_type = doc_type
    doc.is_required = doc_type in REQUIRED_DOC_TYPES
    db.commit()
    db.refresh(doc)
    return doc


def update_contract_aliases(
    db: Session,
    contract_id: uuid.UUID,
    contract_no: str,
    alias_list: list[str],
) -> Optional[Contract]:
    """修正合同号归一化结果。"""
    contract = db.get(Contract, contract_id)
    if contract is None:
        return None
    old_no = contract.contract_no
    # 合并别名（保留原有 + 用户传入）
    merged = merge_aliases(contract.alias_list or [], alias_list or [])
    # 确保新主号在别名中
    if contract_no not in merged:
        merged.insert(0, contract_no)
    contract.contract_no = contract_no
    contract.alias_list = merged
    # 重命名文件目录
    if old_no != contract_no:
        old_dir = settings.ensure_upload_root() / _safe_filename(old_no)
        new_dir = settings.ensure_upload_root() / _safe_filename(contract_no)
        if old_dir.exists():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            old_dir.rename(new_dir)
        # 更新所有文档路径
        for doc in contract.documents:
            old_path = Path(doc.file_path)
            new_path = new_dir / old_path.name
            doc.file_path = str(new_path)
    db.commit()
    db.refresh(contract)
    return contract
