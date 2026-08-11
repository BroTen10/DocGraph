"""规则批量导入服务：用 LLM 把自然语言规则清单解析为结构化规则并入库。

流程（批次 10 v2 契约）：
1. 接收一段自然语言规则清单文本（可含多条规则）
2. 调用 LLM 解析为 rules + ontology：
   - rules 每项含 rule_text / structure / scope（doc_types|ALL）/ intents / tolerance / priority / confidence；
     doc_type / check_category 为可选派生标签（缺失时由结构断言/scope/intents 推导）
   - ontology 登记新文件类型/字段/检查意图
3. 校验放宽：rule_text 与 structure.assertion 至少其一存在
4. 规则集内语义级去重（按文本相似度 + 结构化断言签名），合并后批量写入 rules 表
5. 新文档类型注册为 pending_review（key_fields 由 ontology.fields 预填），返回导入结果
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..constants import ALL_DOC_TYPES, CHECK_CATEGORIES
from ..llm_client import LLMError, get_llm_client
from ..models import DocumentType, Rule, RuleSet
from .rule_service import create_rule
from ..schemas.rule import RuleCreate, ConflictReport
from .rule_parse_engine import (
    RuleParseDirective,
    apply_defaults,
    apply_field_mappings,
    apply_term_normalization,
    apply_text_preprocessing,
)
from .rule_import_task import ImportProgress, update_task

logger = logging.getLogger(__name__)


# ============ LLM 提示词 ============
_SYSTEM_PROMPT = """你是单证审查规则解析助手。任务：把用户提供的自然语言规则清单，解析为结构化的规则列表，并抽象出规则涉及的文件类型、字段与检查意图（本体）。

输出契约（严格 JSON，不要输出任何其他内容）：
{
  "rules": [
    {
      "doc_type": "主文件类型（可选派生标签，见规则1）",
      "check_category": "主检查项（可选派生标签，见规则2）",
      "scope": {"doc_types": ["涉及的多个文件类型"] 或 "ALL"（整批合同/全部文件）或 null, "intents": ["检查意图列表"]},
      "rule_text": "规则文本（简洁、可执行的自然语言描述）",
      "structure": {
        "condition": {"text": "触发条件原文或null", "field": "条件涉及字段名或null", "operator": "等于/包含于等或null", "value": "条件取值或null"},
        "assertion": {
          "source": {"doc_type": "源文件类型", "field": "源字段名", "aggregate": "SUM|ANY|ALL|null（多单汇总语义，分批付款场景用 SUM）", "role": "字段角色（委托方/收款方/收货方等或null）"},
          "operator": "等于|不大于|不小于|时间早于|时间不晚于|总额等于|包含于",
          "target": {"doc_type": "目标文件类型", "field": "目标字段名", "aggregate": "同上", "role": "同上"},
          "currency": "币别（CNY/USD等，原文未涉及填null）",
          "unit": "单位（件/吨/箱等，原文未涉及填null）"
        },
        "exceptions": [{"text": "例外条款原文", "reason": "例外原因或null",
                        "field": "例外判据涉及的字段名或null（如金额/文件类型）",
                        "operator": "等于/不等于/小于/大于/包含/包含于等或null",
                        "value": "例外判据取值或null（如5000）"}]
      },
      "tolerance": {"amount_percent": 数字或null, "weight_kg": 数字或null, "time_days": 数字或null, "allow_same_day": 布尔或null},
      "priority": 数字（越小越先，默认100）,
      "confidence": 0-1之间的浮点数，代表你对这条规则理解的确定程度
    }
  ],
  "ontology": {
    "doc_types": [{"name": "新文件类型名", "description": "简要说明", "aliases": ["别名"]}],
    "fields": [{"name": "字段名", "doc_type": "所属文件类型", "unit": "单位或null", "currency": "币别或null"}],
    "check_intents": [{"name": "新检查意图名", "description": "简要说明"}]
  },
  "defects": [
    {
      "type": "缺陷类型",
      "severity": "error|warning|info",
      "description": "问题描述",
      "rule_index": 该缺陷对应的规则在 rules 数组中的索引（若无对应规则则填null）
    }
  ]
}

