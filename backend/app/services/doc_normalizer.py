"""文档类型/字段写时归一服务（批次 11）。

设计原则：
- 库里只存"规范名"；别名在写入那一刻翻译成规范名（规则解析落库、文档分类落库、
  OCR 字段提取落库），审查匹配仍用精确等值。
- 只映射显式登记的别名（constants 内置 + document_types.aliases/field_aliases，
  用户可在文档类型页维护），不做模糊/近似猜测，避免过拟合。
- 未知名称不硬猜：normalize_doc_type 未命中时原样返回，由调用方走
  "新类型检测 → pending_review 人工确认"流程。
- 字段别名支持聚合语义：{"总数量": {"field": "数量", "aggregate": "SUM"}}
  表达"总数量 = 数量求和"，而不是普通同义词。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import constants as C
from ..models import DocumentType

logger = logging.getLogger(__name__)

_AGGREGATE_PREFIX = re.compile(r"^(总|合计|总计|汇总)")
_AGGREGATE_SUFFIX = re.compile(r"(合计|总计|之和|总数)$")

# 币别类字段（值归一的对象）。涵盖常见写法，避免逐个类型重复登记。
CURRENCY_FIELDS: tuple[str, ...] = ("币别", "币制", "币种", "货币")

_OPERATOR_NORMALIZATION: dict[str, str] = {
    "==": "等于",
    "=": "等于",
    "equals": "等于",
    "equal": "等于",
    "等于": "等于",
    "!=": "不等于",
    "<>": "不等于",
    "≠": "不等于",
    "不等于": "不等于",
    "<=": "不大于",
    "≤": "不大于",
    "不大于": "不大于",
    ">=": "不小于",
    "≥": "不小于",
    "不小于": "不小于",
    "<": "小于",
    "小于": "小于",
    ">": "大于",
    "大于": "大于",
    "早于": "时间早于",
    "时间早于": "时间早于",
    "晚于": "时间晚于",
    "时间晚于": "时间晚于",
    "不早于": "时间不早于",
    "时间不早于": "时间不早于",
    "不晚于": "时间不晚于",
    "时间不晚于": "时间不晚于",
    "总额等于": "总额等于",
    "包含于": "包含于",
    "包含": "包含",
}


def normalize_operator(operator: Any) -> Optional[str]:
    """运算符归一：LLM 输出的 '=='/'= '/english 等 → 引擎可识别的中文运算符。"""
    if not operator:
        return None
    s = str(operator).strip()
    return _OPERATOR_NORMALIZATION.get(s, s or None)


def is_currency_field(field: Any) -> bool:
    """判断字段是否币别类字段（值需要按币种注册表归一）。"""
    if not field:
        return False
    s = str(field).strip()
    return s in CURRENCY_FIELDS or s.endswith("币种") or s.endswith("币别") or s.endswith("币制")


def normalize_currency_value(value: Any) -> Any:
    """币别值归一（批次 12）：分号拼接的多行值展开去重，并把常见同义写法
    （ISO 代码 / 货币符号 / 中文变体）映射为规范中文币名。

    仅当所有片段都能识别为同一币种时收敛为单值；混合币种保留分号拼接，
    交由审查侧按"任意匹配"或 LLM 裁决处理。非字符串原样保留。
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    parts = [p.strip() for p in str(value).replace("；", ";").split(";") if p.strip()]
    if not parts:
        return value
    variant_to_canonical = {
        variant.upper(): canonical
        for canonical, variants in C.CURRENCY_VALUE_ALIASES.items()
        for variant in variants
    }
    canonical: list[str] = []
    for p in parts:
        mapped = variant_to_canonical.get(p.upper())
        if mapped is not None and mapped not in canonical:
            canonical.append(mapped)
        elif mapped is None and p not in canonical:
            canonical.append(p)
    return canonical[0] if len(canonical) == 1 else ";".join(canonical)


# ---------- 主体类字段（公司/机构名，值归一为公司核心名） ----------

