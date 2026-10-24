"""OCR 与文本提取服务。

策略：
- 文本型 PDF：pdfplumber 直接提取文本（不调 OCR）
- 扫描型 PDF：用 PyMuPDF 把每页渲染成图片，逐页调 DeepSeek 多模态模型
- 图片（PNG/JPG）：直接调 DeepSeek 多模态模型
- DOCX：python-docx 提取段落文本
- 提取结果统一为 {text, has_stamp, fields, confidence, success}
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from ..constants import FIELD_TEMPLATES
from ..llm_client import LLMError, get_llm_client
from ..models import Document
from ..ocr_client import get_ocr_client
from .ocr_layout_service import extract_layout_lines
from .settings_service import get_prompt

logger = logging.getLogger(__name__)


def resolve_field_template(
    db,
    tenant_id: uuid.UUID | None,
    doc_type: str,
) -> list[str]:
    """字段模板解析（批次 10 Phase B）：DocumentType.key_fields 优先（动态注册/规则导入预填），
    constants.FIELD_TEMPLATES 兜底。未注册类型返回空（走自由提取兜底）。"""
    try:
        from sqlalchemy import select
        from ..models import DocumentType
        dt = db.execute(
            select(DocumentType).where(
                DocumentType.tenant_id == tenant_id,
                DocumentType.name == doc_type,
            )
        ).scalars().first()
        if dt is not None and dt.key_fields:
            return list(dt.key_fields)
    except Exception:
        logger.warning("解析字段模板失败，回退 constants: %s", doc_type, exc_info=True)
    return FIELD_TEMPLATES.get(doc_type, [])


# ============ 一期：OCR 增强点（类型专属指令 + 备注类补充字段） ============

# 生效范围：默认使用类型增强点；base = 仅基础字段模板（用于对比重跑/排查）
OCR_PROFILE_TYPE = "type"
OCR_PROFILE_BASE = "base"


def compose_field_list(base_fields: list[str], extra_fields: list[str]) -> list[str]:
    """合并字段清单：基础字段在前，补充字段去重后追加，保持原顺序。"""
    out: list[str] = []
    for name in list(base_fields or []) + list(extra_fields or []):
        name = str(name).strip()
        if name and name not in out:
            out.append(name)
    return out


def resolve_ocr_profile(
    db,
    tenant_id: uuid.UUID | None,
    doc_type: str,
    profile: str = OCR_PROFILE_TYPE,
) -> dict:
    """解析某文档类型本次生效的 OCR 增强点。

    profile="type"（默认）：基础字段 + 该类型的备注类补充字段 + 专属指令；
    profile="base"：仅基础字段模板，忽略增强点（未配置增强点时两者等价）。

    Returns:
        {"profile", "base_fields", "extra_fields", "instructions", "field_list"}
    """
    base_fields = list(resolve_field_template(db, tenant_id, doc_type) or [])
    extra_fields: list[str] = []
    instructions: str | None = None

    if profile != OCR_PROFILE_BASE and db is not None:
        try:
            from sqlalchemy import select
            from ..models import DocumentType

            dt = db.execute(
                select(DocumentType).where(
                    DocumentType.tenant_id == tenant_id,
                    DocumentType.name == doc_type,
                )
            ).scalars().first()
            if dt is not None:
                extra_fields = [
                    str(f).strip() for f in (dt.ocr_extra_fields or []) if str(f).strip()
                ]
                raw = (dt.ocr_extract_instructions or "").strip()
                instructions = raw or None
        except Exception:
            logger.warning("解析 OCR 增强点失败（按基础模板处理）: %s", doc_type, exc_info=True)

    return {
        "profile": OCR_PROFILE_BASE if profile == OCR_PROFILE_BASE else OCR_PROFILE_TYPE,
        "base_fields": base_fields,
        "extra_fields": extra_fields,
        "instructions": instructions,
        "field_list": compose_field_list(base_fields, extra_fields),
    }


def render_extra_instructions(
    db,
    tenant_id: uuid.UUID | None,
    doc_type: str,
    profile: dict,
) -> str | None:
    """把类型专属指令套进可配置的包装模板；无指令时返回 None（不注入）。"""
    raw = (profile or {}).get("instructions")
    if not raw:
        return None
    tpl = get_prompt(db, tenant_id, "ocr.image.extra_instructions")
    try:
        return tpl.format(doc_type=doc_type, extra_instructions=raw)
    except (KeyError, IndexError, ValueError):
        # 模板被改坏时不阻断识别，退回指令正文
        logger.warning("OCR 增强点包装模板占位符异常，已退回指令正文")
        return str(raw)


def build_applied_config(profile: str, profiles_by_type: dict[str, dict]) -> dict:
    """生成 OCR 任务生效快照（FR-5），供追溯「这次识别用了哪版增强点」。"""
    return {
        "profile": OCR_PROFILE_BASE if profile == OCR_PROFILE_BASE else OCR_PROFILE_TYPE,
        "doc_types": {
            name: {
                "base_fields": list(p.get("base_fields") or []),
                "extra_fields": list(p.get("extra_fields") or []),
                "instructions": p.get("instructions"),
                "field_list": list(p.get("field_list") or []),
            }
            for name, p in (profiles_by_type or {}).items()
        },
    }


def _extract_text_pdf(pdf_path: str) -> str:
    """文本型 PDF 直接提取文本。"""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
    return "\n".join(parts)


def _is_scanned_pdf(pdf_path: str, min_chars: int = 50) -> bool:
    """判断 PDF 是否为扫描件（文本提取几乎为空）。"""
    try:
        text = _extract_text_pdf(pdf_path)
        return len(text.strip()) < min_chars
    except Exception:
        return True  # 提取失败按扫描件处理


def _pdf_pages_to_images(pdf_path: str, dpi: int = 200) -> list[bytes]:
    """把 PDF 每页渲染成 PNG 字节流。"""
    import fitz  # PyMuPDF

    images: list[bytes] = []
    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))
    finally:
        doc.close()
    return images


def _extract_docx(docx_path: str) -> str:
    """从 DOCX 提取文本。"""
    from docx import Document as DocxDocument

    doc = DocxDocument(docx_path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _build_layout(pages: list[list[dict]]) -> dict:
    """把逐页文本行坐标包装为统一的 OCR layout 结构。"""
    return {
        "version": 1,
        "pages": [
            {"page": index + 1, "lines": lines}
            for index, lines in enumerate(pages)
            if lines
        ],
    }


def _has_extracted_fields(fields: dict | None) -> bool:
    """判断是否包含除内部推断类型以外的真实字段值。"""
    if not fields:
        return False
    return any(
        key != "__inferred_doc_type__" and value not in (None, "")
        for key, value in fields.items()
    )


def _ensure_fields(
    result: dict,
    doc_type: str,
    field_template: list[str],
    db=None,
    extra_instructions: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """多模态返回文字但漏掉字段时，用已识别文本再走一次字段提取。"""
    if not result.get("success") or _has_extracted_fields(result.get("fields")):
        return result
    text = str(result.get("text") or "").strip()
    if not text:
        return result

    fallback = _llm_extract_fields_from_text(
        text,
        doc_type,
        field_template,
        db=db,
        extra_instructions=extra_instructions,
        tenant_id=tenant_id,
    )
    if _has_extracted_fields(fallback.get("fields")):
        result["fields"] = fallback.get("fields") or {}
        if result.get("has_stamp") is None:
            result["has_stamp"] = fallback.get("has_stamp")
        if not result.get("confidence"):
            result["confidence"] = fallback.get("confidence", 0.0)
        logger.info("多模态 OCR 未返回字段，已从识别文本补提字段: %s", doc_type)
    return result


def _llm_extract_fields_from_text(
    text: str,
    doc_type: str,
    field_template: list[str],
    db=None,
    extra_instructions: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """对文本型 PDF/DOCX，调用 LLM 提取结构化字段。

    有 field_template 时按模板提取；
    无 field_template 时让模型推断文档类型 + 自由提取结构化字段。

    返回 {fields: {...}, has_stamp: bool|null, confidence: float}
    """
    if not text.strip():
        return {"fields": {}, "has_stamp": None, "confidence": 0.0}

    llm = get_llm_client()

    if not field_template:
        # 自由提取模式：无预定义字段模板
        system_prompt = get_prompt(db, tenant_id, "ocr.text.system_free")
        user_prompt = get_prompt(db, tenant_id, "ocr.text.user_free").format(
            text=text[:4000],
        )
    else:
        system_prompt = get_prompt(db, tenant_id, "ocr.text.system_template")
        user_prompt = get_prompt(db, tenant_id, "ocr.text.user_template").format(
            doc_type=doc_type,
            field_list=", ".join(field_template),
            text=text[:4000],
        )
    if extra_instructions and str(extra_instructions).strip():
        user_prompt = f"{user_prompt}\n{str(extra_instructions).strip()}"

    try:
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            disable_thinking=True,
        )
        fields = resp.get("fields", {}) or {}
        inferred = resp.get("inferred_doc_type") or ""
        if inferred:
            fields["__inferred_doc_type__"] = inferred
        return {
            "fields": fields,
            "has_stamp": resp.get("has_stamp"),
            "confidence": float(resp.get("confidence", 0.0)),
        }
    except LLMError as e:
        logger.warning("LLM 字段提取失败 [%s]: %s", doc_type, e)
        return {"fields": {}, "has_stamp": None, "confidence": 0.0}


def process_document(
    doc: Document,
    key_fields: list[str] | None = None,
    db=None,
    extra_instructions: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """处理单个文档：根据类型选择 OCR 或文本提取，回写 doc 记录。

    Returns:
        {
            "success": bool,
            "text": str,
            "has_stamp": bool | None,
            "fields": dict,
            "confidence": float,
            "error": str (仅失败时)
        }
    """
    path = Path(doc.file_path)
    if not path.exists():
        return {"success": False, "error": f"文件不存在: {doc.file_path}"}

    field_template = key_fields if key_fields is not None else FIELD_TEMPLATES.get(doc.doc_type, [])

    try:
        if doc.file_type == "pdf":
            if _is_scanned_pdf(str(path)):
                # 扫描型 PDF：逐页 OCR
                return _ocr_scanned_pdf(
                    str(path), doc.doc_type, field_template, db=db,
                    extra_instructions=extra_instructions,
                    tenant_id=tenant_id,
                )
            else:
                # 文本型 PDF：直接提取 + LLM 字段提取
                text = _extract_text_pdf(str(path))
                ext = _llm_extract_fields_from_text(
                    text, doc.doc_type, field_template, db=db,
                    extra_instructions=extra_instructions,
                    tenant_id=tenant_id,
                )
                return {
                    "success": True,
                    "text": text,
                    "has_stamp": ext["has_stamp"],
                    "fields": ext["fields"],
                    "confidence": ext["confidence"],
                }
        elif doc.file_type in ("png", "jpg"):
            return _ensure_fields(
                _ocr_image(
                    str(path), doc.doc_type, field_template, db=db,
                    extra_instructions=extra_instructions,
                    tenant_id=tenant_id,
                ),
                doc.doc_type,
                field_template,
                db=db,
                extra_instructions=extra_instructions,
                tenant_id=tenant_id,
            )
        elif doc.file_type == "docx":
            text = _extract_docx(str(path))
            ext = _llm_extract_fields_from_text(
                text, doc.doc_type, field_template, db=db,
                extra_instructions=extra_instructions,
                tenant_id=tenant_id,
            )
            return {
                "success": True,
                "text": text,
                "has_stamp": ext["has_stamp"],
                "fields": ext["fields"],
                "confidence": ext["confidence"],
            }
        else:
            return {"success": False, "error": f"不支持的文件类型: {doc.file_type}"}
    except Exception as e:
        logger.error("处理文档 %s 失败: %s", doc.file_name, e, exc_info=True)
        return {"success": False, "error": f"处理失败: {e}"}


def _ocr_image(
    image_path: str,
    doc_type: str,
    field_template: list[str],
    db=None,
    extra_instructions: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """对单张图片调用 DeepSeek 多模态模型 OCR。"""
    ocr = get_ocr_client()
    result = ocr.recognize(
        image_path,
        doc_type_hint=doc_type,
        field_template=field_template,
        system_prompt=get_prompt(db, tenant_id, "ocr.image.system"),
        fields_hint=get_prompt(db, tenant_id, "ocr.image.fields_hint"),
        infer_hint=get_prompt(db, tenant_id, "ocr.image.infer_hint"),
        infer_hint_free=get_prompt(db, tenant_id, "ocr.image.infer_hint_free"),
        extra_instructions=extra_instructions,
    )
    if result.get("success"):
        result["layout"] = _build_layout([extract_layout_lines(image_path)])
    return result


def _ocr_scanned_pdf(
    pdf_path: str,
    doc_type: str,
    field_template: list[str],
    db=None,
    extra_instructions: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """扫描型 PDF：逐页转图片 OCR，合并结果。"""
    import tempfile

    ocr = get_ocr_client()
    pages = _pdf_pages_to_images(pdf_path)
    if not pages:
        return {"success": False, "error": "PDF 无页面"}

    all_text: list[str] = []
    page_layouts: list[list[dict]] = []
    merged_fields: dict = {}
    has_stamp_any: Optional[bool] = None
    confidences: list[float] = []
    success_pages = 0

    with tempfile.TemporaryDirectory() as tmp:
        for i, page_bytes in enumerate(pages):
            tmp_img = Path(tmp) / f"page_{i}.png"
            tmp_img.write_bytes(page_bytes)
            r = ocr.recognize(
                str(tmp_img),
                doc_type_hint=doc_type,
                field_template=field_template,
                system_prompt=get_prompt(db, tenant_id, "ocr.image.system"),
                fields_hint=get_prompt(db, tenant_id, "ocr.image.fields_hint"),
                infer_hint=get_prompt(db, tenant_id, "ocr.image.infer_hint"),
                infer_hint_free=get_prompt(db, tenant_id, "ocr.image.infer_hint_free"),
                extra_instructions=extra_instructions,
            )
            if r.get("success"):
                success_pages += 1
                all_text.append(r.get("text", ""))
                page_layouts.append(extract_layout_lines(str(tmp_img)))
                # 多页合并：跳过空值，避免某页未识别出的 null 覆盖其他页已提取的值
                for k, v in (r.get("fields", {}) or {}).items():
                    if v is None or v == "":
                        continue
                    merged_fields[k] = v
                if r.get("has_stamp") is True:
                    has_stamp_any = True
                elif r.get("has_stamp") is False and has_stamp_any is not True:
                    has_stamp_any = False
                confidences.append(float(r.get("confidence", 0.0)))

    if success_pages == 0:
        return {"success": False, "error": "扫描 PDF 所有页面 OCR 均失败"}

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return _ensure_fields({
        "success": True,
        "text": "\n".join(all_text),
        "has_stamp": has_stamp_any,
        "fields": merged_fields,
        "confidence": avg_conf,
        "layout": _build_layout(page_layouts),
    }, doc_type, field_template, db=db, extra_instructions=extra_instructions, tenant_id=tenant_id)
