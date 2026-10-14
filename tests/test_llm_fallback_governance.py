# -*- coding: utf-8 -*-
"""兜底治理回归测试：兜底强制 + 全量复核（异议不改判）。

背景：确定性引擎判不了的结果曾静默停在「无法核验」——LLM 兜底的候选筛选、调用成败、
是否逐条作答都是隐式过程，人工看不出"其实没兜底过"。同时，允许 LLM 直接改判会让
审查结论不可复现（审计场景不可接受）。

本测试验证：
1. 兜底留痕：LLM 调用失败 / 漏答某条 / 完全没跑到，都要留下 llm_fallback 痕迹，
   并把该条标成「需人工复核」；
2. 兜底强制收口：候选结果缺痕迹时补标"需人工确认"；
3. 全量复核只提异议：LLM 存疑不改写 result，只标「需人工复核」并重开 pass 项状态；
   低于阈值不打扰人工；调用异常不影响任何结论。

运行：backend/.venv/Scripts/python.exe tests/test_llm_fallback_governance.py
前置：无需数据库 / 网络（LLM 客户端用替身替换）。
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.llm_client import LLMError  # noqa: E402
from app.models import Document, ReviewResult  # noqa: E402
import app.services.llm_review_service as svc  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


class _FakeLLM:
    """chat_json 替身：返回给定 payload 或抛出指定异常。"""

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def chat_json(self, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


def _patch_llm(fake: _FakeLLM) -> None:
    svc.get_llm_client = lambda: fake  # type: ignore[assignment]


def _unverifiable_result(rule_text: str) -> ReviewResult:
    """构造一条"引擎判不了"的结果（复合字段取不到值 → 无法核验）。"""
    return ReviewResult(
        rule_text=rule_text,
        check_category="数量一致性核验",
        result="unverifiable",
        status="open",
        status_history=[],
        source="graph",
        issue_desc="出口报关单.件数*数量 或 送货单.数量*箱数 字段数据缺失",
        detail={
            "reason": "field_data_missing",
            "evidence": {
                "source": {
                    "docs": [],
                    "missing_fields": [
                        {"field": "件数*数量", "reason": "field_missing", "doc_files": ["报关单.pdf"]}
                    ],
                },
                "target": {
                    "docs": [],
                    "missing_fields": [
                        {"field": "数量*箱数", "reason": "field_missing", "doc_files": ["送货单.pdf"]}
                    ],
                },
            },
        },
    )


def _pass_result(rule_text: str) -> ReviewResult:
    return ReviewResult(
        rule_text=rule_text,
        check_category="金额一致性核验",
        result="pass",
        status="closed",
        status_history=[{"status": "closed", "at": "t0", "by": "system", "note": "审查生成"}],
        source="graph",
        detail={"src_sum": 2932.88, "tgt_sum": 2932.88, "evidence": {"source": {"docs": [{"value": 2932.88}]}}},
    )


def _docs() -> list[Document]:
    return [Document(doc_type="出口报关单", file_name="报关单.pdf", extracted_fields={"数量": 990.0})]


CONTRACT = SimpleNamespace(contract_no="UNKNOWN-9aafc468", alias_list=[])


def main() -> None:
    original_get_llm = svc.get_llm_client
    original_get_setting = svc.get_setting
    try:
        print("1. 兜底留痕：LLM 调用失败")
        r1 = _unverifiable_result("报关单数量应等于送货单数量乘以箱数之和")
        _patch_llm(_FakeLLM(error=LLMError("boom", "network")))
        changed = svc.semantic_adjudication_fallback(None, CONTRACT, _docs(), [r1])
        check("调用失败不改判", changed == 0 and r1.result == "unverifiable", r1.result)
        check("调用失败留痕 llm_error", (r1.detail.get("llm_fallback") or {}).get("outcome") == "llm_error", str(r1.detail.get("llm_fallback")))
        check("调用失败标需人工复核", r1.detail.get("manual_review_required") is True)
        check("调用失败写入问题描述", "需人工确认" in (r1.issue_desc or ""), str(r1.issue_desc))

        print("2. 兜底留痕：LLM 漏答其中一条")
        r2a = _unverifiable_result("规则A")
        r2b = _unverifiable_result("规则B")
        _patch_llm(_FakeLLM(payload={"results": [
            {"index": 0, "condition_met": None, "result": "pass", "confidence": 0.9,
             "reason": "按件数*数量=990 与送货单 990 一致"},
        ]}))
        changed = svc.semantic_adjudication_fallback(None, CONTRACT, _docs(), [r2a, r2b])
        check("作答条被改判", changed == 1 and r2a.result == "pass", f"{r2a.result}/{changed}")
        check("作答条留痕 attempted", (r2a.detail.get("llm_fallback") or {}).get("outcome") == "attempted", str(r2a.detail.get("llm_fallback")))
        check("作答条不再标人工", not r2a.detail.get("manual_review_required"))
        check("漏答条留痕 no_verdict", (r2b.detail.get("llm_fallback") or {}).get("outcome") == "no_verdict", str(r2b.detail.get("llm_fallback")))
        check("漏答条标需人工复核", r2b.detail.get("manual_review_required") is True and r2b.result == "unverifiable")

        print("3. 兜底强制收口")
        r3 = _unverifiable_result("规则C")
        r4 = _unverifiable_result("规则D")
        r4.detail["llm_fallback"] = {"attempted": True, "outcome": "attempted"}
        r5 = ReviewResult(rule_text="规则E", check_category="信息准确性", result="pass", status="closed",
                          status_history=[], source="graph", detail={})
        r6 = ReviewResult(rule_text="规则F", check_category="信息准确性", result="unverifiable", status="open",
                          status_history=[], source="llm", issue_desc="LLM 判不了",
                          detail={"evidence": {"source": {"docs": [{"value": 1}]}}})
        added = svc.ensure_fallback_coverage(None, CONTRACT, _docs(), [r3, r4, r5, r6])
        check("未兜底候选被补标", added == 1 and r3.detail.get("manual_review_required") is True, f"added={added}")
        check("补标写 not_covered", (r3.detail.get("llm_fallback") or {}).get("outcome") == "not_covered", str(r3.detail.get("llm_fallback")))
        check("已有痕迹不被重复处理", not r4.detail.get("manual_review_required"))
        check("非候选不受影响", not r5.detail.get("manual_review_required"))
        check("LLM 自身结论不受影响", not r6.detail.get("manual_review_required"))

        print("4. 全量复核：存疑不改判")
        r7 = _pass_result("若报关单币种为美元，则报关单总价金额之和应等于增值税发票共计美元")
        _patch_llm(_FakeLLM(payload={"results": [
            {"index": 0, "agree": False, "suggested_result": "fail", "confidence": 0.85,
             "reason": "发票为人民币折算，口径不同"},
        ]}))
        marked = svc.semantic_challenge_review(None, CONTRACT, _docs(), [r7])
        check("复核存疑不改判", marked == 1 and r7.result == "pass", f"{r7.result}/{marked}")
        check("复核存疑写证据", (r7.detail.get("llm_challenge") or {}).get("agree") is False, str(r7.detail.get("llm_challenge")))
        check("复核存疑标人工复核", r7.detail.get("manual_review_required") is True)
        check("pass 项被重开状态", r7.status == "open", r7.status)
        check("状态历史留痕", (r7.status_history or [])[-1].get("by") == "llm_challenge", str(r7.status_history))
        check("问题描述含复核结论", "需人工复核" in (r7.issue_desc or ""), str(r7.issue_desc))
        check("未改写置信度", r7.confidence is None, str(r7.confidence))

        print("5. 低于阈值的异议不打扰人工")
        r8 = _pass_result("规则G")
        _patch_llm(_FakeLLM(payload={"results": [
            {"index": 0, "agree": False, "suggested_result": "unverifiable", "confidence": 0.4, "reason": "不太确定"},
        ]}))
        marked = svc.semantic_challenge_review(None, CONTRACT, _docs(), [r8])
        check("低置信异议不标人工", marked == 0 and not r8.detail.get("manual_review_required"), f"marked={marked}")
        check("低置信异议仍留证据", (r8.detail.get("llm_challenge") or {}).get("reason") == "不太确定")
        check("低置信不改判", r8.result == "pass" and r8.status == "closed", f"{r8.result}/{r8.status}")

        print("6. 最低置信度阈值可由设置调整")
        r9 = _pass_result("规则H")
        svc.get_setting = (  # type: ignore[assignment]
            lambda db, key, default=None: 0.95
            if key == "review.llm_challenge_min_confidence"
            else original_get_setting(db, key, default)
        )
        _patch_llm(_FakeLLM(payload={"results": [
            {"index": 0, "agree": False, "suggested_result": "fail", "confidence": 0.85, "reason": "口径不同"},
        ]}))
        marked = svc.semantic_challenge_review(None, CONTRACT, _docs(), [r9])
        check("阈值 0.95 时 0.85 异议不标人工", marked == 0 and not r9.detail.get("manual_review_required"), f"marked={marked}")
        svc.get_setting = original_get_setting  # type: ignore[assignment]

        print("7. 复核调用异常不影响结论")
        r10 = _pass_result("规则I")
        before = (r10.result, r10.status, dict(r10.detail or {}))
        _patch_llm(_FakeLLM(error=LLMError("timeout", "network")))
        marked = svc.semantic_challenge_review(None, CONTRACT, _docs(), [r10])
        check(
            "复核异常零改动",
            marked == 0 and r10.result == before[0] and r10.status == before[1] and dict(r10.detail or {}) == before[2],
            f"{r10.result}/{r10.status}",
        )
    finally:
        svc.get_llm_client = original_get_llm  # type: ignore[assignment]
        svc.get_setting = original_get_setting  # type: ignore[assignment]

    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
