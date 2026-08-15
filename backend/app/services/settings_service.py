"""系统设置服务（批次 11）。

提供：
- 提示词模板的查看/修改（默认值见 prompt_templates.PROMPT_TEMPLATES）
- 少量运行参数的查看/修改（容差、阈值、开关等，默认值见 config/常量）
- LLM 自动优化提示词（返回建议，不直接落库，由前端确认后应用）

原则：DB 有值用 DB，无值回退内置默认；重置 = 删除该 key 的记录。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings as app_settings
from ..llm_client import LLMError, get_llm_client
from ..models import SystemSetting
from ..prompt_templates import PROMPT_META, PROMPT_TEMPLATES, get_prompt_default

logger = logging.getLogger(__name__)


# ============ 运行参数（非提示词）默认值与元信息 ============
KNOBS: dict[str, dict] = {
    "llm.confidence_threshold": {
        "label": "规则解析自动确认阈值",
        "group": "运行参数",
        "description": "LLM 解析规则的置信度 ≥ 该阈值时自动确认（否则进入人工确认）。",
        "kind": "number",
        "default": float(app_settings.llm_confidence_threshold),
    },
    "review.amount_tolerance_percent": {
        "label": "金额容差（%）",
        "group": "运行参数",
        "description": "旧版审查逻辑的全局金额容差百分比（规则自带容差优先）。",
        "kind": "number",
        "default": float(app_settings.amount_tolerance_percent),
    },
    "review.allow_same_day_receive_pay": {
        "label": "允许收付同日",
        "group": "运行参数",
        "description": "旧版时间逻辑：收款与付款同日是否视为合规（规则自带容差优先）。",
        "kind": "boolean",
        "default": bool(app_settings.allow_same_day_receive_pay),
    },
    "review.semantic_adjudication_enabled": {
        "label": "LLM 语义裁决（条件/不可核验复核）",
        "group": "运行参数",
        "description": "开启后，条件预检无法确定或疑似误判、断言不可核验的规则，由 LLM 结合合同上下文与关联单据复核；无法确认时降级为待人工确认。",
        "kind": "boolean",
        "default": True,
    },
    "rules.auto_confirm_new_types": {
        "label": "规则导入新类型自动激活",
        "group": "运行参数",
        "description": "关闭（默认）：规则导入发现的新文档类型注册为 pending_review，人工确认后才可用；开启则直接 active。",
        "kind": "boolean",
        "default": False,
    },
}


def _meta(key: str) -> dict:
    if key in PROMPT_META:
        m = dict(PROMPT_META[key])
        m.setdefault("kind", "text")
        m.setdefault("default", get_prompt_default(key))
        return m
    if key in KNOBS:
        return dict(KNOBS[key])
    return {"label": key, "group": "其他", "description": "", "kind": "text", "default": None}


def get_setting(db: Optional[Session], key: str, default: Any = None) -> Any:
    """读取单条设置；DB 无记录回退内置默认。"""
    if db is not None:
        try:
            row = db.get(SystemSetting, key)
            if row is not None:
                return row.value
        except Exception:
            logger.warning("读取系统设置失败（回退默认）: %s", key, exc_info=True)
    meta = _meta(key)
    if default is not None:
        return default
    return meta.get("default")


def get_prompt(db: Optional[Session], key: str) -> str:
    """读取提示词模板（DB 覆盖或内置默认）。"""
    val = get_setting(db, key)
    return str(val) if val is not None else get_prompt_default(key)


def get_all_settings(db: Session) -> list[dict]:
    """返回全部设置（内置默认 + DB 覆盖），供前端渲染。"""
    rows = {r.key: r for r in db.execute(select(SystemSetting)).scalars()}
    keys = sorted(set(PROMPT_TEMPLATES) | set(KNOBS))
    out: list[dict] = []
    for key in keys:
        meta = _meta(key)
        value = rows[key].value if key in rows else meta.get("default")
        out.append(
            {
                "key": key,
                "label": meta.get("label", key),
                "group": meta.get("group", "其他"),
                "description": meta.get("description", ""),
                "kind": meta.get("kind", "text"),
                "value": value,
                "is_default": key not in rows,
            }
        )
    return out


def update_settings(db: Session, items: list[dict]) -> list[dict]:
    """批量更新设置；value 为 None 表示重置回默认（删除记录）。"""
    for item in items:
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        value = item.get("value")
        meta = _meta(key)
        if value is None:
            row = db.get(SystemSetting, key)
            if row is not None:
                db.delete(row)
            continue
        # 数值/布尔类型做一次类型清洗
        if meta.get("kind") == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
        elif meta.get("kind") == "boolean":
            value = bool(value) if not isinstance(value, str) else value.lower() in ("true", "1", "是", "yes")
        row = db.get(SystemSetting, key)
        if row is None:
            db.add(
                SystemSetting(
                    key=key,
                    value=value,
                    group=meta.get("group", "其他"),
                    label=meta.get("label", key),
                    description=meta.get("description"),
                    kind=meta.get("kind", "text"),
                )
            )
        else:
            row.value = value
    db.commit()
    return get_all_settings(db)


def optimize_prompt(
    db: Session,
    key: str,
    current_prompt: str,
    instruction: str = "",
) -> dict:
    """LLM 自动优化提示词：返回建议文本与理由，不直接落库。

    用户可在前端查看差异后确认应用（应用走 update_settings）。
    """
    llm = get_llm_client()
    meta = _meta(key)
    user_instruction = (
        f"用户补充要求：{instruction}\n\n" if instruction.strip() else ""
    )
    user_prompt = (
        "你是提示词工程专家。请优化下面的系统提示词，使其输出更稳定、更准确，"
        "同时严格保持原有的输出 JSON schema 与占位符（{xxx} 形式的占位符一个都不能丢）。\n\n"
        f"提示词用途：{meta.get('description') or meta.get('label') or key}\n"
        f"{user_instruction}"
        f"当前提示词：\n---\n{current_prompt}\n---\n\n"
        "输出严格 JSON：{\"suggested\": \"优化后的完整提示词\", "
        "\"reasoning\": \"改动点说明（中文，2-4 条）\"}"
    )
    system_prompt = (
        "你是提示词工程专家。只输出 JSON，不要输出其他内容。"
        "优化目标：指令更明确、减少歧义、防止模型漏字段/臆造；"
        "保持占位符 {xxx} 不变；不改变原有功能的语义范围。"
    )
    try:
        resp = llm.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8192,
        )
        suggested = str(resp.get("suggested") or "").strip()
        if not suggested:
            raise ValueError("LLM 未返回建议内容")
        return {
            "key": key,
            "suggested": suggested,
            "reasoning": str(resp.get("reasoning") or "").strip(),
        }
    except (LLMError, ValueError) as e:
        logger.warning("提示词优化失败: %s", e)
        return {"key": key, "suggested": "", "reasoning": "", "error": f"优化失败: {e}"}
