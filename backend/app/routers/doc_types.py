"""文档类型管理路由。

提供文档类型的完整 CRUD，以及规则导入后的新类型检测、样例文档 AI 分析功能。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DocumentType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doc-types", tags=["文档类型管理"])


# ============ Pydantic Schemas ============

class DocTypeOut(BaseModel):
    """文档类型输出模型。"""
    id: str
    name: str
    description: str | None = None
    key_fields: list[str] = []
    stamp_required: str | None = None
    business_meaning: str | None = None
    has_sample: bool = False
    source: str
    status: str
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


class DocTypeCreate(BaseModel):
    """创建文档类型请求。"""
    name: str
    description: str | None = None
    key_fields: list[str] = []
    stamp_required: str | None = None
    business_meaning: str | None = None
    source: str = "manual"


class DocTypeUpdate(BaseModel):
    """更新文档类型请求（所有字段可选）。"""
    name: str | None = None
    description: str | None = None
    key_fields: list[str] | None = None
    stamp_required: str | None = None
    business_meaning: str | None = None


class AnalyzeSampleResult(BaseModel):
    """样例文档分析结果。"""
    detected_name: str
    description: str = ""
    key_fields: list[str] = []
    stamp_required: str | None = None
    business_meaning: str = ""


class DocTypeListResponse(BaseModel):
    """文档类型列表响应。"""
    doc_types: list[DocTypeOut]
    total: int
    pending_count: int = 0


def _to_out(dt: DocumentType) -> DocTypeOut:
    return DocTypeOut(
        id=str(dt.id),
        name=dt.name,
        description=dt.description,
        key_fields=list(dt.key_fields) if dt.key_fields else [],
        stamp_required=dt.stamp_required,
        business_meaning=dt.business_meaning,
        has_sample=bool(dt.sample_file_path),
        source=dt.source,
        status=dt.status,
        created_at=str(dt.created_at) if dt.created_at else None,
        updated_at=str(dt.updated_at) if dt.updated_at else None,
    )


# ============ CRUD 端点 ============


@router.get("", response_model=DocTypeListResponse)
def list_doc_types(
    status: str | None = Query(None, description="筛选状态：active / pending_review"),
    source: str | None = Query(None, description="筛选来源：seed / rule_import / manual"),
    db: Session = Depends(get_db),
):
    """获取文档类型列表，支持按 status / source 过滤。"""
    query = select(DocumentType).order_by(DocumentType.name)

    if status:
        query = query.where(DocumentType.status == status)
    if source:
        query = query.where(DocumentType.source == source)

    rows = db.execute(query).scalars().all()

    # 同时查 pending 数量
    pending_count = db.execute(
        select(func.count(DocumentType.id)).where(DocumentType.status == "pending_review")
    ).scalar() or 0

    return DocTypeListResponse(
        doc_types=[_to_out(r) for r in rows],
        total=len(rows),
        pending_count=pending_count,
    )


@router.get("/{type_id}", response_model=DocTypeOut)
def get_doc_type(type_id: str, db: Session = Depends(get_db)):
    """获取单个文档类型详情。"""
    dt = db.execute(
        select(DocumentType).where(DocumentType.id == uuid.UUID(type_id))
    ).scalars().first()
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    return _to_out(dt)


@router.post("", response_model=DocTypeOut, status_code=201)
def create_doc_type(body: DocTypeCreate, db: Session = Depends(get_db)):
    """创建新的文档类型。"""
    # 检查重名
    existing = db.execute(
        select(DocumentType).where(DocumentType.name == body.name)
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail=f"文档类型「{body.name}」已存在")

    dt = DocumentType(
        name=body.name,
        description=body.description,
        key_fields=body.key_fields,
        stamp_required=body.stamp_required,
        business_meaning=body.business_meaning,
        source=body.source,
        status="active",
    )
    db.add(dt)
    db.commit()
    db.refresh(dt)
    logger.info("新增文档类型: %s (source=%s)", dt.name, dt.source)
    return _to_out(dt)


@router.put("/{type_id}", response_model=DocTypeOut)
def update_doc_type(type_id: str, body: DocTypeUpdate, db: Session = Depends(get_db)):
    """更新文档类型信息，如补充关键字段、业务含义等。"""
    from sqlalchemy.exc import IntegrityError

    dt = db.execute(
        select(DocumentType).where(DocumentType.id == uuid.UUID(type_id))
    ).scalars().first()
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")

    # 若要重命名，先检查重名
    if body.name and body.name != dt.name:
        existing = db.execute(
            select(DocumentType).where(
                DocumentType.name == body.name,
                DocumentType.id != dt.id,
            )
        ).scalars().first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"名称「{body.name}」已被其他文档类型占用（status={existing.status}）",
            )

    updated_fields = []
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(dt, field, val)
        updated_fields.append(field)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        logger.warning("更新文档类型 %s 时发生完整性错误: %s", dt.name, e)
        raise HTTPException(status_code=409, detail=f"数据完整性冲突：{e.orig}")
    db.refresh(dt)
    if updated_fields:
        logger.info("更新文档类型 %s: %s", dt.name, ", ".join(updated_fields))
    return _to_out(dt)


@router.delete("/{type_id}")
def delete_doc_type(type_id: str, db: Session = Depends(get_db)):
    """删除文档类型。"""
    dt = db.execute(
        select(DocumentType).where(DocumentType.id == uuid.UUID(type_id))
    ).scalars().first()
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    db.delete(dt)
    db.commit()
    logger.info("删除文档类型: %s", dt.name)
    return {"ok": True}


# ============ 状态变更端点 ============


@router.post("/{type_id}/confirm", response_model=DocTypeOut)
def confirm_doc_type(type_id: str, db: Session = Depends(get_db)):
    """确认新检测到的文档类型（pending_review → active）。"""
    dt = db.execute(
        select(DocumentType).where(DocumentType.id == uuid.UUID(type_id))
    ).scalars().first()
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    if dt.status != "pending_review":
        raise HTTPException(status_code=400, detail="只有待审核状态的类型可以确认")

    dt.status = "active"
    db.commit()
    db.refresh(dt)
    logger.info("确认文档类型: %s (source=%s)", dt.name, dt.source)
    return _to_out(dt)


@router.post("/{type_id}/reject")
def reject_doc_type(type_id: str, db: Session = Depends(get_db)):
    """拒绝/丢弃新检测到的文档类型。"""
    dt = db.execute(
        select(DocumentType).where(DocumentType.id == uuid.UUID(type_id))
    ).scalars().first()
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    if dt.status == "active":
        raise HTTPException(status_code=400, detail="已确认的类型不能丢弃")

    db.delete(dt)
    db.commit()
    logger.info("丢弃文档类型: %s", dt.name)
    return {"ok": True}


# ============ 样例文档分析端点 ============


@router.post("/analyze-sample", response_model=AnalyzeSampleResult)
async def analyze_sample_document(
    file: UploadFile = File(...),
    doc_type_hint: str | None = Query(None, description="可选的文档类型名称提示"),
    db: Session = Depends(get_db),
):
    """上传一个样例文档，由 AI 分析其文档类型、关键字段和业务含义。

    流程：提取文档文本 → 调用 LLM 分析 → 返回结构化结果。
    用户可在前端确认或编辑后保存为新文档类型。
    """
    import os
    from ..config import settings
    from ..llm_client import get_llm_client, LLMError

    # 保存上传文件
    upload_dir = os.path.join(settings.upload_root, "_sample_analysis")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{uuid.uuid4().hex}_{file.filename}")

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 提取文本（简单根据扩展名处理）
    ext = os.path.splitext(file.filename or "")[1].lower()
    text_content = ""
    try:
        if ext == ".pdf":
            try:
                import PyPDF2
                with open(file_path, "rb") as fh:
                    reader = PyPDF2.PdfReader(fh)
                    text_content = "\n".join(
                        page.extract_text() or "" for page in reader.pages
                    )
            except ImportError:
                text_content = "[PDF 文本提取需要 PyPDF2]"
        elif ext in (".txt", ".md"):
            text_content = content.decode("utf-8", errors="replace")[:8000]
        elif ext == ".docx":
            try:
                from docx import Document as DocxDoc
                docx = DocxDoc(file_path)
                text_content = "\n".join(p.text for p in docx.paragraphs)
            except ImportError:
                text_content = "[DOCX 文本提取需要 python-docx]"
        elif ext in (".png", ".jpg", ".jpeg"):
            text_content = "[图片文件，需 OCR 处理]"
        else:
            text_content = content.decode("utf-8", errors="replace")[:8000]
    except Exception as e:
        logger.warning("文本提取失败: %s", e)
        text_content = "[文本提取失败]"

    # 用 LLM 分析
    llm = get_llm_client()
    hint_text = f"（提示：该文档可能属于「{doc_type_hint}」类型）" if doc_type_hint else ""

    system_prompt = """你是一个单证分析专家。分析用户上传的文档样例，输出以下 JSON：

