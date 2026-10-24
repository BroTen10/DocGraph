"""文档类型管理路由。

提供文档类型的完整 CRUD，以及规则导入后的新类型检测、样例文档 AI 分析功能。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthContext, require_permission
from ..database import get_db
from ..models import DocumentType
from ..tenant_scope import tenant_get

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doc-types", tags=["文档类型管理"])


# ============ Pydantic Schemas ============

class DocTypeOut(BaseModel):
    """文档类型输出模型。"""
    id: str
    name: str
    description: str | None = None
    key_fields: list[str] = []
    aliases: list[str] = []
    field_aliases: dict = {}
    stamp_required: str | None = None
    business_meaning: str | None = None
    # OCR 增强点（一期）：类型专属抽取指令 + 备注类补充字段
    ocr_extract_instructions: str | None = None
    ocr_extra_fields: list[str] = []
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
    aliases: list[str] = []
    field_aliases: dict = {}
    stamp_required: str | None = None
    business_meaning: str | None = None
    ocr_extract_instructions: str | None = None
    ocr_extra_fields: list[str] = []
    source: str = "manual"


class DocTypeUpdate(BaseModel):
    """更新文档类型请求（所有字段可选）。"""
    name: str | None = None
    description: str | None = None
    key_fields: list[str] | None = None
    aliases: list[str] | None = None
    field_aliases: dict | None = None
    stamp_required: str | None = None
    business_meaning: str | None = None
    ocr_extract_instructions: str | None = None
    ocr_extra_fields: list[str] | None = None


class OcrProfileOut(BaseModel):
    """某文档类型的 OCR 增强点与最终字段清单（供前端预览/重跑范围选择）。"""
    doc_type: str
    base_fields: list[str] = []
    extra_fields: list[str] = []
    instructions: str | None = None
    field_list: list[str] = []


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
        aliases=list(dt.aliases or []),
        field_aliases=dict(dt.field_aliases or {}),
        stamp_required=dt.stamp_required,
        business_meaning=dt.business_meaning,
        ocr_extract_instructions=dt.ocr_extract_instructions,
        ocr_extra_fields=list(dt.ocr_extra_fields or []),
        has_sample=bool(dt.sample_file_path),
        source=dt.source,
        status=dt.status,
        created_at=str(dt.created_at) if dt.created_at else None,
        updated_at=str(dt.updated_at) if dt.updated_at else None,
    )


def _tenant_document_type(
    db: Session,
    tenant_id: uuid.UUID,
    type_id: str,
) -> DocumentType | None:
    try:
        parsed_id = uuid.UUID(type_id)
    except ValueError:
        return None
    return tenant_get(db, DocumentType, parsed_id, tenant_id)


# ============ CRUD 端点 ============


@router.get("", response_model=DocTypeListResponse)
def list_doc_types(
    status: str | None = Query(None, description="筛选状态：active / pending_review"),
    source: str | None = Query(None, description="筛选来源：seed / rule_import / manual"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.view")),
):
    """获取文档类型列表，支持按 status / source 过滤。"""
    assert ctx.tenant is not None
    query = (
        select(DocumentType)
        .where(DocumentType.tenant_id == ctx.tenant.id)
        .order_by(DocumentType.name)
    )

    if status:
        query = query.where(DocumentType.status == status)
    if source:
        query = query.where(DocumentType.source == source)

    rows = db.execute(query).scalars().all()

    # 同时查 pending 数量
    pending_count = db.execute(
        select(func.count(DocumentType.id)).where(
            DocumentType.tenant_id == ctx.tenant.id,
            DocumentType.status == "pending_review",
        )
    ).scalar() or 0

    return DocTypeListResponse(
        doc_types=[_to_out(r) for r in rows],
        total=len(rows),
        pending_count=pending_count,
    )


@router.get("/{type_id}", response_model=DocTypeOut)
def get_doc_type(
    type_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.view")),
):
    """获取单个文档类型详情。"""
    assert ctx.tenant is not None
    dt = _tenant_document_type(db, ctx.tenant.id, type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    return _to_out(dt)


@router.get("/{type_id}/ocr-profile", response_model=OcrProfileOut)
def get_ocr_profile(
    type_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.view")),
):
    """获取该文档类型当前的 OCR 增强点与最终字段清单（基础字段 + 备注类补充字段）。

    前端在「文档类型」页编辑增强点后可用它核对最终会送进提示词的字段清单。
    """
    from ..services.ocr_service import OCR_PROFILE_TYPE, resolve_ocr_profile

    assert ctx.tenant is not None
    dt = _tenant_document_type(db, ctx.tenant.id, type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    data = resolve_ocr_profile(db, ctx.tenant.id, dt.name, OCR_PROFILE_TYPE)
    return OcrProfileOut(
        doc_type=dt.name,
        base_fields=data["base_fields"],
        extra_fields=data["extra_fields"],
        instructions=data["instructions"],
        field_list=data["field_list"],
    )


@router.post("", response_model=DocTypeOut, status_code=201)
def create_doc_type(
    body: DocTypeCreate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.manage")),
):
    """创建新的文档类型。"""
    from ..services.doc_normalizer import normalize_doc_type

    assert ctx.tenant is not None
    # 检查重名
    existing = db.execute(
        select(DocumentType).where(
            DocumentType.tenant_id == ctx.tenant.id,
            DocumentType.name == body.name,
        )
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail=f"文档类型「{body.name}」已存在")
    # 批次 11：别名即类型 → 拒绝，避免双轨繁殖
    canonical = normalize_doc_type(db, body.name, tenant_id=ctx.tenant.id)
    if canonical and canonical != body.name:
        raise HTTPException(
            status_code=409,
            detail=f"「{body.name}」是「{canonical}」的别名，请使用规范名或在其别名中维护",
        )

    dt = DocumentType(
        tenant_id=ctx.tenant.id,
        name=body.name,
        description=body.description,
        key_fields=body.key_fields,
        aliases=body.aliases,
        field_aliases=body.field_aliases,
        stamp_required=body.stamp_required,
        business_meaning=body.business_meaning,
        ocr_extract_instructions=body.ocr_extract_instructions,
        ocr_extra_fields=body.ocr_extra_fields,
        source=body.source,
        status="active",
    )
    db.add(dt)
    db.commit()
    db.refresh(dt)
    logger.info("新增文档类型: %s (source=%s)", dt.name, dt.source)
    return _to_out(dt)


@router.put("/{type_id}", response_model=DocTypeOut)
def update_doc_type(
    type_id: str,
    body: DocTypeUpdate,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.manage")),
):
    """更新文档类型信息，如补充关键字段、业务含义等。"""
    from sqlalchemy.exc import IntegrityError
    from ..services.doc_normalizer import normalize_doc_type

    assert ctx.tenant is not None
    dt = _tenant_document_type(db, ctx.tenant.id, type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")

    # 若要重命名，先检查重名
    if body.name and body.name != dt.name:
        canonical = normalize_doc_type(db, body.name, tenant_id=ctx.tenant.id)
        if canonical and canonical != body.name:
            raise HTTPException(
                status_code=409,
                detail=f"「{body.name}」是「{canonical}」的别名，不能作为独立类型名",
            )
        existing = db.execute(
            select(DocumentType).where(
                DocumentType.tenant_id == ctx.tenant.id,
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
def delete_doc_type(
    type_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.manage")),
):
    """删除文档类型。"""
    assert ctx.tenant is not None
    dt = _tenant_document_type(db, ctx.tenant.id, type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    db.delete(dt)
    db.commit()
    logger.info("删除文档类型: %s", dt.name)
    return {"ok": True}


# ============ 状态变更端点 ============


@router.post("/{type_id}/confirm", response_model=DocTypeOut)
def confirm_doc_type(
    type_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.manage")),
):
    """确认新检测到的文档类型（pending_review → active）。"""
    from ..services.doc_normalizer import normalize_doc_type

    assert ctx.tenant is not None
    dt = _tenant_document_type(db, ctx.tenant.id, type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    if dt.status != "pending_review":
        raise HTTPException(status_code=400, detail="只有待审核状态的类型可以确认")
    canonical = normalize_doc_type(db, dt.name, tenant_id=ctx.tenant.id)
    if canonical and canonical != dt.name:
        # 该名称已是某规范类型的别名：不应作为独立类型确认，直接置为 rejected
        dt.status = "rejected"
        db.commit()
        logger.info("文档类型「%s」是「%s」的别名，确认时自动拒绝", dt.name, canonical)
        return _to_out(dt)

    dt.status = "active"
    db.commit()
    db.refresh(dt)
    logger.info("确认文档类型: %s (source=%s)", dt.name, dt.source)
    return _to_out(dt)


@router.post("/{type_id}/reject")
def reject_doc_type(
    type_id: str,
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.manage")),
):
    """拒绝/丢弃新检测到的文档类型。"""
    assert ctx.tenant is not None
    dt = _tenant_document_type(db, ctx.tenant.id, type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="文档类型不存在")
    if dt.status == "active":
        raise HTTPException(status_code=400, detail="已确认的类型不能丢弃")

    db.delete(dt)
    db.commit()
    logger.info("丢弃文档类型: %s", dt.name)
    return {"ok": True}


# ============ 样例文档分析端点 ============

_SAMPLE_ANALYSIS_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
}


def _safe_sample_upload_path(
    tenant_id: uuid.UUID,
    filename: str | None,
) -> tuple[Path, str]:
    """返回租户目录内的随机临时文件路径与可展示文件名。"""
    normalized_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(normalized_name).suffix.lower()
    if extension not in _SAMPLE_ANALYSIS_EXTENSIONS:
        raise HTTPException(status_code=400, detail="暂不支持该样例文件类型")

    from ..config import settings

    upload_dir = (
        Path(settings.upload_root) / str(tenant_id) / "_sample_analysis"
    ).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = (upload_dir / f"{uuid.uuid4().hex}{extension}").resolve()
    if file_path.parent != upload_dir:
        raise HTTPException(status_code=400, detail="非法样例文件名")
    return file_path, normalized_name


@router.post("/analyze-sample", response_model=AnalyzeSampleResult)
async def analyze_sample_document(
    file: UploadFile = File(...),
    doc_type_hint: str | None = Query(None, description="可选的文档类型名称提示"),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(require_permission("doc_types.manage")),
):
    """上传一个样例文档，由 AI 分析其文档类型、关键字段和业务含义。

    流程：提取文档文本 → 调用 LLM 分析 → 返回结构化结果。
    用户可在前端确认或编辑后保存为新文档类型。
    """
    from ..llm_client import get_llm_client, LLMError

    assert ctx.tenant is not None
    file_path, display_name = _safe_sample_upload_path(
        ctx.tenant.id,
        file.filename,
    )
    content = await file.read()
    try:
        file_path.write_bytes(content)
        ext = file_path.suffix
        text_content = ""
        try:
            if ext == ".pdf":
                try:
                    import PyPDF2

                    with file_path.open("rb") as fh:
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
        except Exception as e:
            logger.warning("文本提取失败: %s", e)
            text_content = "[文本提取失败]"

        from ..services.settings_service import get_prompt

        llm = get_llm_client()
        hint_text = (
            f"（提示：该文档可能属于「{doc_type_hint}」类型）"
            if doc_type_hint
            else ""
        )
        system_prompt = get_prompt(db, ctx.tenant.id, "doc_analyze.system")
        user_prompt = get_prompt(db, ctx.tenant.id, "doc_analyze.user").format(
            file_name=display_name,
            hint=hint_text,
            text=text_content[:6000],
        )

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
            display_name,
            result.detected_name,
            len(result.key_fields),
        )
        return result
    finally:
        file_path.unlink(missing_ok=True)


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
    ctx: AuthContext = Depends(require_permission("doc_types.manage")),
):
    """给定规则中提到的一组文档类型名称，检测其中哪些是新的（不在活跃列表中）。"""
    if not body.rule_doc_types:
        return DetectNewTypesResponse(new_types=[], total=0)

    # 查询已有活跃的文档类型名称
    assert ctx.tenant is not None
    existing = db.execute(
        select(DocumentType.name).where(
            DocumentType.tenant_id == ctx.tenant.id,
            DocumentType.status == "active",
        )
    ).scalars().all()
    existing_set = set(existing)

    new_discovered = []
    for name in body.rule_doc_types:
        if name not in existing_set:
            # 检查是否已存在 pending_review 的记录
            pending = db.execute(
                select(DocumentType).where(
                    DocumentType.tenant_id == ctx.tenant.id,
                    DocumentType.name == name,
                    DocumentType.status == "pending_review",
                )
            ).scalars().first()

            if pending:
                new_discovered.append(NewDocTypeInfo(name=name, id=str(pending.id), is_new=False))
            else:
                # 创建 pending_review 记录
                dt = DocumentType(
                    tenant_id=ctx.tenant.id,
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
