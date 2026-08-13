# -*- coding: utf-8 -*-
"""LLM 语义审查引擎（批次 10 Phase C，双引擎中的引擎 B）。

职责：
1. 定性规则审查：确定性引擎（图谱 COMPARE_TO/REQUIRED/MUST_STAMP）无法表达的
   无 structure 规则（含无文件类型的整批规则），由 LLM 结合规则文本 + 文档提取字段判断；
2. 语义兜底：确定性"字符串相等"失败时，由 LLM 判断是否为同义/别名/格式差异；

护栏（防止 LLM 幻觉污染结论）：
- 只依据提供的文档信息判断，信息缺失 → unverifiable；
- fail 判定要求置信度 >= 0.8；pass 要求 >= 0.6；低于阈值一律降级 unverifiable（待人工确认）；
- 任何调用/解析异常 → 返回空/不改动，不影响确定性结果。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from ..constants import CHECK_COMPLETENESS, CHECK_STAMP
from ..llm_client import LLMError, get_llm_client
from ..models import Document, ReviewResult, Rule
from . import result_meta

logger = logging.getLogger(__name__)

# LLM 审查置信度护栏
FAIL_CONFIDENCE_THRESHOLD = 0.8
PASS_CONFIDENCE_THRESHOLD = 0.6
EQUIVALENT_CONFIDENCE_THRESHOLD = 0.8


# ============ 定性规则审查（引擎 B-1） ============

def collect_unstructured_rules(rules: list[Rule]) -> list[Rule]:
    """筛选确定性引擎未覆盖的规则：
    - 无 structure.assertion 的规则（定性规则）；
    - 齐套性/印章类但缺少文件类型（整批语义，图引擎会跳过）。
    """
    out: list[Rule] = []
    for r in rules:
        if not r.enabled or r.status != "confirmed":
            continue
        if (r.structure or {}).get("assertion"):
            continue  # 图引擎确定性覆盖
        if r.check_category in (CHECK_COMPLETENESS, CHECK_STAMP) and r.doc_type:
            continue  # 图引擎确定性覆盖
        out.append(r)
    return out


def _doc_summary(docs: list[Document], max_fields: int = 12) -> list[dict]:
    """压缩文档 OCR 提取结果供 LLM 审查：只保留非空字段。"""
    summary: list[dict] = []
    for d in docs:
        fields = d.extracted_fields or {}
        slim = {
            k: v for k, v in fields.items()
            if v not in (None, "") and not str(k).startswith("__")
        }
        if len(slim) > max_fields:
            slim = dict(list(slim.items())[:max_fields])
        summary.append(
            {
                "file_name": d.file_name,
                "doc_type": d.doc_type,
                "has_stamp": d.has_stamp,
                "ocr_confidence": round(float(d.ocr_confidence or 0.0), 2),
                "fields": slim,
            }
        )
    return summary


def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if 0.0 <= f <= 1.0 else None
    except (TypeError, ValueError):
        return None


_REVIEW_SYSTEM_PROMPT = """你是贸易单证审查专家。根据提供的文档信息（OCR 提取字段），对每一条自然语言规则给出审查结论。

输出严格 JSON：
{
  "results": [
    {
      "rule_index": 0,
      "result": "pass|fail|unverifiable",
      "confidence": 0-1,
      "issue_desc": "问题描述（pass 时简要说明依据）",
      "evidence": "判定依据：引用具体文档与字段值；无法判断时说明缺失内容",
      "suggestion": "不通过时的修正建议（中文，1-2 句）"
    }
  ]
}

