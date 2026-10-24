import { useState, useEffect, useMemo, useRef } from 'react'
import type { UploadProps, UploadFile } from 'antd'
import {
  Card, Row, Col, Table, Tag, Button, Modal, Form, Input, InputNumber, Switch,
  Select, Space, message, Typography, Tooltip, Popconfirm, Popover, Tabs, List, Spin, Alert,
  Upload, Drawer, Progress,
} from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, HistoryOutlined, ImportOutlined, InboxOutlined, FileTextOutlined, SettingOutlined, CheckOutlined, WarningOutlined, ArrowRightOutlined } from '@ant-design/icons'
import { rulesApi, constantsApi, getErrorMessage, isFormValidationError, ruleSetsApi, skillsApi } from '../api/client'
import type { Rule, RuleSet, RuleSnapshot, DocTypeMeta, ConstantsResponse, RuleImportResponse, ImportTask, DefectItem } from '../types'
import {
  classifyDefects, hasOpenDefect, isConflictDefect, isResolvedDefect, needsUserAction, openDefects,
  openSeverityCounts, DEFECT_BUCKET_LABEL, type RuleFilterKey,
} from '../utils/defects'
import PageHeader from '../components/PageHeader'
import { useRuleSet } from '../context/RuleSetContext'
import { useAuth } from '../auth/AuthContext'
import SkillTab from './SkillTab'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'

const { Text } = Typography
const { Dragger } = Upload
// 后端单源化 — fallback（后端返回前兜底）
const FALLBACK_CHECK_CATEGORIES = ['齐套性', '基础判断', '信息准确性', '时间逻辑']
// 批次 10：规则标签可为空，矩阵展示时归入"整批/全部"行与"未分类"列
const GLOBAL_DOC_TYPE = '整批/全部'
const UNCATEGORIZED = '未分类'

/** 冲突检测结果分组 */
type ConflictGroup = {
  rule_ids: string[]
  rule_codes: string[]
  type: string
  severity: string
  description: string
  rules: Rule[]
}
const FILE_ACCEPT = '.pdf,.xlsx,.xls,.docx,.md,.txt'

/** 导入结果对照：原文片段 ↔ 解析出的规则 */
function ImportResultRules({ rules }: { rules: Array<Record<string, unknown>> }) {
  const get = (r: Record<string, unknown>, k: string) => {
    const v = r[k]
    return v == null ? '' : String(v)
  }
  const rows = rules.map((r, i) => ({
    key: i,
    code: get(r, 'rule_code') || '-',
    source: get(r, 'source_text') || get(r, 'rule_text'),
    rule: get(r, 'rule_text'),
    meta: [get(r, 'source_ref'), get(r, 'doc_type'), get(r, 'check_category')].filter(Boolean).join(' / ') || '-',
  }))
  if (rows.length === 0) return null
  return (
    <div style={{ marginTop: 12 }}>
      <Text strong style={{ display: 'block', marginBottom: 8 }}>
        解析结果对照（原文 → 规则）
      </Text>
      <Table
        size="small"
        pagination={false}
        dataSource={rows}
        columns={[
          {
            title: '规则号',
            dataIndex: 'code',
            width: 88,
            render: (v: string) => <Tag color="blue">{v}</Tag>,
          },
          {
            title: '原文片段',
            dataIndex: 'source',
            width: '38%',
            render: (v: string) => (
              <Tooltip title={v}>
                <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{v}</Text>
              </Tooltip>
            ),
          },
          {
            title: '解析结果',
            dataIndex: 'rule',
            render: (v: string) => <Text style={{ fontSize: 12 }}>{v}</Text>,
          },
          {
            title: '类型 / 检查项',
            dataIndex: 'meta',
            width: 180,
            render: (v: string) => <Text style={{ fontSize: 12 }}>{v}</Text>,
          },
        ]}
      />
    </div>
  )
}

// 导入任务状态 → 中文标签
function statusLabel(status: ImportTask['status']): string {
  const map: Record<ImportTask['status'], string> = {
    pending: '排队中',
    extracting: '提取文本',
    parsing: '大模型解析',
    importing: '入库中',
    conflict: '冲突检测',
    done: '完成',
    error: '失败',
  }
  return map[status] || status
}

// 估算整体进度百分比（0~100）
function computeImportProgress(task: ImportTask): number {
  const pct = (a: number, b: number) => (b > 0 ? Math.min(100, Math.round((a / b) * 100)) : 0)
  switch (task.status) {
    case 'pending':
      return 2
    case 'extracting':
      return 8
    case 'parsing':
      return 10 + pct(task.parsed_chunks, task.total_chunks) * 0.5
    case 'importing':
      return 60 + pct(task.imported_rules, Math.max(task.total_rules, 1)) * 0.2
    case 'conflict':
      return 80 + pct(task.conflict_done, Math.max(task.conflict_total, 1)) * 0.2
    case 'done':
      return 100
    case 'error':
      return 100
    default:
      return 0
  }
}

// 进度条上的文字描述
function describeImportProgress(task: ImportTask): string {
  switch (task.status) {
    case 'parsing':
      return `已解析 ${task.parsed_chunks}/${task.total_chunks} 段`
    case 'importing':
      return `已入库 ${task.imported_rules}/${task.total_rules} 条`
    case 'conflict':
      return `冲突检测 ${task.conflict_done}/${task.conflict_total} 组`
    case 'done':
      return `完成（${task.imported_rules} 条）`
    default:
      return ''
  }
}