PARTY_ROLE_KEYWORDS: tuple[str, ...] = (
    "代收方", "收货方", "承运人", "物流", "发货方", "收件人",
    "客户", "委托方", "供应商", "卖方", "买方", "协议方",
    "收付款对象", "单位名称", "公司",
)
_NON_PARTY_HINTS: tuple[str, ...] = (
    "订单号", "单号", "编号", "地址", "邮编", "电话", "税号",
    "联系人", "日期", "数量", "金额", "价格", "银行",
)

# 法律形式后缀（去标点、小写后精确匹配），如 "SA CV" -> "sacv"、
# "S.A. DE C.V." -> "sadecv"。命中即视为公司名附属结构，不进入核心名。
_LEGAL_SUFFIX_TOKENS: frozenset[str] = frozenset(
    {
        "sa", "sacv", "sadecv", "cv", "bv", "bvba", "nv", "ag", "gmbh",
        "kg", "inc", "incorporated", "ltd", "limited", "llc", "llp",
        "plc", "corp", "corporation", "company", "co", "srl", "spa",
        "sas", "oy", "ab", "as", "ooo", "sca", "kgaa", "pt", "pte",
        "pvt", "sro", "tva", "sociedad", "anonima", "capital", "variable",
    }
)


def is_party_field(field: Any) -> bool:
    """判断字段是否主体类字段（公司/收货方/承运人等），值可按公司核心名归一。"""
    if not field:
        return False
    s = str(field).strip()
    if not s or any(h in s for h in _NON_PARTY_HINTS):
        return False
    return any(k in s for k in PARTY_ROLE_KEYWORDS)


