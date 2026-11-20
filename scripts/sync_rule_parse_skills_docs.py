#!/usr/bin/env python3
"""把内置场景知识包（backend/app/rule_parse_skills/*.yaml）的合并结果同步到文档镜像。

镜像文件 docs/通用贸易默认Skill.yaml 是「trade 场景实际生效的解析配置」的可读副本，
由测试 tests/test_default_rule_parse_skill.py 断言与实际内容完全一致。
修改知识包后请执行：

    backend/.venv/bin/python scripts/sync_rule_parse_skills_docs.py

以保持镜像与运行内容同步。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.default_rule_parse_skill import (  # noqa: E402
    DEFAULT_SCENARIO,
    load_builtin_skill_packs,
    load_default_rule_parse_skill_content,
)

_HEADER = """# 本文件是文档镜像，不是运行源文件。
# 运行源文件：backend/app/rule_parse_skills/*.yaml（通用契约包 + 场景知识包）
# 本文件 = trade 场景（通用契约包 + 10_贸易单证.yaml）合并后的可读副本。
# 修改知识包后执行：backend/.venv/bin/python scripts/sync_rule_parse_skills_docs.py
"""


def main() -> int:
    packs = load_builtin_skill_packs()
    content = load_default_rule_parse_skill_content(DEFAULT_SCENARIO)
    body = yaml.safe_dump(
        content,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    target = _REPO_ROOT / "docs" / "通用贸易默认Skill.yaml"
    target.write_text(_HEADER + "\n" + body, encoding="utf-8")

    print(f"已同步 {target.relative_to(_REPO_ROOT)}")
    for pack in packs:
        print(
            f"  - {pack.key}: scenario={pack.scenario} priority={pack.priority} "
            f"指令={len(pack.content['prompt_instructions'])} 条"
        )
    print(f"  合并后指令={len(content['prompt_instructions'])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
