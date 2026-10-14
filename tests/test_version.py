# -*- coding: utf-8 -*-
"""版本号管理单元测试。

覆盖：
- 版本号真相（仓库根 VERSION 文件）存在且可解析；
- 取值优先级：环境变量（镜像 build args）> 工作区 git（本机开发）> VERSION 文件 > 兜底；
- label 格式（version+commit[.dirty]）与 dirty 判定；
- /api/health、/api/version 与 version 模块口径一致。

运行：backend/.venv/Scripts/python.exe tests/test_version.py
前置：无需数据库 / Neo4j / 网络。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import version as V  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


def reset_env() -> None:
    """清掉所有注入变量，回到"本机开发"初始状态。"""
    for key in V.ENV_VARS:
        os.environ.pop(key, None)


def main() -> None:
    reset_env()

    # ---------- 1. 版本号真相：VERSION 文件 ----------
    version_file = V.version_file_path()
    check("仓库根 VERSION 文件存在", version_file.is_file(), str(version_file))
    file_version = V.read_version_file()
    check(
        "VERSION 内容为合法 semver",
        bool(file_version) and bool(re.match(r"^\d+\.\d+\.\d+", file_version or "")),
        repr(file_version),
    )
    check("未设环境变量时 app_version() 取 VERSION 文件", V.app_version() == file_version, V.app_version())

    # ---------- 2. 环境变量优先（镜像 build args 路径） ----------
    os.environ[V.ENV_VERSION] = "9.9.9"
    check("DOCGRAPH_APP_VERSION 优先于 VERSION 文件", V.app_version() == "9.9.9", V.app_version())
    os.environ.update(
        {
            V.ENV_COMMIT: "abc1234",
            V.ENV_DESCRIBE: "v1.2.2-8-gabc1234",
            V.ENV_TAG: "v1.2.2",
            V.ENV_BRANCH: "master",
            V.ENV_DIRTY: "false",
            V.ENV_BUILD_TIME: "2026-10-14T00:00:00Z",
            V.ENV_BUILD_ENV: "prod",
        }
    )
    os.environ[V.ENV_VERSION] = "1.2.3"
    built = V.collect_build_info()
    check("镜像构建：source=build-args", built["source"] == "build-args", built["source"])
    check("镜像构建：build_env=prod", built["build_env"] == "prod", built["build_env"])
    check("镜像构建：字段全部取自注入值", built["version"] == "1.2.3" and built["commit"] == "abc1234" and built["tag"] == "v1.2.2", json.dumps(built, ensure_ascii=False))
    check("镜像构建：built_at 取自注入时间", built["built_at"] == "2026-10-14T00:00:00Z", str(built["built_at"]))
    check("镜像构建：label=version+commit", built["label"] == "1.2.3+abc1234", built["label"])
    check("镜像构建：不做 git 探测（容器内无 .git）", built["commit_full"] is None and built["dirty"] is False, repr(built["dirty"]))

    os.environ[V.ENV_DIRTY] = "true"
    check("有未提交改动时 label 带 .dirty", V.collect_build_info()["label"] == "1.2.3+abc1234.dirty", V.collect_build_info()["label"])

    # ---------- 3. 本机开发：运行时读工作区 git ----------
    reset_env()
    dev = V.collect_build_info()
    if V._git("rev-parse", "--git-dir") is not None:
        check("本机开发：source=runtime-git", dev["source"] == "runtime-git", dev["source"])
        check("本机开发：build_env=dev", dev["build_env"] == "dev", dev["build_env"])
        check("本机开发：commit 为 7 位短号", bool(dev["commit"]) and len(dev["commit"]) == 7, str(dev["commit"]))
        check("本机开发：dirty 为布尔值", isinstance(dev["dirty"], bool), repr(dev["dirty"]))
        check("本机开发：label 以 VERSION 文件值开头", bool(dev["label"].startswith(f"{file_version}+")), dev["label"])
    else:
        print("[SKIP] 当前目录不是 git 工作区，跳过 runtime-git 断言")

    # ---------- 4. 只能读到 VERSION 文件 ----------
    reset_env()
    file_only = V.collect_build_info(use_git=False)
    check("无 git 时：source=version-file", file_only["source"] == "version-file", file_only["source"])
    check("无 git 时：version 仍来自 VERSION 文件", file_only["version"] == file_version, file_only["version"])
    check("无 git 时：提交/分支/dirty 均为空", file_only["commit"] is None and file_only["branch"] is None and file_only["dirty"] is None)
    check("无 git 时：label 只有版本号", file_only["label"] == file_version, file_only["label"])
    check("无 git 时：build_env=unknown（无法判断环境）", file_only["build_env"] == "unknown", file_only["build_env"])

    # ---------- 5. 实时读取：同一进程内重复调用会重算（不缓存） ----------
    reset_env()
    before = V.collect_build_info()
    os.environ[V.ENV_VERSION] = "0.0.1-test"
    os.environ[V.ENV_COMMIT] = "feedface"
    after = V.collect_build_info()
    check(
        "环境变化立刻反映（版本信息不缓存）",
        after["version"] == "0.0.1-test" and after["commit"] == "feedface" and after["label"] == "0.0.1-test+feedface",
        f"before={before['label']} after={after['label']}",
    )

    # ---------- 6. 什么都没有：如实兜底，不伪造版本 ----------
    reset_env()
    original_path = V.version_file_path
    V.version_file_path = lambda: Path(tempfile.gettempdir()) / "docgraph-no-such-VERSION"  # type: ignore[assignment]
    try:
        empty = V.collect_build_info(use_git=False)
        check("无 VERSION、无 git：version 兜底为 0.0.0", empty["version"] == V.FALLBACK_VERSION, empty["version"])
        check("无 VERSION、无 git：source=unknown", empty["source"] == "unknown", empty["source"])
    finally:
        V.version_file_path = original_path  # type: ignore[assignment]

    # ---------- 7. git 不在 PATH 时的兜底（Windows 上装了 git 但没加进 PATH 很常见） ----------
    reset_env()
    original_git = V._git
    original_dirty = V.git_dirty
    V._git = lambda *args, **kwargs: None  # type: ignore[assignment]
    V.git_dirty = lambda: None  # type: ignore[assignment]
    try:
        no_binary = V.collect_build_info()
        if V._git_dir() is not None:
            check(
                "git 命令不可用时：直接读 .git/HEAD 仍拿到提交号",
                bool(no_binary["commit"]) and len(no_binary["commit"]) == 7,
                str(no_binary["commit"]),
            )
            check("git 命令不可用时：仍拿到分支名", bool(no_binary["branch"]), str(no_binary["branch"]))
            check("git 命令不可用时：source 仍为 runtime-git", no_binary["source"] == "runtime-git", no_binary["source"])
            check(
                "git 命令不可用时：describe/dirty 缺失但不报错",
                no_binary["describe"] is None and no_binary["dirty"] is None,
                f"describe={no_binary['describe']} dirty={no_binary['dirty']}",
            )
            check(
                "git 命令不可用时：label 用版本号+提交号",
                no_binary["label"] == f"{file_version}+{no_binary['commit']}",
                no_binary["label"],
            )
        else:
            print("[SKIP] 当前目录不是 git 工作区，跳过 .git/HEAD 兜底断言")
    finally:
        V._git = original_git  # type: ignore[assignment]
        V.git_dirty = original_dirty  # type: ignore[assignment]

    # ---------- 8. 接口口径与模块一致 ----------
    from app.main import app as fastapi_app, health, version_info  # noqa: E402

    paths = {getattr(route, "path", None) for route in fastapi_app.routes}
    check("/api/version 已注册", "/api/version" in paths)
    check("/api/health 仍返回 status=ok", health()["status"] == "ok")
    check("/api/health 版本与 version 模块一致", health()["version"] == V.app_version(), health()["version"])
    check(
        "/api/health 带提交定位（便于一条命令对比环境）",
        health()["label"] == V.collect_build_info()["label"] and health()["commit"] == V.collect_build_info()["commit"],
        health()["label"],
    )
    check(
        "/api/version 结构完整",
        set(V.collect_build_info()) <= set(version_info()),
        ",".join(sorted(set(V.collect_build_info()) - set(version_info()))) or "ok",
    )
    check(
        "/api/version 可 JSON 序列化",
        isinstance(json.dumps(version_info(), ensure_ascii=False), str),
    )
    check("OpenAPI version 与 VERSION 文件一致", fastapi_app.version == file_version, str(fastapi_app.version))

    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
