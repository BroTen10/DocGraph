"""LLM 客户端离线桩测试（不联网、不依赖数据库、不需要 API key）。

用假的 OpenAI client 顶替真实网络调用，覆盖：
- 可重试错误（429 / 超时 / 连接失败）的退避重试与重试耗尽
- 空响应（空 choices / 空白 content）的重试与最终失败
- 不可重试错误（鉴权、欠费）的分类且立即抛出
- chat_json 的 markdown 围栏、JSON 前后夹带文本、字符串内的括号
- 截断 JSON 的降级修复（批次 4-2 行为）
- enable_thinking / disable_thinking 透传为 extra_body
- 全局单例工厂

用法（仓库根目录执行）：
    backend\\.venv\\Scripts\\python.exe tests\\test_llm_client.py

退出码：0 = 全部通过，1 = 存在失败。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # Settings 的 env_file=".env" 按相对路径加载 backend/.env

from openai import (  # noqa: E402
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

import app.llm_client as llm_mod  # noqa: E402
from app.llm_client import LLMClient, LLMError, get_llm_client  # noqa: E402

API_URL = "http://example.invalid/v1/chat/completions"


# ============ 假响应与假 API ============


def ok(content: str):
    """构造一个正常的 chat.completions 响应。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def no_choices():
    """构造 choices 为空的响应（部分推理模型会这样返回）。"""
    return SimpleNamespace(choices=[])


def api_error(cls, status: int = 429, message: str = "boom"):
    """离线构造 openai 的各类错误对象。"""
    request = httpx.Request("POST", API_URL)
    if cls in (APITimeoutError, APIConnectionError):
        return cls(request=request)
    return cls(message, response=httpx.Response(status, request=request), body=None)


