/**
 * 规则缺陷语义（与后端 backend/app/services/rule_defects.py 保持同一口径）。
 *
 * 方案 A：用户「确认规则」即视为认可该规则及其全部缺陷，未处理缺陷被标记为
 * `resolution: 'resolved'`，规则随即进入「正常规则」页签。
 *
 * 规则按"最严重的未处理缺陷"归入唯一页签（互斥且穷尽）：
 * 冲突 > 错误 > 警告 > 提示 > 正常规则
 * 因此 全部 = 正常 + 冲突 + 错误 + 警告 + 提示，各页签计数都是规则条数。
 */

import type { DefectItem, Rule } from '../types'

export type DefectBucket = 'conflict' | 'error' | 'warning' | 'info' | 'healthy'
export type RuleFilterKey = 'all' | DefectBucket

/** 冲突类缺陷类型（规则间矛盾 / 边界重叠 / 冗余） */
export const CONFLICT_DEFECT_TYPES = ['logical_contradiction', 'boundary_overlap', 'redundant'] as const

/** 实质缺陷：影响规则参与图谱构建与文档审查 */
export const BLOCKING_SEVERITIES = ['error', 'warning'] as const

export const DEFECT_BUCKET_LABEL: Record<DefectBucket, string> = {
  conflict: '冲突',
  error: '错误',
  warning: '警告',
  info: '提示',
  healthy: '正常规则',
}

export const isResolvedDefect = (d: DefectItem): boolean => d.resolution === 'resolved'

export const isOpenDefect = (d: DefectItem): boolean => !isResolvedDefect(d)

export const isConflictDefect = (d: DefectItem): boolean =>
  (CONFLICT_DEFECT_TYPES as readonly string[]).includes(d.type)

/** 未处理缺陷 */
export const openDefects = (defects?: DefectItem[] | null): DefectItem[] =>
  (defects || []).filter(isOpenDefect)

/** 已处理缺陷 */
export const resolvedDefects = (defects?: DefectItem[] | null): DefectItem[] =>
  (defects || []).filter(isResolvedDefect)

/** 把规则归入唯一页签 */
export const classifyDefects = (defects?: DefectItem[] | null): DefectBucket => {
  const pending = openDefects(defects)
  if (pending.some(isConflictDefect)) return 'conflict'
  if (pending.some((d) => d.severity === 'error')) return 'error'
  if (pending.some((d) => d.severity === 'warning')) return 'warning'
  if (pending.length > 0) return 'info'
  return 'healthy'
}

/** 规则是否仍有未处理缺陷（需要用户确认/处理） */
export const hasOpenDefect = (rule: Rule): boolean => openDefects(rule.defects).length > 0

/** 规则是否仍需用户操作：未确认，或仍有未处理缺陷 */
export const needsUserAction = (rule: Rule): boolean =>
  rule.status !== 'confirmed' || hasOpenDefect(rule)

/** 未处理缺陷按严重度计数 */
export const openSeverityCounts = (defects?: DefectItem[] | null): Record<'error' | 'warning' | 'info', number> => {
  const counts = { error: 0, warning: 0, info: 0 }
  openDefects(defects).forEach((d) => {
    if (d.severity === 'error' || d.severity === 'warning' || d.severity === 'info') {
      counts[d.severity] += 1
    }
  })
  return counts
}
