"""OCR 任务服务：独立触发 OCR 并跟踪进度。

支持两种粒度：
- 单文档触发 (scope=single_doc)
- 合同级批量触发 (scope=contract_batch)

OCR 调用通义千问 VL，单页耗时 10-60 秒，必须异步后台执行。
后台线程使用独立 SessionLocal()，不复用请求 session，避免连接冲突。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Contract, Document, OcrTask
from .field_extraction_service import normalize_fields
from .ocr_service import process_document

logger = logging.getLogger(__name__)


# ============ 触发入口 ============
def start_ocr_for_doc(db: Session, rule_set_id: uuid.UUID, doc_id: uuid.UUID) -> OcrTask:
    """对单个文档启动 OCR（异步）。

    创建 OcrTask(scope=single_doc)，起后台线程处理，立即返回任务对象。
    """
    doc = db.get(Document, doc_id)
    if doc is None:
        raise ValueError("文档不存在")

    task = OcrTask(
        rule_set_id=rule_set_id,
        scope="single_doc",
        doc_id=doc_id,
        contract_id=doc.contract_id,
        total_count=1,
        stage="准备中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id

    # 起后台线程：使用独立 Session，不复用请求 session
    t = threading.Thread(
        target=_run_single_doc,
        args=(task_id, doc_id),
        name=f"ocr-single-{task_id}",
        daemon=True,
    )
    t.start()
    return task


def start_ocr_for_contract(
    db: Session, rule_set_id: uuid.UUID, contract_id: uuid.UUID
) -> OcrTask:
    """对合同下所有 pending 文档批量启动 OCR（异步）。"""
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError("合同不存在")

    docs = (
        db.execute(
            select(Document).where(
                Document.contract_id == contract_id,
                Document.ocr_status == "pending",
            )
        )
        .scalars()
        .all()
    )
    if not docs:
        raise ValueError("没有待识别的文档")

    task = OcrTask(
        rule_set_id=rule_set_id,
        scope="contract_batch",
        contract_id=contract_id,
        total_count=len(docs),
        stage="准备中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id
    doc_ids = [d.id for d in docs]

    t = threading.Thread(
        target=_run_batch,
        args=(task_id, doc_ids),
        name=f"ocr-batch-{task_id}",
        daemon=True,
    )
    t.start()
    return task


# ============ 后台线程 ============
def _run_single_doc(task_id: uuid.UUID, doc_id: uuid.UUID) -> None:
    """后台线程：处理单个文档 OCR。独立 db session。"""
    db = SessionLocal()
    try:
        task = db.get(OcrTask, task_id)
        doc = db.get(Document, doc_id)
        if task is None or doc is None:
            logger.error("OCR 任务 %s: 任务或文档不存在", task_id)
            return

        task.status = "running"
        task.stage = f"识别中: {doc.file_name}"
        db.commit()

        r = process_document(doc)
        if r.get("success"):
            doc.ocr_status = "done"
            doc.ocr_text = r.get("text", "")
            doc.has_stamp = r.get("has_stamp")
            doc.ocr_confidence = r.get("confidence", 0.0)
            doc.extracted_fields = normalize_fields(doc.doc_type, r.get("fields", {}))
            doc.extracted_at = datetime.now()
            task.success_count = 1
            task.done_count = 1
        else:
            doc.ocr_status = "failed"
            task.failed_count = 1
            task.done_count = 1
            task.failures = [
                {
                    "doc_id": str(doc_id),
                    "file_name": doc.file_name,
                    "error": r.get("error", "未知错误"),
                }
            ]

        task.progress = 100
        task.status = "completed"
        task.stage = "完成"
        task.end_time = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("OCR 任务 %s 失败: %s", task_id, e, exc_info=True)
        try:
            task = db.get(OcrTask, task_id)
            if task is not None:
                task.status = "failed"
                task.error = str(e)
                task.end_time = datetime.now()
                db.commit()
        except Exception:
            logger.error("回写 OCR 失败状态时出错", exc_info=True)
    finally:
        db.close()


def _run_batch(task_id: uuid.UUID, doc_ids: list[uuid.UUID]) -> None:
    """后台线程：批量处理多个文档。独立 db session。"""
    db = SessionLocal()
    try:
        task = db.get(OcrTask, task_id)
        if task is None:
            logger.error("OCR 任务 %s: 任务不存在", task_id)
            return

        task.status = "running"
        db.commit()

        total = len(doc_ids)
        failures: list[dict] = []
        success = 0
        failed = 0

        for i, doc_id in enumerate(doc_ids):
            doc = db.get(Document, doc_id)
            if doc is None:
                failed += 1
                failures.append(
                    {
                        "doc_id": str(doc_id),
                        "file_name": "(已删除)",
                        "error": "文档不存在",
                    }
                )
                continue

            task.stage = f"识别中 ({i + 1}/{total}): {doc.file_name}"
            db.commit()

            try:
                r = process_document(doc)
                if r.get("success"):
                    doc.ocr_status = "done"
                    doc.ocr_text = r.get("text", "")
                    doc.has_stamp = r.get("has_stamp")
                    doc.ocr_confidence = r.get("confidence", 0.0)
                    doc.extracted_fields = normalize_fields(
                        doc.doc_type, r.get("fields", {})
                    )
                    doc.extracted_at = datetime.now()
                    success += 1
                else:
                    doc.ocr_status = "failed"
                    failed += 1
                    failures.append(
                        {
                            "doc_id": str(doc_id),
                            "file_name": doc.file_name,
                            "error": r.get("error", "未知错误"),
                        }
                    )
            except Exception as e:
                doc.ocr_status = "failed"
                failed += 1
                failures.append(
                    {
                        "doc_id": str(doc_id),
                        "file_name": doc.file_name,
                        "error": str(e),
                    }
                )

            db.commit()
            task.done_count = i + 1
            task.success_count = success
            task.failed_count = failed
            task.failures = failures
            task.progress = int((i + 1) / max(total, 1) * 100)
            db.commit()

        task.status = "completed"
        task.stage = f"完成: 成功 {success}/{total}, 失败 {failed}"
        task.end_time = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("OCR 任务 %s 失败: %s", task_id, e, exc_info=True)
        try:
            task = db.get(OcrTask, task_id)
            if task is not None:
                task.status = "failed"
                task.error = str(e)
                task.end_time = datetime.now()
                db.commit()
        except Exception:
            logger.error("回写 OCR 失败状态时出错", exc_info=True)
    finally:
        db.close()


# ============ 查询 ============
def list_ocr_tasks(
    db: Session,
    rule_set_id: uuid.UUID,
    contract_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> list[OcrTask]:
    """查询 OCR 任务列表（按 rule_set_id 过滤，可按 contract_id 二次过滤）。"""
    q = select(OcrTask).where(OcrTask.rule_set_id == rule_set_id)
    if contract_id is not None:
        q = q.where(OcrTask.contract_id == contract_id)
    q = q.order_by(OcrTask.created_at.desc()).limit(limit)
    return list(db.execute(q).scalars().all())


def get_ocr_task(db: Session, task_id: uuid.UUID) -> Optional[OcrTask]:
    """查询单个 OCR 任务。"""
    return db.get(OcrTask, task_id)