规则：
1. doc_type 是**可选派生标签**：仅当规则明确指向单一文件类型、且该类型与下方"已知文件类型"一致时填写；跨文件比对、整批规则不填 doc_type，改用 scope.doc_types（列表）或 "ALL"。**不要为满足枚举而臆造文件类型**
2. check_category 同样是**可选派生标签**：仅当规则核心意图能明确归入下方"已知检查项"时填写；否则不填，改在 scope.intents 中给出准确的检查意图名
3. scope 每个规则都尽量给出（doc_types 列表 / "ALL" / null，intents 至少 1 个）；规则涉及未注册的新文件类型时，**以原文表述为准**提出简洁、无歧义的新类型名，并登记到 ontology.doc_types；新字段登记到 ontology.fields（附所属文件类型）；无法归入已知检查项的新意图登记到 ontology.check_intents
4. rule_text 用简洁中文描述，如"报关单数量应不大于委托单数量"
5. tolerance 只在规则涉及金额/重量/时间比对时填写，否则字段留 null
6. priority 默认 100；齐套性规则建议 10，基础判断建议 20，信息准确性建议 30，时间逻辑建议 40
7. 一条自然语言描述拆为一条规则；若用户文本含多条规则，全部解析
7.1 structure 为可选字段：规则文本包含"如果/若/除非"等条件表述时填入 condition；规则涉及跨文件字段比对时填入 assertion（source/target 必须来自规则文本，不能臆造）；包含"除…外/例外"表述时填入 exceptions；无法结构化时整个 structure 置 null。若例外条款可归结为"某字段满足某条件即豁免"（如"金额小于5000元时除外"），请在 exceptions 中补充 field/operator/value，执行引擎可程序化豁免；纯文本例外（无法归因到字段）则只填 text
8. 忽略与单证审查无关的内容
9. confidence 反映你对这条规则确信程度：规则描述非常清楚、无歧义则接近 1.0；含模糊表述（如"部分情况""一般""可能"）则适当降低；完全不确定或不合理则为 0.0
10. 为减少输出体积，值为 null 的字段省略不输出（客户端自动补全），confidence 字段值为 1.0 时同理省略；defects 无缺陷时整个数组省略；ontology 无新概念时省略
11. 紧凑输出（防止长清单被 max_tokens 截断）：rules 数组按"每行一条规则"输出、逗号分隔，不输出多余空行、注释或解释文字；structure/tolerance 仅在有内容时输出

### 缺陷检测指令
对每条被解析的规则，执行以下检查，将结果填入 `defects` 数组：

1. `ambiguous_reference`：规则中是否包含"相关""相应""有关""等"等模糊引用，导致执行者无法确定具体指代？
2. `incomplete_condition`：规则的条件是否不完整？例如提到金额比对但没有说和什么比、缺少比较对象？
3. `missing_value`：规则涉及金额/重量/时间的比对，但原文没有给出任何容差或阈值？
4. `contradiction`：这条规则是否和同一批次中已解析的其他规则存在明显的语义矛盾？
5. `uncertainty`：你对这条规则的理解是否有任何不确定的地方？比如术语含义模糊、多种可能的解读？

缺陷类型说明：
- ambiguous_reference：涉及模糊引用，让用户确认具体指代
- incomplete_condition：条件不完整，需要用户补充信息
- missing_value：缺少关键数值参数
- contradiction：规则间存在矛盾
- uncertainty：存在理解上的不确定

severity 说明：
- error：大概率有问题的规则，需要用户处理
- warning：可能存在问题的规则，建议用户检查
- info：仅供参考，不影响规则执行"""

_USER_PROMPT_TEMPLATE = """已知文件类型（建议复用，不强制；规则出现新类型时以原文为准并提出新名称）：{doc_types}

已知检查项（建议复用，不强制；规则出现新意图时以原文为准并提出新名称）：{check_categories}

请解析以下规则清单：

---
{raw_text}
---

请输出 JSON。"""


# 单次 LLM 调用允许的最大输入文本长度（字符）。超过则分段解析，避免输出截断。
_MAX_CHUNK_CHARS = 2000


def _split_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """把长文本切成 ≤ max_chars 的块，尽量按段落/行边界切，避免切断一条规则。

    返回至少包含一个元素的列表；若整体超短则整段返回。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    # 先按空行分段
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) + 2 <= max_chars:
            buf = (buf + "\n\n" + para) if buf else para
        else:
            flush()
            if len(para) <= max_chars:
                buf = para
            else:
                # 单段超长，退化为按行切；行仍超长则按字符硬切
                for line in para.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if len(line) > max_chars:
                        # 批次 4-1：表格行超长时优先按单元格分隔符 '|' 切（不切断单元格内容）
                        cells = [s.strip() for s in line.split("|") if s.strip()]
                        if len(cells) > 1 and all(len(c) <= max_chars for c in cells):
                            for seg in cells:
                                if len(buf) + len(seg) + 1 <= max_chars:
                                    buf = (buf + "\n" + seg) if buf else seg
                                else:
                                    flush()
                                    buf = seg
                        else:
                            for i in range(0, len(line), max_chars):
                                seg = line[i : i + max_chars]
                                if len(buf) + len(seg) + 1 <= max_chars:
                                    buf = (buf + "\n" + seg) if buf else seg
                                else:
                                    flush()
                                    buf = seg
                        continue
                    if len(buf) + len(line) + 1 <= max_chars:
                        buf = (buf + "\n" + line) if buf else line
                    else:
                        flush()
                        buf = line
    flush()
    return chunks or [text]


