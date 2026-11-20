/** 导入弹窗「出厂场景 / 自定义场景」两条通道的装配预览计算。
 *
 * 与后端 `rule_import_service.import_rules_with_skills` 的两条通道一一对应：
 *
 * - **出厂场景**（未勾选任何 Skill）：通用解析契约（系统预设）
 *   + 本次场景的场景知识包 + 本规则集自定义 Skill；
 * - **自定义场景**（勾选了 Skill）：系统预设（通用解析契约）+ 勾选的 Skill，
 *   **不加载任何场景知识包**——避免出厂领域知识与客户自定义场景混用。
 *
 * 两个下拉因此是互斥的：一旦勾选 Skill，业务场景下拉即被禁用。
 */

/** 未声明场景时的兜底场景键（与后端 default_rule_parse_skill.DEFAULT_SCENARIO 一致） */
export const FALLBACK_SCENARIO = 'trade'

export type ImportAssemblyMode = 'scenario' | 'custom'

export interface CompositionSkill {
  id: string
  name: string
  is_builtin: boolean
  enabled: boolean
  /** 场景键：null = 通用解析契约（系统预设）；非 null = 场景知识包 */
  scenario: string | null
}

export interface ImportSkillComposition<S extends CompositionSkill = CompositionSkill> {
  /** 当前通道：scenario = 出厂场景；custom = 自定义场景 */
  mode: ImportAssemblyMode
  /** 出厂场景通道实际使用的场景键；自定义场景通道为 null */
  effectiveScenario: string | null
  /** 场景键是否有对应的内置场景包 */
  scenarioRegistered: boolean
  /** 本次生效的装配链路（有序，后写覆盖先写） */
  chain: S[]
  /** 自定义场景下拉的可选项：自定义 Skill + 系统预设（不含场景包） */
  selectable: S[]
  /** 勾选项里会被叠加的（不在系统预设中） */
  extras: S[]
  /** 勾选项里已在系统预设中的（不重复合并） */
  redundant: S[]
}

/** 系统预设：内置且无场景键的包（通用解析契约），所有通道都生效 */
export function isPresetSkill(s: CompositionSkill): boolean {
  return s.is_builtin && !s.scenario
}

/** 出厂场景知识包：内置且有场景键（只能通过业务场景下拉选择，不放进 Skill 下拉） */
export function isScenarioPack(s: CompositionSkill): boolean {
  return s.is_builtin && !!s.scenario
}

export function computeImportSkillComposition<S extends CompositionSkill>(params: {
  skills: S[]
  /** 导入弹窗里显式选择的场景（空 = 未声明） */
  importScenario?: string
  /** 当前规则集的默认场景 */
  ruleSetScenario?: string | null
  /** 当前规则集是否启用内置默认 Skill */
  useDefaultSkill?: boolean
  /** 已勾选的 Skill ID */
  selectedSkillIds: string[]
  /** 已登记的场景键清单 */
  knownScenarios: string[]
}): ImportSkillComposition<S> {
  const {
    skills,
    importScenario,
    ruleSetScenario,
    useDefaultSkill = true,
    selectedSkillIds,
    knownScenarios,
  } = params

  const enabled = skills.filter((s) => s.enabled)
  const preset = enabled.filter(isPresetSkill)
  const customs = enabled.filter((s) => !s.is_builtin)
  // 自定义场景下拉只放自定义 Skill + 系统预设，不再重复出厂场景包
  const selectable = enabled.filter((s) => !isScenarioPack(s))

  const selected = selectedSkillIds
    .map((id) => skills.find((s) => s.id === id))
    .filter((s): s is S => Boolean(s))
  const presetIds = new Set(preset.map((s) => s.id))
  const extras = selected.filter((s) => !presetIds.has(s.id))
  const redundant = selected.filter((s) => presetIds.has(s.id))

  if (selectedSkillIds.length > 0) {
    // 自定义场景通道：出厂场景包与未勾选的自定义 Skill 都不参与
    return {
      mode: 'custom',
      effectiveScenario: null,
      scenarioRegistered: true,
      chain: [...preset, ...extras],
      selectable,
      extras,
      redundant,
    }
  }

  const effectiveScenario = importScenario || ruleSetScenario || FALLBACK_SCENARIO
  const scenarioPacks = useDefaultSkill
    ? enabled.filter((s) => isScenarioPack(s) && s.scenario === effectiveScenario)
    : []
  const chain = useDefaultSkill ? [...preset, ...scenarioPacks, ...customs] : [...customs]

  return {
    mode: 'scenario',
    effectiveScenario,
    scenarioRegistered: knownScenarios.includes(effectiveScenario),
    chain,
    selectable,
    extras: [],
    redundant: [],
  }
}
