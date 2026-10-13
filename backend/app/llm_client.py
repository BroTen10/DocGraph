"""LLM 客户端封装（OpenAI 兼容格式，用于 DeepSeek 文本与多模态模型）。

职责划分：
- 出口两个：``chat()`` 要文本，``chat_json()`` 要 dict
- 可重试故障（限流 / 超时 / 连接失败）按指数退避重试，最多 ``max_retries`` 次
- 模型偶发返回空内容（推理模型耗尽 max_tokens、多模态返回空串）同样重试
- 不可重试故障立即抛出，并带上错误分类码：
  ``auth_error`` / ``arrearage`` / ``retryable`` / ``invalid_response`` / ``unknown``
- JSON 提取容错：markdown 围栏、JSON 前后夹带文本、字符串里含括号
- max_tokens 截断导致 JSON 不完整时降级修复，保留已经完整的部分
- 进程内单例工厂 ``get_llm_client()``
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any, Optional

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from .config import settings

logger = logging.getLogger(__name__)

# ============ 错误分类码 ============

ERROR_AUTH = "auth_error"
ERROR_ARREARAGE = "arrearage"
ERROR_RETRYABLE = "retryable"
ERROR_INVALID_RESPONSE = "invalid_response"
ERROR_UNKNOWN = "unknown"

# ============ 重试策略 ============

_RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError)
_RETRY_BASE_SECONDS = 1.0
_RETRY_MAX_SECONDS = 30.0
_RETRY_JITTER_SECONDS = 1.0

# ============ 错误信息特征（异常类型区分不出来时靠消息判断） ============

_AUTH_HINTS = ("auth", "unauthorized", "invalid api key", "401")
_ARREARAGE_HINTS = ("arrearage", "overdue", "insufficient balance", "余额", "欠费")


def _backoff_seconds(attempt: int) -> float:
    """第 attempt 次失败后的等待秒数：指数增长 + 抖动，封顶 30 秒。"""
    ceiling = min(_RETRY_BASE_SECONDS * 2 ** (attempt - 1), _RETRY_MAX_SECONDS)
    return ceiling + random.uniform(0, _RETRY_JITTER_SECONDS)


def _error_code(e: Exception) -> str:
    """给异常定一个分类码，供调用方决定是重试、提示用户还是告警。"""
    message = str(e).lower()
    if any(hint in message for hint in _AUTH_HINTS):
        return ERROR_AUTH
    if any(hint in message for hint in _ARREARAGE_HINTS):
        return ERROR_ARREARAGE
    if isinstance(e, _RETRYABLE_ERRORS):
        return ERROR_RETRYABLE
    return ERROR_UNKNOWN


class LLMError(Exception):
    """LLM 调用错误。error_type 见模块顶部的分类码。"""

    def __init__(self, message: str, error_type: str = ERROR_UNKNOWN) -> None:
        super().__init__(message)
        self.error_type = error_type


class LLMClient:
    """OpenAI 兼容 LLM 客户端。文本与多模态共用一套调用与重试逻辑。"""

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
        self._max_retries = max(1, max_retries)
        if not self.api_key:
            raise ValueError("LLM API key 未配置（请在 .env 中设置 LLM_API_KEY）")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self._timeout)

    # ---------- 对外接口 ----------

    def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = 0.3,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        disable_thinking: bool = False,
    ) -> str:
        """发一次聊天请求并返回文本内容，可重试故障自动重试。"""
        return self._call_with_retry(
            self._build_kwargs(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                model=model,
                enable_thinking=enable_thinking,
                disable_thinking=disable_thinking,
            ),
            purpose="LLM 调用",
        )

    def chat_json(
        self,
        messages: list[dict],
        temperature: Optional[float] = 0.3,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        disable_thinking: bool = False,
    ) -> dict:
        """发一次聊天请求并解析 JSON，兼容围栏代码块与截断输出。"""
        raw = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            model=model,
            enable_thinking=enable_thinking,
            disable_thinking=disable_thinking,
        )
        payload = _extract_json_payload(raw)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as e:
            # 批次 4-2：max_tokens 截断导致末尾不完整时降级保留完整部分
            repaired = _repair_truncated_json(payload)
            if repaired is not None:
                logger.warning(
                    "JSON 截断修复成功（%d → %d 字符），已降级保留完整部分",
                    len(payload), len(repaired),
                )
                return json.loads(repaired)
            logger.error("JSON 解析失败: %s\n原始内容前 200 字: %r", e, raw[:200])
            raise LLMError(f"模型返回的不是有效 JSON: {e}", ERROR_INVALID_RESPONSE) from e

    # ---------- 内部实现 ----------

    def _build_kwargs(
        self,
        messages: list[dict],
        temperature: Optional[float],
        max_tokens: Optional[int],
        response_format: Optional[dict],
        model: Optional[str],
        enable_thinking: Optional[bool],
        disable_thinking: bool,
    ) -> dict:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.3 if temperature is None else temperature,
            "model": model or self.model,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["response_format"] = response_format
        extra_body = _thinking_params(enable_thinking, disable_thinking)
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    def _call_with_retry(self, kwargs: dict, purpose: str) -> str:
        """调用底层接口，按错误类型决定重试还是立即抛出。"""
        attempt = 0
        while True:
            attempt += 1
            last_error: Exception
            try:
                return _read_content(self.client.chat.completions.create(**kwargs))
            except _RETRYABLE_ERRORS as e:
                if attempt >= self._max_retries:
                    raise LLMError(
                        f"{purpose} 连续 {attempt} 次失败：{e}", ERROR_RETRYABLE
                    ) from e
                last_error = e
            except LLMError as e:
                # 空响应通常是瞬时的（推理模型耗尽 max_tokens），值得再试
                if e.error_type != ERROR_INVALID_RESPONSE or attempt >= self._max_retries:
                    raise
                last_error = e
            except Exception as e:
                raise LLMError(f"{purpose} 失败：{e}", _error_code(e)) from e
            delay = _backoff_seconds(attempt)
            logger.warning("%s 第 %d 次重试（%.1fs 后）：%s", purpose, attempt, delay, last_error)
            time.sleep(delay)


def _read_content(response: Any) -> str:
    """从响应里取文本内容；拿不到有效内容就按空响应处理。"""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMError("模型返回空响应", ERROR_INVALID_RESPONSE)
    content = getattr(getattr(choices[0], "message", None), "content", None)
    if content is None or not str(content).strip():
        raise LLMError("模型返回空响应", ERROR_INVALID_RESPONSE)
    return content


def _thinking_params(enable_thinking: Optional[bool], disable_thinking: bool) -> dict:
    """把两种开/关思考的写法合并成同一个 extra_body。"""
    extra: dict[str, Any] = {}
    # 部分 OpenAI 兼容模型（如通义千问 Qwen3+）用 enable_thinking 扩展参数
    if enable_thinking is not None:
        extra["enable_thinking"] = enable_thinking
    # 部分推理模型须显式关闭思考，否则耗尽 max_tokens 后只剩 reasoning_content、content 为空
    if disable_thinking:
        extra["thinking"] = {"type": "disabled"}
    return extra


# ============ JSON 提取与截断修复 ============


def _json_span(text: str) -> Optional[tuple[int, int]]:
    """找出文本里第一个完整的 JSON 对象或数组，返回起止下标；没有则 None。

    扫描过程维护字符串与转义状态，所以字符串里的括号和引号不会干扰配对。
    """
    start = -1
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            break
    if start < 0:
        return None

    stack: list[str] = []
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            if not stack or stack.pop() != char:
                return None
            if not stack:
                return start, index + 1
    return None


def _from_first_bracket(text: str) -> str:
    """从第一个 { 或 [ 起截取，用于内容本身不完整、需要交给修复逻辑的场景。"""
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if not starts:
        return text
    tail = text[min(starts):]
    if "```" in tail:
        tail = tail.split("```")[0]
    return tail.strip()


def _extract_json_payload(text: str) -> str:
    """从模型输出里取出 JSON 片段，容忍围栏代码块与前后夹带的说明文字。"""
    text = (text or "").strip()
    span = _json_span(text)
    if span is not None:
        return text[span[0]:span[1]]
    return _from_first_bracket(text)


def _repair_truncated_json(text: str) -> Optional[str]:
    """修复被 max_tokens 截断的 JSON（批次 4-2）。

    按顺序尝试三种补法，取第一个能解析的结果，尽量多保留数据：
    1. 补上未闭合的字符串引号（并去掉尾部残留逗号）后补齐括号；
    2. 直接补齐括号；
    3. 退到最后一个完整的 ``}`` / ``]``，丢弃确实不完整的尾部。
    返回修复后的字符串；无法修复返回 None。
    """
    s = (text or "").strip()
    if not s:
        return None

    candidates = [_balance_brackets(_close_trailing_string(s)), _balance_brackets(s)]
    cut = _last_closing_index(s)
    if cut >= 0:
        candidates.append(_balance_brackets(_close_trailing_string(s[: cut + 1])))

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
    escaped = False
    for index, char in enumerate(s):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_str = not in_str
            continue
        if not in_str and char in "}]":
            last = index
    return last


def _close_trailing_string(s: str) -> str:
    """末尾若停在未闭合的字符串里就补一个引号；顺带去掉尾部残留逗号。"""
    out = s.rstrip()
    while out.endswith(","):
        out = out[:-1].rstrip()
    in_str = False
    escaped = False
    for char in out:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_str = not in_str
    return out + '"' if in_str else out


def _balance_brackets(s: str) -> str:
    """补齐缺失的右括号/花括号（感知字符串，忽略引号内的括号）。"""
    stack: list[str] = []
    in_str = False
    escaped = False
    for char in s:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            if stack and stack[-1] == char:
                stack.pop()
    return s + "".join(reversed(stack))


# ============ 单例 ============

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