{
  "detected_name": "推测的文档类型名称（简洁无歧义，如"代理协议""出口报关单"）",
  "description": "对该文档类型功能的简短描述",
  "key_fields": ["字段1", "字段2", ...],
  "stamp_required": "用印要求描述，不要求则 null",
  "business_meaning": "该文档在贸易单证流程中承载的业务意义（1-3句话）"
}

规则：
1. detected_name 从文档内容、标题、格式综合推断
2. key_fields 列出该文档类型下应该提取的关键字段（如合同号、金额、日期等）
3. business_meaning 解释该文档在业务流程中的位置和作用
4. 如果文档内容不足以判断，基于文件名和常识做合理推测"""

    user_prompt = f"""请分析以下文档内容，判断其文档类型、关键字段和业务意义。

文件名：{file.filename}
{hint_text}

文档内容（前 6000 字符）：
---
{text_content[:6000]}
---

请输出 JSON。"""

    try:
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
    except (LLMError, ValueError) as e:
        logger.error("样例文档分析失败: %s", e)
        raise HTTPException(status_code=500, detail=f"AI 分析失败: {e}")

    result = AnalyzeSampleResult(
        detected_name=resp.get("detected_name", "未知类型"),
        description=resp.get("description", ""),
        key_fields=resp.get("key_fields", []),
        stamp_required=resp.get("stamp_required"),
        business_meaning=resp.get("business_meaning", ""),
    )

    logger.info(
        "样例文档分析完成: %s → 推测类型=%s, 字段数=%d",
        file.filename, result.detected_name, len(result.key_fields)
    )
    return result


# ============ 规则导入后检测新类型端点 ============


class DetectNewTypesRequest(BaseModel):
    """规则导入后检测新文档类型请求。"""
    rule_doc_types: list[str]


class NewDocTypeInfo(BaseModel):
    """新检测到的文档类型信息。"""
    name: str
    id: str | None = None
    is_new: bool = True


class DetectNewTypesResponse(BaseModel):
    """新类型检测响应。"""
    new_types: list[NewDocTypeInfo]
    total: int = 0


@router.post("/detect-from-rules", response_model=DetectNewTypesResponse)
def detect_new_types_from_rules(
    body: DetectNewTypesRequest,
    db: Session = Depends(get_db),
):
    """给定规则中提到的一组文档类型名称，检测其中哪些是新的（不在活跃列表中）。"""
    if not body.rule_doc_types:
        return DetectNewTypesResponse(new_types=[], total=0)

    # 查询已有活跃的文档类型名称
    existing = db.execute(
        select(DocumentType.name).where(DocumentType.status == "active")
    ).scalars().all()
    existing_set = set(existing)

    new_discovered = []
    for name in body.rule_doc_types:
        if name not in existing_set:
            # 检查是否已存在 pending_review 的记录
            pending = db.execute(
                select(DocumentType).where(
                    DocumentType.name == name,
                    DocumentType.status == "pending_review",
                )
            ).scalars().first()

            if pending:
                new_discovered.append(NewDocTypeInfo(name=name, id=str(pending.id), is_new=False))
            else:
                # 创建 pending_review 记录
                dt = DocumentType(
                    name=name,
                    source="rule_import",
                    status="pending_review",
                )
                db.add(dt)
                db.commit()
                db.refresh(dt)
                new_discovered.append(NewDocTypeInfo(name=name, id=str(dt.id), is_new=True))
                logger.info("规则导入发现新文档类型（待确认）: %s", name)

    return DetectNewTypesResponse(
        new_types=new_discovered,
        total=len(new_discovered),
    )
