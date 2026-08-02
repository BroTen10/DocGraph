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
import type { RuleSet, RuleSetCreate } from '../types'

const STORAGE_KEY = 'current_rule_set_id'

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
  /** 刷新规则集列表(外部修改后调用) */
  refresh: () => Promise<void>
}

const RuleSetContext = createContext<RuleSetContextValue | null>(null)

export function RuleSetProvider({ children }: { children: ReactNode }) {
  const [ruleSets, setRuleSets] = useState<RuleSet[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await ruleSetsApi.list()
      setRuleSets(list)
      // 决定当前规则集:
      // 1. localStorage 中保存的 id 仍存在
      // 2. 否则取 is_default 的
      // 3. 否则取列表第一个
      const savedId = localStorage.getItem(STORAGE_KEY)
      let next: RuleSet | null = null
      if (savedId) {
        next = list.find((r) => r.id === savedId) || null
      }
      if (!next) {
        next = list.find((r) => r.is_default) || null
      }
      if (!next && list.length > 0) {
        next = list[0]
      }
      const nextId = next?.id || null
      if (nextId && nextId !== savedId) {
        localStorage.setItem(STORAGE_KEY, nextId)
      }
      setCurrentId(nextId)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      console.error('加载规则集列表失败:', e)
      message.error('加载规则集列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const switchTo = useCallback((id: string) => {
    localStorage.setItem(STORAGE_KEY, id)
    setCurrentId(id)
  }, [])

  const create = useCallback(
    async (data: RuleSetCreate) => {
      const created = await ruleSetsApi.create(data)
      await refresh()
      // 自动切换到新建的规则集
      localStorage.setItem(STORAGE_KEY, created.id)
      setCurrentId(created.id)
      message.success(`规则集「${created.name}」已创建并切换`)
      return created
    },
    [refresh],
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
      refresh,
    }),
    [ruleSets, current, currentId, loading, error, switchTo, create, refresh],
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