def normalize_party_name(value: Any) -> Optional[str]:
    """主体值归一：提取公司/机构核心名，去除地址、法律形式后缀与大小写/标点差异。

    例（均归一为 'exampleinternational'）：
      'Example International, 100 Sample Ave, Sample City, 000000, Country'
      'Example International, SA CV'
      'Example International SA CV'
    无法识别核心名（空值等）返回 None，调用方回退字面比较 / LLM 语义复核。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # 单证中主体通常排版为 '公司名, 地址…'，取首个逗号前片段作为核心名
    head = s.split(",", 1)[0].strip()
    if not head:
        return None
    tokens = re.split(r"[\s/]+", head)
    core_parts: list[str] = []
    for tok in tokens:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", tok, flags=re.UNICODE).lower()
        if not cleaned or cleaned in _LEGAL_SUFFIX_TOKENS:
            continue
        core_parts.append(cleaned)
    if not core_parts:
        return None
    return "".join(core_parts)


def _registry_rows(
    db: Optional[Session],
    tenant_id: uuid.UUID | None = None,
) -> list[DocumentType]:
    """读取文档类型注册表；db 不可用时返回空（纯常量兜底）。"""
    if db is None:
        return []
    try:
        stmt = select(DocumentType)
        if tenant_id is not None:
            stmt = stmt.where(DocumentType.tenant_id == tenant_id)
        return list(db.execute(stmt).scalars().all())
    except Exception:  # pragma: no cover
        logger.warning("读取文档类型注册表失败，回退常量别名", exc_info=True)
        return []


def build_doc_type_alias_index(
    db: Optional[Session] = None,
    tenant_id: uuid.UUID | None = None,
) -> dict[str, str]:
    """构建 alias -> 规范名 索引（常量内置 + 注册表 active 类型声明的别名）。

    注册表别名优先于常量（用户可在文档类型页覆盖/扩展）。
    """
    index: dict[str, str] = {}
    for canonical, aliases in C.DOC_TYPE_ALIASES.items():
        for a in aliases:
            a = str(a).strip()
            if a:
                index.setdefault(a, canonical)
    for dt in _registry_rows(db, tenant_id):
        if not dt or dt.status != "active":
            continue
        for a in (dt.aliases or []):
            a = str(a).strip()
            if a and a != dt.name:
                index[a] = dt.name
    return index


def normalize_doc_type(
    db: Optional[Session],
    name: Any,
    tenant_id: uuid.UUID | None = None,
) -> str:
    """名称 → 规范类型名。

    命中显式别名 → 返回规范名；本身是 active 类型名 → 原样返回；
    未命中任何规范/别名 → 原样返回（交给新类型检测 → pending_review）。
    """
    if not name:
        return name or ""
    s = str(name).strip()
    if not s:
        return ""
    index = build_doc_type_alias_index(db, tenant_id)
    if s in index:
        return index[s]
    for dt in _registry_rows(db, tenant_id):
        if dt.status == "active" and dt.name == s:
            return s
    return s


def resolve_field_aliases(
    db: Optional[Session],
    doc_type: Any,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """解析某文档类型的字段别名表（常量 + 注册表，注册表优先）。"""
    doc_type = str(doc_type or "").strip()
    aliases: dict = dict(C.FIELD_ALIASES.get(doc_type, {}))
    for dt in _registry_rows(db, tenant_id):
        if dt.name == doc_type and dt.field_aliases:
            merged = dict(aliases)
            merged.update(dt.field_aliases or {})
            return merged
    return aliases


def _canonical_fields(
    db: Optional[Session],
    doc_type: Any,
    tenant_id: uuid.UUID | None = None,
) -> set[str]:
    """规范字段集合（constants 模板 + 注册表 key_fields）。"""
    doc_type = str(doc_type or "").strip()
    fields: set[str] = set(C.FIELD_TEMPLATES.get(doc_type, []))
    for dt in _registry_rows(db, tenant_id):
        if dt.name == doc_type:
            fields.update(dt.key_fields or [])
            break
    return fields


def _strip_aggregate_words(field: str) -> str:
    """去掉字段名的聚合前缀/后缀，如 总数量→数量、数量合计→数量、总计数量→数量。"""
    base = _AGGREGATE_PREFIX.sub("", field)
    base = _AGGREGATE_SUFFIX.sub("", base)
    return base.strip()


# ============ 字段名限定后缀归一（批次 13） ============
# 背景（验收 BX-2026-0312）：文档类型注册表/OCR 提取到的字段名常带单位限定，
# 如「报销金额（元）」；规则侧只写「报销金额」。两者字面不等，确定性引擎记
# field_missing → 规则停在「无法核验」，LLM 也因"字段不同名"不敢下结论。
# 处理原则与别名一致（写时归一、可审计），并额外收紧两条：
# 1. 只剥离**单位类**限定词（元/美元/%/吨/件…），不剥离「含税」「税前」等语义限定词，
#    避免把「单价（不含税）」错误并到「单价」；
# 2. 剥离后必须能被该文件类型的规范字段集接住，且该规范名不歧义（两个规范字段
#    只差单位时不猜），否则原样保留。
_FIELD_SPACE_RE = re.compile(r"[\s\u3000]+")
_FIELD_QUALIFIER_RE = re.compile(r"[（(\[【]\s*([^）)\]】]{1,12}?)\s*[）)\]】]\s*$")

_UNIT_QUALIFIERS: frozenset[str] = frozenset(
    q.upper()
    for q in (
        # 金额/币别
        "元", "万元", "亿元", "千元", "元整", "人民币", "人民币元",
        "美元", "美金", "港元", "港币", "欧元", "日元", "英镑", "澳元",
        "加元", "台币", "韩元",
        "CNY", "RMB", "USD", "US$", "HKD", "EUR", "JPY", "GBP",
        "AUD", "CAD", "TWD", "KRW",
        # 比例
        "%", "％", "百分比", "千分比",
        # 重量/体积
        "吨", "千克", "公斤", "KG", "克", "G", "斤", "立方米", "平方米",
        "升", "L", "毫升", "ML",
        # 数量/包装/计量
        "件", "个", "只", "箱", "包", "台", "套", "米", "M", "厘米", "MM",
        "张", "份", "批", "车", "桶", "袋", "卷", "盘",
        # 时间/人次
        "天", "日", "次", "人", "人天", "小时", "H", "月", "年", "周",
    )
)


def normalize_field_name(field: Any) -> str:
    """字段名外壳归一：去首尾/中间空白（含全角空格）。"""
    return _FIELD_SPACE_RE.sub("", str(field if field is not None else "")).strip()


def split_field_qualifier(field: Any) -> tuple[str, Optional[str]]:
    """拆出字段名尾部的括号限定词。报销金额（元）→ ("报销金额", "元")；无则返回 (原名, None)。"""
    s = normalize_field_name(field)
    m = _FIELD_QUALIFIER_RE.search(s)
    if not m:
        return s, None
    return s[: m.start()].strip(), m.group(1).strip()


def is_unit_qualifier(qualifier: Any) -> bool:
    """括号限定词是否为"单位类"（可安全剥离）。"""
    q = normalize_field_name(qualifier).upper()
    return bool(q) and q in _UNIT_QUALIFIERS


def strip_unit_qualifier(field: Any) -> str:
    """剥离单位类限定后缀；非单位限定（如「单价（不含税）」）原样返回。"""
    s = normalize_field_name(field)
    base, qualifier = split_field_qualifier(s)
    if qualifier and base and is_unit_qualifier(qualifier):
        return base
    return s


class _FieldNameIndex:
    """字段名归一索引：规范名集合 + 歧义 base + 别名词表（一次构建，逐键复用）。"""

    __slots__ = ("canonical", "ambiguous", "aliases")

    def __init__(self, canonical_fields, aliases: dict | None):
        raw = [normalize_field_name(f) for f in (canonical_fields or [])]
        raw = [f for f in raw if f]
        base_index: dict[str, set[str]] = {}
        for f in raw:
            base_index.setdefault(strip_unit_qualifier(f), set()).add(f)
        self.canonical = set(base_index)
        # 同一 base 对应多个不同规范字段（如 金额（元）/金额（美元））→ 不猜
        self.ambiguous = {b for b, group in base_index.items() if len(group) > 1}
        self.aliases = {
            normalize_field_name(k): v for k, v in (aliases or {}).items()
        }


def _alias_target(entry: Any, fallback: str) -> str:
    target = entry.get("field") if isinstance(entry, dict) else entry
    return normalize_field_name(target) or fallback


def _canonicalize_with_index(key: Any, index: _FieldNameIndex) -> str:
    """按索引把单个字段键映射到规范名；未命中任何规则时原样返回（归一后外壳）。"""
    raw = normalize_field_name(key)
    if not raw:
        return raw
    if raw in index.aliases:
        return _alias_target(index.aliases[raw], raw)
    if raw in index.canonical:
        return raw  # 已是规范字段名：即使存在同 base 歧义也保持原样
    base = strip_unit_qualifier(raw)
    if base != raw and base:
        if base in index.aliases:
            return _alias_target(index.aliases[base], raw)
        if base in index.canonical and base not in index.ambiguous:
            return base
    return raw


def canonicalize_field_key(
    db: Optional[Session],
    doc_type: Any,
    key: Any,
    tenant_id: uuid.UUID | None = None,
    *,
    canonical_fields: Any = None,
    aliases: Optional[dict] = None,
) -> str:
    """单个字段键 → 规范字段名（别名 + 单位限定后缀）。

    canonical_fields / aliases 显式传入时不查库（供批量调用与测试复用）。
    """
    index = _build_field_name_index(db, doc_type, tenant_id, canonical_fields, aliases)
    return _canonicalize_with_index(key, index)


def _build_field_name_index(
    db: Optional[Session],
    doc_type: Any,
    tenant_id: uuid.UUID | None = None,
    canonical_fields: Any = None,
    aliases: Optional[dict] = None,
) -> _FieldNameIndex:
    if aliases is None:
        aliases = resolve_field_aliases(db, doc_type, tenant_id=tenant_id)
    if canonical_fields is None:
        canonical_fields = _canonical_fields(db, doc_type, tenant_id=tenant_id)
    return _FieldNameIndex(canonical_fields, aliases)


def normalize_field(
    db: Optional[Session],
    doc_type: Any,
    field: Any,
    aggregate: Optional[str] = None,
    tenant_id: uuid.UUID | None = None,
) -> tuple[str, Optional[str]]:
    """字段引用 → (规范字段名, 聚合语义)。

    规则解析与存量清洗共用：把规则里写的字段名映射到该文档类型提取模板的规范字段。
    聚合兜底：字段名含"总/合计/总计"且去词后命中规范字段 → 规范字段 + SUM。
    """
    if not field:
        return (field or ""), aggregate
    s = str(field).strip()
    if not s:
        return field, aggregate
    aliases = resolve_field_aliases(db, doc_type, tenant_id=tenant_id)
    if s in aliases:
        m = aliases[s]
        if isinstance(m, dict):
            return str(m.get("field") or s), (m.get("aggregate") or aggregate or None)
        return str(m), aggregate
    base = _strip_aggregate_words(s)
    if base != s and base in _canonical_fields(db, doc_type, tenant_id=tenant_id):
        return base, aggregate or "SUM"
    return s, aggregate


def normalize_scope(
    db: Optional[Session],
    scope: Any,
    tenant_id: uuid.UUID | None = None,
) -> Any:
    """规则 scope 归一：doc_types 列表中的每个名称映射到规范名。"""
    if not isinstance(scope, dict):
        return scope
    out = dict(scope)
    dt_list = out.get("doc_types")
    if isinstance(dt_list, list):
        normalized = []
        seen = set()
        for x in dt_list:
            nx = normalize_doc_type(db, x, tenant_id=tenant_id) or str(x)
            if nx not in seen:
                seen.add(nx)
                normalized.append(nx)
        out["doc_types"] = normalized
    return out


def normalize_structure(
    db: Optional[Session],
    structure: Any,
    rule_doc_type: Any = None,
    tenant_id: uuid.UUID | None = None,
) -> Any:
    """规则 structure 归一：assertion 两侧 + condition + exceptions 的
    doc_type / field 全部映射到规范名。聚合语义（SUM）在字段归一时保留/补全。
    """
    if not isinstance(structure, dict):
        return structure
    rule_doc_type = (
        normalize_doc_type(db, rule_doc_type, tenant_id=tenant_id)
        or rule_doc_type
    )
    out = dict(structure)

    assertion = out.get("assertion")
    if isinstance(assertion, dict):
        a = dict(assertion)
        op = normalize_operator(a.get("operator"))
        if op:
            a["operator"] = op
        for side in ("source", "target"):
            sd = a.get(side)
            if not isinstance(sd, dict):
                continue
            s2 = dict(sd)
            if s2.get("doc_type"):
                s2["doc_type"] = (
                    normalize_doc_type(db, s2["doc_type"], tenant_id=tenant_id)
                    or s2["doc_type"]
                )
            side_dt = s2.get("doc_type") or rule_doc_type
            fld, agg = normalize_field(
                db,
                side_dt,
                s2.get("field"),
                s2.get("aggregate"),
                tenant_id=tenant_id,
            )
            s2["field"] = fld
            if agg:
                s2["aggregate"] = agg
            a[side] = s2
        out["assertion"] = a

    cond = out.get("condition")
    if isinstance(cond, dict):
        c2 = dict(cond)
        op = normalize_operator(c2.get("operator"))
        if op:
            c2["operator"] = op
        fld, _ = normalize_field(
            db,
            rule_doc_type,
            c2.get("field"),
            None,
            tenant_id=tenant_id,
        )
        c2["field"] = fld
        # 币别类条件的值按注册表归一（如规则解析输出 "USD" → "美元"）
        if is_currency_field(fld) and c2.get("value") is not None:
            c2["value"] = normalize_currency_value(c2["value"])
        out["condition"] = c2

    exceptions = out.get("exceptions")
    if isinstance(exceptions, list):
        ex2 = []
        for e in exceptions:
            if isinstance(e, dict) and e.get("field"):
                e2 = dict(e)
                fld, _ = normalize_field(
                    db,
                    rule_doc_type,
                    e2.get("field"),
                    None,
                    tenant_id=tenant_id,
                )
                e2["field"] = fld
                if is_currency_field(fld) and e2.get("value") is not None:
                    e2["value"] = normalize_currency_value(e2["value"])
                ex2.append(e2)
            else:
                ex2.append(e)
        out["exceptions"] = ex2

    # requirement（P3：存在性/齐备要求）：字段名与文件类型同样归一，
    # 让"部门负责人签字"这类要求能与文档类型注册表/学习到的别名对齐。
    requirement = out.get("requirement")
    if isinstance(requirement, dict):
        req = dict(requirement)
        items = req.get("items") or req.get("all_of")
        if isinstance(items, list):
            norm_items = []
            for item in items:
                if not isinstance(item, dict) or not item.get("field"):
                    norm_items.append(item)
                    continue
                i2 = dict(item)
                item_doc_type = i2.get("doc_type") or rule_doc_type
                i2["doc_type"] = (
                    normalize_doc_type(db, item_doc_type, tenant_id=tenant_id)
                    or item_doc_type
                )
                fld, _ = normalize_field(
                    db,
                    i2["doc_type"],
                    i2.get("field"),
                    None,
                    tenant_id=tenant_id,
                )
                i2["field"] = fld
                i2.setdefault("must", "present")
                norm_items.append(i2)
            req["items"] = norm_items
            req.pop("all_of", None)
        out["requirement"] = req

    return out


def normalize_extracted_keys(
    db: Optional[Session],
    doc_type: Any,
    fields: Any,
    tenant_id: uuid.UUID | None = None,
    *,
    canonical_fields: Any = None,
    aliases: Optional[dict] = None,
) -> Any:
    """存量文档 extracted_fields 的字段键归一。

    两类映射（批次 11 别名 + 批次 13 单位限定后缀）：
    1. 别名表：按该类型 field_aliases / 内置别名重命名；
    2. 单位限定后缀：报销金额（元）→ 该类型规范字段「报销金额」（仅单位类限定、无歧义时）。

    规范键已存在时保留规范键值，丢弃别名键；未命中任何规则的键原样保留；
    备注类字段（「备注-」前缀）一律不归一，备注原文语义不稳定（FR-3）。
    """
    if not isinstance(fields, dict):
        return fields
    index = _build_field_name_index(db, doc_type, tenant_id, canonical_fields, aliases)
    if not index.canonical and not index.aliases:
        return fields
    preferred = index.canonical | set(index.aliases)

    def _rank(item: tuple) -> int:
        name = normalize_field_name(item[0])
        if name in index.canonical:
            return 0  # 已是规范名：最可信
        return 1 if name in preferred else 2

    out: dict[str, Any] = {}
    # 规范名先处理：同一目标出现多次时保留"更规范"的那个值，避免结果依赖 OCR 返回的
    # 键顺序（如 金额=100 与 总价=200 同时出现时保留 总价=200）。
    for k, v in sorted(fields.items(), key=_rank):
        target = k if C.is_remark_field(k) else _canonicalize_with_index(k, index)
        if target in out:
            continue  # 规范键已存在，保留规范值
        out[target] = v
    return out


def normalize_document_fields(
    db: Optional[Session],
    doc_type: Any,
    fields: Any,
    tenant_id: uuid.UUID | None = None,
) -> Any:
    """文档字段写入侧统一入口：值归一（金额/日期/币别…）+ 字段键归一（别名/单位后缀）。

    OCR 提取、合同样例分析等所有落库路径都应走这里，保证库里只存规范字段名，
    审查匹配保持精确等值、可预期、可审计（批次 11 设计原则 + 批次 13）。
    """
    if not isinstance(fields, dict):
        return fields
    from .field_extraction_service import normalize_fields  # 延迟导入：避免模块循环

    aliases = resolve_field_aliases(db, doc_type, tenant_id=tenant_id)
    valued = normalize_fields(doc_type, fields, aliases=aliases)
    return normalize_extracted_keys(
        db, doc_type, valued, tenant_id=tenant_id, aliases=aliases
    )
