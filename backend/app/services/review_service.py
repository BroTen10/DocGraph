"""审查推理服务：执行 4 类检查并生成结果。

检查类型：
1. 齐套性：必备文件是否齐全
2. 基础判断（印章）：印章有无
3. 信息准确性：跨文档字段比对 + 一对多总额比对
4. 时间逻辑：时间顺序校验（协议⊇合同<报关<提单/签收；收≤付；入库≥出库）

三态结果：pass / fail / unverifiable
- 字段无法提取 → unverifiable
- 超容差 → fail
- 容差内 → pass
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..constants import (
    CHECK_ACCURACY,
    CHECK_COMPLETENESS,
    CHECK_STAMP,
    CHECK_TIME_LOGIC,
    DOC_AGENCY_AGREEMENT,
    DOC_CUSTOMS_DECLARATION,
    DOC_ENTRUST_CONFIRM,
    DOC_PAY_VOUCHER,
    DOC_RECEIPT,
    DOC_RECEIVE_VOUCHER,
    DOC_VAT_INVOICE,
    DOC_WAYBILL,
    DOC_WAREHOUSE_INOUT,
    STAMP_REQUIREMENTS,
)
from ..models import Contract, Document, ReviewResult, ReviewTask, Rule
from .contract_normalizer import extract_contract_numbers, normalize_contract_no
from .field_extraction_service import (
    aggregate_amount,
    normalize_fields,
    parse_amount,
    parse_date,
)
from .ocr_service import process_document
from .suggestion_service import build_suggestion

logger = logging.getLogger(__name__)


# ============ 辅助函数 ============
def _docs_by_type(docs: list[Document], doc_type: str) -> list[Document]:
    return [d for d in docs if d.doc_type == doc_type]


def _get_field(doc: Document, key: str, aliases: list[str] | None = None):
    """从文档的 extracted_fields 取字段值，支持别名。"""
    fields = doc.extracted_fields or {}
    if key in fields and fields[key] is not None:
        return fields[key]
    for alias in (aliases or []):
        if alias in fields and fields[alias] is not None:
            return fields[alias]
    return None


def _amount_diff_pct(actual: float, expected: float) -> float:
    """计算差额百分比。"""
    if expected == 0:
        return 0.0 if actual == 0 else 100.0
    return abs(actual - expected) / expected * 100.0


def _compare_amount(
    actual: Optional[float],
    expected: Optional[float],
    tolerance_percent: float,
) -> tuple[str, Optional[float]]:
    """比较两个金额，返回 (结果, 差额)。"""
    if actual is None or expected is None:
        return "unverifiable", None
    diff = actual - expected
    pct = _amount_diff_pct(actual, expected)
    return ("pass" if pct <= tolerance_percent else "fail"), diff


def _compare_dates(
    earlier: Optional[str],
    later: Optional[str],
    allow_same_day: bool = True,
) -> str:
    """比较两个日期：earlier 应 <= later。返回 pass/fail/unverifiable。"""
    if earlier is None or later is None:
        return "unverifiable"
    try:
        d1 = datetime.fromisoformat(earlier).date()
        d2 = datetime.fromisoformat(later).date()
    except Exception:
        return "unverifiable"
    if d1 < d2:
        return "pass"
    if d1 == d2:
        return "pass" if allow_same_day else "fail"
    return "fail"


# ============ 主流程 ============
def start_review(
    db: Session,
    contract_id: uuid.UUID,
    snapshot_id: Optional[uuid.UUID] = None,
) -> ReviewTask:
    """启动审查任务（异步后台线程执行，立即返回任务）。

    完整流程：OCR → 字段提取 → 4 类检查 → 生成建议 → 持久化结果。
    """
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"合同不存在: {contract_id}")

    # 创建任务
    task = ReviewTask(
        contract_id=contract_id,
        status="running",
        progress=0,
        stage="初始化",
        start_time=datetime.now(),
        summary={},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id

    # 后台线程：使用独立的 DB Session 执行管线（避免请求结束后 session 被关闭）
    from ..database import SessionLocal as SessionFactory

    def _run_in_thread():
        thread_db = SessionFactory()
        try:
            thread_task = thread_db.get(ReviewTask, task_id)
            thread_contract = thread_db.get(Contract, contract_id)
            if thread_task is None or thread_contract is None:
                logger.error("后台审查任务 %s: 任务或合同不存在", task_id)
                return
            docs = list(thread_contract.documents)
            _run_review_pipeline(thread_db, thread_task, thread_contract, docs)
            thread_db.commit()
        except Exception as e:
            logger.error("审查任务 %s 失败: %s", task_id, e, exc_info=True)
            try:
                thread_task = thread_db.get(ReviewTask, task_id)
                if thread_task is not None:
                    thread_task.status = "failed"
                    thread_task.error = str(e)
                    thread_task.end_time = datetime.now()
                    thread_db.commit()
            except Exception:
                logger.error("回写审查失败状态时出错", exc_info=True)
        finally:
            thread_db.close()

    t = threading.Thread(target=_run_in_thread, name=f"review-{task_id}", daemon=True)
    t.start()

    return task


def _run_review_pipeline(
    db: Session,
    task: ReviewTask,
    contract: Contract,
    docs: list[Document],
) -> None:
    """执行审查管线。"""
    total = len(docs)
    results: list[ReviewResult] = []

    # ============ 阶段1：OCR + 字段提取 ============
    task.stage = "OCR 与字段提取中"
    task.progress = 5
    db.commit()

    for i, doc in enumerate(docs):
        if doc.ocr_status == "done" and doc.extracted_fields:
            # 已处理过，跳过
            continue
        r = process_document(doc)
        if r.get("success"):
            doc.ocr_status = "done"
            doc.ocr_text = r.get("text", "")
            doc.has_stamp = r.get("has_stamp")
            doc.ocr_confidence = r.get("confidence", 0.0)
            # 规范化字段
            doc.extracted_fields = normalize_fields(doc.doc_type, r.get("fields", {}))
            doc.extracted_at = datetime.now()
        else:
            doc.ocr_status = "failed"
            doc.ocr_confidence = 0.0
            logger.warning("文档 %s OCR 失败: %s", doc.file_name, r.get("error"))
        db.commit()
        task.progress = 5 + int((i + 1) / max(total, 1) * 50)  # 5%-55%
        db.commit()

    # ============ 阶段2：规则比对（图谱优先 + 旧逻辑 fallback） ============
    task.stage = "规则比对中"
    task.progress = 60
    db.commit()

    results_from_graph: list[ReviewResult] = []
    graph_used = False
    try:
        from .graph_review_service import run_graph_review_with_contract
        results_from_graph = run_graph_review_with_contract(db, contract, docs)
        if results_from_graph:
            graph_used = True
            logger.info("审查任务 %s: 使用图谱驱动，生成 %d 条结果", task.id, len(results_from_graph))
        else:
            logger.info("审查任务 %s: 图谱返回空结果，fallback 到旧逻辑", task.id)
    except ValueError as e:
        # 无图谱快照，fallback
        logger.info("审查任务 %s: 图谱不可用（%s），fallback 到旧逻辑", task.id, e)
    except Exception as e:
        # 图谱查询异常，fallback
        logger.warning("审查任务 %s: 图谱驱动审查失败（%s），fallback 到旧逻辑", task.id, e, exc_info=True)

    if graph_used:
        results.extend(results_from_graph)
    else:
        # 旧逻辑：加载该合同所属规则集下的启用规则（按 doc_type + check_category 索引）
        rules_by_key: dict[tuple[str, str], list[Rule]] = {}
        for rule in _load_enabled_rules(db, contract.rule_set_id):
            rules_by_key.setdefault((rule.doc_type, rule.check_category), []).append(rule)

        # 检查1：齐套性
        results.extend(_check_completeness(db, docs, rules_by_key, contract))

        # 检查2：印章
        results.extend(_check_stamp(db, docs, rules_by_key))

        # 检查3：信息准确性
        results.extend(_check_accuracy(db, docs, rules_by_key, contract))

        # 检查4：时间逻辑
        results.extend(_check_time_logic(db, docs, rules_by_key, contract))

    # ============ 阶段3：生成建议 + 持久化 ============
    task.stage = "生成报告中"
    task.progress = 90
    db.commit()

    for r in results:
        r.task_id = task.id
        if r.result == "fail" and not r.suggestion:
            r.suggestion = build_suggestion(
                check_category=r.check_category or "",
                doc_type=r.doc_type or "",
                issue_desc=r.issue_desc or "",
                detail=r.detail or {},
            )
        db.add(r)

    # 汇总
    summary = {
        "total": len(results),
        "pass": sum(1 for r in results if r.result == "pass"),
        "fail": sum(1 for r in results if r.result == "fail"),
        "unverifiable": sum(1 for r in results if r.result == "unverifiable"),
    }
    task.summary = summary
    task.status = "completed"
    task.progress = 100
    task.stage = "完成"
    task.end_time = datetime.now()
    db.commit()


def _load_enabled_rules(db: Session, rule_set_id: uuid.UUID) -> list[Rule]:
    """加载指定规则集下的启用规则（按 priority 排序）。"""
    from sqlalchemy import select

    stmt = (
        select(Rule)
        .where(Rule.rule_set_id == rule_set_id)
        .where(Rule.enabled.is_(True))
        .order_by(Rule.priority)
    )
    return list(db.execute(stmt).scalars().all())


def _make_result(
    rule: Optional[Rule],
    doc: Optional[Document],
    result: str,
    issue_desc: str = "",
    detail: Optional[dict] = None,
    suggestion: str = "",
) -> ReviewResult:
    return ReviewResult(
        rule_id=rule.id if rule else None,
        rule_text=rule.rule_text if rule else None,
        doc_type=rule.doc_type if rule else (doc.doc_type if doc else None),
        check_category=rule.check_category if rule else None,
        doc_id=doc.id if doc else None,
        result=result,
        issue_desc=issue_desc or None,
        detail=detail or {},
        suggestion=suggestion or None,
    )


def _find_rule(
    rules_by_key: dict[tuple[str, str], list[Rule]],
    doc_type: str,
    check_category: str,
) -> Optional[Rule]:
    rules = rules_by_key.get((doc_type, check_category), [])
    return rules[0] if rules else None


# ============ 检查1：齐套性 ============
def _check_completeness(
    db: Session,
    docs: list[Document],
    rules_by_key: dict[tuple[str, str], list[Rule]],
    contract: Contract,
) -> list[ReviewResult]:
    """齐套性检查：从启用的齐套性规则推导必备文件类型，检查是否齐全。多余文件不报错。"""
    results: list[ReviewResult] = []
    present_types = {d.doc_type for d in docs}

    # 从���套性规则中提取"必备"文件类型
    required_types = set()
    for (doc_type, cat), rules in rules_by_key.items():
        if cat == CHECK_COMPLETENESS:
            for rule in rules:
                if rule.enabled and rule.status == 'confirmed':
                    required_types.add(doc_type)

    if not required_types:
        logger.warning("未找到启用的齐套性规则，跳过齐套性检查")
        return results

    for doc_type in sorted(required_types):
        rule = _find_rule(rules_by_key, doc_type, CHECK_COMPLETENESS)
        if doc_type in present_types:
            results.append(_make_result(rule, None, "pass", detail={"doc_type": doc_type}))
        else:
            results.append(
                _make_result(
                    rule,
                    None,
                    "fail",
                    issue_desc=f"缺失必备文件：{doc_type}",
                    detail={"missing_doc_type": doc_type},
                )
            )
    return results


# ============ 检查2：基础判断（印章） ============
def _check_stamp(
    db: Session,
    docs: list[Document],
    rules_by_key: dict[tuple[str, str], list[Rule]],
) -> list[ReviewResult]:
    """印章有无检查。三态：有(pass) / 无(fail) / 无法核验(unverifiable)。"""
    results: list[ReviewResult] = []
    for doc in docs:
        req = STAMP_REQUIREMENTS.get(doc.doc_type)
        if not req:
            continue  # 该文件类型不要求用印
        rule = _find_rule(rules_by_key, doc.doc_type, CHECK_STAMP)
        if doc.ocr_status == "failed":
            results.append(
                _make_result(
                    rule, doc, "unverifiable",
                    issue_desc=f"{doc.file_name} OCR 失败，无法判断印章",
                    detail={"reason": "ocr_failed"},
                )
            )
        elif doc.has_stamp is True:
            results.append(_make_result(rule, doc, "pass", detail={"has_stamp": True}))
        elif doc.has_stamp is False:
            results.append(
                _make_result(
                    rule, doc, "fail",
                    issue_desc=f"{doc.file_name} 未检测到印章（要求：{req}）",
                    detail={"has_stamp": False, "required": req},
                )
            )
        else:
            results.append(
                _make_result(
                    rule, doc, "unverifiable",
                    issue_desc=f"{doc.file_name} 印章无法判断（OCR 置信度低）",
                    detail={"has_stamp": None, "reason": "low_confidence"},
                )
            )
    return results


# ============ 检查3：信息准确性 ============
def _check_accuracy(
    db: Session,
    docs: list[Document],
    rules_by_key: dict[tuple[str, str], list[Rule]],
    contract: Contract,
) -> list[ReviewResult]:
    """信息准确性检查：跨文档字段比对 + 一对多总额比对。"""
    results: list[ReviewResult] = []
    tolerance_pct = settings.amount_tolerance_percent

    # 3.1 合同号归一化：所有文档的合同号应归一到 contract.contract_no
    for doc in docs:
        contract_no = _get_field(doc, "合同号", ["合同协议号"])
        if contract_no is None:
            continue
        candidates = extract_contract_numbers(str(contract_no))
        if not candidates:
            continue
        canonical, _ = normalize_contract_no(candidates)
        rule = _find_rule(rules_by_key, doc.doc_type, CHECK_ACCURACY)
        if canonical and canonical != contract.contract_no and canonical not in (contract.alias_list or []):
            results.append(
                _make_result(
                    rule, doc, "fail",
                    issue_desc=f"{doc.file_name} 合同号 {canonical} 与主合同号 {contract.contract_no} 不一致",
                    detail={"actual": canonical, "expected": contract.contract_no, "aliases": contract.alias_list},
                )
            )

    # 3.2 协议方 = 委托方
    agency_docs = _docs_by_type(docs, DOC_AGENCY_AGREEMENT)
    entrust_docs = _docs_by_type(docs, DOC_ENTRUST_CONFIRM)
    if agency_docs and entrust_docs:
        a_party = _get_field(agency_docs[0], "协议方", ["委托方"])
        e_party = _get_field(entrust_docs[0], "委托方")
        rule = _find_rule(rules_by_key, DOC_AGENCY_AGREEMENT, CHECK_ACCURACY)
        if a_party is None or e_party is None:
            results.append(_make_result(rule, None, "unverifiable", issue_desc="协议方/委托方字段无法提取"))
        elif str(a_party).strip() != str(e_party).strip():
            results.append(
                _make_result(
                    rule, None, "fail",
                    issue_desc=f"代理协议协议方 [{a_party}] 与委托单委托方 [{e_party}] 不一致",
                    detail={"agency_party": a_party, "entrust_party": e_party},
                )
            )
        else:
            results.append(_make_result(rule, None, "pass"))

    # 3.3 报关单数量/金额 ≤ 委托单（容差内）
    customs_docs = _docs_by_type(docs, DOC_CUSTOMS_DECLARATION)
    if customs_docs and entrust_docs:
        entrust_qty = _get_field(entrust_docs[0], "数量")
        customs_qty = _get_field(customs_docs[0], "数量")
        rule = _find_rule(rules_by_key, DOC_CUSTOMS_DECLARATION, CHECK_ACCURACY)
        if entrust_qty is None or customs_qty is None:
            results.append(_make_result(rule, customs_docs[0], "unverifiable", issue_desc="数量字段无法提取"))
        else:
            try:
                cq, eq = float(customs_qty), float(entrust_qty)
                if cq > eq and _amount_diff_pct(cq, eq) > tolerance_pct:
                    results.append(
                        _make_result(
                            rule, customs_docs[0], "fail",
                            issue_desc=f"报关单数量 {cq} 大于委托单数量 {eq}（超容差 {tolerance_pct}%）",
                            detail={"customs_qty": cq, "entrust_qty": eq, "diff_pct": _amount_diff_pct(cq, eq)},
                        )
                    )
                else:
                    results.append(_make_result(rule, customs_docs[0], "pass"))
            except (TypeError, ValueError):
                results.append(_make_result(rule, customs_docs[0], "unverifiable", issue_desc="数量字段无法解析为数值"))

    # 3.4 一对多总额比对：收款总额 / 付款总额 / 增值税发票价税合计
    receive_docs = _docs_by_type(docs, DOC_RECEIVE_VOUCHER)
    pay_docs = _docs_by_type(docs, DOC_PAY_VOUCHER)
    vat_docs = _docs_by_type(docs, DOC_VAT_INVOICE)

    # aggregate_amount 期望入参形如 [{"fields": {...}}, ...]，
    # 而 document.extracted_fields 本身就是扁平字段 dict，需包一层 fields，否则解析恒为 None。
    receive_total = aggregate_amount(DOC_RECEIVE_VOUCHER, [{"fields": d.extracted_fields} for d in receive_docs]) if receive_docs else None
    pay_total = aggregate_amount(DOC_PAY_VOUCHER, [{"fields": d.extracted_fields} for d in pay_docs]) if pay_docs else None
    vat_total = aggregate_amount(DOC_VAT_INVOICE, [{"fields": d.extracted_fields} for d in vat_docs]) if vat_docs else None

    rule_pay = _find_rule(rules_by_key, DOC_PAY_VOUCHER, CHECK_ACCURACY)

    # 付款总额 vs 增值税发票总额
    if pay_total is not None and vat_total is not None:
        diff = pay_total - vat_total
        pct = _amount_diff_pct(pay_total, vat_total)
        if pct > tolerance_pct:
            results.append(
                _make_result(
                    rule_pay, None, "fail",
                    issue_desc=(
                        f"付款总额 ¥{pay_total:,.2f} 与增值税发票价税合计 ¥{vat_total:,.2f} "
                        f"差额 ¥{diff:,.2f}（{pct:.1f}%），超出 {tolerance_pct}% 容差"
                    ),
                    detail={
                        "pay_total": pay_total, "vat_total": vat_total,
                        "diff": diff, "diff_pct": pct,
                        "pay_count": len(pay_docs), "vat_count": len(vat_docs),
                    },
                )
            )
        else:
            results.append(_make_result(rule_pay, None, "pass", detail={"pay_total": pay_total, "vat_total": vat_total}))

    # 收款总额 vs 付款总额（收 ≤ 付原则，收款不应大于付款）
    if receive_total is not None and pay_total is not None:
        if receive_total > pay_total and _amount_diff_pct(receive_total, pay_total) > tolerance_pct:
            results.append(
                _make_result(
                    rule_pay, None, "fail",
                    issue_desc=f"收款总额 ¥{receive_total:,.2f} 大于付款总额 ¥{pay_total:,.2f}，违反收≤付原则",
                    detail={"receive_total": receive_total, "pay_total": pay_total},
                )
            )

    # 3.5 孤立付款检测：无对应收款的付款
    if pay_docs and not receive_docs:
        results.append(
            _make_result(
                rule_pay, None, "fail",
                issue_desc=f"存在 {len(pay_docs)} 笔付款水单但无收款水单，疑似孤立付款",
                detail={"pay_count": len(pay_docs), "receive_count": 0},
            )
        )

    # 3.6 收货方一致：委托单.客户 == 运单/签收单.收货方
    entrust_customer = _get_field(entrust_docs[0], "客户") if entrust_docs else None
    for dt in (DOC_WAYBILL, DOC_RECEIPT):
        d_list = _docs_by_type(docs, dt)
        if not d_list or not entrust_customer:
            continue
        receiver = _get_field(d_list[0], "收货方", ["收货人"])
        rule = _find_rule(rules_by_key, dt, CHECK_ACCURACY)
        if receiver is None:
            results.append(_make_result(rule, d_list[0], "unverifiable", issue_desc=f"{dt} 收货方字段无法提取"))
        elif str(receiver).strip() != str(entrust_customer).strip():
            results.append(
                _make_result(
                    rule, d_list[0], "fail",
                    issue_desc=f"{dt} 收货方 [{receiver}] 与委托单客户 [{entrust_customer}] 不一致",
                    detail={"receiver": receiver, "customer": entrust_customer},
                )
            )
        else:
            results.append(_make_result(rule, d_list[0], "pass"))

    return results


# ============ 检查4：时间逻辑 ============
def _check_time_logic(
    db: Session,
    docs: list[Document],
    rules_by_key: dict[tuple[str, str], list[Rule]],
    contract: Contract,
) -> list[ReviewResult]:
    """时间逻辑检查。"""
    results: list[ReviewResult] = []
    allow_same_day = settings.allow_same_day_receive_pay

    agency_docs = _docs_by_type(docs, DOC_AGENCY_AGREEMENT)
    entrust_docs = _docs_by_type(docs, DOC_ENTRUST_CONFIRM)
    customs_docs = _docs_by_type(docs, DOC_CUSTOMS_DECLARATION)
    waybill_docs = _docs_by_type(docs, DOC_WAYBILL)
    receipt_docs = _docs_by_type(docs, DOC_RECEIPT)
    receive_docs = _docs_by_type(docs, DOC_RECEIVE_VOUCHER)
    pay_docs = _docs_by_type(docs, DOC_PAY_VOUCHER)
    warehouse_docs = _docs_by_type(docs, DOC_WAREHOUSE_INOUT)

    # 4.1 协议时间 ⊇ 合同时间
    if agency_docs and entrust_docs:
        a_start = _get_field(agency_docs[0], "协议开始日期")
        a_end = _get_field(agency_docs[0], "协议结束日期")
        e_date = _get_field(entrust_docs[0], "签订日期")
        rule = _find_rule(rules_by_key, DOC_AGENCY_AGREEMENT, CHECK_TIME_LOGIC)
        if a_start and a_end and e_date:
            r1 = _compare_dates(a_start, e_date, allow_same_day=True)
            r2 = _compare_dates(e_date, a_end, allow_same_day=True)
            if r1 == "pass" and r2 == "pass":
                results.append(_make_result(rule, None, "pass"))
            elif r1 == "unverifiable" or r2 == "unverifiable":
                results.append(_make_result(rule, None, "unverifiable", issue_desc="协议/合同日期无法解析"))
            else:
                results.append(
                    _make_result(
                        rule, None, "fail",
                        issue_desc=f"委托单签订日期 {e_date} 不在协议期 [{a_start}, {a_end}] 内",
                        detail={"agree_start": a_start, "agree_end": a_end, "sign_date": e_date},
                    )
                )
        else:
            results.append(_make_result(rule, None, "unverifiable", issue_desc="协议/合同日期字段无法提取"))

    # 4.2 合同 < 报关 < 提单/签收
    e_date = _get_field(entrust_docs[0], "签订日期") if entrust_docs else None
    c_date = _get_field(customs_docs[0], "出口日期", ["申报日期"]) if customs_docs else None
    w_date = _get_field(waybill_docs[0], "起运日期") if waybill_docs else None
    r_date = _get_field(receipt_docs[0], "签收日期") if receipt_docs else None

    rule_c = _find_rule(rules_by_key, DOC_CUSTOMS_DECLARATION, CHECK_TIME_LOGIC)
    if e_date and c_date:
        res = _compare_dates(e_date, c_date, allow_same_day=True)
        if res == "fail":
            results.append(
                _make_result(
                    rule_c, customs_docs[0] if customs_docs else None, "fail",
                    issue_desc=f"报关日期 {c_date} 早于合同签订日期 {e_date}",
                    detail={"contract_date": e_date, "customs_date": c_date},
                )
            )
        elif res == "pass":
            results.append(_make_result(rule_c, customs_docs[0] if customs_docs else None, "pass"))
        else:
            results.append(_make_result(rule_c, customs_docs[0] if customs_docs else None, "unverifiable", issue_desc="日期无法解析"))

    if c_date and r_date:
        rule_r = _find_rule(rules_by_key, DOC_RECEIPT, CHECK_TIME_LOGIC)
        res = _compare_dates(c_date, r_date, allow_same_day=True)
        if res == "fail":
            results.append(
                _make_result(
                    rule_r, receipt_docs[0], "fail",
                    issue_desc=f"签收日期 {r_date} 早于报关日期 {c_date}",
                    detail={"customs_date": c_date, "receipt_date": r_date},
                )
            )

    # 4.3 收 ≤ 付（允许同日 T+0）
    if receive_docs and pay_docs:
        rule_p = _find_rule(rules_by_key, DOC_PAY_VOUCHER, CHECK_TIME_LOGIC)
        # 取最早的收款日期与最早的付款日期比较
        recv_dates = [_get_field(d, "收款日期") for d in receive_docs]
        pay_dates = [_get_field(d, "付款日期") for d in pay_docs]
        recv_dates = [d for d in recv_dates if d]
        pay_dates = [d for d in pay_dates if d]
        if recv_dates and pay_dates:
            earliest_recv = min(recv_dates)
            earliest_pay = min(pay_dates)
            res = _compare_dates(earliest_recv, earliest_pay, allow_same_day=allow_same_day)
            if res == "fail":
                results.append(
                    _make_result(
                        rule_p, None, "fail",
                        issue_desc=(
                            f"最早付款日期 {earliest_pay} 早于最早收款日期 {earliest_recv}，"
                            f"违反收≤付原则{'（允许同日）' if allow_same_day else '（严格模式）'}"
                        ),
                        detail={"earliest_recv": earliest_recv, "earliest_pay": earliest_pay, "allow_same_day": allow_same_day},
                    )
                )
            elif res == "pass":
                results.append(_make_result(rule_p, None, "pass"))
            else:
                results.append(_make_result(rule_p, None, "unverifiable", issue_desc="收付日期无法解析"))
        else:
            results.append(_make_result(rule_p, None, "unverifiable", issue_desc="收付日期字段无法提取"))

    # 4.4 入库 ≥ 出库（出入仓单）
    if warehouse_docs:
        rule_w = _find_rule(rules_by_key, DOC_WAREHOUSE_INOUT, CHECK_TIME_LOGIC)
        in_dates = [parse_date(str(_get_field(d, "日期") or "")) for d in warehouse_docs]
        # 简化：多张出入仓单时取所有日期判断是否有逆序
        in_dates_sorted = [d for d in in_dates if d]
        if len(in_dates_sorted) >= 2:
            # 检查是否非递减
            ok = all(in_dates_sorted[i] <= in_dates_sorted[i + 1] for i in range(len(in_dates_sorted) - 1))
            if not ok:
                results.append(
                    _make_result(
                        rule_w, warehouse_docs[0], "fail",
                        issue_desc="出入仓日期顺序异常（出库早于入库）",
                        detail={"dates": in_dates_sorted},
                    )
                )

    return results


# ============ 结果过滤 ============

# "字段无法提取"类 unverifiable 的典型模式
_UNEXTRACTABLE_PATTERN = re.compile(r"字段无法提取|日期字段无法提取|日期无法解析|字段无法解析")


def _dedup_key(item: dict) -> tuple:
    """生成去重键：同一 (result, check_category, doc_type, issue_desc, doc_id) 视为重复。"""
    return (
        item.get("result"),
        item.get("check_category"),
        item.get("doc_type"),
        item.get("issue_desc"),
        str(item.get("doc_id") or ""),
    )


def _filter_results(items: list[dict]) -> list[dict]:
    """过滤审查结果：
    1. 去除完全重复项（同一结果/类型/描述/doc_id）
    2. 将同类"字段无法提取"的 unverifiable 合并为一条摘要，
       避免几十条"X.字段 与 Y.字段 字段无法提取"淹没真正的 fail
    """
    # ---- 去重 ----
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for item in items:
        key = _dedup_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    # ---- 合并"字段无法提取"类 unverifiable ----
    unextractable: list[dict] = []   # 需要合并的项
    kept: list[dict] = []            # 保留的项
    for item in deduped:
        if (
            item.get("result") == "unverifiable"
            and item.get("issue_desc")
            and _UNEXTRACTABLE_PATTERN.search(item["issue_desc"])
        ):
            unextractable.append(item)
        else:
            kept.append(item)

    if not unextractable:
        return kept

    # 按 check_category 分组合并
    groups: dict[str, list[dict]] = {}
    for item in unextractable:
        cat = item.get("check_category") or "其他"
        groups.setdefault(cat, []).append(item)

    for cat, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        # 合并为摘要项：doc_type 列出涉及的文件类型
        doc_types = sorted({it.get("doc_type") or "" for it in group})
        # 按 doc_type 维度统计规则跳过数，而非简单取 issue_desc 前 N 条
        dt_counts: dict[str, int] = {}
        for it in group:
            d = it.get("doc_type") or ""
            dt_counts[d] = dt_counts.get(d, 0) + 1
        # 按跳过数量降序排列，取前 5
        sorted_dts = sorted(dt_counts.items(), key=lambda x: -x[1])
        examples = [f"{dt}（涉及 {cnt} 条规则）" for dt, cnt in sorted_dts[:5]]
        merged = {
            "id": group[0]["id"],
            "rule_id": None,
            "rule_text": None,
            "doc_type": "、".join(dt for dt in doc_types if dt) or None,
            "check_category": cat,
            "doc_id": None,
            "doc_name": None,
            "result": "unverifiable",
            "issue_desc": f"共 {len(group)} 条规则核验项被跳过，涉及: {'、'.join(dt for dt in doc_types if dt)}",
            "detail": {
                "skipped_rule_count": len(group),
                "doc_types": doc_types,
                "examples": examples,
            },
            "suggestion": "请补充上传相关文件或确认文件 OCR 质量后重新审查",
        }
        kept.append(merged)

    # 保持排序：fail > unverifiable > pass
    priority = {"fail": 0, "unverifiable": 1, "pass": 2}
    kept.sort(key=lambda r: (priority.get(r.get("result", ""), 3), r.get("check_category") or "", r.get("doc_type") or ""))
    return kept


def _recompute_summary(items: list[dict]) -> dict:
    """根据过滤后的结果重算汇总。"""
    return {
        "total": len(items),
        "pass": sum(1 for r in items if r.get("result") == "pass"),
        "fail": sum(1 for r in items if r.get("result") == "fail"),
        "unverifiable": sum(1 for r in items if r.get("result") == "unverifiable"),
    }


# ============ 结果查询 ============
def get_task_status(db: Session, task_id: uuid.UUID) -> Optional[ReviewTask]:
    return db.get(ReviewTask, task_id)


def get_results_by_rule(db: Session, task_id: uuid.UUID) -> dict:
    """按规则维度视图：所有结果列表 + 汇总。"""
    from sqlalchemy import select

    task = db.get(ReviewTask, task_id)
    if task is None:
        return {"task_id": task_id, "results": [], "summary": {}}

    stmt = (
        select(ReviewResult)
        .where(ReviewResult.task_id == task_id)
        .order_by(ReviewResult.check_category, ReviewResult.doc_type)
    )
    results = list(db.execute(stmt).scalars().all())

    items = []
    for r in results:
        doc_name = r.document.file_name if r.document else None
        items.append(
            {
                "id": str(r.id),
                "rule_id": str(r.rule_id) if r.rule_id else None,
                "rule_text": r.rule_text,
                "doc_type": r.doc_type,
                "check_category": r.check_category,
                "doc_id": str(r.doc_id) if r.doc_id else None,
                "doc_name": doc_name,
                "result": r.result,
                "issue_desc": r.issue_desc,
                "detail": r.detail,
                "suggestion": r.suggestion,
            }
        )

    # 过滤：去重 + 合并 unverifiable 字段缺失噪声
    items = _filter_results(items)
    summary = _recompute_summary(items)

    return {"task_id": str(task_id), "results": items, "summary": summary}


def get_results_by_doc(db: Session, task_id: uuid.UUID) -> dict:
    """按文档维度视图：每个文件参与的所有检查结果。"""
    from sqlalchemy import select

    task = db.get(ReviewTask, task_id)
    if task is None:
        return {"task_id": task_id, "docs": [], "summary": {}}

    stmt = (
        select(ReviewResult)
        .where(ReviewResult.task_id == task_id)
        .order_by(ReviewResult.doc_id)
    )
    results = list(db.execute(stmt).scalars().all())

    # 先构造全部 items 列表，便于统一过滤
    all_items: list[dict] = []
    for r in results:
        doc_id_str = str(r.doc_id) if r.doc_id else None
        item = {
            "id": str(r.id),
            "rule_id": str(r.rule_id) if r.rule_id else None,
            "rule_text": r.rule_text,
            "doc_type": r.doc_type,
            "check_category": r.check_category,
            "doc_id": doc_id_str,
            "doc_name": r.document.file_name if r.document else None,
            "result": r.result,
            "issue_desc": r.issue_desc,
            "detail": r.detail,
            "suggestion": r.suggestion,
        }
        all_items.append(item)

    # 过滤：去重 + 合并 unverifiable 字段缺失噪声
    filtered_items = _filter_results(all_items)
    summary = _recompute_summary(filtered_items)

    # 按文档分组
    docs_map: dict[str, dict] = {}
    no_doc_results: list[dict] = []
    for item in filtered_items:
        # 过滤后合并项的 doc_id 被置空，归到"未绑定文件"组
        doc_id_str = item.get("doc_id")
        if not doc_id_str:
            no_doc_results.append(item)
            continue
        if doc_id_str not in docs_map:
            # 从原始结果中找文档元信息
            orig = next((r for r in results if str(r.id) == item["id"]), None)
            doc = orig.document if orig else None
            docs_map[doc_id_str] = {
                "doc_id": doc_id_str,
                "file_name": doc.file_name if doc else item.get("doc_name"),
                "doc_type": item.get("doc_type") or (doc.doc_type if doc else None),
                "results": [],
            }
        docs_map[doc_id_str]["results"].append(item)

    docs_list = list(docs_map.values())
    # 有问题的文件置顶
    docs_list.sort(
        key=lambda d: (
            0 if any(r["result"] in ("fail", "unverifiable") for r in d["results"]) else 1,
            d.get("file_name") or "",
        )
    )
    if no_doc_results:
        docs_list.insert(0, {"file_name": None, "doc_type": None, "results": no_doc_results})

    return {"task_id": str(task_id), "docs": docs_list, "summary": summary}
