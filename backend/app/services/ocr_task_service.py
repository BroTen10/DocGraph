"""OCR 任务服务：独立触发 OCR 并跟踪进度。

支持两种粒度：
- 单文档触发 (scope=single_doc)
- 合同级批量触发 (scope=contract_batch)

OCR 调用 DeepSeek 多模态模型，单页耗时 10-60 秒，必须异步后台执行。
后台线程使用独立 SessionLocal()，不复用请求 session，避免连接冲突。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..constants import DOC_OTHER
from ..database import SessionLocal, bind_tenant
from ..models import Contract, Document, DocumentType, OcrTask
from ..tenant_scope import tenant_get
from .doc_normalizer import normalize_doc_type, resolve_field_aliases
from .field_extraction_service import normalize_fields
from .ocr_service import (
    OCR_PROFILE_TYPE,
    build_applied_config,
    process_document,
    render_extra_instructions,
    resolve_ocr_profile,
    resolve_field_template,
)

logger = logging.getLogger(__name__)

# running/pending 任务超过该时长即视为中断（单页识别 10-60s，30 分钟足够宽松）
STALE_TASK_MINUTES = 30

# 本进程启动时刻（数据库时钟）。由 lifespan 在启动时写入：
# 早于本进程启动的任务不可能还活着，可立即回收，无需等满 STALE_TASK_MINUTES。
# 前提是单进程部署（本地 run.py / Dockerfile 的 uvicorn 均未使用 --workers）。
_PROCESS_STARTED_DB_TS: datetime | None = None


def set_process_start(db: Session) -> None:
    """记录本进程启动时刻（数据库时钟），供中断任务回收使用。"""
    global _PROCESS_STARTED_DB_TS
    try:
        _PROCESS_STARTED_DB_TS = db.execute(select(func.now())).scalar()
    except Exception:
        logger.warning("读取数据库时钟失败，中断任务回收将只按超时判定", exc_info=True)
        _PROCESS_STARTED_DB_TS = None


def reconcile_stale_tasks(db: Session, *, force_all: bool = False) -> int:
    """回收中断的 OCR 任务，避免任务行永久停在 running。

    背景：OCR 在进程内后台线程执行，服务重启（含 debug reload）或进程退出会让线程
    消失，但任务行仍是 running/pending；前端据此一直显示"识别中"并禁用触发按钮。

    force_all=True（服务启动时）：此时不可能有存活任务，全部置 failed；
    force_all=False（查询/触发时）：处理「超过 STALE_TASK_MINUTES」或「早于本进程
    启动时刻」的任务——后者在单进程部署下必然已中断，可让界面立刻恢复可用。

    Returns: 被回收的任务数
    """
    stmt = select(OcrTask).where(OcrTask.status.in_(("pending", "running")))
    if not force_all:
        conds = [OcrTask.start_time < func.now() - timedelta(minutes=STALE_TASK_MINUTES)]
        if _PROCESS_STARTED_DB_TS is not None:
            conds.append(OcrTask.start_time < _PROCESS_STARTED_DB_TS)
        stmt = stmt.where(or_(*conds))
    rows = db.execute(stmt).scalars().all()
    if not rows:
        return 0
    for t in rows:
        t.status = "failed"
        t.stage = "已中断"
        t.error = t.error or "任务中断（服务重启或识别进程退出），请重新触发 OCR"
        t.end_time = func.now()
        if t.done_count < t.total_count:
            t.failed_count = max(t.failed_count, t.total_count - t.done_count)
    db.commit()
    logger.warning("已回收 %d 个中断的 OCR 任务", len(rows))
    return len(rows)


def _apply_inferred_doc_type(
    db: Session,
    tenant_id: uuid.UUID,
    doc: Document,
    fields: dict,
) -> None:
    """OCR 完成后自动关联模型推测的文档类型（仅当当前类型为「其他」时）。

    规则：把模型推测的类型名（含显式别名）与已定义且激活（status=active）的
    文档类型做精确匹配，命中则更新 doc.doc_type 并同步 is_required；
    未命中任何已激活类型则保持原值，留给用户人工修改。
    """
    if doc.doc_type != DOC_OTHER:
        return
    inferred = (fields or {}).get("__inferred_doc_type__")
    if not inferred:
        return
    # 轻量清洗：去掉常见 LLM 输出尾巴（句号/顿号/空白等），再做规范名映射
    inferred_clean = str(inferred).strip().rstrip("。.，,、:：;； ")
    if not inferred_clean:
        return
    canonical = normalize_doc_type(db, inferred_clean, tenant_id=tenant_id)
    if not canonical:
        return
    active = db.execute(
        select(DocumentType.name, DocumentType.is_required).where(
            DocumentType.tenant_id == tenant_id,
            DocumentType.status == "active"
        )
    ).all()
    active_map = {name: bool(is_required) for name, is_required in active}
    if canonical in active_map and canonical != doc.doc_type:
        doc.doc_type = canonical
        doc.is_required = active_map[canonical]
        logger.info(
            "文档 %s 模型推测类型「%s」命中已激活类型，自动更新为「%s」",
            doc.file_name,
            inferred_clean,
            canonical,
        )


# ============ 触发入口 ============
def _prepare_profile(
    db: Session,
    tenant_id: uuid.UUID,
    doc_type: str,
    profile: str,
) -> dict:
    """解析某类型的生效增强点，并预渲染专属指令（任务启动时取快照）。"""
    data = resolve_ocr_profile(db, tenant_id, doc_type, profile)
    data["rendered_instructions"] = render_extra_instructions(
        db,
        tenant_id,
        doc_type,
        data,
    )
    return data


def start_ocr_for_doc(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    doc_id: uuid.UUID,
    profile: str = OCR_PROFILE_TYPE,
) -> OcrTask:
    """对单个文档启动 OCR（异步）。

    创建 OcrTask(scope=single_doc)，起后台线程处理，立即返回任务对象。
    profile="type" 使用该文档类型的 OCR 增强点（默认）；"base" 仅基础字段模板。
    """
    doc = tenant_get(db, Document, doc_id, tenant_id)
    if doc is None:
        raise ValueError("文档不存在")

    # 先回收中断的旧任务，避免前端因陈旧 running 状态禁用触发按钮
    reconcile_stale_tasks(db)

    profile_data = _prepare_profile(db, tenant_id, doc.doc_type, profile)
    task = OcrTask(
        tenant_id=tenant_id,
        rule_set_id=rule_set_id,
        scope="single_doc",
        doc_id=doc_id,
        contract_id=doc.contract_id,
        total_count=1,
        stage="准备中",
        applied_config=build_applied_config(profile, {doc.doc_type: profile_data}),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id

    # 起后台线程：使用独立 Session，不复用请求 session
    t = threading.Thread(
        target=_run_single_doc,
        args=(task_id, tenant_id, doc_id, profile_data),
        name=f"ocr-single-{task_id}",
        daemon=True,
    )
    t.start()
    return task


def start_ocr_for_contract(
    db: Session,
    tenant_id: uuid.UUID,
    rule_set_id: uuid.UUID,
    contract_id: uuid.UUID,
    force: bool = False,
    profile: str = OCR_PROFILE_TYPE,
) -> OcrTask:
    """对合同下文档批量启动 OCR（异步）。

    force=False（默认）：仅处理 ocr_status=pending 的未识别文档；
    force=True：重置式重新识别，覆盖该合同下全部文档（含已 done/failed 的）。
    profile="type" 使用各文档类型的 OCR 增强点（默认）；"base" 仅基础字段模板。
    """
    contract = tenant_get(db, Contract, contract_id, tenant_id)
    if contract is None:
        raise ValueError("合同不存在")

    reconcile_stale_tasks(db)

    stmt = select(Document).where(Document.contract_id == contract_id)
    if not force:
        stmt = stmt.where(Document.ocr_status == "pending")
    docs = db.execute(stmt).scalars().all()
    if not docs:
        raise ValueError("没有待识别的文档" if not force else "该合同下没有可重新识别的文档")

    # 按文档类型解析增强点（同一合同下可能含多种类型），并在任务启动时取快照
    profiles_by_type: dict[str, dict] = {}
    profiles_by_doc: dict[str, dict] = {}
    for d in docs:
        if d.doc_type not in profiles_by_type:
            profiles_by_type[d.doc_type] = _prepare_profile(
                db,
                tenant_id,
                d.doc_type,
                profile,
            )
        profiles_by_doc[str(d.id)] = profiles_by_type[d.doc_type]

    task = OcrTask(
        tenant_id=tenant_id,
        rule_set_id=rule_set_id,
        scope="contract_batch",
        contract_id=contract_id,
        total_count=len(docs),
        stage="准备中",
        applied_config=build_applied_config(profile, profiles_by_type),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id
    doc_ids = [d.id for d in docs]

    t = threading.Thread(
        target=_run_batch,
        args=(task_id, tenant_id, doc_ids, profiles_by_doc),
        name=f"ocr-batch-{task_id}",
        daemon=True,
    )
    t.start()
    return task


# ============ 后台线程 ============
def _run_single_doc(
    task_id: uuid.UUID,
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    profile_data: dict | None = None,
) -> None:
    """后台线程：处理单个文档 OCR。独立 db session。"""
    db = SessionLocal()
    try:
        bind_tenant(db, tenant_id)
        task = tenant_get(db, OcrTask, task_id, tenant_id)
        doc = tenant_get(db, Document, doc_id, tenant_id)
        if task is None or doc is None:
            logger.error("OCR 任务 %s: 任务或文档不存在", task_id)
            return

        task.status = "running"
        task.stage = f"识别中: {doc.file_name}"
        db.commit()

        r = process_document(
            doc,
            key_fields=(profile_data or {}).get("field_list")
            or resolve_field_template(db, tenant_id, doc.doc_type),
            db=db,
            extra_instructions=(profile_data or {}).get("rendered_instructions"),
            tenant_id=tenant_id,
        )
        if r.get("success"):
            # OCR 完成后自动将模型推测类型关联到已激活的文档类型（命中则更新 doc_type）
            _apply_inferred_doc_type(db, tenant_id, doc, r.get("fields", {}))
            doc.ocr_status = "done"
            doc.ocr_text = r.get("text", "")
            doc.has_stamp = r.get("has_stamp")
            doc.ocr_confidence = r.get("confidence", 0.0)
            doc.ocr_layout = r.get("layout", {}) or {}
            doc.extracted_fields = normalize_fields(
                doc.doc_type,
                r.get("fields", {}),
                aliases=resolve_field_aliases(db, doc.doc_type, tenant_id=tenant_id),
            )
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
            task = tenant_get(db, OcrTask, task_id, tenant_id)
            if task is not None:
                task.status = "failed"
                task.error = str(e)
                task.end_time = datetime.now()
                db.commit()
        except Exception:
            logger.error("回写 OCR 失败状态时出错", exc_info=True)
    finally:
        db.close()


def _run_batch(
    task_id: uuid.UUID,
    tenant_id: uuid.UUID,
    doc_ids: list[uuid.UUID],
    profiles_by_doc: dict[str, dict] | None = None,
) -> None:
    """后台线程：批量处理多个文档。独立 db session。"""
    db = SessionLocal()
    try:
        bind_tenant(db, tenant_id)
        task = tenant_get(db, OcrTask, task_id, tenant_id)
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
            doc = tenant_get(db, Document, doc_id, tenant_id)
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
                pdata = (profiles_by_doc or {}).get(str(doc_id)) or {}
                r = process_document(
                    doc,
                    key_fields=pdata.get("field_list")
                    or resolve_field_template(db, tenant_id, doc.doc_type),
                    db=db,
                    extra_instructions=pdata.get("rendered_instructions"),
                    tenant_id=tenant_id,
                )
                if r.get("success"):
                    # OCR 完成后自动将模型推测类型关联到已激活的文档类型（命中则更新 doc_type）
                    _apply_inferred_doc_type(db, tenant_id, doc, r.get("fields", {}))
                    doc.ocr_status = "done"
                    doc.ocr_text = r.get("text", "")
                    doc.has_stamp = r.get("has_stamp")
                    doc.ocr_confidence = r.get("confidence", 0.0)
                    doc.ocr_layout = r.get("layout", {}) or {}
                    doc.extracted_fields = normalize_fields(
                        doc.doc_type,
                        r.get("fields", {}),
                        aliases=resolve_field_aliases(db, doc.doc_type, tenant_id=tenant_id),
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
            task = tenant_get(db, OcrTask, task_id, tenant_id)
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
