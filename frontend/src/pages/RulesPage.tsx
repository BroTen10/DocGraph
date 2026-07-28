import { useState, useEffect, useMemo } from 'react'
import type { UploadProps, UploadFile } from 'antd'
import {
  Card, Row, Col, Table, Tag, Button, Modal, Form, Input, InputNumber, Switch,
  Select, Space, message, Typography, Tooltip, Popconfirm, Tabs, List, Spin, Alert,
  Upload, Drawer,
} from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, ThunderboltOutlined, HistoryOutlined, ImportOutlined, InboxOutlined, FileTextOutlined, SettingOutlined, CheckOutlined, WarningOutlined } from '@ant-design/icons'
import { rulesApi, graphApi, constantsApi, ruleSetsApi } from '../api/client'
import type { Rule, RuleSet, RuleSnapshot, DocTypeMeta, ConstantsResponse, RuleImportResponse, RuleDocumentImportResponse } from '../types'
import PageHeader from '../components/PageHeader'
import { useRuleSet } from '../context/RuleSetContext'
import SkillTab from './SkillTab'
import dayjs from 'dayjs'

const { Text } = Typography
const { Dragger } = Upload
const CHECK_CATEGORIES = ['齐套性', '基础判断', '信息准确性', '时间逻辑']
const FILE_ACCEPT = '.pdf,.xlsx,.xls,.docx,.md,.txt'