def _normalize_text(text: str) -> str:
    """归一化规则文本：去标点、去空格、小写，用于相似度比对。"""
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _text_similarity(a: str, b: str) -> float:
    """计算两个归一化文本的相似度（0-1）。使用字符级 Jaccard + 包含关系。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _structure_signature(structure: dict | None) -> tuple | None:
    """提取结构化断言的特征签名（operator + source/target 的 doc_type/field）。
    用于跨标签识别同一条规则（批次 10：语义级去重）。"""
    if not structure:
        return None
    assertion = structure.get("assertion") or {}
    if not assertion:
        return None
    src = assertion.get("source") or {}
    tgt = assertion.get("target") or {}
    sig = (
        str(assertion.get("operator") or "").strip(),
        str(src.get("doc_type") or "").strip(),
        str(src.get("field") or "").strip(),
        str(tgt.get("doc_type") or "").strip(),
        str(tgt.get("field") or "").strip(),
    )
    return sig if any(sig) else None


def _find_similar_rule(
    new_text: str,
    new_normed: str,
    existing_rules: list,
    threshold: float = 0.75,
    new_structure: dict | None = None,
):
    """在规则集内查找与新规则高度相似的规则（批次 10：不再按格子分组）。
    匹配策略：
    1. 文本相似度 >= threshold 直接命中；
    2. 结构化断言签名一致（operator + 源/目标类型与字段）且文本相似度 >= 0.6 也命中。
    """
    new_sig = _structure_signature(new_structure)
    for rule in existing_rules:
        existing_normed = _normalize_text(rule.rule_text)
        sim = _text_similarity(new_normed, existing_normed)
        if sim >= threshold:
            return rule
        if new_sig:
            old_sig = _structure_signature(rule.structure)
            if old_sig and old_sig == new_sig and sim >= 0.6:
                return rule
    return None


def _normalize_scope(raw) -> dict | None:
    """归一化 LLM 输出的 scope 字段为 {"doc_types": [...] | "ALL"} 与 {"intents": [...]}。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.upper() in ("ALL", "整批", "全部"):
            return {"doc_types": "ALL"}
        return {"doc_types": [s]}
    if isinstance(raw, dict):
        out: dict = {}
        dt = raw.get("doc_types")
        if isinstance(dt, list):
            raw_names = [str(x).strip() for x in dt if x is not None and str(x).strip()]
            non_all = [n for n in raw_names if n.upper() not in ("ALL", "整批", "全部")]
            if not non_all and raw_names:
                out["doc_types"] = "ALL"  # 整个列表都是全局语义
            elif non_all:
                out["doc_types"] = sorted(set(non_all))
        elif isinstance(dt, str) and dt.strip().upper() in ("ALL", "整批", "全部"):
            out["doc_types"] = "ALL"
        its = raw.get("intents")
        if isinstance(its, list):
            ints = sorted({str(x).strip() for x in its if x is not None and str(x).strip()})
            if ints:
                out["intents"] = ints
        return out or None
    return None


def _extract_intents(item: dict, scope: dict | None) -> list[str]:
    """提取检查意图标签：scope.intents + item.intents + check_category（去重保序）。"""
    intents: list[str] = []
    scope_intents = (scope or {}).get("intents")
    if isinstance(scope_intents, list):
        intents.extend(scope_intents)
    item_intents = item.get("intents")
    if isinstance(item_intents, list):
        for x in item_intents:
            s = str(x).strip() if x is not None else ""
            if s and s not in intents:
                intents.append(s)
    item_cat = str(item.get("check_category") or "").strip()
    if item_cat and item_cat not in intents:
        intents.insert(0, item_cat)
    return intents


def _derive_doc_type(structure: dict | None, scope: dict | None) -> str:
    """doc_type 缺失时从结构化断言 source 或 scope.doc_types 推导（派生标签兜底）。"""
    if structure and structure.get("assertion"):
        src = structure["assertion"].get("source") or {}
        sdt = str(src.get("doc_type") or "").strip()
        if sdt:
            return sdt
    scope_dt = (scope or {}).get("doc_types")
    if isinstance(scope_dt, list) and scope_dt:
        return "" if scope_dt[0].upper() in ("ALL", "整批", "全部") else scope_dt[0]
    if isinstance(scope_dt, str):
        return "" if scope_dt.upper() in ("ALL", "整批", "全部") else scope_dt
    return ""


def _merge_ontology(target: dict, source: dict) -> None:
    """合并各分段的 ontology（doc_types/fields/check_intents 按关键字段去重）。"""
    if not isinstance(source, dict):
        return
    for key, merge_key in (("doc_types", "name"), ("fields", "name"), ("check_intents", "name")):
        items = source.get(key)
        if not isinstance(items, list):
            continue
        seen = {str(x.get(merge_key)) for x in target.setdefault(key, []) if isinstance(x, dict)}
        for x in items:
            if not isinstance(x, dict):
                continue
            k = str(x.get(merge_key) or "").strip()
            if not k or k in seen:
                continue
            seen.add(k)
            target[key].append(x)


