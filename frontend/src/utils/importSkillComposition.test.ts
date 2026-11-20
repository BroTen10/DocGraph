import { describe, it, expect } from 'vitest'
import {
  computeImportSkillComposition,
  isPresetSkill,
  isScenarioPack,
  FALLBACK_SCENARIO,
  type CompositionSkill,
} from './importSkillComposition'

/** 与内置包结构一致的样例：通用契约（系统预设）+ 两个场景包 + 一个自定义 Skill */
const SKILLS: CompositionSkill[] = [
  { id: 'generic', name: '通用规则解析契约', is_builtin: true, enabled: true, scenario: null },
  { id: 'trade', name: '场景包：贸易合同与过程单证', is_builtin: true, enabled: true, scenario: 'trade' },
  { id: 'expense', name: '场景包：费用报销', is_builtin: true, enabled: true, scenario: 'expense' },
  { id: 'custom', name: '客户自定义场景', is_builtin: false, enabled: true, scenario: null },
]

const KNOWN = ['trade', 'expense']

const base = {
  skills: SKILLS,
  selectedSkillIds: [] as string[],
  knownScenarios: KNOWN,
}

describe('isPresetSkill / isScenarioPack', () => {
  it('通用契约是系统预设，带场景键的内置包是场景包', () => {
    expect(isPresetSkill(SKILLS[0])).toBe(true)
    expect(isScenarioPack(SKILLS[0])).toBe(false)
    expect(isScenarioPack(SKILLS[1])).toBe(true)
    expect(isPresetSkill(SKILLS[3])).toBe(false)
  })
})

describe('出厂场景通道（未勾选 Skill）', () => {
  it('未声明场景时兜底 trade，链路为 系统预设 → 场景包 → 自定义 Skill', () => {
    const c = computeImportSkillComposition(base)

    expect(c.mode).toBe('scenario')
    expect(c.effectiveScenario).toBe(FALLBACK_SCENARIO)
    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'trade', 'custom'])
    expect(c.scenarioRegistered).toBe(true)
  })

  it('导入参数优先于规则集默认场景', () => {
    const c = computeImportSkillComposition({
      ...base,
      importScenario: 'expense',
      ruleSetScenario: 'trade',
    })

    expect(c.effectiveScenario).toBe('expense')
    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'expense', 'custom'])
  })

  it('未登记的场景键被标记，链路里没有场景包', () => {
    const c = computeImportSkillComposition({ ...base, importScenario: 'supplier' })

    expect(c.scenarioRegistered).toBe(false)
    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'custom'])
  })

  it('关闭内置默认 Skill 时链路只剩自定义 Skill', () => {
    const c = computeImportSkillComposition({ ...base, useDefaultSkill: false })

    expect(c.chain.map((s) => s.id)).toEqual(['custom'])
  })
})

describe('自定义场景通道（勾选了 Skill）', () => {
  it('切换通道：只保留系统预设 + 勾选的 Skill，不加载场景包', () => {
    const c = computeImportSkillComposition({ ...base, selectedSkillIds: ['custom'] })

    expect(c.mode).toBe('custom')
    expect(c.effectiveScenario).toBeNull()
    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'custom'])
  })

  it('规则集默认场景不再影响装配（场景通道被旁路）', () => {
    const c = computeImportSkillComposition({
      ...base,
      ruleSetScenario: 'trade',
      importScenario: 'expense',
      selectedSkillIds: ['custom'],
    })

    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'custom'])
  })

  it('勾选系统预设本身不重复合并', () => {
    const c = computeImportSkillComposition({
      ...base,
      selectedSkillIds: ['generic', 'custom'],
    })

    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'custom'])
    expect(c.redundant.map((s) => s.id)).toEqual(['generic'])
    expect(c.extras.map((s) => s.id)).toEqual(['custom'])
  })

  it('未勾选的自定义 Skill 不生效', () => {
    const skills: CompositionSkill[] = [
      ...SKILLS,
      { id: 'custom2', name: '另一个自定义场景', is_builtin: false, enabled: true, scenario: null },
    ]
    const c = computeImportSkillComposition({
      ...base,
      skills,
      selectedSkillIds: ['custom'],
    })

    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'custom'])
  })
})

describe('下拉可选项（两个下拉不重复）', () => {
  it('自定义场景下拉 = 自定义 Skill + 系统预设，不含出厂场景包', () => {
    const c = computeImportSkillComposition(base)

    expect(c.selectable.map((s) => s.id)).toEqual(['generic', 'custom'])
    expect(c.selectable.some(isScenarioPack)).toBe(false)
  })

  it('没有自定义 Skill 时，下拉保留系统预设（不会为空）', () => {
    const c = computeImportSkillComposition({
      ...base,
      skills: SKILLS.filter((s) => s.is_builtin),
    })

    expect(c.selectable.map((s) => s.id)).toEqual(['generic'])
  })

  it('停用的 Skill 不出现在可选项与生效链路里', () => {
    const skills: CompositionSkill[] = SKILLS.map((s) =>
      s.id === 'custom' ? { ...s, enabled: false } : s,
    )
    const c = computeImportSkillComposition({ ...base, skills })

    expect(c.selectable.map((s) => s.id)).toEqual(['generic'])
    expect(c.chain.map((s) => s.id)).toEqual(['generic', 'trade'])
  })

  it('忽略不存在的勾选 ID（下拉项被删除后的残留选择）', () => {
    const c = computeImportSkillComposition({
      ...base,
      selectedSkillIds: ['not-exist'],
    })

    // 勾选 ID 非空即进入自定义通道，但没有任何 Skill 被解析出来
    expect(c.mode).toBe('custom')
    expect(c.chain.map((s) => s.id)).toEqual(['generic'])
    expect(c.extras).toEqual([])
  })
})
