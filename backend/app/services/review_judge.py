# -*- coding: utf-8 -*-
"""统一判定（Unified Review Judgement）：一条判定路径 + 一个验证门 + 一个反馈环。

替代此前"图引擎 → B-2 语义复核 → B-3 语义裁决 → B-4 全量复核 → 兜底收口"的
多路径串联（每段一个候选筛选器，每段都可能静默丢弃）：

    intent（条款 + 意图 + 确定性初判）
      + evidence bundle（全部单据事实，带出处的唯一真相）
      └─► 大模型判定（必须带 citations / checks / bindings）
            └─► Python 验证门（引用存在性 + 数值/日期重算 + 规则一致性）
                  ├─ 通过 → 落库（overrides 显式留痕）
                  └─ 不通过 → 带失败原因重试一次 → 仍不通过 → 人工确认
            └─► 绑定沉淀（下次遇到同样说法不必再改代码）

关键点：**大模型负责理解与判定，Python 负责算术/存在性/一致性的机器验证**。
验证门让"模型是否说错"变成可检测事件，而不是审计风险。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..llm_client import LLMError, get_llm_client
from ..models import Document, ReviewResult, Rule
from . import result_meta
from .field_binding_service import (
    DEFAULT_PROMOTE_THRESHOLD,
    learned_role_fields,
    promote_bindings,
    record_bindings,
)
from .review_evidence import (
    build_evidence_bundle,
    bundle_payload,
    candidate_keys,
    evaluate_check,
    resolve_citation,
)
from .review_intent import compile_intents, intent_payload
from .settings_service import get_prompt, get_setting

logger = logging.getLogger(__name__)

# 置信度护栏：与我们没有证据时的"猜"相比，"判不了"更诚实
FAIL_CONFIDENCE_THRESHOLD = 0.8
PASS_CONFIDENCE_THRESHOLD = 0.6
# 单批意图数：控制单次上下文规模
JUDGE_BATCH_SIZE = 8
# 未通过验证门时的重试次数（带失败原因重问）。重试只针对没过的条目，成本很小；
# 实测给到 2 次能把"check 写法不可重算"这类问题收敛掉。
JUDGE_MAX_RETRIES = 2

_VERDICTS = ("pass", "fail", "unverifiable")


class JudgeReport:
    """一次统一判定的可观测结果（写入审查任务摘要与日志）。"""

    def __init__(self) -> None:
        self.enabled = True
        self.items_total = 0
        self.items_sent = 0
        self.items_skipped = 0
        self.verified = 0
        self.overrides = 0
        self.created = 0
        self.retried = 0
        self.escalated = 0
        self.llm_error = 0
        self.citation_errors = 0
        self.recompute_errors = 0
        self.bindings_recorded = 0
        self.bindings_promoted = 0
        self.bindings_dropped = 0
        self.role_bindings = 0

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "items_total": self.items_total,
            "items_sent": self.items_sent,
            "items_skipped": self.items_skipped,
            "verified": self.verified,
            "overrides": self.overrides,
            "created": self.created,
            "retried": self.retried,
            "escalated": self.escalated,
            "llm_error": self.llm_error,
            "citation_errors": self.citation_errors,
            "recompute_errors": self.recompute_errors,
            "bindings_recorded": self.bindings_recorded,
            "bindings_promoted": self.bindings_promoted,
            "bindings_dropped": self.bindings_dropped,
            "role_bindings": self.role_bindings,
        }


# ============ 结果定位与落地 ============

def result_rule_id(r: ReviewResult) -> str:
    """从结果里取出规则流水号（图谱结果历史上只把它放在证据链里）。"""
    detail = r.detail or {}
    for candidate in (
        detail.get("rule_id"),
        (detail.get("rule") or {}).get("rule_id") if isinstance(detail.get("rule"), dict) else None,
        ((detail.get("evidence") or {}).get("rule") or {}).get("rule_id")
        if isinstance(detail.get("evidence"), dict)
        else None,
    ):
        if candidate:
            return str(candidate)
    return ""


def _prelim_index(results: list[ReviewResult]) -> dict[tuple[str, str, str], ReviewResult]:
    out: dict[tuple[str, str, str], ReviewResult] = {}
    for r in results:
        rid = result_rule_id(r)
        if rid:
            out[(rid, str(r.graph_source or ""), str(r.graph_target or ""))] = r
    return out


def _locate_result(
    intent: dict,
    prelim_index: dict[tuple[str, str, str], ReviewResult],
) -> Optional[ReviewResult]:
    """按 (规则流水号, 图谱节点对) 找到对应结果；退化为同规则同节点、再退化为同规则同条款。"""
    rid = str(intent.get("rule_id") or "")
    nodes = intent.get("graph_nodes") or {}
    src, tgt = str(nodes.get("source") or ""), str(nodes.get("target") or "")
    hit = prelim_index.get((rid, src, tgt))
    if hit is not None:
        return hit
    # 节点名可能带 |SUM 等聚合后缀：按前缀再试一次
    for (r_id, r_src, r_tgt), r in prelim_index.items():
        if r_id != rid:
            continue
        if src and r_src.split("|", 1)[0] == src.split("|", 1)[0] and \
                tgt and r_tgt.split("|", 1)[0] == tgt.split("|", 1)[0]:
            return r
    # 同规则且条款一致（自由文本/齐套/用印类意图）
    clause = str(intent.get("clause") or "")
    for (r_id, _s, _t), r in prelim_index.items():
        if r_id == rid and clause and str(r.rule_text or "") == clause:
            return r
    return None


def _escalate(r: ReviewResult, reason: str, errors: Optional[list[str]] = None) -> None:
    """验证门不通过 → 显性交人工。

    关键取舍：若该条已有**可复现的确定性结论**（source=graph 且 result ∈ pass/fail），
    保留结论并标「需人工复核」——大模型没能给出可验证的判定，不构成推翻确定性结论的理由；
    把 pass/fail 降级成"无法核验"是信息损失（历史上就是这么丢结论的）。
    只有本来就没结论（unverifiable / 新建）的条目才落到 unverifiable。
    """
    detail = dict(r.detail or {})
    detail["manual_review_required"] = True
    detail["manual_review_reason"] = reason
    if errors:
        detail["judge_errors"] = errors[:8]
    keep_deterministic = r.source == "graph" and r.result in ("pass", "fail")
    if keep_deterministic:
        detail["judge_challenged"] = True
        r.detail = detail
        if reason and reason not in (r.issue_desc or ""):
            r.issue_desc = f"{r.issue_desc}；{reason}" if r.issue_desc else reason
        r.status = "open"
        r.severity, r.deviation = result_meta.compute_severity(
            r.result, r.check_category, detail
        )
        return
    r.detail = detail
    r.result = "unverifiable"
    r.issue_desc = reason
    r.status = "open"
    r.severity, r.deviation = result_meta.compute_severity(r.result, r.check_category, detail)


def _apply_verdict(
    r: ReviewResult,
    intent: dict,
    verdict: dict,
    *,
    citation_meta: list[dict],
) -> None:
    """把通过验证门的判定落到结果上。"""
    old = r.result
    new_result = verdict["verdict"]
    detail = dict(r.detail or {})
    detail["judge"] = {
        "intent_id": intent.get("intent_id"),
        "verified": True,
        "confidence": verdict.get("confidence"),
        "reason": verdict.get("reason"),
        "checks": verdict.get("checks") or [],
        "citations": verdict.get("citations") or [],
        "citation_resolution": citation_meta,
        "condition_met": verdict.get("condition_met"),
        "modeling_warnings": intent.get("warnings") or [],
    }
    if old != new_result:
        change = {
            "from": old,
            "to": new_result,
            "why": verdict.get("reason"),
            # override：推翻了确定性的 pass/fail；resolved：把"判不了"收敛成结论
            "kind": "override" if old in ("pass", "fail") else "resolved",
        }
        detail["adjudicated_from"] = old
        detail["original_issue"] = r.issue_desc or ""
        detail["judge_decision"] = change
        if old in ("pass", "fail"):
            detail["judge_override"] = change
    r.result = new_result
    r.issue_desc = _issue_text(intent, verdict)
    r.confidence = verdict.get("confidence")
    r.detail = detail
    r.source = "judge"
    r.status = result_meta.default_status(new_result)
    r.severity, r.deviation = result_meta.compute_severity(new_result, r.check_category, detail)
    if new_result == "unverifiable":
        detail["manual_review_required"] = True
        detail["manual_review_reason"] = "判定结论为无法核验，需人工确认"
        r.detail = detail


def _issue_text(intent: dict, verdict: dict) -> str:
    reason = str(verdict.get("reason") or "").strip()
    if verdict["verdict"] == "pass":
        if verdict.get("condition_met") is False:
            return f"触发条件不成立，规则不适用（判定依据：{reason}）" if reason else "触发条件不成立，规则不适用"
        return f"判定通过（依据：{reason}）" if reason else "判定通过"
    if verdict["verdict"] == "fail":
        return f"不符合规则要求（依据：{reason}）" if reason else "不符合规则要求"
    return f"无法核验：{reason}" if reason else "无法核验"


# ============ 验证门 ============

def verify_verdict(
    bundle: dict,
    intent: dict,
    verdict: Any,
) -> tuple[bool, list[str], list[dict], list[dict]]:
    """验证一条判定。Returns (通过, 错误清单, 引用解析明细, 被丢弃的绑定)。

    致命项（打回重判）：结论取值、置信度护栏、checks 重算、citations 存在性、结论自洽。
    非致命项（丢弃并留痕）：bindings —— 它只喂"字段名等价"学习，不影响结论正确性，
    指向不存在字段的绑定直接丢掉（避免污染本体，也不该因此让人工重判）。
    """
    errors: list[str] = []
    citation_meta: list[dict] = []
    dropped_bindings: list[dict] = []
    if not isinstance(verdict, dict):
        return False, ["verdict_not_object"], citation_meta, dropped_bindings
    result = str(verdict.get("verdict") or "").strip()
    if result not in _VERDICTS:
        errors.append(f"verdict_invalid:{result or '-'}")
        return False, errors, citation_meta

    confidence = verdict.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    if result == "fail" and (confidence is None or confidence < FAIL_CONFIDENCE_THRESHOLD):
        errors.append(f"fail_confidence_too_low:{confidence}")
    if result == "pass" and (confidence is None or confidence < PASS_CONFIDENCE_THRESHOLD):
        errors.append(f"pass_confidence_too_low:{confidence}")

    condition_met = verdict.get("condition_met")
    if condition_met is False and result != "pass":
        errors.append("condition_not_met_but_verdict_not_pass")
    if condition_met not in (True, False, None):
        errors.append("condition_met_invalid")

    # 1) checks：大模型声明的比较/算术，按证据包重算
    checks = verdict.get("checks") or []
    if not isinstance(checks, list):
        errors.append("checks_not_list")
        checks = []
    failed_checks = 0
    for check in checks:
        outcome = evaluate_check(bundle, check)
        if not outcome.get("ok"):
            errors.append(f"check_failed:{outcome.get('reason')}")
        elif isinstance(check, dict) and check.get("ok") is False:
            failed_checks += 1
    # 结论必须与自己声明的比较自洽：全部 check 通过却说"不通过"（或反之）属自相矛盾，
    # 打回重判——这类"理由说通过、字段写不通过"的输出此前会直接污染结论。
    if result == "fail" and checks and failed_checks == 0:
        errors.append("fail_without_failing_check")
    if result == "pass" and failed_checks:
        errors.append("pass_with_failing_check")
    # 2) citations：必须逐字来自证据包
    citations = verdict.get("citations") or []
    if not isinstance(citations, list):
        errors.append("citations_not_list")
        citations = []
    if result in ("pass", "fail") and not citations:
        errors.append("citations_required")
    for cite in citations:
        if not isinstance(cite, dict):
            errors.append("citation_not_object")
            continue
        outcome = resolve_citation(
            bundle, cite.get("doc"), cite.get("key"), cite.get("value")
        )
        if not outcome.get("ok"):
            errors.append(f"citation_invalid:{outcome.get('reason')}")
            continue
        citation_meta.append(
            {
                # cited_key：模型写的规则侧字段名；key：证据包里实际命中的单据字段名
                "cited_key": str(cite.get("key") or ""),
                "doc": outcome.get("doc"),
                "doc_type": outcome.get("doc_type"),
                "key": outcome.get("fact_key"),
                "value": outcome.get("fact_value"),
                "resolved_via": outcome.get("resolved_via"),
                "partial": outcome.get("partial", False),
            }
        )
    # 3) bindings：只能绑定证据包里真实存在的单据字段（不存在的直接丢弃并留痕）
    all_keys = {
        str(f.get("key"))
        for d in bundle.get("docs") or []
        for f in d.get("facts") or []
    }
    for binding in verdict.get("bindings") or []:
        if not isinstance(binding, dict):
            dropped_bindings.append({"reason": "binding_not_object", "binding": binding})
            continue
        doc_key = str(binding.get("doc_key") or "").strip()
        if not doc_key or doc_key not in all_keys:
            dropped_bindings.append(
                {"reason": f"binding_key_not_found:{doc_key or '-'}", "binding": binding}
            )

    # 4) 条款一致性：intent 必须是我们发出去的那条
    if intent.get("rule_id") and verdict.get("rule_id"):
        if str(verdict["rule_id"]) != str(intent["rule_id"]):
            errors.append(f"rule_id_mismatch:{verdict['rule_id']}")
    return (not errors), errors, citation_meta, dropped_bindings


# ============ 主流程 ============

def run_unified_judgement(
    db: Optional[Session],
    tenant_id: Optional[uuid.UUID],
    docs: list[Document],
    rules: list[Rule],
    prelim_results: list[ReviewResult],
    *,
    graph_data: Optional[dict] = None,
) -> JudgeReport:
    """统一判定入口。就地改写 prelim_results / 追加新结果并返回报告。"""
    report = JudgeReport()
    if not docs or not rules:
        report.enabled = False
        return report
    if db is not None and tenant_id is not None:
        enabled = get_setting(db, tenant_id, "review.judge_enabled", True)
    else:
        enabled = True
    if not enabled:
        report.enabled = False
        logger.info("统一判定已关闭（review.judge_enabled=false），保留确定性结论")
        return report

    bundle = build_evidence_bundle(docs)
    intents = compile_intents(rules, graph_data, prelim_results)
    report.items_total = len(intents)
    if not intents:
        report.enabled = False
        return report

    override_deterministic = True
    if db is not None and tenant_id is not None:
        override_deterministic = bool(
            get_setting(db, tenant_id, "review.judge_override_deterministic", True)
        )
    prelim_index = _prelim_index(prelim_results)

    # 送审范围：默认全部送审（不允许静默丢弃）；关闭覆盖时只送"未结案"的
    send: list[dict] = []
    for intent in intents:
        has_prelim = intent.get("prelim") is not None
        if not has_prelim or override_deterministic:
            send.append(intent)
        elif intent["prelim"].get("result") == "unverifiable":
            send.append(intent)
        else:
            report.items_skipped += 1
    report.items_sent = len(send)
    if not send:
        return report

    payload_bundle = bundle_payload(bundle)
    system_prompt = get_prompt(db, tenant_id, "review.judge_system")

    pending = list(send)
    last_failures: dict[str, list[str]] = {}
    llm_error_ids: set[str] = set()
    rejected: dict[str, dict] = {}  # 最近一次被验证门打回的原始判定（留痕供人工复核）
    for attempt in range(JUDGE_MAX_RETRIES + 1):
        if not pending:
            break
        failures: dict[str, list[str]] = {}
        for start_idx in range(0, len(pending), JUDGE_BATCH_SIZE):
            batch = pending[start_idx:start_idx + JUDGE_BATCH_SIZE]
            raw = _call_judge(
                system_prompt,
                payload_bundle,
                bundle,
                batch,
                last_failures if attempt else None,
            )
            if raw is None:
                for intent in batch:
                    if intent["intent_id"] not in llm_error_ids:
                        llm_error_ids.add(intent["intent_id"])
                        report.llm_error += 1
                    failures.setdefault(intent["intent_id"], ["llm_call_failed"])
                continue
            handled: set[str] = set()
            for item in raw:
                if not isinstance(item, dict):
                    continue
                intent_id = str(item.get("intent_id") or "").strip()
                intent = next((x for x in batch if x.get("intent_id") == intent_id), None)
                if intent is None:
                    continue
                handled.add(intent_id)
                ok, errors, citation_meta, dropped_bindings = verify_verdict(
                    bundle, intent, item
                )
                if dropped_bindings:
                    report.bindings_dropped += len(dropped_bindings)
                    item = {**item, "bindings": [
                        b
                        for b in (item.get("bindings") or [])
                        if isinstance(b, dict)
                        and str(b.get("doc_key") or "").strip() not in {
                            str(d.get("binding", {}).get("doc_key") or "")
                            for d in dropped_bindings
                        }
                    ]}
                if not ok:
                    for err in errors:
                        if err.startswith("check_failed:check_recompute_mismatch"):
                            report.recompute_errors += 1
                        elif err.startswith("citation_invalid"):
                            report.citation_errors += 1
                    failures[intent_id] = errors
                    rejected[intent_id] = {
                        "verdict": item.get("verdict"),
                        "confidence": item.get("confidence"),
                        "reason": item.get("reason"),
                        "checks": item.get("checks"),
                        "citations": item.get("citations"),
                    }
                    continue
                target = _materialize(prelim_index, prelim_results, intent)
                old_result = target.result
                _apply_verdict(target, intent, item, citation_meta=citation_meta)
                if old_result != target.result and intent.get("prelim") is not None:
                    report.overrides += 1
                if intent.get("prelim") is None:
                    report.created += 1
                report.verified += 1
                _sediment_bindings(
                    db, tenant_id, intent, item, citation_meta, docs, report
                )
            for intent in batch:
                if intent.get("intent_id") in handled:
                    continue
                failures.setdefault(intent["intent_id"], ["judge_returned_no_verdict"])

        # 只有"确实没结案"的条目才进入重试/交人工——已落库的不再重复处理
        unresolved = [x for x in pending if x.get("intent_id") in failures]
        last_failures = failures
        if unresolved and attempt < JUDGE_MAX_RETRIES:
            report.retried += len(unresolved)
            pending = unresolved
            logger.info("统一判定：%d 项未通过验证门，带失败原因重试一次", len(unresolved))
            continue
        for intent in unresolved:
            errors = failures.get(intent.get("intent_id") or "", [])
            if errors and all(e == "llm_call_failed" for e in errors):
                reason = "LLM 判定调用失败，需人工确认"
            elif errors:
                reason = "判定未通过验证门（" + "；".join(errors[:4]) + "），需人工确认"
            else:
                reason = "判定未返回结论，需人工确认"
            _materialize_and_escalate(
                prelim_index, prelim_results, intent, reason, errors,
                rejected.get(intent.get("intent_id") or ""),
            )
            report.escalated += 1
        pending = []

    if db is not None and tenant_id is not None:
        threshold = get_setting(
            db, tenant_id, "review.binding_promote_threshold", DEFAULT_PROMOTE_THRESHOLD
        )
        try:
            promoted = promote_bindings(db, tenant_id, threshold=threshold)
            report.bindings_promoted += len(promoted)
        except Exception as e:  # pragma: no cover - 沉淀失败不影响审查结论
            logger.warning("字段绑定升级失败（不影响本次判定）：%s", e)
    return report


def _call_judge(
    system_prompt: str,
    payload_bundle: dict,
    bundle: dict,
    batch: list[dict],
    previous_failures: Optional[dict[str, list[str]]],
) -> Optional[list[dict]]:
    """调用大模型判定一批意图。Returns results 列表或 None（调用失败）。"""
    items = []
    for intent in batch:
        item = intent_payload(intent)
        hints: dict[str, list[dict]] = {}
        for need in intent.get("evidence_needed") or []:
            field = need.get("field")
            if not field:
                continue
            candidates = candidate_keys(bundle, str(field))
            if candidates:
                hints[str(field)] = candidates[:3]
        if hints:
            item["field_hints"] = hints
        if previous_failures and intent.get("intent_id") in previous_failures:
            item["verification_failed_last_time"] = previous_failures[intent["intent_id"]][:6]
            item["instruction"] = "上次提交的判定未通过机器验证，请按失败原因修正后重新判定"
        items.append(item)

    user_prompt = (
        "证据包（本案全部单据事实，citations 必须严格取自这里）：\n"
        f"{json.dumps(payload_bundle, ensure_ascii=False, default=str)}\n\n"
        f"待判定项：\n{json.dumps(items, ensure_ascii=False, default=str)}\n\n"
        "请输出 JSON。"
    )
    try:
        llm = get_llm_client()
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            disable_thinking=True,
        )
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        logger.warning("统一判定调用失败（保留确定性结论并交人工）: %s", e)
        return None
    results = resp.get("results")
    if not isinstance(results, list):
        return None
    return results


def _materialize(
    prelim_index: dict[tuple[str, str, str], ReviewResult],
    prelim_results: list[ReviewResult],
    intent: dict,
) -> ReviewResult:
    """找到或创建该意图对应的结果对象。"""
    hit = _locate_result(intent, prelim_index)
    if hit is not None:
        return hit
    nodes = intent.get("graph_nodes") or {}
    r = ReviewResult(
        rule_text=intent.get("clause"),
        doc_type=intent.get("doc_type"),
        check_category=intent.get("check_category"),
        result="unverifiable",
        status="open",
        status_history=[
            {
                "status": "open",
                "at": datetime.now().isoformat(timespec="seconds"),
                "by": "judge",
                "note": "统一判定生成",
            }
        ],
        source="judge",
        graph_source=nodes.get("source"),
        graph_target=nodes.get("target"),
        issue_desc="待判定",
        detail={"rule_id": intent.get("rule_id"), "reason": "judge_pending"},
    )
    prelim_results.append(r)
    key = (
        str(intent.get("rule_id") or ""),
        str(nodes.get("source") or ""),
        str(nodes.get("target") or ""),
    )
    prelim_index[key] = r
    return r


def _materialize_and_escalate(
    prelim_index: dict[tuple[str, str, str], ReviewResult],
    prelim_results: list[ReviewResult],
    intent: dict,
    reason: str,
    errors: Optional[list[str]] = None,
    rejected: Optional[dict] = None,
) -> None:
    r = _materialize(prelim_index, prelim_results, intent)
    detail = dict(r.detail or {})
    detail.setdefault("rule_id", intent.get("rule_id"))
    if rejected:
        # 被打回的原始判定一并留痕：人工能看到"模型当时说了什么、为什么没过机器验证"
        detail["judge_rejected"] = rejected
    detail["judge"] = {"intent_id": intent.get("intent_id"), "verified": False}
    r.detail = detail
    _escalate(r, reason, errors)


def _sediment_bindings(
    db: Optional[Session],
    tenant_id: Optional[uuid.UUID],
    intent: dict,
    verdict: dict,
    citation_meta: list[dict],
    docs: list[Document],
    report: JudgeReport,
) -> None:
    """把验证通过的等价映射沉淀为绑定知识（下一次不必再靠模型现场发现）。"""
    if db is None or tenant_id is None:
        return
    def _bare_field(name: Any) -> str:
        """字段名去掉"文件类型."前缀（证据里可能是节点名）。"""
        text = str(name or "").strip()
        if "." in text and not text.startswith("."):
            head, _, tail = text.partition(".")
            if tail and head and " " not in head:
                return tail.strip()
        return text

    fallback_type = intent.get("doc_type")
    if not fallback_type:
        doc_types = {d.doc_type for d in docs}
        fallback_type = sorted(doc_types)[0] if len(doc_types) == 1 else None

    by_type: dict[str, list[dict]] = {}

    def _add(doc_type: Any, rule_field: Any, doc_key: Any, kind: str) -> None:
        dt = str(doc_type or fallback_type or "").strip()
        rf, dk = _bare_field(rule_field), _bare_field(doc_key)
        if not dt or not rf or not dk or rf == dk:
            return
        by_type.setdefault(dt, []).append({"rule_field": rf, "doc_key": dk, "kind": kind})

    for item in verdict.get("bindings") or []:
        if isinstance(item, dict):
            _add(
                item.get("doc_type") or fallback_type,
                item.get("rule_field"),
                item.get("doc_key"),
                str(item.get("kind") or "alias"),
            )
    # 引用解析时发现的"规则字段名 → 单据字段名"等价，同样沉淀
    # （模型写"报销金额"、证据包里实际命中"报销金额（元）"→ 这是一条可复用的绑定知识）
    for meta in citation_meta or []:
        cited = str(meta.get("cited_key") or "").strip()
        fact_key = str(meta.get("key") or "").strip()
        if cited and fact_key and cited != fact_key:
            _add(meta.get("doc_type"), cited, fact_key, "alias")

    if not by_type:
        return
    total = 0
    try:
        for doc_type, items in by_type.items():
            total += record_bindings(
                db,
                tenant_id,
                doc_type,
                items,
                rule_id=str(intent.get("rule_id") or ""),
            )
    except Exception as e:  # pragma: no cover - 沉淀失败不影响结论
        logger.warning("字段绑定沉淀失败（不影响判定）：%s", e)
        return
    report.bindings_recorded += total
    report.role_bindings += sum(
        1
        for items in by_type.values()
        for b in items
        if str(b.get("kind")) == "role_signature"
    )
