/** 全局规则集 Context：管理当前选中的规则集,所有页面通过 useRuleSet() 获取。 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import type { ReactNode } from 'react'
import { message } from 'antd'
import { ruleSetsApi } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import type { RuleSet, RuleSetCreate } from '../types'

const LEGACY_STORAGE_KEY = 'current_rule_set_id'
const storageKeyFor = (tenantId: string | null | undefined) =>
  tenantId ? `${LEGACY_STORAGE_KEY}:${tenantId}` : null

interface RuleSetContextValue {
  /** 所有规则集列表 */
  ruleSets: RuleSet[]
  /** 当前选中的规则集(可能为 null,表示尚未加载或没有规则集) */
  current: RuleSet | null
  /** 当前规则集 ID(便于直接传给 API) */
  currentId: string | null
  /** 是否正在加载规则集列表 */
  loading: boolean
  /** 批次 5-11：加载失败时的错误消息（null 表示无错误），用于区分加载中/失败 */
  error: string | null
  /** 切换当前规则集 */
  switchTo: (id: string) => void
  /** 创建新规则集并自动切换到新集 */
  create: (data: RuleSetCreate) => Promise<RuleSet>
  /** 删除规则集（后端会级联清空其全部数据），删除后自动切换/刷新 */
  remove: (id: string) => Promise<void>
  /** 刷新规则集列表(外部修改后调用) */
  refresh: () => Promise<void>
}

const RuleSetContext = createContext<RuleSetContextValue | null>(null)

export function RuleSetProvider({ children }: { children: ReactNode }) {
  const { tenant } = useAuth()
  const tenantId = tenant?.id || null
  const storageKey = storageKeyFor(tenantId)
  const [ruleSets, setRuleSets] = useState<RuleSet[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!tenantId || !storageKey) {
      setRuleSets([])
      setCurrentId(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const list = await ruleSetsApi.list()
      setRuleSets(list)
      // 决定当前规则集:
      // 1. localStorage 中保存的 id 仍存在
      // 2. 否则取 is_default 的
      // 3. 否则取列表第一个
      const savedId = localStorage.getItem(storageKey)
      let next: RuleSet | null = null
      if (savedId) {
        next = list.find((r) => r.id === savedId) || null
        if (!next) localStorage.removeItem(storageKey)
      }
      if (!next) {
        next = list.find((r) => r.is_default) || null
      }
      if (!next && list.length > 0) {
        next = list[0]
      }
      const nextId = next?.id || null
      if (nextId && nextId !== savedId) {
        localStorage.setItem(storageKey, nextId)
      }
      localStorage.removeItem(LEGACY_STORAGE_KEY)
      setCurrentId(nextId)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      console.error('加载规则集列表失败:', e)
      message.error('加载规则集列表失败')
    } finally {
      setLoading(false)
    }
  }, [storageKey, tenantId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const switchTo = useCallback((id: string) => {
    if (!storageKey) return
    localStorage.setItem(storageKey, id)
    setCurrentId(id)
  }, [storageKey])

  const create = useCallback(
    async (data: RuleSetCreate) => {
      const created = await ruleSetsApi.create(data)
      await refresh()
      // 自动切换到新建的规则集
      if (storageKey) localStorage.setItem(storageKey, created.id)
      setCurrentId(created.id)
      message.success(`规则集「${created.name}」已创建并切换`)
      return created
    },
    [refresh, storageKey],
  )

  const remove = useCallback(
    async (id: string) => {
      await ruleSetsApi.delete(id)
      // 若删除的是当前规则集，先清除本地记忆，refresh 会自动切到默认/首个剩余规则集
      if (id === currentId && storageKey) {
        localStorage.removeItem(storageKey)
      }
      await refresh()
      message.success('规则集已删除，关联数据已全部清空')
    },
    [currentId, refresh, storageKey],
  )

  const current = useMemo(
    () => ruleSets.find((r) => r.id === currentId) || null,
    [ruleSets, currentId],
  )

  const value = useMemo<RuleSetContextValue>(
    () => ({
      ruleSets,
      current,
      currentId,
      loading,
      error,
      switchTo,
      create,
      remove,
      refresh,
    }),
    [ruleSets, current, currentId, loading, error, switchTo, create, remove, refresh],
  )

  return <RuleSetContext.Provider value={value}>{children}</RuleSetContext.Provider>
}

/** 获取当前规则集上下文。必须在 RuleSetProvider 内使用。 */
export function useRuleSet() {
  const ctx = useContext(RuleSetContext)
  if (!ctx) {
    throw new Error('useRuleSet 必须在 RuleSetProvider 内使用')
  }
  return ctx
}