export default function RulesPage() {
  const { currentId } = useRuleSet()
  const [rules, setRules] = useState<Rule[]>([])
  const [snapshots, setSnapshots] = useState<RuleSnapshot[]>([])
  const [docTypes, setDocTypes] = useState<DocTypeMeta[]>([])
  const [ruleSet, setRuleSet] = useState<RuleSet | null>(null)  // 当前规则集详情（含 doc_types / check_categories）
  const [loading, setLoading] = useState(false)
  const [building, setBuilding] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Rule | null>(null)
  const [form] = Form.useForm()
  const [importOpen, setImportOpen] = useState(false)
  const [importMode, setImportMode] = useState<'text' | 'file'>('text')
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<RuleImportResponse | null>(null)
  // 文件导入
  const [importFile, setImportFile] = useState<File | null>(null)
  const [fileImportResult, setFileImportResult] = useState<RuleDocumentImportResponse | null>(null)
  // 导入阶段提示（解决"点了没反馈"的问题）
  const [importStage, setImportStage] = useState<'parsing' | 'llm' | 'saving' | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  // 批量确认
  const [confirming, setConfirming] = useState(false)
  // Skill 选择
  const [allSkills, setAllSkills] = useState<Array<{ id: string; name: string; is_builtin: boolean }>>([])
  const [importSkillIds, setImportSkillIds] = useState<string[]>([])
  // 冲突检测
  const [conflictDetecting, setConflictDetecting] = useState(false)
  const [conflictModalOpen, setConflictModalOpen] = useState(false)
  const [conflictData, setConflictData] = useState<Array<{ rule_ids: string[]; type: string; severity: string; description: string; rules: Rule[] }>>([])
  // 缺陷详情侧边栏（统一展示冲突/错误/警告）
  const [defectDrawerOpen, setDefectDrawerOpen] = useState(false)
  const [defectDrawerTab, setDefectDrawerTab] = useState<'conflict' | 'error' | 'warning'>('error')

  const STAGE_TIP: Record<NonNullable<typeof importStage>, string> = {
    parsing: '正在解析文档为文本（PDF/Excel/Word）...',
    llm: '正在调用大模型提取规则（约 20-30 秒）...',
    saving: '正在入库，请稍候...',
  }

  const load = async () => {
    if (!currentId) return
    setLoading(true)
    try {
      const [r, s, c, rs] = await Promise.all([
        rulesApi.list(currentId),
        rulesApi.listSnapshots(currentId),
        constantsApi.docTypes(),
        ruleSetsApi.get(currentId),
      ])
      setRules(r)
      setSnapshots(s)
      setDocTypes(c.doc_types)
      setRuleSet(rs)
    } catch (e: any) {
      message.error('加载失败: ' + (e?.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [currentId])

  // 二维表格：行=文件类型，列=检查项
  const matrix = useMemo(() => {
    const m: Record<string, Record<string, Rule[]>> = {}
    rules.forEach((r) => {
      if (!m[r.doc_type]) m[r.doc_type] = {}
      if (!m[r.doc_type][r.check_category]) m[r.doc_type][r.check_category] = []
      m[r.doc_type][r.check_category].push(r)
    })
    return m
  }, [rules])

  // 动态文件类型列表（种子 + 规则集声明 + 实际规则中的类型，去重）

  // 动态文件类型列表（种子 + 规则集声明 + 实际规则中的类型，去重）
  const effectiveDocTypes = useMemo(() => {
    const seed: string[] = docTypes.map((d) => d.name)
    const fromSet: string[] = ruleSet?.doc_types || []
    const fromRules: string[] = [...new Set(rules.map((r) => r.doc_type))]
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
    const seed: string[] = [...CHECK_CATEGORIES]
    const fromSet: string[] = ruleSet?.check_categories || []
    const fromRules: string[] = [...new Set(rules.map((r) => r.check_category))]
    return [...new Set([...seed, ...fromSet, ...fromRules])]
  }, [ruleSet, rules])

  // 计算缺陷统计
  const defectSummary = useMemo(() => {
    const summary = { error: 0, warning: 0, info: 0, total: 0, conflict: 0 }
    rules.forEach((r) => {
      const defects = (r as any).defects || []
      defects.forEach((d: any) => {
        const sev = d.severity || 'info'
        // 冲突类单独计数
        if (['logical_contradiction', 'boundary_overlap', 'redundant'].includes(d.type)) {
          summary.conflict++
        }
        if (sev === 'error') summary.error++
        else if (sev === 'warning') summary.warning++
        else summary.info++
        summary.total++
      })
    })
    return summary
  }, [rules])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ enabled: true, priority: 100, tolerance: {} })
    setModalOpen(true)
  }

  const openEdit = (rule: Rule) => {
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

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      // 组装容差参数
      const tolerance: Record<string, unknown> = {}
      if (values.tolerance_amount != null) tolerance.amount_percent = values.tolerance_amount
      if (values.tolerance_weight != null) tolerance.weight_kg = values.tolerance_weight
      if (values.tolerance_time != null) tolerance.time_days = values.tolerance_time
      if (values.allow_same_day != null) tolerance.allow_same_day = values.allow_same_day

      const payload = {
        doc_type: values.doc_type,
        check_category: values.check_category,
        rule_text: values.rule_text,
        tolerance,
        enabled: values.enabled,
        priority: values.priority,
      }
      if (editing) {
        await rulesApi.update(editing.id, payload)
        message.success('规则已更新')
      } else {
        await rulesApi.create(currentId!, payload as any)
        message.success('规则已新增')
      }
      setModalOpen(false)
      await load()
    } catch (e: any) {
      if (e?.errorFields) return // 表单校验错误
      message.error('保存失败: ' + (e?.message || e))
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await rulesApi.delete(id)
      message.success('已删除')
      await load()
    } catch (e: any) {
      message.error('删除失败: ' + (e?.message || e))
    }
  }

  const handleBuild = async (autoAll = false) => {
    setBuilding(true)
    try {
      const resp = await graphApi.build(currentId!, autoAll)
      message.success(`${resp.message}（节点 ${resp.node_count} / 关系 ${resp.edge_count}）`)
      await load()
    } catch (e: any) {
      message.error('图谱构建失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setBuilding(false)
    }
  }

  /** 批量删除规则：传 ids 仅删指定；不传则清空当前规则集全部规则 */
  const handleBatchDelete = async (ids?: string[]) => {
    if (!currentId) return
    setDeleting(true)
    try {
      const resp = await rulesApi.batchDelete(currentId, ids)
      message.success(`已删除 ${resp.deleted} 条规则`)
      setSelectedRowKeys([])
      await load()
    } catch (e: any) {
      message.error('删除失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setDeleting(false)
    }
  }

  /** 批量确认：传 ids 仅确认指定；不传则确认所有 pending 规则 */
  const handleBatchConfirm = async (ids?: string[]) => {
    if (!currentId) return
    setConfirming(true)
    try {
      const resp = await rulesApi.confirmBatch(currentId, ids)
      message.success(resp.message)
      await load()
    } catch (e: any) {
      message.error('确认失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setConfirming(false)
    }
  }

  const handleDetectConflicts = async (openInDrawer = false) => {
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
    } catch (e: any) {
      message.error('冲突检测失败: ' + (e?.response?.data?.detail || e?.message || e))
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
        setAllSkills(skills.map((s) => ({ id: s.id, name: s.name, is_builtin: s.is_builtin })))
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
      } else {
        message.warning(`未导入任何规则，跳过 ${resp.skipped} 条`)
      }
    } catch (e: any) {
      const msg = '导入失败: ' + (e?.response?.data?.detail || e?.message || e)
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
    // 阶段 1：上传+解析（典型 1-3 秒）
    await new Promise((r) => setTimeout(r, 50)) // 让 UI 渲染出阶段 1
    setImportStage('llm')
    try {
      const resp = await rulesApi.importDocument(currentId!, importFile, importSkillIds.length ? importSkillIds : undefined)
      setImportStage('saving')
      setFileImportResult(resp)
      if (resp.imported > 0) {
        message.success(`导入完成：成功 ${resp.imported} 条，跳过 ${resp.skipped} 条`)
        await load()
      } else {
        message.warning(`未导入任何规则，跳过 ${resp.skipped} 条`)
      }
    } catch (e: any) {
      const msg = '文件导入失败: ' + (e?.response?.data?.detail || e?.message || e)
      setImportError(msg)
      message.error(msg)
    } finally {
      setImporting(false)
      setImportStage(null)
    }
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
            originFileObj: importFile as any,
          } as UploadFile,
        ]
      : [],
    beforeUpload: (file) => {
      setImportFile(file as File)
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
    return (
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={thStyle}>文件类型 \ 检查项</th>
            {effectiveCheckCategories.map((c) => <th key={c} style={thStyle}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {effectiveDocTypes.map((dt) => {
            const meta = docTypes.find((d) => d.name === dt)
            return (
            <tr key={dt}>
              <td style={tdStyle}>
                <Text strong>{dt}</Text>
                {meta?.is_required && <Tag color="red" style={{ marginLeft: 4 }}>必备</Tag>}
                {meta?.is_optional && <Tag color="blue" style={{ marginLeft: 4 }}>非必备</Tag>}
              </td>
              {effectiveCheckCategories.map((cc) => {
                const cellRules = matrix[dt]?.[cc] || []
                return (
                  <td key={cc} style={tdStyle} onClick={() => cellRules.length > 0 && openEdit(cellRules[0])}>
                    {cellRules.length === 0 ? (
                      <Text type="secondary">-</Text>
                    ) : (
                      <Tooltip title={cellRules.map((r) => r.rule_text).join('\n')}>
                        <Tag color={cellRules[0].enabled ? 'blue' : 'default'} style={{ cursor: 'pointer' }}>
                          {cellRules.length} 条
                        </Tag>
                      </Tooltip>
                    )}
                  </td>
                )
              })}
            </tr>
          )})}
        </tbody>
      </table>
    )
  }

  const thStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: 8, background: '#fafafa', textAlign: 'center' }
  const tdStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: 8, textAlign: 'center', cursor: 'pointer' }

  const ruleColumns = [
    { title: '文件类型', dataIndex: 'doc_type', key: 'doc_type', width: 140 },
    { title: '检查项', dataIndex: 'check_category', key: 'check_category', width: 120 },
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
      title: '缺陷', key: 'defects', width: 140,
      render: (_: unknown, row: Rule) => {
        const defects = row.defects || []
        if (defects.length === 0) return <Text type="secondary">-</Text>
        const errors = defects.filter((d) => d.severity === 'error')
        const warnings = defects.filter((d) => d.severity === 'warning')
        const infos = defects.filter((d) => d.severity === 'info')
        return (
          <Space size={4} wrap>
            {errors.length > 0 && (
              <Tooltip title={errors.map((d) => d.description).join('\n')}>
                <Tag color="red">{errors.length} 错误</Tag>
              </Tooltip>
            )}
            {warnings.length > 0 && (
              <Tooltip title={warnings.map((d) => d.description).join('\n')}>
                <Tag color="orange">{warnings.length} 警告</Tag>
              </Tooltip>
            )}
            {infos.length > 0 && (
              <Tooltip title={infos.map((d) => d.description).join('\n')}>
                <Tag color="blue">{infos.length} 提示</Tag>
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
        <Switch
          size="small"
          checked={v}
          onChange={async (checked) => {
            try {
              await rulesApi.update(row.id, { enabled: checked })
              await load()
            } catch (e: any) {
              message.error('更新失败: ' + (e?.message || e))
            }
          }}
        />
      ),
    },
    {
      title: '操作', key: 'action', width: 160,
      render: (_: unknown, row: Rule) => (
        <Space>
          {row.status !== 'confirmed' && (
            <Popconfirm title="确认该条规则？" onConfirm={() => handleBatchConfirm([row.id])}>
              <Button size="small" type="primary" ghost icon={<CheckOutlined />}>确认</Button>
            </Popconfirm>
          )}
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
          <Popconfirm title="确定删除该规则？" onConfirm={() => handleDelete(row.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
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
            <Button icon={<PlusOutlined />} onClick={openCreate}>新增规则</Button>
            <Button icon={<ImportOutlined />} onClick={openImport}>批量导入</Button>
            <Popconfirm title="一键自动确认全部规则（忽略置信度）？" onConfirm={() => handleBuild(true)}>
              <Button icon={<ThunderboltOutlined />} loading={building}>一键自动确认</Button>
            </Popconfirm>
            <Button icon={<WarningOutlined />} loading={conflictDetecting} onClick={() => handleDetectConflicts()}>检测冲突</Button>
            <Button icon={<HistoryOutlined />} onClick={load}>刷新</Button>
            <Popconfirm
              title={`确定清空当前规则集的全部 ${rules.length} 条规则？此操作不可恢复`}
              onConfirm={() => handleBatchDelete()}
              okText="清空"
              okButtonProps={{ danger: true }}
            >
              <Button danger icon={<DeleteOutlined />} loading={deleting}>清空规则</Button>
            </Popconfirm>
          </Space>
        }
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
                {defectSummary.total > 0 && (
                  <Alert
                    type="warning"
                    showIcon
                    icon={conflictDetecting ? <Spin size="small" /> : <WarningOutlined />}
                    style={{ marginBottom: 12, cursor: defectSummary.conflict > 0 ? 'pointer' : 'default' }}
                    onClick={() => defectSummary.conflict > 0 && handleDetectConflicts(true)}
                    message={
                      <span>
                        规则缺陷概览：
                        {defectSummary.conflict > 0 && <Tag color="red" style={{ marginLeft: 8, cursor: conflictDetecting ? 'not-allowed' : 'pointer' }} onClick={(e) => { e.stopPropagation(); handleDetectConflicts(true) }}>{defectSummary.conflict} 个冲突{conflictDetecting ? '（检测中...）' : ''}</Tag>}
                        {defectSummary.error > 0 && <Tag color="red" style={{ marginLeft: 4, cursor: 'pointer' }} onClick={(e) => { e.stopPropagation(); handleShowDefects('error') }}>{defectSummary.error} 个错误</Tag>}
                        {defectSummary.warning > 0 && <Tag color="orange" style={{ cursor: 'pointer' }} onClick={(e) => { e.stopPropagation(); handleShowDefects('warning') }}>{defectSummary.warning} 个警告</Tag>}
                        {defectSummary.info > 0 && <Tag color="blue">{defectSummary.info} 条提示</Tag>}
                        <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                          缺陷规则需手工确认修正
                        </Text>
                      </span>
                    }
                  />
                )}
                <Space style={{ marginBottom: 12 }}>
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
                  <Popconfirm
                    title="一键确认全部待定规则（忽略置信度）？"
                    onConfirm={() => handleBatchConfirm()}
                  >
                    <Button icon={<CheckOutlined />} loading={confirming}>
                      一键确认待定
                    </Button>
                  </Popconfirm>
                </Space>
                <Table
                  dataSource={rules}
                  columns={ruleColumns}
                  rowKey="id"
                  size="small"
                  pagination={{ pageSize: 20 }}
                  rowSelection={{
                    selectedRowKeys,
                    onChange: (keys) => setSelectedRowKeys(keys as string[]),
                  }}
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
            children: <SkillTab ruleSetId={currentId!} />,
          },
        ]}
      />

      <Modal
        title={editing ? '编辑规则' : '新增规则'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={640}
        confirmLoading={loading}
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="doc_type" label="文件类型" rules={[{ required: true }]}>
                <Select options={docTypes.map((d) => ({ value: d.name, label: d.name }))} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="check_category" label="检查项" rules={[{ required: true }]}>
                <Select options={CHECK_CATEGORIES.map((c) => ({ value: c, label: c }))} />
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
                        可用文件类型：{docTypes.map((d) => d.name).join('、')}<br />
                        可用检查项：{CHECK_CATEGORIES.join('、')}
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
                      />
                      {allSkills.length > 0 && (
                        <Text type="secondary" style={{ fontSize: 12, lineHeight: '32px' }}>
                          已选 {importSkillIds.length} 个
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
                        可用文件类型：{docTypes.map((d) => d.name).join('、')} · 可用检查项：{CHECK_CATEGORIES.join('、')}
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
                        <Tag color="purple">提取文本长度 {fileImportResult.extracted_text_length} 字符</Tag>
                        <Tag>来源: {fileImportResult.source_filename}</Tag>
                      </Space>
                      {fileImportResult.extracted_text_preview && (
                        <Card
                          size="small"
                          type="inner"
                          title={<Text type="secondary" style={{ fontSize: 12 }}>提取文本预览（前 500 字符）</Text>}
                          style={{ marginTop: 8 }}
                        >
                          <pre style={{
                            maxHeight: 160,
                            overflow: 'auto',
                            margin: 0,
                            padding: 8,
                            background: '#f6f8fa',
                            borderRadius: 4,
                            fontSize: 12,
                            fontFamily: 'monospace',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                          }}>
                            {fileImportResult.extracted_text_preview}
                          </pre>
                        </Card>
                      )}
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
                      title={<Tag color="blue">{r.doc_type} / {r.check_category}</Tag>}
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
              if (conflictData.length === 0 && !conflictDetecting) {
                handleDetectConflicts(true)
              }
              return
            }
            setDefectDrawerTab(k as 'error' | 'warning')
          }}
          items={[
            ...(defectSummary.conflict > 0
              ? [{
                  key: 'conflict' as const,
                  label: `冲突（${defectSummary.conflict}）`,
                  children: (
                    <div>
                      {conflictDetecting ? (
                        <div style={{ textAlign: 'center', padding: 40 }}>
                          <Spin tip="正在检测语义冲突..." />
                        </div>
                      ) : conflictData.length === 0 ? (
                        <Alert type="info" showIcon message="点击「检测冲突」按钮查看详细报告" />
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
                            <Button size="small" loading={conflictDetecting} onClick={() => handleDetectConflicts(true)}>
                              重新检测
                            </Button>
                          </Space>
                          <Table
                            dataSource={conflictData}
                            rowKey={(_, i) => `c-${i}`}
                            size="small"
                            pagination={{ pageSize: 10, showSizeChanger: true, pageSizeOptions: ['5', '10', '20'], size: 'small' }}
                            columns={[
                              {
                                title: '类型', width: 90,
                                render: (_: any, group: any) => (
                                  <Tag color={group.severity === 'error' ? 'red' : group.severity === 'warning' ? 'orange' : 'blue'}>
                                    {group.type === 'logical_contradiction' ? '逻辑矛盾' : group.type === 'boundary_overlap' ? '边界冲突' : '冗余'}
                                  </Tag>
                                ),
                              },
                              { title: '描述', dataIndex: 'description', ellipsis: true },
                              {
                                title: '涉及规则', width: 80,
                                render: (_: any, group: any) => <Tag>{group.rules.length} 条</Tag>,
                              },
                              {
                                title: '操作', width: 80,
                                render: (_: any, group: any) => (
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
            ...(defectSummary.error > 0
              ? [{
                  key: 'error' as const,
                  label: `错误（${defectSummary.error}）`,
                  children: (() => {
                    const entries: Array<{ ruleId: string; docType: string; checkCategory: string; ruleText: string; type: string; description: string }> = []
                    rules.forEach((r) => {
                      ;(r.defects || []).filter((d) => d.severity === 'error').forEach((d) => {
                        entries.push({
                          ruleId: r.id, docType: r.doc_type, checkCategory: r.check_category,
                          ruleText: r.rule_text, type: d.type, description: d.description,
                        })
                      })
                    })
                    return (
                      <Table
                        dataSource={entries}
                        rowKey={(_, i) => `e-${i}`}
                        size="small"
                        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ['10', '20', '50'], size: 'small' }}
                        columns={[
                          { title: '文件类型', dataIndex: 'docType', width: 100 },
                          { title: '检查项', dataIndex: 'checkCategory', width: 80 },
                          { title: '规则文本', dataIndex: 'ruleText', ellipsis: true },
                          { title: '缺陷描述', dataIndex: 'description', ellipsis: true },
                          {
                            title: '操作', width: 80,
                            render: (_: any, row: any) => (
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
                  })(),
                }]
              : []),
            ...(defectSummary.warning > 0
              ? [{
                  key: 'warning' as const,
                  label: `警告（${defectSummary.warning}）`,
                  children: (() => {
                    const entries: Array<{ ruleId: string; docType: string; checkCategory: string; ruleText: string; type: string; description: string }> = []
                    rules.forEach((r) => {
                      ;(r.defects || []).filter((d) => d.severity === 'warning').forEach((d) => {
                        entries.push({
                          ruleId: r.id, docType: r.doc_type, checkCategory: r.check_category,
                          ruleText: r.rule_text, type: d.type, description: d.description,
                        })
                      })
                    })
                    return (
                      <Table
                        dataSource={entries}
                        rowKey={(_, i) => `w-${i}`}
                        size="small"
                        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ['10', '20', '50'], size: 'small' }}
                        columns={[
                          { title: '文件类型', dataIndex: 'docType', width: 100 },
                          { title: '检查项', dataIndex: 'checkCategory', width: 80 },
                          { title: '规则文本', dataIndex: 'ruleText', ellipsis: true },
                          { title: '缺陷描述', dataIndex: 'description', ellipsis: true },
                          {
                            title: '操作', width: 80,
                            render: (_: any, row: any) => (
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
                  })(),
                }]
              : []),
          ]}
        />
      </Drawer>
    </div>
  )
}
