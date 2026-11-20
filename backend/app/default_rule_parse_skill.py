"""内置「场景知识包」的加载与元数据。

设计：把原本单一的默认 Skill 拆成两层——
- 通用解析契约包（pack.scenario = null）：场景无关的解析规则，任何场景都生效；
- 场景知识包（pack.scenario = <key>）：某一业务领域特有口径（贸易单证 / 费用报销 / 供应商审查 …）。

包以 ``rule_parse_skills/*.yaml`` 文件维护：新增业务场景 = 新增一个 YAML 文件，
不需要改代码；启动时幂等同步到内置 Skill 记录（每个场景一条）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .schemas.rule_parse_skill import RuleParseSkillContent

#: 通用契约包的场景键（None = 所有场景都生效）
GENERIC_SCENARIO: str | None = None
#: 未显式声明场景时使用的兼容默认场景（存量规则集均为贸易单证场景）
DEFAULT_SCENARIO = "trade"
#: 场景键与中文名的展示用映射由各包 pack.name 提供，这里只保留兜底
SCENARIO_LABELS: dict[str, str] = {
    "trade": "贸易合同与过程单证",
    "expense": "费用报销",
    "supplier": "供应商审查",
}

_PACKS_DIR = Path(__file__).with_name("rule_parse_skills")


@dataclass(frozen=True)
class BuiltinSkillPack:
    """一个内置场景知识包。"""

    key: str
    name: str
    description: str
    scenario: str | None
    priority: int
    content: dict

    @property
    def is_generic(self) -> bool:
        return self.scenario is None


def _pack_paths() -> list[Path]:
    if not _PACKS_DIR.is_dir():
        return []
    return sorted(
        path
        for path in _PACKS_DIR.glob("*.yaml")
        if not path.name.startswith((".", "_"))
    )


def load_builtin_skill_packs() -> list[BuiltinSkillPack]:
    """读取并校验全部内置场景知识包（按文件名排序 = 合并顺序）。"""
    packs: list[BuiltinSkillPack] = []
    seen_scenarios: set[str | None] = set()
    for path in _pack_paths():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"内置场景包 {path.name} 顶层必须是键值映射")
        meta = raw.get("pack") or {}
        if not isinstance(meta, dict):
            raise ValueError(f"内置场景包 {path.name} 的 pack 段必须是键值映射")
        content_raw = raw.get("content")
        if content_raw is None:
            # 兼容扁平写法：整个文件即 Skill content
            content_raw = {k: v for k, v in raw.items() if k != "pack"}
        if not isinstance(content_raw, dict):
            raise ValueError(f"内置场景包 {path.name} 的 content 段必须是键值映射")

        content = RuleParseSkillContent(**content_raw).model_dump(mode="json")
        scenario_raw = meta.get("scenario")
        scenario = str(scenario_raw).strip() if scenario_raw not in (None, "") else None
        if scenario in seen_scenarios:
            raise ValueError(f"内置场景包 {path.name} 的 scenario={scenario} 与其他包重复")
        seen_scenarios.add(scenario)
        packs.append(
            BuiltinSkillPack(
                key=str(meta.get("key") or path.stem),
                name=str(meta.get("name") or path.stem),
                description=str(meta.get("description") or ""),
                scenario=scenario,
                priority=int(meta.get("priority") or 100),
                content=content,
            )
        )
    return packs


def packs_for_scenario(scenario: str | None) -> list[BuiltinSkillPack]:
    """返回某场景下应生效的包：通用包始终生效，场景包按 key 匹配。"""
    key = (str(scenario).strip() if scenario else "") or DEFAULT_SCENARIO
    selected = []
    for pack in load_builtin_skill_packs():
        if pack.is_generic or pack.scenario == key:
            selected.append(pack)
    return selected


def known_scenarios() -> set[str]:
    """已登记的内置场景键（用于提示未登记场景）。"""
    return {p.scenario for p in load_builtin_skill_packs() if p.scenario}


def scenario_label(scenario: str | None) -> str:
    """场景键对应的中文名（未知返回空串）；去掉「场景包：」前缀便于展示。"""
    key = str(scenario).strip() if scenario else ""
    if not key:
        return ""
    for pack in load_builtin_skill_packs():
        if pack.scenario == key:
            return pack.name.split("：", 1)[-1]
    return SCENARIO_LABELS.get(key, "")


def merge_skill_contents(contents: list[dict]) -> dict:
    """按引擎的合并语义把多个 Skill content 合并成一份（用于文档镜像与自检）。"""
    from .services.rule_parse_engine import (
        RuleParseDirective,
        _merge_content,
        directive_to_content,
    )

    directive = RuleParseDirective()
    for content in contents:
        _merge_content(directive, content or {})
    return directive_to_content(directive)


def load_default_rule_parse_skill_content(scenario: str | None = DEFAULT_SCENARIO) -> dict:
    """读取某场景下实际生效的内置解析配置（通用契约 + 场景包）合并结果。

    保持旧函数名：文档镜像 docs/通用贸易默认Skill.yaml 即本函数的 trade 场景输出。
    """
    return merge_skill_contents([pack.content for pack in packs_for_scenario(scenario)])


DEFAULT_RULE_PARSE_SKILL_DESCRIPTION = (
    "面向贸易合同与过程单证的系统感知默认解析配置：保留场景、条件、组合齐套、"
    "来源身份和执行边界，适用于大多数审查规则文档。"
)
DEFAULT_RULE_PARSE_SKILL_NAME = "通用贸易规则解析配置"


def legacy_skill_name() -> str:
    """存量单包模式下内置记录的名称（升级时用于识别并改名）。"""
    return DEFAULT_RULE_PARSE_SKILL_NAME