判定规则：
1. 只能依据给定的文档字段信息判断，不得臆造字段值；规则涉及的文件或字段在文档中缺失 → unverifiable
2. 字段无法解析/OCR 置信度过低 → unverifiable
3. 规则为整批/全部文档的定性要求（如"文件应清晰可辨""签字与盖章一致"）时，结合所有文档综合判断；无法从字段证据得出结论 → unverifiable
4. confidence 反映你对结论的确定程度；只有证据充分、无歧义时才给高置信度
5. 没有发现问题 → pass；发现问题 → fail；无法判断 → unverifiable"""


def _rule_label(rule: Rule) -> str:
    scope = rule.scope or {}
    scope_dt = scope.get("doc_types")
    scope_str = "整批/全部" if scope_dt == "ALL" else (
        "、".join(scope_dt) if isinstance(scope_dt, list) and scope_dt else (rule.doc_type or "整批/全部")
    )
    intents = "、".join(rule.intents or []) or rule.check_category or "未分类"
    return f"适用范围={scope_str}；检查意图={intents}；规则文本={rule.rule_text}"


def review_unstructured_rules(
    db,
    contract,
    docs: list[Document],
    rules: list[Rule],
) -> list[dict]:
    """对定性规则执行 LLM 批量审查。

    Returns:
        规范化结果列表，每项含 rule/result/confidence/issue_desc/detail/suggestion。
        任何失败返回空列表（不影响确定性结果）。
    """
    targets = collect_unstructured_rules(rules)
    if not targets:
        return []
    if not docs:
        return []

    rules_block = "\n".join(f"{i}. {_rule_label(r)}" for i, r in enumerate(targets))
    docs_block = json.dumps(_doc_summary(docs), ensure_ascii=False)
    aliases = "、".join(contract.alias_list or []) if contract else ""
    user_prompt = f"""合同号：{contract.contract_no if contract else '-'}（别名：{aliases or '-'}）

文档信息（OCR 提取字段）：
{docs_block}

需要审查的规则：
{rules_block}

请输出 JSON。"""

    try:
        llm = get_llm_client()
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        logger.warning("LLM 定性规则审查失败（跳过引擎 B）: %s", e)
        return []

    results: list[dict] = []
    raw_results = resp.get("results", [])
    if not isinstance(raw_results, list):
        return []

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        idx = item.get("rule_index")
        if not isinstance(idx, int) or not (0 <= idx < len(targets)):
            continue
        rule = targets[idx]
        result = str(item.get("result") or "").strip()
        if result not in ("pass", "fail", "unverifiable"):
            continue
        confidence = _to_float(item.get("confidence"))
        guardrail = ""
        # 护栏：fail 需高置信；pass 需中等置信；不足一律降级 unverifiable 待人工确认
        if result == "fail" and (confidence is None or confidence < FAIL_CONFIDENCE_THRESHOLD):
            result = "unverifiable"
            guardrail = "LLM 判定不通过但置信度不足，降级为待人工确认"
        elif result == "pass" and (confidence is None or confidence < PASS_CONFIDENCE_THRESHOLD):
            result = "unverifiable"
            guardrail = "LLM 判定通过但置信度不足，降级为待人工确认"

        detail: dict[str, Any] = {
            "reason": "llm_review",
            "llm_evidence": str(item.get("evidence") or ""),
            "llm_confidence": confidence,
        }
        if guardrail:
            detail["llm_guardrail"] = guardrail

        issue = str(item.get("issue_desc") or "").strip()
        if not issue:
            issue = "规则未发现违规" if result == "pass" else (
                "无法核验" if result == "unverifiable" else "不符合规则要求"
            )
        results.append(
            {
                "rule": rule,
                "result": result,
                "confidence": confidence,
                "issue_desc": issue,
                "detail": detail,
                "suggestion": str(item.get("suggestion") or "").strip(),
            }
        )
    return results


# ============ 语义兜底：字符串相等失败复核（引擎 B-2） ============

_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([%％]|[千万元]|元)?$")
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


def _is_string_mismatch_item(r: ReviewResult) -> bool:
    """是否适合做 LLM 语义复核：确定性"字符串相等"失败的单项比对。"""
    if r.result != "fail":
        return False
    d = r.detail or {}
    src, tgt = d.get("src"), d.get("tgt")
    if not (isinstance(src, list) and isinstance(tgt, list)):
        return False
    if len(src) != 1 or len(tgt) != 1:
        return False  # 多值聚合语义太复杂，不交给 LLM
    if "diff" in d or "diff_pct" in d:
        return False  # 数值类，走确定性结论
    for v in (src[0], tgt[0]):
        s = str(v).strip()
        if not s:
            return False
        if _NUMBER_RE.match(s) or _DATE_RE.match(s):
            return False
    return True


_SEMANTIC_SYSTEM_PROMPT = """你是贸易单证字段一致性核验专家。两个字符串值在字面上不一致，请判断它们是否指代同一事物（同义词、别名、简称、格式/大小写差异、多余空格等）。