export default function RulesPage() {
  const { currentId } = useRuleSet()
  const { hasPermission } = useAuth()
  const canManageRules = hasPermission('rules.manage')
  const [rules, setRules] = useState<Rule[]>([])
  const [snapshots, setSnapshots] = useState<RuleSnapshot[]>([])
  const [docTypes, setDocTypes] = useState<DocTypeMeta[]>([])
  const [checkCategories, setCheckCategories] = useState<string[]>([])
  const [completenessCategory, setCompletenessCategory] = useState<string>('齐套性')  // 批次 3-5：后端单源
  const [ruleSet, setRuleSet] = useState<RuleSet | null>(null)  // 当前规则集详情（含 doc_types / check_categories）
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const [deleting, setDeleting] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [saving, setSaving] = useState(false)  // 批次 5-1：防重复提交
  const [editing, setEditing] = useState<Rule | null>(null)
  const [form] = Form.useForm()
  const [importOpen, setImportOpen] = useState(false)
  const [importMode, setImportMode] = useState<'text' | 'file'>('text')
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<RuleImportResponse | null>(null)
  // 文件导入
  // RcFile = antd Upload 的 beforeUpload 参数类型（File + uid），可直接作 originFileObj
  const [importFile, setImportFile] = useState<NonNullable<Parameters<NonNullable<UploadProps['beforeUpload']>>[0]> | null>(null)
  const [fileImportResult, setFileImportResult] = useState<RuleImportResponse | null>(null)
  // 导入阶段提示（解决"点了没反馈"的问题）
  const [importStage, setImportStage] = useState<'parsing' | 'llm' | 'saving' | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  // 异步导入任务进度（轮询用）
  const [importTask, setImportTask] = useState<ImportTask | null>(null)
  // 导入轮询取消标志 + 计数:组件卸载或重新触发导入时置 true 终止递归;count 超上限自动退出
  const importPollRef = useRef<{ cancelled: boolean; count: number }>({ cancelled: false, count: 0 })
  useEffect(() => {
    return () => {
      // 组件卸载时终止正在进行的导入轮询,避免对已卸载组件 setState
      importPollRef.current.cancelled = true
    }
  }, [])
  // 批量确认
  const [confirming, setConfirming] = useState(false)
  // Skill 选择
  const [allSkills, setAllSkills] = useState<Array<{ id: string; name: string; is_builtin: boolean; enabled: boolean }>>([])
  const [importSkillIds, setImportSkillIds] = useState<string[]>([])
  // 冲突检测
  const [conflictDetecting, setConflictDetecting] = useState(false)
  const [conflictModalOpen, setConflictModalOpen] = useState(false)
  const [conflictData, setConflictData] = useState<Array<{ rule_ids: string[]; rule_codes: string[]; type: string; severity: string; description: string; rules: Rule[] }>>([])
  // 缺陷详情侧边栏（统一展示冲突/错误/警告）
  const [defectDrawerOpen, setDefectDrawerOpen] = useState(false)
  const [defectDrawerTab, setDefectDrawerTab] = useState<'conflict' | 'error' | 'warning'>('error')

  // 规则列表子标签页：按缺陷归桶过滤（互斥：正常/冲突/错误/警告/提示）
  const [activeRuleFilter, setActiveRuleFilter] = useState<RuleFilterKey>('all')
  const [defectSummaryRemote, setDefectSummaryRemote] = useState<{ healthy: number; conflict: number; error: number; warning: number; info: number; total_rules: number } | null>(null)

  const STAGE_TIP: Record<NonNullable<typeof importStage>, string> = {
    parsing: '正在解析文档为文本（PDF/Excel/Word）...',
    llm: '正在调用大模型提取规则（约 20-30 秒）...',
    saving: '正在入库，请稍候...',
  }

  const load = async (filterKey?: RuleFilterKey) => {
    if (!currentId) return
    const fk = filterKey ?? activeRuleFilter
    setLoading(true)
    try {
      // 根据过滤键构建 API 参数
      const apiParams: { defect_severity?: string } = {}
      if (fk === 'healthy') apiParams.defect_severity = 'none'
      else if (fk === 'conflict') apiParams.defect_severity = 'conflict'
      else if (fk === 'error') apiParams.defect_severity = 'error'
      else if (fk === 'warning') apiParams.defect_severity = 'warning'
      else if (fk === 'info') apiParams.defect_severity = 'info'

      const [r, s, c, rs, ds] = await Promise.all([
        rulesApi.list(currentId, apiParams),
        rulesApi.listSnapshots(currentId),
        constantsApi.docTypes(),
        ruleSetsApi.get(currentId),
        rulesApi.getDefectSummary(currentId).catch(() => null),
      ])
      setRules(r)
      setSnapshots(s)
      setDocTypes(c.doc_types)
      setCheckCategories(c.check_categories)
      setCompletenessCategory(c.completeness_category || '齐套性')
      setRuleSet(rs)
      if (ds) setDefectSummaryRemote(ds)
    } catch (e) {
      message.error('加载失败: ' + getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [currentId])

  /** 切换规则列表子标签时重新加载过滤数据 */
  const handleFilterChange = (key: RuleFilterKey) => {
    setActiveRuleFilter(key)
    load(key)
  }

  // 二维表格：行=文件类型，列=检查项
  const matrix = useMemo(() => {
    const m: Record<string, Record<string, Rule[]>> = {}
    rules.forEach((r) => {
      const dt = r.doc_type || GLOBAL_DOC_TYPE
      const cc = r.check_category || UNCATEGORIZED
      if (!m[dt]) m[dt] = {}
      if (!m[dt][cc]) m[dt][cc] = []
      m[dt][cc].push(r)
    })
    return m
  }, [rules])

  // 动态文件类型列表（种子 + 规则集声明 + 实际规则中的类型，去重）

  // 动态文件类型列表（种子 + 规则集声明 + 实际规则中的类型，去重）
  const effectiveDocTypes = useMemo(() => {
    const seed: string[] = docTypes.map((d) => d.name)
    const fromSet: string[] = ruleSet?.doc_types || []
    const fromRules: string[] = [...new Set(rules.map((r) => r.doc_type || GLOBAL_DOC_TYPE))]
    const merged = [...new Set([...seed, ...fromSet, ...fromRules])]
    // 排序：已声明的种子靠前（保持 seed 相对顺序），新增的放后面
    const seedOrder = new Map(seed.map((n, i) => [n, i]))
    return merged.sort((a, b) => {
      const ia = seedOrder.has(a) ? seedOrder.get(a)! : 999
      const ib = seedOrder.has(b) ? seedOrder.get(b)! : 999
      return ia - ib
    })
  }, [docTypes, ruleSet, rules])

  // 动态检查项列表（种子 + 规则集声明 + 实际规则，去重）
  const effectiveCheckCategories = useMemo(() => {
    const seed: string[] = [...(checkCategories.length > 0 ? checkCategories : FALLBACK_CHECK_CATEGORIES)]
    const fromSet: string[] = ruleSet?.check_categories || []
    const fromRules: string[] = [...new Set(rules.map((r) => r.check_category || UNCATEGORIZED))]
    return [...new Set([...seed, ...fromSet, ...fromRules])]
  }, [ruleSet, rules])

  // 计算缺陷统计（与后端同口径：按"最严重的未处理缺陷"归桶，各页签都是规则条数）。
  // 后端 defect-summary 为权威来源，此处仅作兜底与无网络时的降级展示。
  const defectSummary = useMemo(() => {
    const summary = { healthy: 0, conflict: 0, error: 0, warning: 0, info: 0, total_rules: rules.length }
    rules.forEach((r) => {
      summary[classifyDefects(r.defects)] += 1
    })
    return summary
  }, [rules])

  /** 页签计数：优先用后端规则集全量统计（当前 rules 可能已被页签过滤） */
  const summary = defectSummaryRemote ?? defectSummary

  /** 缺陷侧边栏计数：按**未处理缺陷条目**统计（与表格逐条展示保持一致） */
  const openDefectCounts = useMemo(() => {
    const counts = { conflict: 0, error: 0, warning: 0, info: 0 }
    rules.forEach((r) => {
      openDefects(r.defects).forEach((d) => {
        if (isConflictDefect(d)) counts.conflict += 1
        if (d.severity === 'error') counts.error += 1
        else if (d.severity === 'warning') counts.warning += 1
        else counts.info += 1
      })
    })
    return counts
  }, [rules])

  /** 当前视图中仍需用户操作的规则（未确认，或仍有未处理缺陷） */
  const actionableRules = useMemo(() => rules.filter(needsUserAction), [rules])

  const openCreate = () => {
    if (!canManageRules) return
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ enabled: true, priority: 100, tolerance: {} })
    setModalOpen(true)
  }

  const openEdit = (rule: Rule) => {
    if (!canManageRules) return
    setEditing(rule)
    form.setFieldsValue({
      ...rule,
      tolerance_amount: rule.tolerance?.amount_percent,
      tolerance_weight: rule.tolerance?.weight_kg,
      tolerance_time: rule.tolerance?.time_days,
      allow_same_day: rule.tolerance?.allow_same_day,
    })
    setModalOpen(true)
  }

  /** 在二维表格空格点击，预填���件类型 + 检查项 */
  /** 在二维表格空格点击，预填文件类型 + 检查项 */
  const openCreateWithDefaults = (doc_type: string, check_category: string) => {
    if (!canManageRules) return
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ doc_type, check_category, enabled: true, priority: 100, tolerance: {} })
    setModalOpen(true)
  }

  /** 人工修正规则后，询问是否把修正经验写回解析 Skill（持续增强自定义 Skill） */
  const promptLearnFromCorrection = (before: Rule, after: Record<string, unknown>) => {
    const LEARN_FIELDS: Array<{ key: keyof Rule & string; label: string }> = [
      { key: 'rule_text', label: '规则文本' },
      { key: 'check_category', label: '检查类别' },
      { key: 'doc_type', label: '适用单据' },
    ]
    const changes = LEARN_FIELDS.filter(
      (f) => after[f.key] != null && String(after[f.key]) !== String(before[f.key] ?? ''),
    )
    if (!changes.length) return

    Modal.confirm({
      title: '将本次修正作为解析经验？',
      width: 560,
      content: (
        <div>
          <p style={{ marginBottom: 8 }}>
            检测到你修正了解析结果。可以把这次修正写入「经验修正（自动累积）」Skill，
            下次导入规则时自动注入解析提示词，减少同类错误。
          </p>
          {changes.map((f) => (
            <div key={f.key} style={{ fontSize: 12, marginBottom: 4 }}>
              <Tag>{f.label}</Tag>
              <Text delete type="secondary">{String(before[f.key] ?? '（空）')}</Text>
              {' → '}
              <Text strong>{String(after[f.key])}</Text>
            </div>
          ))}
        </div>
      ),
      okText: '写入经验',
      cancelText: '仅本次修正',
      onOk: async () => {
        try {
          const beforeData: Record<string, unknown> = {}
          const afterData: Record<string, unknown> = {}
          changes.forEach((f) => {
            beforeData[f.key] = before[f.key]
            afterData[f.key] = after[f.key]
          })
          // rule_text 未变时也带上，作为经验的定位锚点
          if (afterData.rule_text == null) {
            beforeData.rule_text = before.rule_text
            afterData.rule_text = after.rule_text ?? before.rule_text
          }
          const resp = await skillsApi.learn(currentId!, {
            rule_id: before.id,
            before: beforeData,
            after: afterData,
          })
          message.success(
            `已写入 Skill「${resp.skill.name}」（v${resp.skill.version}，新增 ${resp.added_instructions.length} 条经验）`,
          )
    } catch (e) {
      message.error('写入经验失败: ' + getErrorMessage(e))
        }
      },
    })
  }

  const handleSave = async () => {
    if (saving) return
    setSaving(true)
    try {
      const values = await form.validateFields()
      // 组装容差参数
      const tolerance: Record<string, unknown> = {}
      if (values.tolerance_amount != null) tolerance.amount_percent = values.tolerance_amount
      if (values.tolerance_weight != null) tolerance.weight_kg = values.tolerance_weight
      if (values.tolerance_time != null) tolerance.time_days = values.tolerance_time
      if (values.allow_same_day != null) tolerance.allow_same_day = values.allow_same_day

      const payload = {
        doc_type: values.doc_type ?? null,
        check_category: values.check_category ?? null,
        rule_text: values.rule_text,
        tolerance,
        enabled: values.enabled,
        priority: values.priority,
      }
      if (editing) {
        await rulesApi.update(editing.id, payload)
        message.success('规则已更新')
        promptLearnFromCorrection(editing, payload)
      } else {
        await rulesApi.create(currentId!, payload)
        message.success('规则已新增')
      }
      setModalOpen(false)
      await load()
    } catch (e) {
      if (isFormValidationError(e)) return // 表单校验错误
      message.error('保存失败: ' + getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!canManageRules) return
    try {
      await rulesApi.delete(id)
      message.success('已删除')
      await load()
    } catch (e) {
      message.error('删除失败: ' + getErrorMessage(e))
    }
  }


  /** 批量删除规则：传 ids 仅删指定；不传则清空当前规则集全部规则 */
  const handleBatchDelete = async (ids?: string[]) => {
    if (!canManageRules) return
    if (!currentId) return
    setDeleting(true)
    try {
      const resp = await rulesApi.batchDelete(currentId, ids)
      message.success(`已删除 ${resp.deleted} 条规则`)
      setSelectedRowKeys([])
      await load()
    } catch (e) {
      message.error('删除失败: ' + getErrorMessage(e))
    } finally {
      setDeleting(false)
    }
  }

  /** 批量确认：传 ids 仅确认指定；不传则确认所有 pending 规则。同时启用这些规则 */
  const handleBatchConfirm = async (ids?: string[]) => {
    if (!canManageRules) return
    if (!currentId) return
    setConfirming(true)
    try {
      // 后端确认时已同时置 enabled=true，并把未处理缺陷标记为"已处理"
      const resp = await rulesApi.confirmBatch(currentId, ids)
      message.success(resp.message + '，缺陷已标记为已处理并进入「正常规则」')
      await load()
    } catch (e) {
      message.error('确认失败: ' + getErrorMessage(e))
    } finally {
      setConfirming(false)
    }
  }

  const handleDetectConflicts = async (openInDrawer = false) => {
    if (!canManageRules) return
    if (!currentId || conflictDetecting) return
    setConflictDetecting(true)
    try {
      const resp = await rulesApi.detectConflicts(currentId)
      if (resp.total_conflicts === 0) {
        message.success('未检测到语义冲突')
        await load()
        return
      }
      // 将冲突与规则数据关联
      const conflictGroups = resp.conflicts.map((c) => ({
        ...c,
        rules: c.rule_ids.map((rid) => rules.find((r) => r.id === rid)!).filter(Boolean),
      }))
      setConflictData(conflictGroups)
      if (openInDrawer) {
        setDefectDrawerTab('conflict')
        setDefectDrawerOpen(true)
      } else {
        setConflictModalOpen(true)
      }
      message.success(`检测到 ${resp.total_conflicts} 个冲突，涉及 ${resp.affected_rules} 条规则`)
      await load()
    } catch (e) {
      message.error('冲突检测失败: ' + getErrorMessage(e))
    } finally {
      setConflictDetecting(false)
    }
  }

  /** 打开缺陷侧边栏（数据在 Drawer 中实时计算，无需预加载） */
  const handleShowDefects = (severity: 'error' | 'warning') => {
    setDefectDrawerTab(severity)
    setDefectDrawerOpen(true)
  }

  const openImport = async () => {
    if (!canManageRules) return
    setImportText('')
    setImportResult(null)
    setImportFile(null)
    setFileImportResult(null)
    setImportStage(null)
    setImportError(null)
    setImportMode('text')
    setImportSkillIds([])
    // 加载可用 Skill
    if (currentId) {
      try {
        const { skillsApi } = await import('../api/client')
        const skills = await skillsApi.list(currentId)
        setAllSkills(skills.map((s) => ({ id: s.id, name: s.name, is_builtin: s.is_builtin, enabled: s.enabled })))
      } catch {
        // 加载 Skill 失败不阻塞导入
      }
    }
    setImportOpen(true)
  }

  const handleImport = async () => {
    if (!importText.trim()) {
      message.warning('请粘贴规则清单文本')
      return
    }
    setImporting(true)
    setImportResult(null)
    setImportStage('llm')
    setImportError(null)
    try {
      const resp = await rulesApi.importBatch(currentId!, importText, importSkillIds.length ? importSkillIds : undefined)
      setImportResult(resp)
      if (resp.imported > 0) {
        message.success(`导入完成：成功 ${resp.imported} 条，跳过 ${resp.skipped} 条`)
        await load()
        // 检测新的文档类型
        const newTypes = resp.new_doc_types
        if (newTypes && newTypes.length > 0) {
          message.info(
            `发现 ${newTypes.length} 个新的文档类型，可在「文档类型」页面查看并补充样例`,
            6,
          )
        }
      } else {
        message.warning(`未导入任何规则，跳过 ${resp.skipped} 条`)
      }
    } catch (e) {
      const msg = '导入失败: ' + getErrorMessage(e)
      setImportError(msg)
      message.error(msg)
    } finally {
      setImporting(false)
      setImportStage(null)
    }
  }

  /** 文件导入：支持 PDF/Excel/Word/MD/TXT，后端解析为文本后调用 LLM 转规则 */
  const handleImportFile = async () => {
    if (!importFile) {
      message.warning('请先选择要导入的规则文档')
      return
    }
    setImporting(true)
    setFileImportResult(null)
    setImportError(null)
    setImportStage('parsing')
    setImportTask(null)
    // 阶段 1：上传文件，后端立即返回 task_id（异步任务模式）
    let taskId: string
    try {
      const task = await rulesApi.importDocument(
        currentId!,
        importFile,
        importSkillIds.length ? importSkillIds : undefined,
      )
      taskId = task.task_id
      setImportStage('llm')
    } catch (e) {
      const msg = '文件上传失败: ' + getErrorMessage(e)
      setImportError(msg)
      message.error(msg)
      setImporting(false)
      setImportStage(null)
      return
    }

    // 阶段 2：轮询进度，直到 done / error
    // 重置取消标志与计数;MAX_POLL × 1.5s ≈ 5 分钟上限,防止后端卡死时无限轮询;
    // 组件卸载或重新触发导入时 importPollRef.cancelled 会被置 true 终止递归
    importPollRef.current = { cancelled: false, count: 0 }
    const MAX_POLL = 200
    const poll = async (): Promise<void> => {
      if (importPollRef.current.cancelled) return
      importPollRef.current.count += 1
      if (importPollRef.current.count > MAX_POLL) {
        const msg = '导入超时：任务运行超过 5 分钟仍未完成，请稍后在规则列表查看结果'
        setImportError(msg)
        message.warning(msg)
        setImporting(false)
        setImportStage(null)
        return
      }
      const task = await rulesApi.getImportTask(taskId)
      if (importPollRef.current.cancelled) return
      setImportTask(task)
      if (task.status === 'done') {
        setFileImportResult(task.result)
        if (task.result && task.result.imported > 0) {
          message.success(`导入完成：成功 ${task.result.imported} 条，跳过 ${task.result.skipped} 条`)
          await load()
          // 检测新的文档类型
    const newTypes = task.result?.new_doc_types
          if (newTypes && newTypes.length > 0) {
            message.info(
              `发现 ${newTypes.length} 个新的文档类型，可在「文档类型」页面查看并补充样例`,
              6,
            )
          }
        } else {
          message.warning(`未导入任何规则${task.result ? `，跳过 ${task.result.skipped} 条` : ''}`)
        }
        setImporting(false)
        setImportStage(null)
        return
      }
      if (task.status === 'error') {
        const msg = '文件导入失败: ' + (task.error || '未知错误')
        setImportError(msg)
        message.error(msg)
        setImporting(false)
        setImportStage(null)
        return
      }
      // 仍在进行中，1.5s 后继续
      await new Promise((r) => setTimeout(r, 1500))
      if (importPollRef.current.cancelled) return
      return poll()
    }
    await poll()
  }

  // 文件上传组件 props：单文件，手动触发上传
  const fileUploadProps: UploadProps = {
    accept: FILE_ACCEPT,
    multiple: false,
    maxCount: 1,
    showUploadList: true,
    fileList: importFile
      ? [
          {
            uid: '-1',
            name: importFile.name,
            size: importFile.size,
            type: importFile.type,
            status: 'done',
            originFileObj: importFile,
          } as UploadFile,
        ]
      : [],
    beforeUpload: (file) => {
      setImportFile(file)
      setFileImportResult(null)
      return false // 阻止自动上传
    },
    onRemove: () => {
      setImportFile(null)
      setFileImportResult(null)
    },
  }

  // 二维表格渲染
  const renderMatrix = () => {
    // 列宽：行头列 168、中间检查项列 96、齐套率 96；总宽 < 容器则不滚，否则横向滚动
    const headerColW = 168
    const checkColW = 96
    const coverageColW = 96
    const totalW = headerColW + checkColW * effectiveCheckCategories.length + coverageColW
    return (
      <div style={{ width: '100%', overflowX: 'auto' }}>
      <table style={{ width: totalW, borderCollapse: 'collapse', fontSize: 12, tableLayout: 'fixed' }}>
        <colgroup>
          <col style={{ width: headerColW }} />
          {effectiveCheckCategories.map((c) => <col key={c} style={{ width: checkColW }} />)}
          <col style={{ width: coverageColW }} />
        </colgroup>
        <thead>
          <tr>
            <th style={headerThStyle}>文件类型 \ 检查项</th>
            {effectiveCheckCategories.map((c) => <th key={c} style={checkThStyle}>{c}</th>)}
            <th style={coverageThStyle}>齐套率</th>
          </tr>
        </thead>
        <tbody>
            {effectiveDocTypes.map((dt) => {
              const meta = docTypes.find((d) => d.name === dt)
              // 必备/非必备从齐套性规则推导：该文档类型在"齐套性"列下有已确认规则即为必备
              const isRequired = (matrix[dt]?.[completenessCategory] || []).some(
                (r) => r.enabled && r.status === 'confirmed'
              )
              // 计算该行已覆盖列数（指有至少 1 条 confirmed+enabled 规则）
            const coveredCols = effectiveCheckCategories.filter((cc) => {
              return (matrix[dt]?.[cc] || []).filter((r) => r.enabled && r.status === 'confirmed').length > 0
            }).length
            const totalCols = effectiveCheckCategories.length
            const coverage = totalCols > 0 ? Math.round((coveredCols / totalCols) * 100) : 0
            const coverageColor = coverage >= 80 ? 'green' : coverage >= 50 ? 'orange' : 'red'
            return (
            <tr key={dt}>
              <td style={headerTdStyle}>
                <Tooltip title={
                  meta ? (
                    <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                      <div>关键字段：{meta.key_fields?.length ? meta.key_fields.join('、') : '-'}</div>
                      <div>业务含义：{meta.business_meaning || '-'}</div>
                      <div>印章要求：{meta.stamp_required || '无'}</div>
                      <div>样例文档：{meta.has_sample ? '已上传' : '未上传'}</div>
                    </div>
                  ) : <span style={{ fontSize: 12 }}>未在文档类型中定义</span>
                }>
                  <span style={fileNameStyle}>{dt}</span>
                </Tooltip>
                <div style={{ marginTop: 2 }}>
                  {isRequired && <Tag color="red" style={{ marginInlineEnd: 0, fontSize: 10 }}>必备</Tag>}
                </div>
              </td>
              {effectiveCheckCategories.map((cc) => {
                const cellRules = matrix[dt]?.[cc] || []
                const confirmedRules = cellRules.filter((r) => r.enabled && r.status === 'confirmed')
                const confirmedCount = confirmedRules.length
                const totalCount = cellRules.length
                return (
                  <td
                    key={cc}
                    style={checkTdStyle}
                    onClick={canManageRules && totalCount === 0 ? () => openCreateWithDefaults(dt, cc) : undefined}
                  >
                    {totalCount === 0 ? (
                      <a
                        style={{ fontSize: 11, color: canManageRules ? '#1890ff' : '#94a3b8' }}
                      >
                        {canManageRules ? '+ 添加' : '未配置'}
                      </a>
                    ) : totalCount === 1 && confirmedCount === 1 ? (
                      <Tooltip title={cellRules[0].rule_text}>
                        <Tag color="blue" style={{ cursor: canManageRules ? 'pointer' : 'default' }} onClick={canManageRules ? () => openEdit(cellRules[0]) : undefined}>
                          1 条
                        </Tag>
                      </Tooltip>
                    ) : (
                      <Popover
                        trigger="click"
                        placement="right"
                        title={`${dt} · ${cc}（共 ${totalCount} 条, 生效 ${confirmedCount} 条, 点击编辑）`}
                        content={
                          <div style={{ maxWidth: 360, maxHeight: 320, overflowY: 'auto' }}>
                            {cellRules.map((r, i) => {
                              const isActive = r.enabled && r.status === 'confirmed'
                              return (
                                <div
                                  key={r.id}
                                  style={{
                                    padding: '6px 8px',
                                    cursor: canManageRules ? 'pointer' : 'default',
                                    borderBottom: i < totalCount - 1 ? '1px solid #f1f5f9' : 'none',
                                    fontSize: 12,
                                  }}
                                  onClick={canManageRules ? () => openEdit(r) : undefined}
                                >
                                  <Tag
                                    color={isActive ? 'blue' : r.status === 'confirmed' ? 'default' : 'orange'}
                                    style={{ marginRight: 6, fontSize: 10 }}
                                  >
                                    {r.rule_code}
                                  </Tag>
                                  <Text type={isActive ? undefined : 'secondary'} style={{ fontSize: 12 }}>
                                    {r.rule_text}
                                  </Text>
                                </div>
                              )
                            })}
                          </div>
                        }
                      >
                        <Tag
                          color={
                            confirmedCount === totalCount ? 'blue'
                            : confirmedCount > 0 ? 'orange'
                            : 'default'
                          }
                          style={{ cursor: canManageRules ? 'pointer' : 'default' }}
                        >
                          {confirmedCount} / {totalCount} 条
                        </Tag>
                      </Popover>
                    )}
                  </td>
                )
              })}
              {/* 齐套率汇总列 */}
              <td style={coverageTdStyle}>
                <Tag color={coverageColor} style={{ fontSize: 11 }}>{coverage}%</Tag>
                <div style={{ fontSize: 10, color: '#999', marginTop: 2 }}>({coveredCols}/{totalCols})</div>
              </td>
            </tr>
          )})}
        </tbody>
      </table>
      </div>
    )
  }

  // 批次 5-6：错误/警告 Tab 共用缺陷表格渲染（消除重复）
  // 方案 A：只列**未处理**缺陷；已处理缺陷保留在规则详情/缺陷列中做审计展示。
  const renderDefectTable = (severity: 'error' | 'warning', rowPrefix: string) => {
    const entries: Array<{ ruleId: string; ruleCode: string; docType: string | null; checkCategory: string | null; ruleText: string; type: string; description: string }> = []
    rules.forEach((r) => {
      openDefects(r.defects).filter((d) => d.severity === severity).forEach((d) => {
        entries.push({
          ruleId: r.id, ruleCode: r.rule_code, docType: r.doc_type, checkCategory: r.check_category,
          ruleText: r.rule_text, type: d.type, description: d.description,
        })
      })
    })
    return (
      <Table
        dataSource={entries}
        rowKey={(_, i) => `${rowPrefix}-${i}`}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ['10', '20', '50'], size: 'small' }}
        columns={[
          { title: '规则号', dataIndex: 'ruleCode', width: 88, render: (v: string) => <Tag color="blue">{v}</Tag> },
          { title: '文件类型', dataIndex: 'docType', width: 100 },
          { title: '检查项', dataIndex: 'checkCategory', width: 80 },
          { title: '规则文本', dataIndex: 'ruleText', ellipsis: true },
          { title: '缺陷描述', dataIndex: 'description', ellipsis: true },
          {
            title: '操作', width: 80,
            render: (_: unknown, row: { ruleId: string }) => (
              <Button
                size="small"
                type="link"
                onClick={() => {
                  setSelectedRowKeys([row.ruleId])
                  setDefectDrawerOpen(false)
                }}
              >
                定位
              </Button>
            ),
          },
        ]}
      />
    )
  }

  const headerThStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: '6px 8px', background: '#fafafa', textAlign: 'left', fontSize: 12, fontWeight: 600 }
  const checkThStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: '6px 8px', background: '#fafafa', textAlign: 'center', fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' }
  const coverageThStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: '6px 8px', background: '#fafafa', textAlign: 'center', fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' }
  const headerTdStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: '6px 8px', textAlign: 'left', verticalAlign: 'middle', background: '#fafafa' }
  const checkTdStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: '6px 8px', textAlign: 'center', verticalAlign: 'middle', cursor: 'pointer' }
  const coverageTdStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: '6px 8px', textAlign: 'center', verticalAlign: 'middle' }
  const fileNameStyle: React.CSSProperties = { display: 'inline-block', maxWidth: 132, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600, fontSize: 12, verticalAlign: 'middle' }

  const ruleColumns = [
    {
      title: '规则号', dataIndex: 'rule_code', key: 'rule_code', width: 92,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '文件类型', dataIndex: 'doc_type', key: 'doc_type', width: 140,
      render: (v: string | null) => v || <Text type="secondary">整批/全部</Text>,
    },
    {
      title: '检查项', dataIndex: 'check_category', key: 'check_category', width: 120,
      render: (v: string | null) => v || <Text type="secondary">未分类</Text>,
    },
    { title: '规则文本', dataIndex: 'rule_text', key: 'rule_text', ellipsis: true },
    {
      title: '置信度', key: 'confidence', width: 90,
      render: (_: unknown, row: Rule) => {
        if (row.confidence == null) return <Text type="secondary">-</Text>
        const c = row.confidence
        const color = c >= 0.9 ? 'green' : c >= 0.7 ? 'orange' : 'red'
        return <Tag color={color}>{(c * 100).toFixed(0)}%</Tag>
      },
    },
    {
      title: '状态', key: 'status', width: 90,
      render: (_: unknown, row: Rule) => (
        row.status === 'confirmed'
          ? <Tag color="green">已确认</Tag>
          : <Tag color="orange">待确认</Tag>
      ),
    },
    {
      title: '缺陷', key: 'defects', width: 150,
      render: (_: unknown, row: Rule) => {
        const defects = row.defects || []
        if (defects.length === 0) return <Text type="secondary">-</Text>
        const pending = openDefects(defects)
        const handled = defects.length - pending.length
        const { error: errors, warning: warnings, info: infos } = openSeverityCounts(defects)
        const tag = (count: number, color: string, label: string, list: DefectItem[]) => (
          <Tooltip title={list.map((d) => d.description).join('\n')}>
            <Tag color={color}>{count} {label}</Tag>
          </Tooltip>
        )
        return (
          <Space size={4} wrap>
            {errors > 0 && tag(errors, 'red', '错误', pending.filter((d) => d.severity === 'error'))}
            {warnings > 0 && tag(warnings, 'orange', '警告', pending.filter((d) => d.severity === 'warning'))}
            {infos > 0 && tag(infos, 'blue', '提示', pending.filter((d) => d.severity === 'info'))}
            {handled > 0 && (
              <Tooltip
                title={defects.filter(isResolvedDefect).map((d) => `${d.description}（已处理${d.resolved_by ? `：${d.resolved_by}` : ''}）`).join('\n')}
              >
                <Tag color="default">{handled} 已处理</Tag>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
    {
      title: '容差', key: 'tolerance', width: 200,
      render: (_: unknown, row: Rule) => {
        const t = row.tolerance || {}
        const parts: string[] = []
        if (t.amount_percent != null) parts.push(`金额±${t.amount_percent}%`)
        if (t.weight_kg != null) parts.push(`重量±${t.weight_kg}kg`)
        if (t.allow_same_day != null) parts.push(t.allow_same_day ? '允许同日' : '不允许同日')
        if (t.time_days != null) parts.push(`时间±${t.time_days}天`)
        return parts.length ? parts.join(' / ') : <Text type="secondary">无</Text>
      },
    },
    {
      title: '启用', dataIndex: 'enabled', key: 'enabled', width: 80,
      render: (v: boolean, row: Rule) => (
        canManageRules ? (
          <Switch
            size="small"
            checked={v}
            onChange={async (checked) => {
              try {
                const payload: Partial<Rule> = { enabled: checked }
                // 启用即视为用户认可：未确认的规则同时置为已确认，
                // 后端会把未处理缺陷标记为"已处理"，规则随后进入「正常规则」。
                if (checked && row.status !== 'confirmed') {
                  payload.status = 'confirmed'
                }
                await rulesApi.update(row.id, payload)
                const accepted = checked && row.status !== 'confirmed'
                message.success(
                  accepted
                    ? '规则已确认并启用，缺陷已标记为已处理，将参与图谱构建'
                    : checked
                      ? (hasOpenDefect(row) ? '规则已启用，缺陷已标记为已处理' : '规则已启用')
                      : '规则已禁用，已从图谱中移除'
                )
                await load()
              } catch (e) {
                message.error('更新失败: ' + getErrorMessage(e))
              }
            }}
          />
        ) : <Tag color={v ? 'green' : 'default'}>{v ? '已启用' : '已停用'}</Tag>
      ),
    },
    {
      title: '操作', key: 'action', width: 180,
      render: (_: unknown, row: Rule) => {
        if (!canManageRules) return <Text type="secondary">只读</Text>
        const openCount = openDefects(row.defects).length
        const needConfirm = row.status !== 'confirmed'
        // 已确认但仍有未处理缺陷（例如历史数据/后续新检出）时，仍提供"标记已处理"入口
        const showAction = needConfirm || openCount > 0
        return (
          <Space>
            {showAction && (
              <Popconfirm
                title={
                  needConfirm
                    ? '确认并启用该条规则？确认后缺陷将标记为已处理，规则进入「正常规则」并参与图谱构建和文档审查'
                    : `将 ${openCount} 条未处理缺陷标记为已处理？规则已确认启用，处理后将进入「正常规则」`
                }
                onConfirm={async () => {
                  await rulesApi.update(
                    row.id,
                    needConfirm ? { status: 'confirmed', enabled: true } : { status: 'confirmed' },
                  )
                  message.success(
                    needConfirm ? '规则已确认并启用，缺陷已标记为已处理' : '缺陷已标记为已处理，规则已进入「正常规则」',
                  )
                  await load()
                }}
              >
                <Button size="small" type="primary" icon={<CheckOutlined />}>
                  {needConfirm ? '确认启用' : '标记已处理'}
                </Button>
              </Popconfirm>
            )}
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
            <Popconfirm title="确定删除该规则？" onConfirm={() => handleDelete(row.id)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      <PageHeader
        title="规则管理"
        subtitle="维护审查规则，支持新增、批量导入。规则按文档类型与检查类别分类管理"
        icon={<SettingOutlined />}
        extra={
          <Space>
            {canManageRules && (
              <>
                <Button icon={<PlusOutlined />} onClick={openCreate}>新增规则</Button>
                <Button icon={<ImportOutlined />} onClick={openImport}>批量导入</Button>
              </>
            )}
            <Button icon={<ArrowRightOutlined />} onClick={() => navigate('/graph')}>
              前往构建图谱
            </Button>
            {canManageRules && (
              <Button icon={<WarningOutlined />} loading={conflictDetecting} onClick={() => handleDetectConflicts()}>检测冲突</Button>
            )}
            <Button icon={<HistoryOutlined />} onClick={() => load()}>刷新</Button>
            {canManageRules && (
              <Popconfirm
                title={`确定清空当前规则集的全部 ${rules.length} 条规则？此操作不可恢复`}
                onConfirm={() => handleBatchDelete()}
                okText="清空"
                okButtonProps={{ danger: true }}
              >
                <Button danger icon={<DeleteOutlined />} loading={deleting}>清空规则</Button>
              </Popconfirm>
            )}
          </Space>
        }
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="规则编辑完成后，请前往「知识图谱」页构建并确认图谱"
        description="本页不再内置「构建图谱」入口，构建动作统一由知识图谱页发起，以保证图谱数据与确认流程的唯一归属。"
      />

      <Tabs
        defaultActiveKey="matrix"
        items={[
          {
            key: 'matrix',
            label: '二维表格视图',
            children: (
              <Card loading={loading}>
                {renderMatrix()}
              </Card>
            ),
          },
          {
            key: 'list',
            label: '规则列表',
            children: (
              <Card loading={loading}>
                {/* 子标签页：按规则健康状态分类 */}
                <Tabs
                  activeKey={activeRuleFilter}
                  onChange={(k) => handleFilterChange(k as RuleFilterKey)}
                  size="small"
                  style={{ marginBottom: 12 }}
                  items={[
                    { key: 'all', label: `全部（${summary.total_rules}）` },
                    {
                      key: 'healthy',
                      label: (
                        <Tooltip title="无未处理缺陷的规则（已确认的规则可直接参与图谱构建）">
                          <span style={{ color: '#52c41a' }}>
                            {DEFECT_BUCKET_LABEL.healthy}（{summary.healthy}）
                          </span>
                        </Tooltip>
                      ),
                    },
                    {
                      key: 'conflict',
                      label: (
                        <Tooltip title="存在未处理的语义冲突缺陷（逻辑矛盾/边界冲突/冗余）的规则条数">
                          <span style={{ color: '#ff4d4f' }}>
                            {DEFECT_BUCKET_LABEL.conflict}（{summary.conflict}）
                          </span>
                        </Tooltip>
                      ),
                    },
                    {
                      key: 'error',
                      label: (
                        <Tooltip title="存在未处理的错误级缺陷的规则条数">
                          <span style={{ color: '#ff4d4f' }}>
                            {DEFECT_BUCKET_LABEL.error}（{summary.error}）
                          </span>
                        </Tooltip>
                      ),
                    },
                    {
                      key: 'warning',
                      label: (
                        <Tooltip title="存在未处理的警告级缺陷的规则条数">
                          <span style={{ color: '#fa8c16' }}>
                            {DEFECT_BUCKET_LABEL.warning}（{summary.warning}）
                          </span>
                        </Tooltip>
                      ),
                    },
                    {
                      key: 'info',
                      label: (
                        <Tooltip title="存在未处理的提示级缺陷的规则条数（提示不影响规则执行）">
                          <span style={{ color: '#1890ff' }}>
                            {DEFECT_BUCKET_LABEL.info}（{summary.info}）
                          </span>
                        </Tooltip>
                      ),
                    },
                  ]}
                />

                {/* 操作用工具栏：只要当前视图还有"未确认 / 有未处理缺陷"的规则就提供一键处理 */}
                {canManageRules && <Space style={{ marginBottom: 12 }}>
                  {actionableRules.length > 0 && (
                    <Popconfirm
                      title={`确认并启用当前视图中的 ${actionableRules.length} 条规则？确认即视为认可其全部缺陷，缺陷将标记为已处理，规则进入「正常规则」并参与图谱构建`}
                      onConfirm={async () => {
                        await handleBatchConfirm(actionableRules.map((r) => r.id))
                        await load()
                      }}
                    >
                      <Button type="primary" icon={<CheckOutlined />} loading={confirming}>
                        一键确认并启用（{actionableRules.length}）
                      </Button>
                    </Popconfirm>
                  )}
                  <Popconfirm
                    title={`确定删除选中的 ${selectedRowKeys.length} 条规则？此操作不可恢复`}
                    onConfirm={() => handleBatchDelete(selectedRowKeys)}
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    disabled={selectedRowKeys.length === 0}
                  >
                    <Button danger icon={<DeleteOutlined />} loading={deleting} disabled={selectedRowKeys.length === 0}>
                      删除选中 ({selectedRowKeys.length})
                    </Button>
                  </Popconfirm>
                  {selectedRowKeys.length > 0 && (
                    <Button onClick={() => setSelectedRowKeys([])}>取消选择</Button>
                  )}
                  <Button icon={<WarningOutlined />} loading={conflictDetecting} onClick={() => handleDetectConflicts()}>
                    检测冲突
                  </Button>
                </Space>}

                {/* 正常规则提示：已确认的规则可直接参与构建；未确认的需显式确认 */}
                {activeRuleFilter === 'healthy' && (
                  <>
                    <Alert
                      type="success"
                      showIcon
                      icon={<CheckOutlined />}
                      style={{ marginBottom: 12 }}
                      message="以下规则没有未处理缺陷；已确认并启用的规则可直接参与图谱构建和文档审查"
                    />
                    {rules.some((r) => r.status !== 'confirmed') && (
                      <Alert
                        type="warning"
                        showIcon
                        style={{ marginBottom: 12 }}
                        message={`其中 ${rules.filter((r) => r.status !== 'confirmed').length} 条规则尚未确认，不会参与图谱构建；请点击「一键确认并启用」`}
                      />
                    )}
                  </>
                )}

                {/* 问题规则提示：确认即认可缺陷并使其进入「正常规则」 */}
                {activeRuleFilter !== 'healthy' && activeRuleFilter !== 'all' && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message={
                      activeRuleFilter === 'conflict'
                        ? '以下规则存在未处理的语义冲突，默认为禁用状态。确认即视为认可该冲突，缺陷将标记为已处理并进入「正常规则」；建议先修正规则文本再确认'
                        : activeRuleFilter === 'info'
                          ? '以下规则存在未处理的提示信息（不影响规则执行）。确认后提示将标记为已处理，规则进入「正常规则」'
                          : '以下规则存在未处理的缺陷，默认为禁用状态。确认即视为认可该缺陷，缺陷将标记为已处理并进入「正常规则」，确认后的规则将参与图谱构建'
                    }
                  />
                )}

                <Table
                  dataSource={rules}
                  columns={ruleColumns}
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 20 }}
                  rowSelection={canManageRules ? {
                    selectedRowKeys,
                    onChange: (keys) => setSelectedRowKeys(keys as string[]),
                  } : undefined}
                />
              </Card>
            ),
          },
          {
            key: 'snapshots',
            label: '规则快照历史',
            children: (
              <Card loading={loading}>
                <List
                  dataSource={snapshots}
                  renderItem={(s) => (
                    <List.Item>
                      <List.Item.Meta
                        title={`${dayjs(s.snapshot_time).format('YYYY-MM-DD HH:mm:ss')} - ${s.rule_count} 条规则`}
                        description={
                          <Space>
                            {s.graph_id && <Tag color="blue">graph: {s.graph_id.slice(0, 24)}...</Tag>}
                            <Tag>节点 {s.node_count ?? '-'}</Tag>
                            <Tag>关系 {s.edge_count ?? '-'}</Tag>
                            {s.operator && <Tag>操作人: {s.operator}</Tag>}
                            {s.note && <Text type="secondary">{s.note}</Text>}
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              </Card>
            ),
          },
          {
            key: 'skills',
            label: '解析 Skill',
            children: <SkillTab ruleSetId={currentId!} readOnly={!canManageRules} />,
          },
        ]}
      />

      <Modal
        title={editing ? '编辑规则' : '新增规则'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={640}
        confirmLoading={saving}
        okButtonProps={{ disabled: saving }}
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="doc_type" label="文件类型" tooltip="可选：仅规则明确指向单一文件类型时填写；跨文件/整批规则留空">
                <Select
                  allowClear
                  placeholder="不填则视为整批/全部"
                  options={docTypes.map((d) => ({ value: d.name, label: d.name }))}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="check_category" label="检查项" tooltip="可选：规则核心意图无法归入已知检查项时留空，解析时自动发现新意图">
                <Select
                  allowClear
                  placeholder="不填则视为未分类"
                  options={(checkCategories.length > 0 ? checkCategories : FALLBACK_CHECK_CATEGORIES).map((c) => ({ value: c, label: c }))}
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="rule_text" label="规则文本（自然语言）" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="如：报关单数量应不大于委托单数量..." />
          </Form.Item>
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item name="tolerance_amount" label="金额容差(%)">
                <InputNumber min={0} max={100} style={{ width: '100%' }} placeholder="如 5" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="tolerance_weight" label="重量容差(kg)">
                <InputNumber min={0} style={{ width: '100%' }} placeholder="如 0.5" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="tolerance_time" label="时间容差(天)">
                <InputNumber min={0} style={{ width: '100%' }} placeholder="如 0" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="allow_same_day" label="允许同日" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="priority" label="优先级（数字越小越先）">
                <InputNumber min={1} max={999} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="enabled" label="启用" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title="批量导入规则"
        open={importOpen}
        onCancel={() => !importing && setImportOpen(false)}
        closable={!importing}
        maskClosable={!importing}
        width={760}
        footer={null}
        destroyOnClose
      >
        {/* importing 时全屏遮罩 + 阶段提示（解决"点了没反馈"的问题） */}
        <Spin spinning={importing} tip={importStage ? STAGE_TIP[importStage] : '处理中...'} size="large">
          {importError && (
            <Alert
              type="error"
              showIcon
              closable
              onClose={() => setImportError(null)}
              style={{ marginBottom: 12 }}
              message="导入失败"
              description={importError}
            />
          )}
          <Tabs
          activeKey={importMode}
          onChange={(k) => setImportMode(k as 'text' | 'file')}
          items={[
            {
              key: 'text',
              label: <span><ImportOutlined /> 文本导入</span>,
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="粘贴自然语言规则清单，系统会调用大模型解析为结构化规则并自动入库"
                    description={
                      <span style={{ fontSize: 12 }}>
                        已知文件类型（建议复用，新类型由解析自动发现并登记）：{docTypes.map((d) => d.name).join('、')}<br />
                        已知检查项（建议复用，新意图由解析自动发现）：{(checkCategories.length > 0 ? checkCategories : FALLBACK_CHECK_CATEGORIES).join('、')}
                      </span>
                    }
                  />
                  <div style={{ marginBottom: 12 }}>
                    <Space style={{ width: '100%' }} align="start">
                      <Select
                        mode="multiple"
                        style={{ minWidth: 360 }}
                        placeholder="选择应用的 Skill（不选则使用默认配置）"
                        value={importSkillIds}
                        onChange={setImportSkillIds}
                        options={allSkills
                          .filter((s) => s.enabled)
                          .map((s) => ({
                            value: s.id,
                            label: `${s.name}${s.is_builtin ? ' (内置)' : ''}`,
                          }))}
                        allowClear
                      />
                      {allSkills.length > 0 && (
                        <Text type="secondary" style={{ fontSize: 12, lineHeight: '32px' }}>
                          已选 {importSkillIds.length} 个
                          {allSkills.some((s) => !s.enabled)
                            ? `（${allSkills.filter((s) => !s.enabled).length} 个已停用，不参与导入）`
                            : ''}
                        </Text>
                      )}
                    </Space>
                  </div>
                  <Input.TextArea
                    rows={10}
                    value={importText}
                    onChange={(e) => setImportText(e.target.value)}
                    placeholder={`请粘贴规则清单，例如：\n1. 代理协议的协议方应与委托出口确认单的委托方一致\n2. 出口报关单数量应不大于委托出口确认单数量（金额容差5%）\n3. 代理协议必须双方回签用印\n4. 委托出口确认单签订日期应在代理协议有效期内`}
                    disabled={importing}
                  />
                  <div style={{ marginTop: 12, textAlign: 'right' }}>
                    <Space>
                      <Button onClick={() => setImportOpen(false)} disabled={importing}>关闭</Button>
                      <Button type="primary" loading={importing} onClick={handleImport}>
                        开始解析并导入
                      </Button>
                    </Space>
                  </div>
                  {importResult && (
                    <div style={{ marginTop: 12 }}>
                      <Space>
                        <Tag color="blue">解析 {importResult.total} 条</Tag>
                        <Tag color="green">成功 {importResult.imported} 条</Tag>
                        {importResult.skipped > 0 && <Tag color="orange">跳过 {importResult.skipped} 条</Tag>}
                      </Space>
                      {importResult.errors.length > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message="跳过的规则及原因"
                          description={
                            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
                              {importResult.errors.map((err, i) => <li key={i}>{err}</li>)}
                            </ul>
                          }
                        />
                      )}
                      {(importResult.import_warnings?.length ?? 0) > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message={
                            importResult.source_coverage
                              ? `导入校验提示（源表 ${importResult.source_coverage.expected_rows} 行，覆盖 ${importResult.source_coverage.covered_rows} 行）`
                              : '导入校验提示'
                          }
                          description={
                            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
                              {(importResult.import_warnings || []).map((warn, i) => <li key={i}>{warn}</li>)}
                            </ul>
                          }
                        />
                      )}
                      {importResult.conflict_report && importResult.conflict_report.total_defects > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          icon={<WarningOutlined />}
                          style={{ marginTop: 8 }}
                          message={
                            <span>
                             缺陷检测报告：{importResult.conflict_report.by_severity.error > 0 && <Tag color="red">{importResult.conflict_report.by_severity.error} 错误</Tag>}{importResult.conflict_report.by_severity.warning > 0 && <Tag color="orange">{importResult.conflict_report.by_severity.warning} 警告</Tag>}{importResult.conflict_report.by_severity.info > 0 && <Tag color="blue">{importResult.conflict_report.by_severity.info} 提示</Tag>}
                              <Text type="secondary" style={{ fontSize: 12 }}>（回到规则列表可查看详情）</Text>
                            </span>
                          }
                        />
                      )}
                      {importResult.rules.length > 0 && <ImportResultRules rules={importResult.rules} />}
                    </div>
                  )}
                </>
              ),
            },
            {
              key: 'file',
              label: <span><FileTextOutlined /> 文件导入</span>,
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="上传规则描述文档，后端会先解析为文本，再调用大模型解析为结构化规则"
                    description={
                      <span style={{ fontSize: 12 }}>
                        支持格式：PDF、Excel(.xlsx/.xls)、Word(.docx)、Markdown(.md)、文本(.txt)<br />
                        已知文件类型（建议复用，新类型由解析自动发现并登记）：{docTypes.map((d) => d.name).join('、')} · 已知检查项（建议复用，新意图由解析自动发现）：{(checkCategories.length > 0 ? checkCategories : FALLBACK_CHECK_CATEGORIES).join('、')}
                      </span>
                    }
                  />
                  <div style={{ marginBottom: 12 }}>
                    <Space style={{ width: '100%' }} align="start">
                      <Select
                        mode="multiple"
                        style={{ minWidth: 360 }}
                        placeholder="选择应用的 Skill（不选则使用默认配置）"
                        value={importSkillIds}
                        onChange={setImportSkillIds}
                        options={allSkills.map((s) => ({
                          value: s.id,
                          label: `${s.name}${s.is_builtin ? ' (内置)' : ''}`,
                        }))}
                        allowClear
                        disabled={importing}
                      />
                      {allSkills.length > 0 && (
                        <Text type="secondary" style={{ fontSize: 12, lineHeight: '32px' }}>
                          已选 {importSkillIds.length} 个
                        </Text>
                      )}
                </Space>
              </div>
              {importTask && importTask.status !== 'done' && importTask.status !== 'error' && (
                <Card size="small" style={{ marginTop: 12 }} title={<Text strong>{importTask.message || '正在处理…'}</Text>}>
                  <Progress
                    percent={computeImportProgress(importTask)}
                    status="active"
                    format={() => describeImportProgress(importTask)}
                  />
                  <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
                    {importTask.file_name} · 状态：{statusLabel(importTask.status)}
                  </div>
                </Card>
              )}
              <Dragger {...fileUploadProps} disabled={importing}>
                    <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                    <p className="ant-upload-text">{importing ? '解析中...' : '点击或拖拽文件到此区域'}</p>
                    <p className="ant-upload-hint">单文件上传，解析后可查看提取的文本预览</p>
                  </Dragger>
                  <div style={{ marginTop: 12, textAlign: 'right' }}>
                    <Space>
                      <Button onClick={() => setImportOpen(false)} disabled={importing}>关闭</Button>
                      <Button type="primary" loading={importing} onClick={handleImportFile} disabled={!importFile}>
                        开始解析并导入
                      </Button>
                    </Space>
                  </div>
                  {fileImportResult && (
                    <div style={{ marginTop: 12 }}>
                      <Space wrap>
                        <Tag color="blue">解析 {fileImportResult.total} 条</Tag>
                        <Tag color="green">成功 {fileImportResult.imported} 条</Tag>
                        {fileImportResult.skipped > 0 && <Tag color="orange">跳过 {fileImportResult.skipped} 条</Tag>}
                        {typeof fileImportResult.conflict_detected === 'number' && fileImportResult.conflict_detected > 0 && (
                          <Tag color="red">冲突 {fileImportResult.conflict_detected} 个</Tag>
                        )}
                      </Space>
                      {fileImportResult.errors.length > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message="跳过的规则及原因"
                          description={
                            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
                              {fileImportResult.errors.map((err, i) => <li key={i}>{err}</li>)}
                            </ul>
                          }
                        />
                      )}
                      {(fileImportResult.import_warnings?.length ?? 0) > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message={
                            fileImportResult.source_coverage
                              ? `导入校验提示（源表 ${fileImportResult.source_coverage.expected_rows} 行，覆盖 ${fileImportResult.source_coverage.covered_rows} 行）`
                              : '导入校验提示'
                          }
                          description={
                            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
                              {(fileImportResult.import_warnings || []).map((warn, i) => <li key={i}>{warn}</li>)}
                            </ul>
                          }
                        />
                      )}
                      {fileImportResult.conflict_report && fileImportResult.conflict_report.total_defects > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          icon={<WarningOutlined />}
                          style={{ marginTop: 8 }}
                          message={
                            <span>
                             缺陷检测报告：{fileImportResult.conflict_report.by_severity.error > 0 && <Tag color="red">{fileImportResult.conflict_report.by_severity.error} 错误</Tag>}{fileImportResult.conflict_report.by_severity.warning > 0 && <Tag color="orange">{fileImportResult.conflict_report.by_severity.warning} 警告</Tag>}{fileImportResult.conflict_report.by_severity.info > 0 && <Tag color="blue">{fileImportResult.conflict_report.by_severity.info} 提示</Tag>}
                              <Text type="secondary" style={{ fontSize: 12 }}>（回到规则列表可查看详情）</Text>
                            </span>
                          }
                        />
                      )}
                      {fileImportResult.rules.length > 0 && <ImportResultRules rules={fileImportResult.rules} />}
                    </div>
                  )}
                </>
              ),
            },
          ]}
        />
        </Spin>
      </Modal>

      {/* 冲突详情弹窗（"检测冲突"按钮专用，详细报告） */}
      <Modal
        title={`语义冲突检测报告（${conflictData.length} 个冲突）`}
        open={conflictModalOpen}
        onCancel={() => setConflictModalOpen(false)}
        footer={<Button onClick={() => setConflictModalOpen(false)}>关闭</Button>}
        width={800}
      >
        {conflictData.map((group, gi) => {
          // 查找冲突规则在表格里的行 key
          const ruleRows = group.rules.map((r) => r.id)
          return (
            <Card
              key={gi}
              size="small"
              style={{ marginBottom: 12 }}
              title={
                <Space>
                  <Tag color={group.severity === 'error' ? 'red' : group.severity === 'warning' ? 'orange' : 'blue'}>
                    {group.type === 'logical_contradiction' ? '逻辑矛盾' : group.type === 'boundary_overlap' ? '边界冲突' : '冗余'}
                  </Tag>
                  <Text strong>{group.description}</Text>
                </Space>
              }
            >
              <List
                size="small"
                dataSource={group.rules}
                renderItem={(r) => (
                  <List.Item
                    actions={[
                      <Button
                        size="small"
                        type="link"
                        onClick={() => {
                          // 跳转到该规则（选中并滚动）
                          setSelectedRowKeys([r.id])
                          setConflictModalOpen(false)
                        }}
                      >
                        定位
                      </Button>,
                    ]}
                  >
                      <List.Item.Meta
                      title={
                        <Space size={4}>
                          <Tag color="blue">{r.rule_code}</Tag>
                          <Tag>{r.doc_type || GLOBAL_DOC_TYPE} / {r.check_category || UNCATEGORIZED}</Tag>
                        </Space>
                      }
                      description={<Text style={{ fontSize: 12 }}>{r.rule_text}</Text>}
                    />
                  </List.Item>
                )}
              />
            </Card>
          )
        })}
        {conflictData.length === 0 && (
          <Alert type="success" showIcon message="未检测到语义冲突" />
        )}
      </Modal>

      {/* 统一缺陷处理侧边栏（从缺陷概览标签点击进入，含分页防卡顿） */}
      <Drawer
        title="规则缺陷处理"
        open={defectDrawerOpen}
        onClose={() => setDefectDrawerOpen(false)}
        width={720}
        extra={<Button size="small" onClick={() => setDefectDrawerOpen(false)}>关闭</Button>}
      >
        <Tabs
          activeKey={defectDrawerTab}
          onChange={(k) => {
            if (k === 'conflict') {
              // 切换到冲突 tab 时如有数据直接显示，无数据触发检测
              if (canManageRules && conflictData.length === 0 && !conflictDetecting) {
                handleDetectConflicts(true)
              }
              return
            }
            setDefectDrawerTab(k as 'error' | 'warning')
          }}
          items={[
            ...(openDefectCounts.conflict > 0
              ? [{
                  key: 'conflict' as const,
                  label: `冲突（${openDefectCounts.conflict}）`,
                  children: (
                    <div>
                      {conflictDetecting ? (
                        <div style={{ textAlign: 'center', padding: 40 }}>
                          <Spin tip="正在检测语义冲突..."><div style={{ padding: 20 }} /></Spin>
                        </div>
                      ) : conflictData.length === 0 ? (
                        <Alert type="info" showIcon message={canManageRules ? '点击「检测冲突」按钮查看详细报告' : '当前账号无冲突检测权限'} />
                      ) : (
                        <div>
                          <Alert
                            type="info"
                            showIcon
                            style={{ marginBottom: 12 }}
                            message={`共 ${conflictData.length} 个冲突组，涉及 ${new Set(conflictData.flatMap((g) => g.rule_ids)).size} 条规则`}
                          />
                          <Space style={{ marginBottom: 12 }}>
                            <Button size="small" onClick={() => { setDefectDrawerOpen(false); setConflictModalOpen(true) }}>
                              查看详细报告
                            </Button>
                            {canManageRules && (
                              <Button size="small" loading={conflictDetecting} onClick={() => handleDetectConflicts(true)}>
                                重新检测
                              </Button>
                            )}
                          </Space>
                          <Table
                            dataSource={conflictData}
                            rowKey={(_, i) => `c-${i}`}
                            size="small"
                            pagination={{ pageSize: 10, showSizeChanger: true, pageSizeOptions: ['5', '10', '20'], size: 'small' }}
                            columns={[
                              {
                                title: '类型', width: 90,
                                render: (_: unknown, group: ConflictGroup) => (
                                  <Tag color={group.severity === 'error' ? 'red' : group.severity === 'warning' ? 'orange' : 'blue'}>
                                    {group.type === 'logical_contradiction' ? '逻辑矛盾' : group.type === 'boundary_overlap' ? '边界冲突' : '冗余'}
                                  </Tag>
                                ),
                              },
                              { title: '描述', dataIndex: 'description', ellipsis: true },
                              {
                                title: '规则号', width: 150,
                                render: (_: unknown, group: ConflictGroup) => (
                                  <Space size={2} wrap>
                                    {(group.rule_codes || []).map((code) => (
                                      <Tag key={code} color="blue">{code}</Tag>
                                    ))}
                                  </Space>
                                ),
                              },
                              {
                                title: '涉及规则', width: 80,
                                render: (_: unknown, group: ConflictGroup) => <Tag>{group.rules.length} 条</Tag>,
                              },
                              {
                                title: '操作', width: 80,
                                render: (_: unknown, group: ConflictGroup) => (
                                  <Button
                                    size="small"
                                    type="link"
                                    onClick={() => {
                                      setSelectedRowKeys(group.rules.map((r: Rule) => r.id))
                                      setDefectDrawerOpen(false)
                                    }}
                                  >
                                    定位
                                  </Button>
                                ),
                              },
                            ]}
                          />
                        </div>
                      )}
                    </div>
                  ),
                }]
              : []),
            ...(openDefectCounts.error > 0
              ? [{
                  key: 'error' as const,
                  label: `错误（${openDefectCounts.error}）`,
                  children: renderDefectTable('error', 'e'),
                }]
              : []),
            ...(openDefectCounts.warning > 0
              ? [{
                  key: 'warning' as const,
                  label: `警告（${openDefectCounts.warning}）`,
                  children: renderDefectTable('warning', 'w'),
                }]
              : []),
          ]}
        />
      </Drawer>
    </div>
  )
}
