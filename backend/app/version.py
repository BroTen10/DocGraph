# -*- coding: utf-8 -*-
"""版本信息单一入口：本机开发环境与服务器正式环境用同一口径对比。

版本号真相在仓库根 ``VERSION`` 文件（一行 semver，发版时打同名 tag ``v0.3.0``）；
精确到提交的定位靠 git（``git describe --tags --dirty``）。两者合成一个 ``label``，
例如 ``0.3.0+4b00ac1.dirty``。

``source`` 字段说明这份信息从哪来，各环境对应一种：

- ``build-args``：Docker 镜像。镜像内没有 ``.git``，版本由 ``deploy.sh`` 算好，
  经 compose 的 build args → 镜像 ENV 注入；
- ``runtime-git``：本机开发。运行时直接读工作区的 ``.git``，改代码立刻反映；
- ``version-file``：只能读到 ``VERSION`` 文件（拿不到任何 git 信息）；
- ``unknown``：都没有（例如未经 ``deploy.sh`` 直接 ``docker compose build``）。

本机开发这一路不强依赖 git 可执行文件：git 不在 PATH 时仍会直接读 ``.git/HEAD``
解析出提交号与分支（见 ``read_git_head``），只有 ``describe`` / ``tag`` / ``dirty`` /
提交时间这些需要 git 计算的信息才会缺失。

对外只有两个入口：``app_version()``（人类版本号）与 ``collect_build_info()``
（``GET /api/version`` 的完整结构，也是前端页脚与运维对比的唯一口径）。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

APP_NAME = "DocGraph"
FALLBACK_VERSION = "0.0.0"

ENV_VERSION = "DOCGRAPH_APP_VERSION"
ENV_COMMIT = "DOCGRAPH_GIT_COMMIT"
ENV_DESCRIBE = "DOCGRAPH_GIT_DESCRIBE"
ENV_TAG = "DOCGRAPH_GIT_TAG"
ENV_BRANCH = "DOCGRAPH_GIT_BRANCH"
ENV_DIRTY = "DOCGRAPH_GIT_DIRTY"
ENV_BUILD_TIME = "DOCGRAPH_BUILD_TIME"
ENV_BUILD_ENV = "DOCGRAPH_BUILD_ENV"
ENV_VARS = (
    ENV_VERSION,
    ENV_COMMIT,
    ENV_DESCRIBE,
    ENV_TAG,
    ENV_BRANCH,
    ENV_DIRTY,
    ENV_BUILD_TIME,
    ENV_BUILD_ENV,
)

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")

# backend/app/version.py -> backend/app -> backend -> 仓库根
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GIT_TIMEOUT = 5


def _clean(value: str | None) -> str | None:
    """去空白；空串视为未设置。"""
    if value is None:
        return None
    value = value.strip()
    return value or None


def version_file_path() -> Path:
    """仓库根 ``VERSION`` 文件路径（容器内该文件不存在，由调用方判存在）。"""
    return _REPO_ROOT / "VERSION"


def read_version_file() -> str | None:
    """读 ``VERSION`` 文件；缺失或内容不是合法 semver 时返回 None。"""
    try:
        raw = version_file_path().read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line if _SEMVER_RE.match(line) else None
    return None


def app_version() -> str:
    """当前版本号：环境变量 > ``VERSION`` 文件 > 兜底值。"""
    return _clean(os.environ.get(ENV_VERSION)) or read_version_file() or FALLBACK_VERSION


def _git_raw(*args: str) -> tuple[bool, str]:
    """在仓库根执行 git 子命令，返回 (是否成功, stdout)。

    git 未安装、不是 git 仓库、命令失败、超时都算失败，绝不抛异常。
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if done.returncode != 0:
        return False, ""
    return True, done.stdout


def _git(*args: str) -> str | None:
    ok, out = _git_raw(*args)
    return _clean(out) if ok else None


def git_dirty() -> bool | None:
    """工作区是否有未提交改动；不是 git 仓库时返回 None。"""
    ok, out = _git_raw("status", "--porcelain")
    return out.strip() != "" if ok else None


def _git_dir() -> Path | None:
    """定位 ``.git`` 目录（兼容 worktree/submodule 的 gitdir 指针文件）。"""
    dot = _REPO_ROOT / ".git"
    try:
        if dot.is_dir():
            return dot
        if dot.is_file():
            text = dot.read_text(encoding="utf-8", errors="replace").strip()
            if text.lower().startswith("gitdir:"):
                value = text.split(":", 1)[1].strip()
                path = Path(value)
                return (
                    path.resolve()
                    if path.is_absolute()
                    else (_REPO_ROOT / path).resolve()
                )
    except OSError:
        return None
    return None


