"""LLM 客户端封装（OpenAI 兼容格式，用于 DeepSeek 与通义千问）。

参考 MiroFish-Explorer 的 llm_client.py 设计：指数退避重试、JSON 解析容错、单例工厂。
本模块重新实现，去除对原项目 services 包的耦合，保留关键能力：
- 自动重试可重试错误（RateLimit / Timeout / Connection）
- chat_json 返回结构化 JSON（带 markdown 代码块容错）
- 全局单例工厂 get_llm_client()
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from typing import Any, Optional

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from .config import settings

logger = logging.getLogger(__name__)

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError)


def _sleep_with_jitter(attempt: int, max_jitter: float = 1.0) -> None:
    """指数退避 + 随机抖动。"""
    delay = min(2 ** attempt, 30)
    time.sleep(delay + random.uniform(0, max_jitter))


class LLMError(Exception):
    """LLM 调用错误。"""

    def __init__(self, message: str, error_type: str = "unknown") -> None:
        super().__init__(message)
        self.error_type = error_type


class LLMClient:
    """OpenAI 兼容 LLM 客户端。可同时用于 DeepSeek 文本模型和通义千问多模态模型。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 120,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model_name
        self._timeout = timeout
        self._max_retries = max_retries
        if not self.api_key:
            raise ValueError("LLM API key 未配置（请在 .env 中设置 LLM_API_KEY）")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self._timeout)

    def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = 0.3,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
    ) -> str:
        """带自动重试的聊天请求。返回响应文本。"""
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else 0.3,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["response_format"] = response_format
        # 通义千问 Qwen3+ 系列：enable_thinking 走 extra_body（OpenAI 兼容端点）
        if enable_thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": enable_thinking}

        last_err: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content
                if content is None:
                    raise LLMError("模型返回空响应", "invalid_response")
                return content
            except RETRYABLE_ERRORS as e:
                last_err = e
                if attempt == self._max_retries - 1:
                    break
                _sleep_with_jitter(attempt)
                logger.warning("LLM 第 %d 次重试: %s", attempt + 1, e)
            except Exception as e:
                raise LLMError(f"LLM 调用失败: {e}", _classify_error(e)) from e
        raise LLMError(f"LLM 重试 {self._max_retries} 次后仍失败: {last_err}", "retryable") from last_err

    def chat_json(
        self,
        messages: list[dict],
        temperature: Optional[float] = 0.3,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
    ) -> dict:
        """聊天请求并返回解析后的 JSON。带 markdown 代码块容错。"""
        resp = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            model=model,
            enable_thinking=enable_thinking,
        )
        json_str = _extract_json(resp)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            # 批次 4-2：截断 JSON 容错修复（max_tokens 截断导致末尾不完整）
            repaired = _repair_truncated_json(json_str)
            if repaired is not None:
                logger.warning(
                    "JSON 截断修复成功（%d → %d 字符），已降级保留完整部分",
                    len(json_str), len(repaired),
                )
                return json.loads(repaired)
            logger.error("JSON 解析失败: %s\n原始内容前 200 字: %r", e, resp[:200])
            raise LLMError(f"模型返回的不是有效 JSON: {e}", "invalid_response") from e


def _classify_error(e: Exception) -> str:
    """错误分类。"""
    msg = str(e).lower()
    if "auth" in msg or "unauthorized" in msg or "401" in msg:
        return "auth_error"
    if "arrearage" in msg or "overdue" in msg or "余额" in msg or "欠费" in msg:
        return "arrearage"
    if isinstance(e, RETRYABLE_ERRORS):
        return "retryable"
    return "unknown"


def _extract_json(text: str) -> str:
    """从可能包含 markdown 代码块的文本中提取 JSON。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 找最外层大括号
    if text.startswith("{"):
        depth = 0
        for i, c in enumerate(text):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[: i + 1]
    if text.startswith("["):
        depth = 0
        for i, c in enumerate(text):
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return text[: i + 1]
    return text


def _repair_truncated_json(text: str) -> Optional[str]:
    """修复被 max_tokens 截断的 JSON（批次 4-2）。

    策略（按优先级，优先保留完整数据）：
    1. 回退到最后一个完整的 '}' / ']'（丢弃被截断的尾部不完整元素）
    2. 闭合末尾未闭合的字符串 + 补齐缺失的右括号/花括号
    3. 仅补齐括号（字符串完整但缺闭合符的场景）
    返回修复后的 JSON 字符串；无法修复返回 None。
    """
    s = text.strip()
    if not s:
        return None
    candidates: list[str] = []
    cut = _last_closing_index(s)
    if cut >= 0:
        candidates.append(_balance_brackets(_close_trailing_string(s[: cut + 1])))
    candidates.append(_balance_brackets(_close_trailing_string(s)))
    candidates.append(_balance_brackets(s))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return None


def _last_closing_index(s: str) -> int:
    """返回字符串外最后一个 '}' 或 ']' 的索引；无则 -1（感知引号与转义）。"""
    last = -1
    in_str = False
    escape = False
    for i, c in enumerate(s):
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if not in_str and c in "}]":
            last = i
    return last


def _close_trailing_string(s: str) -> str:
    """若末尾处于未闭合的字符串内，补一个双引号；同时去掉尾部残留逗号。"""
    out = s.rstrip()
    while out.endswith(","):
        out = out[:-1].rstrip()
    in_str = False
    escape = False
    for c in out:
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
    return out + '"' if in_str else out


def _balance_brackets(s: str) -> str:
    """补齐缺失的右括号/花括号（感知字符串，忽略引号内的括号）。"""
    stack: list[str] = []
    in_str = False
    escape = False
    for c in s:
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            stack.append("}")
        elif c == "[":
            stack.append("]")
        elif c in "}]":
            if stack and stack[-1] == c:
                stack.pop()
    return s + "".join(reversed(stack))


_global_llm: Optional[LLMClient] = None
_global_llm_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """全局单例 LLM 客户端（DeepSeek 文本模型）。"""
    global _global_llm
    if _global_llm is not None:
        return _global_llm
    with _global_llm_lock:
        if _global_llm is None:
            _global_llm = LLMClient()
        return _global_llm