def _merge_into_existing(
    db,
    existing_rule,
    new_rule_text: str,
    new_confidence: float | None,
    new_tolerance: dict,
    new_priority: int,
    new_defects: list[dict],
    new_scope: dict | None = None,
    new_intents: list[str] | None = None,
    new_doc_type: str | None = None,
    new_check_category: str | None = None,
) -> int:
    """将新规则合并到已有规则中。返回合并的字段数。

    合并策略：
    - confidence: 取较高值
    - tolerance: 合并（新值覆盖旧值为空的字段）
    - defects: 去重合并（按 type + description 去重）
    - rule_text: 保留更长的（通常更完整）
    - priority: 保留较小的（更优先）
    - scope/intents: 并集合并
    - doc_type/check_category: 旧值为空时补全（批次 10）
    """
    changes = 0

    # 标签补全：旧值为空时用新值
    if new_doc_type and not existing_rule.doc_type:
        existing_rule.doc_type = new_doc_type
        changes += 1
    if new_check_category and not existing_rule.check_category:
        existing_rule.check_category = new_check_category
        changes += 1

    # scope 合并（doc_types 并集 / ALL 优先；intents 并集）
    if new_scope:
        old_scope = dict(existing_rule.scope or {})
        merged_scope = dict(old_scope)
        new_dt = new_scope.get("doc_types")
        old_dt = old_scope.get("doc_types")
        if new_dt == "ALL":
            merged_scope["doc_types"] = "ALL"
        elif isinstance(new_dt, list):
            old_names = old_dt if isinstance(old_dt, list) else []
            merged_names = sorted(set(old_names) | set(new_dt))
            if merged_names:
                merged_scope["doc_types"] = merged_names
        merged_ints = sorted(
            set(old_scope.get("intents") or []) | set(new_scope.get("intents") or [])
        )
        if merged_ints:
            merged_scope["intents"] = merged_ints
        if merged_scope != old_scope:
            existing_rule.scope = merged_scope
            changes += 1

    # intents 合并（去重保序）
    if new_intents:
        old_ints = list(existing_rule.intents or [])
        merged_ints = list(old_ints)
        for x in new_intents:
            if x and x not in merged_ints:
                merged_ints.append(x)
        if merged_ints != old_ints:
            existing_rule.intents = merged_ints
            changes += 1

    # confidence: 取较高值
    if new_confidence is not None:
        old_conf = existing_rule.confidence
        if old_conf is None or new_confidence > old_conf:
            existing_rule.confidence = new_confidence
            changes += 1

    # tolerance: 合并
    old_tol = existing_rule.tolerance or {}
    for k, v in (new_tolerance or {}).items():
        if v is not None and old_tol.get(k) is None:
            old_tol[k] = v
            changes += 1
    if changes > 0:
        existing_rule.tolerance = dict(old_tol)

    # priority: 取较小值
    if new_priority < (existing_rule.priority or 100):
        existing_rule.priority = new_priority
        changes += 1

    # rule_text: 保留更长的
    if len(new_rule_text) > len(existing_rule.rule_text):
        existing_rule.rule_text = new_rule_text
        changes += 1

    # defects: 去重合并
    old_defects = existing_rule.defects or []
    old_keys = {(d.get("type"), d.get("description")) for d in old_defects}
    for d in (new_defects or []):
        key = (d.get("type"), d.get("description"))
        if key not in old_keys:
            old_defects.append(d)
            old_keys.add(key)
            changes += 1
    if changes > 0:
        existing_rule.defects = old_defects
        # 重新评估规则健康状态：合并后如果仍有缺陷，保持 pending/disabled
        has_defects = any(
            d.get("severity") in ("error", "warning")
            for d in old_defects
        )
        if not has_defects and existing_rule.status != "confirmed":
            existing_rule.status = "confirmed"
            existing_rule.enabled = True
            changes += 1

    db.commit()
    return changes


def _known_doc_types(db: Session, rule_set_id: uuid.UUID) -> list[str]:
    """构造规则导入提示词中的"已知文件类型"清单。

    来源优先级：
    1. 当前规则集声明的适用文件类型（rule_set.doc_types，保持声明顺序）
    2. 全局 DocumentType 注册表（active + pending_review，按名称排序）

    两者去重合并；若均无内容（如注册表尚未初始化），回退到 constants 内置清单，
    保证导入流程在任何情况下都能拿到候选类型。
    """
    names: list[str] = []
    seen: set[str] = set()

    rs = db.get(RuleSet, rule_set_id)
    if rs is not None:
        for name in rs.doc_types or []:
            name = str(name).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    rows = db.execute(
        select(DocumentType.name)
        .where(DocumentType.status.in_(("active", "pending_review")))
        .order_by(DocumentType.name)
    ).scalars().all()
    for name in rows:
        name = str(name).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    if not names:
        return list(ALL_DOC_TYPES)
    return names