def _git_ref_dir(git_dir: Path) -> Path:
    """返回保存 refs/packed-refs 的目录；linked worktree 使用 commondir。"""
    try:
        value = (git_dir / "commondir").read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    except OSError:
        return git_dir
    if not value:
        return git_dir
    common = Path(value)
    return common.resolve() if common.is_absolute() else (git_dir / common).resolve()


def read_git_head() -> tuple[str | None, str | None]:
    """直接读 ``.git`` 得到 (完整提交号, 分支名)，不需要 git 可执行文件。

    本机（尤其 Windows）常常装了 git 却没加进 PATH，此时 ``git`` 命令用不了；
    直接读 ``.git/HEAD`` 与对应 ref / ``packed-refs`` 仍能给出提交定位。
    """
    git_dir = _git_dir()
    if git_dir is None:
        return None, None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None, None
    if not head:
        return None, None

    if not head.startswith("ref:"):
        # detached HEAD：HEAD 里直接就是提交号
        return (_clean(head) if re.fullmatch(r"[0-9a-fA-F]{40}", head) else None), None

    ref = head.split(":", 1)[1].strip()
    branch = ref.rsplit("/", 1)[-1] or None
    ref_dir = _git_ref_dir(git_dir)
    sha: str | None = None
    try:
        sha = _clean((ref_dir / ref).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        sha = None
    if sha is None:
        # 引用可能被打包进 packed-refs（git gc 之后很常见）
        try:
            for line in (ref_dir / "packed-refs").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.startswith(("#", "^")):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    sha = parts[0]
                    break
        except OSError:
            pass
    return sha, branch


def collect_build_info(use_git: bool = True) -> dict:
    """汇总版本信息。

    每次调用都实时读取，不做进程级缓存：``git commit`` 之后提交号立刻更新，
    工程目录里的未提交改动也立刻反映到 ``dirty``——否则"进程启动那一刻的版本"
    会被误当成当前版本，正好在"提交完就和服务器对版本"时给出错误结论。

    ``use_git=False`` 时完全跳过 git，便于测试与无 git 环境。
    """
    env_present = any(_clean(os.environ.get(name)) for name in ENV_VARS)
    file_version = read_version_file()

    commit = _clean(os.environ.get(ENV_COMMIT))
    commit_full: str | None = None
    commit_date: str | None = None
    describe = _clean(os.environ.get(ENV_DESCRIBE))
    tag = _clean(os.environ.get(ENV_TAG))
    branch = _clean(os.environ.get(ENV_BRANCH))
    dirty: bool | None = None

    dirty_raw = _clean(os.environ.get(ENV_DIRTY))
    if dirty_raw is not None:
        dirty = dirty_raw.lower() in ("1", "true", "yes", "y")

    # 环境变量优先（镜像里没有 .git），其次读工作区 git（本机开发）
    if use_git and commit is None:
        commit_full = _git("rev-parse", "HEAD")
        commit = commit_full[:7] if commit_full else None
        commit_date = _git("log", "-1", "--format=%cI")
        describe = describe or _git("describe", "--tags", "--dirty", "--always")
        tag = tag or _git("describe", "--tags", "--abbrev=0")
        branch = branch or _git("rev-parse", "--abbrev-ref", "HEAD")
        if dirty is None:
            dirty = git_dirty()

    if use_git and commit is None:
        # git 不在 PATH（或不是仓库）时退一步：直接读 .git，至少拿到提交号与分支
        head_sha, head_branch = read_git_head()
        if head_sha:
            commit_full = head_sha
            commit = head_sha[:7]
            branch = branch or head_branch

    version = _clean(os.environ.get(ENV_VERSION)) or file_version or FALLBACK_VERSION

    if env_present:
        source = "build-args"
    elif commit is not None:
        source = "runtime-git"
    elif file_version is not None:
        source = "version-file"
    else:
        source = "unknown"

    build_env = _clean(os.environ.get(ENV_BUILD_ENV))
    if build_env is None:
        # deploy.sh 注入的一律是正式构建；能读到工作区 git 就是开发机；都不是则未知
        if env_present:
            build_env = "prod"
        elif commit is not None:
            build_env = "dev"
        else:
            build_env = "unknown"

    label = version if commit is None else f"{version}+{commit}" + (".dirty" if dirty else "")

    return {
        "kind": "backend",
        "name": APP_NAME,
        "version": version,
        "label": label,
        "describe": describe,
        "tag": tag,
        "commit": commit,
        "commit_full": commit_full,
        "commit_date": commit_date,
        "branch": branch,
        "dirty": dirty,
        "build_env": build_env,
        "built_at": _clean(os.environ.get(ENV_BUILD_TIME)),
        "source": source,
    }


def version_label() -> str:
    """一行版本标识，供日志与命令行使用，如 ``0.3.0+4b00ac1.dirty``。"""
    return collect_build_info()["label"]