输出严格 JSON：
{
  "results": [
    {
      "index": 0,
      "equivalent": true|false|null,
      "confidence": 0-1,
      "reason": "判断依据（引用双方值，说明是同一事物或确实不同；无法判断时说明原因）"
    }
  ]
}

规则：
1. equivalent=true 仅当语义明确指向同一实体/同一表述（如"上海XX物流有限公司" vs "上海XX物流"）；
2. 无法确定 → equivalent=null 且 confidence 给低值；
3. confidence < 0.8 一律视为无法确认。"""


def semantic_equivalence_fallback(
    db,
    contract,
    docs: list[Document],
    results: list[ReviewResult],
) -> int:
    """对确定性字符串相等失败的规则做 LLM 语义复核。
    修正策略：
    - 判为同义（高置信）→ 结果改为 pass；
    - 判为确实不同（高置信）→ 保持 fail，补充证据；
    - 无法确认 → 降级 unverifiable（待人工确认，护栏）。
    Returns: 受影响的条数。
    """
    targets = [r for r in results if _is_string_mismatch_item(r)]
    if not targets:
        return 0

    pairs = [
        {
            "index": i,
            "src": str(r.detail["src"][0]),
            "tgt": str(r.detail["tgt"][0]),
            "rule": r.rule_text or "",
            "check_category": r.check_category,
        }
        for i, r in enumerate(targets)
    ]
    user_prompt = (
        "待核验的字符串对：\n"
        + "\n".join(
            f"{p['index']}. 文档A值: {p['src']}  vs  文档B值: {p['tgt']}"
            + (f"（规则：{p['rule']}）" if p["rule"] else "")
            for p in pairs
        )
        + "\n\n请输出 JSON。"
    )

    try:
        llm = get_llm_client()
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": _SEMANTIC_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
    except (LLMError, ValueError, json.JSONDecodeError) as e:
        logger.warning("LLM 语义复核失败（保留确定性结论）: %s", e)
        return 0

    changed = 0
    for item in resp.get("results", []):
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(targets)):
            continue
        r = targets[idx]
        equivalent = item.get("equivalent")
        confidence = _to_float(item.get("confidence"))
        reason = str(item.get("reason") or "").strip()
        detail = dict(r.detail or {})
        detail["semantic_fallback"] = True
        if reason:
            detail["llm_evidence"] = reason

        if equivalent is True and confidence is not None and confidence >= EQUIVALENT_CONFIDENCE_THRESHOLD:
            # 同义 → 改为 pass（确定性 fail 被 LLM 复核推翻，需要高置信）
            r.result = "pass"
            r.issue_desc = "字符串字面不一致，但经 LLM 语义复核判断为同义/别名/格式差异，判定通过"
        elif equivalent is False and confidence is not None and confidence >= EQUIVALENT_CONFIDENCE_THRESHOLD:
            # 确认不同 → 保持 fail，仅增强证据
            r.issue_desc = (r.issue_desc or "") + "（LLM 语义复核确认不一致）"
        else:
            # 无法确认 → 降级 unverifiable 待人工确认（护栏）
            r.result = "unverifiable"
            r.issue_desc = "字符串一致性无法由程序确定，LLM 语义复核未给出明确结论，需人工确认"
            detail["reason"] = "llm_uncertain_semantic"
        r.confidence = confidence
        r.detail = detail
        # 结果变化后重算状态/严重度
        r.status = result_meta.default_status(r.result)
        r.severity, r.deviation = result_meta.compute_severity(r.result, r.check_category, detail)
        changed += 1

    return changed
