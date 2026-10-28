"""内置通用规则解析 Skill 的加载与元数据。

默认 Skill 内容以包内 YAML 文件维护，避免在数据库初始化代码中重复维护
大段提示词。加载时复用对外 Schema，保证仓库中的默认配置在启动前即可校验。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schemas.rule_parse_skill import RuleParseSkillContent

DEFAULT_RULE_PARSE_SKILL_NAME = "通用贸易规则解析配置"
DEFAULT_RULE_PARSE_SKILL_DESCRIPTION = (
    "面向贸易合同与过程单证的系统感知默认解析配置：保留场景、条件、组合齐套、"
    "来源身份和执行边界，适用于大多数审查规则文档。"
)

_DEFAULT_CONTENT_PATH = Path(__file__).with_name("default_rule_parse_skill.yaml")


def load_default_rule_parse_skill_content() -> dict:
    """读取并校验内置 Skill YAML，返回可写入 JSONB 的规范字典。"""
    raw = _DEFAULT_CONTENT_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("内置规则解析 Skill YAML 顶层必须是键值映射")
    content = RuleParseSkillContent(**data)
    return content.model_dump(mode="json")