def _known_check_categories(db: Session, rule_set_id: uuid.UUID) -> list[str]:
    """构造规则导入提示词中的"已知检查项"清单。

    与 _known_doc_types 对称：当前规则集已累积的检查项/意图
    （rule_set.check_categories，保持声明顺序）优先，再补充内置常量，
    两者去重；注册表为空时回退到常量清单。
    """
    names: list[str] = []
    seen: set[str] = set()

    rs = db.get(RuleSet, rule_set_id)
    if rs is not None:
        for name in rs.check_categories or []:
            name = str(name).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    for name in CHECK_CATEGORIES:
        name = str(name).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    if not names:
        return list(CHECK_CATEGORIES)
    return names


def import_rules_from_text(
    db: Session, rule_set_id: uuid.UUID, raw_text: str,
    directive: RuleParseDirective | None = None,
    progress: ImportProgress | None = None,
) -> dict[str, Any]:
    """从自然语言规则清单文本批量导入规则。

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID（导入规则归到该规则集下）
        raw_text: 自然语言规则清单文本
        directive: Skill 编译后的解析指令（可选），指定后应用预处理/字段映射/默认值等

    Returns:
        {"total": 解析总数, "imported": 入库成功数, "skipped": 跳过数, "rules": [入库的规则],
         "errors": [跳过原因], "conflict_report": 冲突与缺陷报告}
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ValueError("规则清单文本为空")

    # 应用文本预处理（如果指定了 directive）
    if directive:
        raw_text = apply_text_preprocessing(raw_text, directive.text_preprocessing)

    llm = get_llm_client()
    doc_types_str = "、".join(_known_doc_types(db, rule_set_id))
    check_categories_str = "、".join(_known_check_categories(db, rule_set_id))

    # 长文本分段解析：避免单次输出超 max_tokens 被截断（JSON 不完整）
    chunks = _split_text(raw_text)
    logger.info("规则导入分段: 共 %d 段, 各段长度=%s", len(chunks), [len(c) for c in chunks])
    if progress is not None:
        update_task(progress, status="parsing", total_chunks=len(chunks), parsed_chunks=0,
                    message=f"正在解析规则（共 {len(chunks)} 段）…")
    raw_rules: list[dict] = []
    all_defects: list[dict] = []  # 收集所有段的 defects
    chunk_errors: list[str] = []
    rule_chunks: list[int] = []  # 每条 raw_rule 所属分段（用于 provenance）
    ontology: dict[str, list] = {"doc_types": [], "fields": [], "check_intents": []}
    for idx, chunk in enumerate(chunks, start=1):
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            doc_types=doc_types_str,
            check_categories=check_categories_str,
            raw_text=chunk,
        )
        # 构建动态 System Prompt（附加 Skill 指令 + 领域上下文）
        system_content = _SYSTEM_PROMPT
        if directive:
            if directive.prompt_additions:
                system_content += "\n\n### 用户自定义解析指令\n" + "\n".join(f"- {a}" for a in directive.prompt_additions)
            ctx = directive.domain_context
            if ctx:
                glossary = ctx.get("glossary") or {}
                patterns = ctx.get("common_patterns") or []
                parts = []
                if glossary:
                    parts.append("### 领域术语定义\n" + "\n".join(f"- {k}: {v}" for k, v in glossary.items()))
                if patterns:
                    parts.append("### 常见规则模式\n" + "\n".join(f"- {p}" for p in patterns))
                if parts:
                    system_content += "\n\n" + "\n\n".join(parts)

        try:
            resp = llm.chat_json(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=8192,
            )
        except (LLMError, ValueError) as e:
            logger.error("规则导入 第 %d/%d 段 LLM 解析失败: %s", idx, len(chunks), e)
            chunk_errors.append(f"第 {idx} 段解析失败: {e}")
            if progress is not None:
                update_task(progress, parsed_chunks=idx,
                            message=f"第 {idx}/{len(chunks)} 段解析失败，已跳过")
            continue

        if progress is not None:
            update_task(progress, parsed_chunks=idx,
                        message=f"已解析 {idx}/{len(chunks)} 段")

        # 提取 rules
        rules = resp.get("rules", [])
        if isinstance(rules, list):
            offset = len(raw_rules)
            for r in rules:
                if isinstance(r, dict):
                    raw_rules.append(r)
                    rule_chunks.append(idx)
            # 收集 ontology（新文件类型/字段/检查意图）
            ont = resp.get("ontology")
            if isinstance(ont, dict):
                _merge_ontology(ontology, ont)
            # 提取 defects，修正 rule_index 为全局索引
            defects = resp.get("defects", [])
            if isinstance(defects, list):
                for d in defects:
                    if isinstance(d, dict):
                        ri = d.get("rule_index")
                        if ri is not None:
                            d = dict(d)
                            d["rule_index"] = ri + offset
                        all_defects.append(d)
        else:
            chunk_errors.append(f"第 {idx} 段：LLM 返回结构异常，已跳过")

    if not raw_rules:
        detail = f"（共 {len(chunks)} 段，{len(chunk_errors)} 段失败）" if chunk_errors else ""
        raise ValueError(f"LLM 未解析出任何规则{detail}")

    if progress is not None:
        update_task(progress, status="importing", total_rules=len(raw_rules),
                    imported_rules=0, import_errors=0,
                    message=f"正在入库 {len(raw_rules)} 条规则…")

    # 应用 Skill 后处理：字段映射、默认值、术语归一化
    if directive:
        raw_rules = apply_field_mappings(raw_rules, directive.field_mappings)
        raw_rules = apply_defaults(raw_rules, directive.defaults)
        raw_rules = apply_term_normalization(raw_rules, directive.term_normalization)

    # 按全局 rule_index 建立 defects 索引
    defects_by_rule: dict[int, list[dict]] = {}
    for d in all_defects:
        ri = d.get("rule_index")
        if ri is not None and isinstance(ri, int):
            defects_by_rule.setdefault(ri, []).append(d)

    imported: list[dict] = []
    errors: list[str] = []
    # 收集新发现的类型，用于后续更新 rule_set.doc_types / check_categories
    new_doc_types: set[str] = set()
    new_check_categories: set[str] = set()

    for i, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {i} 条：非合法对象，跳过")
            continue
        # ----- 0. 派生标签与规则自描述（批次 10）-----
        # doc_type / check_category 均为可选派生标签；scope/intents 承载真正的适用范围
        doc_type_raw = item.get("doc_type")
        doc_type = str(doc_type_raw).strip() if doc_type_raw is not None else ""
        check_category_raw = item.get("check_category")
        check_category = str(check_category_raw).strip() if check_category_raw is not None else ""
        rule_text = str(item.get("rule_text", "")).strip()

        # 结构化审查意图（批次 7）：LLM 可选输出，清洗为 dict 或 None
        structure: dict[str, Any] | None = None
        structure_raw = item.get("structure")
        if isinstance(structure_raw, dict) and structure_raw.get("assertion"):
            structure = {
                "condition": structure_raw.get("condition") or None,
                "assertion": structure_raw.get("assertion"),
                "exceptions": structure_raw.get("exceptions") or [],
            }

        # 规则自描述：scope + intents（批次 10）
        scope = _normalize_scope(item.get("scope"))
        intents = _extract_intents(item, scope)

        # 校验放宽（批次 10）：不再强制 doc_type/check_category，只要求有可执行内容
        if not rule_text and not (structure and structure.get("assertion")):
            errors.append(f"第 {i} 条：规则文本与结构化断言均为空，跳过")
            continue
        # rule_text 缺失时用结构化断言生成可读文本（RuleCreate.rule_text 必填）
        if not rule_text and structure and structure.get("assertion"):
            a = structure["assertion"]
            src = a.get("source") or {}
            tgt = a.get("target") or {}
            rule_text = (
                f"{src.get('doc_type') or ''}.{src.get('field') or ''} "
                f"{a.get('operator') or '等于'} "
                f"{tgt.get('doc_type') or ''}.{tgt.get('field') or ''}"
            ).strip()

        # 派生标签兜底：doc_type 缺失时从结构化断言/scope 推导；check_category 缺失时取首个意图
        if not doc_type:
            doc_type = _derive_doc_type(structure, scope)
        if not check_category and intents:
            check_category = intents[0]

        # 收集新发现的类型/意图（含 scope 声明；ontology 声明在循环后并入）
        if doc_type:
            new_doc_types.add(doc_type)
        if check_category:
            new_check_categories.add(check_category)
        scope_dt = (scope or {}).get("doc_types")
        if isinstance(scope_dt, list):
            new_doc_types.update(x for x in scope_dt if x)

        # ----- 1. 先提取元数据（包容差/置信度/缺陷）---------
        tol_raw = item.get("tolerance") or {}
        tolerance: dict[str, Any] = {}
        if isinstance(tol_raw, dict):
            if tol_raw.get("amount_percent") is not None:
                tolerance["amount_percent"] = tol_raw["amount_percent"]
            if tol_raw.get("weight_kg") is not None:
                tolerance["weight_kg"] = tol_raw["weight_kg"]
            if tol_raw.get("time_days") is not None:
                tolerance["time_days"] = tol_raw["time_days"]
            if tol_raw.get("allow_same_day") is not None:
                tolerance["allow_same_day"] = tol_raw["allow_same_day"]

        priority = item.get("priority", 100)
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = 100

        confidence = item.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        # 关联该规则的 defects（rule_index 从 0 开始）
        rule_defects = defects_by_rule.get(i - 1, [])
        clean_defects = []
        for d in rule_defects:
            clean_defects.append({
                "type": d.get("type", "unknown"),
                "severity": d.get("severity", "info"),
                "description": d.get("description", ""),
            })

        # 来源追溯（批次 10）
        provenance: dict[str, Any] | None = {
            "chunk_index": rule_chunks[i - 1] if i - 1 < len(rule_chunks) else None,
            "text": rule_text[:200],
        }

        # ----- 2. 规则集内语义去重+智能合并（批次 10：不再按 (doc_type, check_category) 格子分组）-----
        normed = _normalize_text(rule_text)
        existing_rules = db.execute(
            select(Rule).where(Rule.rule_set_id == rule_set_id)
        ).scalars().all()
        dup_rule = _find_similar_rule(rule_text, normed, existing_rules, new_structure=structure)
        if dup_rule:
            merged_count = _merge_into_existing(
                db, dup_rule, rule_text, confidence, tolerance, priority, clean_defects,
                new_scope=scope, new_intents=intents,
                new_doc_type=doc_type or None, new_check_category=check_category or None,
            )
            label = f"{doc_type or '整批/全部'}/{check_category or '未分类'}"
            logger.info("同集合并: [%s] %s... -> %s (合并数=%d)",
                        label, rule_text[:40], dup_rule.id, merged_count)
            skipped_detail = f"第 {i} 条：与已有规则 [{label}] 相似，已自动合并（{rule_text[:30]}...）"
            errors.append(skipped_detail)
            continue

        # ----- 3. 规则健康状态分类 -----
        # 无实质缺陷(error/warning) -> 自动确认+启用
        # 有实质缺陷 -> 待确认+禁用
        has_real_defect = any(
            d.get("severity") in ("error", "warning")
            for d in clean_defects
        )
        if has_real_defect:
            status = "pending"
            enabled = False
        else:
            status = "confirmed"
            enabled = True

        try:
            payload = RuleCreate(
                doc_type=doc_type or None,
                check_category=check_category or None,
                rule_text=rule_text,
                tolerance=tolerance,
                structure=structure,
                scope=scope,
                intents=intents,
                provenance=provenance,
                enabled=enabled,
                priority=priority,
                confidence=confidence,
                status=status,
                defects=clean_defects,
            )
            rule_out = create_rule(db, rule_set_id, payload)
            rule_dict = rule_out.model_dump(mode="json")
            # 原文对照：记录该规则来源的分段原文，供前端"原文 ↔ 解析结果"视图
            ci = rule_chunks[i - 1] if i - 1 < len(rule_chunks) else None
            if isinstance(ci, int) and 0 <= ci - 1 < len(chunks):
                rule_dict["source_text"] = chunks[ci - 1]
            imported.append(rule_dict)
            if progress is not None:
                update_task(progress, imported_rules=len(imported),
                            message=f"已入库 {len(imported)}/{len(raw_rules)} 条")
        except Exception as e:
            errors.append(f"第 {i} 条：入库失败 - {e}")
            if progress is not None:
                update_task(progress, import_errors=len(errors))

    errors.extend(chunk_errors)

    # 将 LLM ontology 声明的新文件类型/检查意图并入发现集合（批次 10）
    for x in ontology.get("doc_types") or []:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            new_doc_types.add(str(x["name"]).strip())
    for x in ontology.get("check_intents") or []:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            new_check_categories.add(str(x["name"]).strip())

    # 更新 rule_set.doc_types / check_categories，合并新发现的类型
    if imported:
        try:
            rs = db.execute(select(RuleSet).where(RuleSet.id == rule_set_id)).scalars().first()
            if rs:
                cur_docs = set(rs.doc_types or [])
                cur_cats = set(rs.check_categories or [])
                merged_docs = sorted(cur_docs | new_doc_types)
                merged_cats = sorted(cur_cats | new_check_categories)
                if merged_docs != rs.doc_types or merged_cats != rs.check_categories:
                    rs.doc_types = merged_docs
                    rs.check_categories = merged_cats
                    db.commit()
                    logger.info(
                        "已更新规则集 %s 类型: doc_types=%d, check_categories=%d",
                        rule_set_id, len(merged_docs), len(merged_cats)
                    )
        except Exception:
            logger.warning("更新规则集类型失败（不影响已入库规则）", exc_info=True)

    # 从 ontology.fields 建立 类型→字段 映射，用于预填新类型的 key_fields（批次 10）
    field_map: dict[str, list[str]] = {}
    for f in ontology.get("fields") or []:
        if not isinstance(f, dict):
            continue
        fdt = str(f.get("doc_type") or "").strip()
        fname = str(f.get("name") or "").strip()
        if fdt and fname and fname not in field_map.setdefault(fdt, []):
            field_map[fdt].append(fname)

    # 检测新文档类型：将不在document_types表中的类型注册为pending_review
    new_doc_type_names = set()
    if new_doc_types:
        try:
            from ..models import DocumentType
            # 查询已有活跃类型名称
            existing = db.execute(
                select(DocumentType.name).where(DocumentType.status == "active")
            ).scalars().all()
            existing_set = set(existing)

            for name in sorted(new_doc_types):
                if name in existing_set:
                    continue
                # 检查是否已有 pending 记录
                pending = db.execute(
                    select(DocumentType).where(
                        DocumentType.name == name,
                        DocumentType.status == "pending_review",
                    )
                ).scalars().first()
                if pending:
                    # 已有 pending 记录：合并补充 key_fields（避免重复创建）
                    new_fields = field_map.get(name) or []
                    if new_fields:
                        cur = list(pending.key_fields or [])
                        merged = cur + [x for x in new_fields if x not in cur]
                        if merged != cur:
                            pending.key_fields = merged
                            db.commit()
                    new_doc_type_names.add(name)
                    continue
                # 创建新类型
                dt = DocumentType(
                    name=name,
                    source="rule_import",
                    status="pending_review",
                    key_fields=field_map.get(name, []),
                )
                db.add(dt)
                db.commit()
                new_doc_type_names.add(name)
                logger.info("规则导入发现新文档类型（pending_review）: %s", name)
        except Exception:
            logger.warning("注册新文档类型失败（不影响已导入规则）", exc_info=True)

    # 构建冲突报告
    all_clean_defects = []
    for d in all_defects:
        all_clean_defects.append({
            "type": d.get("type", "unknown"),
            "severity": d.get("severity", "info"),
            "description": d.get("description", ""),
            "rule_index": d.get("rule_index"),
        })

    by_severity = {"error": 0, "warning": 0, "info": 0}
    for d in all_clean_defects:
        sev = d.get("severity", "info")
        if sev in by_severity:
            by_severity[sev] += 1

    conflict_report = ConflictReport(
        total_defects=len(all_clean_defects),
        by_severity=by_severity,
        defects=all_clean_defects,
    )

    return {
        "total": len(raw_rules),
        "imported": len(imported),
        "skipped": len(errors),
        "rules": imported,
        "errors": errors,
        "conflict_report": conflict_report.model_dump(mode="json") if conflict_report.total_defects > 0 else None,
        "new_doc_types": list(new_doc_types),
    }


def import_rules_with_skills(
    db: Session,
    rule_set_id: uuid.UUID,
    raw_text: str,
    skill_ids: list[uuid.UUID] | None = None,
    progress: ImportProgress | None = None,
) -> dict[str, Any]:
    """从文本导入规则，自动加载并应用 Skill。

    流程：编译 Skill directive → 应用预处理 → LLM ��析 → 应用后处理 → 入库

    Args:
        db: 数据库会话
        rule_set_id: 规则集 ID
        raw_text: 规则文本
        skill_ids: 指定应用的 Skill ID，不传则使用该规则集所有已启用的 Skill

    Returns:
        同 import_rules_from_text 的返回
    """
    if skill_ids:
        # 指定了具体的 Skill 列表，只加载这些（批次 3-6：停用/不存在的 Skill 显式报错，杜绝静默丢弃）
        from ..models import RuleParseSkill
        from sqlalchemy import select
        requested = list(dict.fromkeys(skill_ids))
        skills = db.execute(
            select(RuleParseSkill).where(RuleParseSkill.id.in_(requested))
        ).scalars().all()
        by_id = {s.id: s for s in skills}
        missing = [sid for sid in requested if sid not in by_id]
        if missing:
            raise ValueError(f"指定的 Skill 不存在: {missing}")
        disabled = [s.name for s in skills if not s.enabled]
        if disabled:
            raise ValueError(f"以下 Skill 已停用，无法用于本次导入: {'、'.join(disabled)}")

        directive = RuleParseDirective()
        from .rule_parse_engine import _merge_content
        for s in skills:
            _merge_content(directive, s.content or {})
    else:
        # 未指定，从数据库编译（内置默认 + 自定义）
        from .rule_parse_engine import compile_directive
        directive = compile_directive(db, rule_set_id)

    result = import_rules_from_text(db, rule_set_id, raw_text, directive=directive, progress=progress)

    # 导入完成后自动触发冲突检测
    if result.get("imported", 0) > 0:
        try:
            from .rule_conflict_detector import run_conflict_detection
            if progress is not None:
                update_task(progress, status="conflict", message="正在检测规则冲突…", conflict_found=0)
            conflict_result = run_conflict_detection(db, str(rule_set_id), progress=progress)
            if progress is not None:
                update_task(progress, conflict_found=conflict_result.get("total_conflicts", 0))
            if conflict_result.get("total_conflicts", 0) > 0:
                logger.info(
                    "导入后冲突检测: %d 个冲突, %d 条规则受影响",
                    conflict_result["total_conflicts"],
                    conflict_result["affected_rules"],
                )
                # 将冲突信息合并到返回结果中
                result["conflict_detected"] = conflict_result["total_conflicts"]
        except Exception:
            logger.warning("导入后冲突检测失败（不影响已导入规则）", exc_info=True)

    return result