class FakeAPI:
    """按脚本依次返回响应或抛异常，并记录每次调用收到的 kwargs。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else ok("{}")
        if isinstance(item, BaseException):
            raise item
        return item


class Harness:
    """把 LLMClient 底层 API 换成假实现，并记录 sleep 时长（不真的等待）。"""

    def __init__(self, script, **kwargs):
        self.client = LLMClient(
            api_key="test-key",
            base_url="http://example.invalid/v1",
            model="test-model",
            **kwargs,
        )
        self.api = FakeAPI(script)
        self.client.client = SimpleNamespace(chat=SimpleNamespace(completions=self.api))
        self.sleeps: list[float] = []
        self._real_time = llm_mod.time
        shim = {name: getattr(time, name) for name in dir(time)}
        shim["sleep"] = self._record_sleep
        llm_mod.time = SimpleNamespace(**shim)

    def _record_sleep(self, seconds):
        self.sleeps.append(float(seconds))

    def restore(self):
        llm_mod.time = self._real_time

    def chat(self, **kwargs):
        return self.client.chat(messages=[{"role": "user", "content": "hi"}], **kwargs)


def expect_llm_error(fn, error_type: str | None = None) -> LLMError:
    try:
        fn()
    except LLMError as e:
        if error_type is not None and e.error_type != error_type:
            raise AssertionError(f"error_type 期望 {error_type!r}，实际 {e.error_type!r}（{e}）")
        return e
    raise AssertionError("期望抛 LLMError，但没有抛出")


def check(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ============ 重试与退避 ============


def test_rate_limit_then_success():
    h = Harness([api_error(RateLimitError), ok("hello")])
    try:
        out = h.chat()
        check(out == "hello", f"应返回 hello，实际 {out!r}")
        check(len(h.api.calls) == 2, f"应调用 2 次，实际 {len(h.api.calls)}")
        check(len(h.sleeps) == 1, f"应退避 1 次，实际 {len(h.sleeps)}")
        check(1.0 <= h.sleeps[0] <= 3.0, f"首次退避应在 1~3 秒，实际 {h.sleeps[0]}")
    finally:
        h.restore()


def test_retries_exhausted_marks_retryable():
    h = Harness([api_error(RateLimitError) for _ in range(6)])
    try:
        expect_llm_error(h.chat, "retryable")
        check(len(h.api.calls) == 3, f"默认最多尝试 3 次，实际 {len(h.api.calls)}")
        check(len(h.sleeps) == 2, f"3 次尝试之间应退避 2 次，实际 {len(h.sleeps)}")
        check(h.sleeps[1] > h.sleeps[0], f"退避应递增：{h.sleeps}")
    finally:
        h.restore()


def test_timeout_is_retried():
    h = Harness([api_error(APITimeoutError), ok("done")])
    try:
        check(h.chat() == "done", "超时后重试应成功")
        check(len(h.api.calls) == 2, f"应调用 2 次，实际 {len(h.api.calls)}")
    finally:
        h.restore()


def test_connection_error_is_retried():
    h = Harness([api_error(APIConnectionError), ok("done")])
    try:
        check(h.chat() == "done", "连接失败后重试应成功")
        check(len(h.api.calls) == 2, f"应调用 2 次，实际 {len(h.api.calls)}")
    finally:
        h.restore()


# ============ 空响应 ============


def test_empty_choices_retried():
    h = Harness([no_choices(), ok("done")])
    try:
        check(h.chat() == "done", "choices 为空后重试应成功")
        check(len(h.api.calls) == 2, f"应调用 2 次，实际 {len(h.api.calls)}")
    finally:
        h.restore()


def test_blank_content_retried():
    h = Harness([ok("   "), ok("done")])
    try:
        check(h.chat() == "done", "空白 content 后重试应成功")
        check(len(h.api.calls) == 2, f"应调用 2 次，实际 {len(h.api.calls)}")
    finally:
        h.restore()


def test_empty_content_exhausts_with_invalid_response():
    h = Harness([no_choices() for _ in range(6)])
    try:
        expect_llm_error(h.chat, "invalid_response")
        check(len(h.api.calls) == 3, f"空响应也应重试到上限，实际尝试 {len(h.api.calls)} 次")
    finally:
        h.restore()


# ============ 错误分类（不重试） ============


def test_auth_error_not_retried():
    err = api_error(AuthenticationError, 401, "Error code: 401 - Authentication Fails, invalid api key")
    h = Harness([err])
    try:
        expect_llm_error(h.chat, "auth_error")
        check(len(h.api.calls) == 1, f"鉴权失败不应重试，实际调用 {len(h.api.calls)} 次")
        check(h.sleeps == [], f"鉴权失败不应退避，实际 {h.sleeps}")
    finally:
        h.restore()


def test_arrearage_not_retried():
    h = Harness([RuntimeError("Error code: 402 - Insufficient Balance 余额不足")])
    try:
        expect_llm_error(h.chat, "arrearage")
        check(len(h.api.calls) == 1, f"欠费不应重试，实际调用 {len(h.api.calls)} 次")
    finally:
        h.restore()


def test_unknown_error_classified_as_unknown():
    h = Harness([RuntimeError("something odd happened")])
    try:
        expect_llm_error(h.chat, "unknown")
    finally:
        h.restore()


# ============ chat_json ============


def test_chat_json_parses_fenced_markdown():
    h = Harness([ok('```json\n{"a": 1, "b": [2, 3]}\n```')])
    try:
        out = h.client.chat_json(messages=[{"role": "user", "content": "hi"}])
        check(out == {"a": 1, "b": [2, 3]}, f"围栏 JSON 解析结果不对：{out!r}")
        check(h.api.calls[0]["response_format"] == {"type": "json_object"}, "应要求 json_object")
    finally:
        h.restore()


def test_chat_json_extracts_from_surrounding_text():
    h = Harness([ok('结果如下：\n{"a": {"b": [1, {"c": 2}]}, "d": 3}\n以上。')])
    try:
        out = h.client.chat_json(messages=[{"role": "user", "content": "hi"}])
        check(out == {"a": {"b": [1, {"c": 2}]}, "d": 3}, f"夹带文本时应能取出 JSON：{out!r}")
    finally:
        h.restore()


def test_chat_json_handles_braces_inside_strings():
    h = Harness([ok('{"a": "}", "b": {"c": "["}, "d": "a\\"b"}')])
    try:
        out = h.client.chat_json(messages=[{"role": "user", "content": "hi"}])
        check(out == {"a": "}", "b": {"c": "["}, "d": 'a"b'}, f"字符串内括号被误判：{out!r}")
    finally:
        h.restore()


def test_chat_json_repairs_truncated_array():
    h = Harness([ok('{"items": [{"name": "a"}, {"name": "b"')])
    try:
        out = h.client.chat_json(messages=[{"role": "user", "content": "hi"}])
        check(out == {"items": [{"name": "a"}, {"name": "b"}]}, f"截断修复结果不对：{out!r}")
    finally:
        h.restore()


def test_chat_json_repairs_unterminated_string():
    h = Harness([ok('{"a": 1, "b": "hello wo')])
    try:
        out = h.client.chat_json(messages=[{"role": "user", "content": "hi"}])
        check(out == {"a": 1, "b": "hello wo"}, f"未闭合字符串修复不对：{out!r}")
    finally:
        h.restore()


def test_chat_json_repairs_trailing_comma():
    h = Harness([ok('{"a": 1, "b": 2,')])
    try:
        out = h.client.chat_json(messages=[{"role": "user", "content": "hi"}])
        check(out == {"a": 1, "b": 2}, f"尾逗号修复不对：{out!r}")
    finally:
        h.restore()


def test_chat_json_rejects_non_json():
    h = Harness([ok("完全不是 JSON")])
    try:
        expect_llm_error(
            lambda: h.client.chat_json(messages=[{"role": "user", "content": "hi"}]),
            "invalid_response",
        )
    finally:
        h.restore()


# ============ 请求参数 ============


def test_request_kwargs_defaults():
    h = Harness([ok("x"), ok("y"), ok("z")])
    try:
        h.chat(temperature=None)
        h.chat(max_tokens=128, model="other-model")
        h.chat()
        first, second, third = h.api.calls
        check(first["model"] == "test-model", f"默认模型应取实例模型：{first['model']}")
        check(first["temperature"] == 0.3, f"temperature=None 时应回落到 0.3：{first['temperature']}")
        check("max_tokens" not in first, "未指定 max_tokens 时不应传该参数")
        check(second["model"] == "other-model" and second["max_tokens"] == 128, "按调用覆盖模型/max_tokens")
        check("response_format" not in third, "chat() 不应默认带 response_format")
    finally:
        h.restore()


def test_disable_thinking_sets_thinking_flag():
    h = Harness([ok("x")])
    try:
        h.chat(disable_thinking=True)
        check(
            h.api.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}},
            f"disable_thinking 应传 thinking.type=disabled：{h.api.calls[0].get('extra_body')}",
        )
    finally:
        h.restore()


def test_enable_thinking_false_is_forwarded():
    h = Harness([ok("x")])
    try:
        h.chat(enable_thinking=False)
        check(
            h.api.calls[0]["extra_body"] == {"enable_thinking": False},
            f"enable_thinking=False 应透传：{h.api.calls[0].get('extra_body')}",
        )
    finally:
        h.restore()


def test_thinking_flags_merge_in_one_extra_body():
    h = Harness([ok("x")])
    try:
        h.chat(enable_thinking=False, disable_thinking=True)
        check(
            h.api.calls[0]["extra_body"] == {"enable_thinking": False, "thinking": {"type": "disabled"}},
            f"两个参数应合并进同一个 extra_body：{h.api.calls[0].get('extra_body')}",
        )
    finally:
        h.restore()


# ============ 截断修复辅助函数 ============


def test_last_closing_index_ignores_strings():
    text = '{"a": "}", "b": 1}'
    check(
        llm_mod._last_closing_index(text) == len(text) - 1,
        "字符串内的 } 不应被当作结束位置",
    )


def test_balance_brackets_ignores_strings():
    check(
        llm_mod._balance_brackets('{"a": "[{"') == '{"a": "[{"}',
        "字符串内的括号不应被计数",
    )


def test_repair_returns_none_for_garbage():
    check(llm_mod._repair_truncated_json("完全不是 JSON") is None, "无法修复时应返回 None")
    check(llm_mod._repair_truncated_json("   ") is None, "空白输入应返回 None")


def test_repair_still_returns_valid_json():
    repaired = llm_mod._repair_truncated_json('{"a": 1, "b": [2,')
    check(repaired is not None, "可修复的截断应返回字符串")
    check(json.loads(repaired) == {"a": 1, "b": [2]}, f"修复结果应为合法 JSON：{repaired!r}")


# ============ 单例 ============


def test_global_client_is_singleton():
    from app.config import settings

    if not settings.llm_api_key:
        print("      （跳过：未配置 LLM_API_KEY）")
        return
    check(get_llm_client() is get_llm_client(), "get_llm_client() 应返回同一实例")


TESTS = [
    test_rate_limit_then_success,
    test_retries_exhausted_marks_retryable,
    test_timeout_is_retried,
    test_connection_error_is_retried,
    test_empty_choices_retried,
    test_blank_content_retried,
    test_empty_content_exhausts_with_invalid_response,
    test_auth_error_not_retried,
    test_arrearage_not_retried,
    test_unknown_error_classified_as_unknown,
    test_chat_json_parses_fenced_markdown,
    test_chat_json_extracts_from_surrounding_text,
    test_chat_json_handles_braces_inside_strings,
    test_chat_json_repairs_truncated_array,
    test_chat_json_repairs_unterminated_string,
    test_chat_json_repairs_trailing_comma,
    test_chat_json_rejects_non_json,
    test_request_kwargs_defaults,
    test_disable_thinking_sets_thinking_flag,
    test_enable_thinking_false_is_forwarded,
    test_thinking_flags_merge_in_one_extra_body,
    test_last_closing_index_ignores_strings,
    test_balance_brackets_ignores_strings,
    test_repair_returns_none_for_garbage,
    test_repair_still_returns_valid_json,
    test_global_client_is_singleton,
]


def main() -> int:
    passed, failed = 0, []
    print(f"[LLM 桩测试] 共 {len(TESTS)} 项（离线，不访问网络）\n")
    for fn in TESTS:
        name = fn.__name__
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"[ OK ] {name}")
    print(f"\n通过 {passed}/{len(TESTS)}")
    if failed:
        print("失败项：")
        for name, e in failed:
            print(f"  - {name}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
