"""OCR 与文本提取服务。

策略：
- 文本型 PDF：pdfplumber 直接提取文本（不调 OCR）
- 扫描型 PDF：用 PyMuPDF 把每页渲染成图片，逐页调通义千问多模态模型
- 图片（PNG/JPG）：直接调通义千问多模态模型
- DOCX：python-docx 提取段落文本
- 提取结果统一为 {text, has_stamp, fields, confidence, success}
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..constants import FIELD_TEMPLATES
from ..llm_client import LLMError, get_llm_client
from ..models import Document
from ..ocr_client import get_ocr_client
from .settings_service import get_prompt

logger = logging.getLogger(__name__)


def resolve_field_template(db, doc_type: str) -> list[str]:
    """字段模板解析（批次 10 Phase B）：DocumentType.key_fields 优先（动态注册/规则导入预填），
    constants.FIELD_TEMPLATES 兜底。未注册类型返回空（走自由提取兜底）。"""
    try:
        from sqlalchemy import select
        from ..models import DocumentType
        dt = db.execute(
            select(DocumentType).where(DocumentType.name == doc_type)
        ).scalars().first()
        if dt is not None and dt.key_fields:
            return list(dt.key_fields)
    except Exception:
        logger.warning("解析字段模板失败，回退 constants: %s", doc_type, exc_info=True)
    return FIELD_TEMPLATES.get(doc_type, [])


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


def _llm_extract_fields_from_text(
    text: str,
    doc_type: str,
    field_template: list[str],
    db=None,
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
        system_prompt = get_prompt(db, "ocr.text.system_free")
        user_prompt = get_prompt(db, "ocr.text.user_free").format(
            text=text[:4000],
        )
    else:
        system_prompt = get_prompt(db, "ocr.text.system_template")
        user_prompt = get_prompt(db, "ocr.text.user_template").format(
            doc_type=doc_type,
            field_list=", ".join(field_template),
            text=text[:4000],
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
                return _ocr_scanned_pdf(str(path), doc.doc_type, field_template, db=db)
            else:
                # 文本型 PDF：直接提取 + LLM 字段提取
                text = _extract_text_pdf(str(path))
                ext = _llm_extract_fields_from_text(text, doc.doc_type, field_template, db=db)
                return {
                    "success": True,
                    "text": text,
                    "has_stamp": ext["has_stamp"],
                    "fields": ext["fields"],
                    "confidence": ext["confidence"],
                }
        elif doc.file_type in ("png", "jpg"):
            return _ocr_image(str(path), doc.doc_type, field_template, db=db)
        elif doc.file_type == "docx":
            text = _extract_docx(str(path))
            ext = _llm_extract_fields_from_text(text, doc.doc_type, field_template, db=db)
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


def _ocr_image(image_path: str, doc_type: str, field_template: list[str], db=None) -> dict:
    """对单张图片调用通义千问多模态模型 OCR。"""
    ocr = get_ocr_client()
    return ocr.recognize(
        image_path,
        doc_type_hint=doc_type,
        field_template=field_template,
        system_prompt=get_prompt(db, "ocr.image.system"),
        fields_hint=get_prompt(db, "ocr.image.fields_hint"),
        infer_hint=get_prompt(db, "ocr.image.infer_hint"),
        infer_hint_free=get_prompt(db, "ocr.image.infer_hint_free"),
    )


def _ocr_scanned_pdf(
    pdf_path: str,
    doc_type: str,
    field_template: list[str],
    db=None,
) -> dict:
    """扫描型 PDF：逐页转图片 OCR，合并结果。"""
    import tempfile

    ocr = get_ocr_client()
    pages = _pdf_pages_to_images(pdf_path)
    if not pages:
        return {"success": False, "error": "PDF 无页面"}

    all_text: list[str] = []
    merged_fields: dict = {}
    has_stamp_any: Optional[bool] = None
    confidences: list[float] = []

    with tempfile.TemporaryDirectory() as tmp:
        for i, page_bytes in enumerate(pages):
            tmp_img = Path(tmp) / f"page_{i}.png"
            tmp_img.write_bytes(page_bytes)
            r = ocr.recognize(
                str(tmp_img),
                doc_type_hint=doc_type,
                field_template=field_template,
                system_prompt=get_prompt(db, "ocr.image.system"),
                fields_hint=get_prompt(db, "ocr.image.fields_hint"),
                infer_hint=get_prompt(db, "ocr.image.infer_hint"),
                infer_hint_free=get_prompt(db, "ocr.image.infer_hint_free"),
            )
            if r.get("success"):
                all_text.append(r.get("text", ""))
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

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return {
        "success": True,
        "text": "\n".join(all_text),
        "has_stamp": has_stamp_any,
        "fields": merged_fields,
        "confidence": avg_conf,
    }
