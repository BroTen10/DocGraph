import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Card, Row, Col, Tabs, Table, Tag, Button, message, Typography,
  Empty, Statistic, Space, Tooltip, Collapse, Select,
  Drawer, Spin, Descriptions, Divider,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ReloadOutlined, FileSearchOutlined,
  EyeOutlined, FileTextOutlined,
} from '@ant-design/icons'
import { reviewsApi, contractsApi, getErrorMessage } from '../api/client'
import type { ReviewResultItem, ReviewResultByRule, ReviewResultByDoc, ReviewTaskListItem, DocumentBrief } from '../types'
import { RESULT_COLOR, RESULT_LABEL, SEVERITY_COLOR, SEVERITY_LABEL } from '../types'
import PageHeader from '../components/PageHeader'
import DocumentCompare from '../components/DocumentCompare'
import { useRuleSet } from '../context/RuleSetContext'
import dayjs from 'dayjs'

const { Text, Paragraph } = Typography

const statusColor: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
}

const statusLabel: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
}

const formatDetailValue = (value: unknown): string => {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** detail 字段名 → 友好中文标签 */
const detailLabelMap: Record<string, string> = {
  skipped_rule_count: '被跳过规则数',
  merged_count: '被跳过规则数',
  doc_types: '涉及文档类型',
  examples: '涉及规则分布',
}

export default function ResultsPage() {
  const { currentId } = useRuleSet()
  const [searchParams, setSearchParams] = useSearchParams()
  const [tasks, setTasks] = useState<ReviewTaskListItem[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined)
  const [byRule, setByRule] = useState<ReviewResultByRule | null>(null)
  const [byDoc, setByDoc] = useState<ReviewResultByDoc | null>(null)
  const [loading, setLoading] = useState(false)

  // 问题详情抽屉
  const [detailItem, setDetailItem] = useState<ReviewResultItem | null>(null)
  const [detailVisible, setDetailVisible] = useState(false)

  // 原件/OCR 对照抽屉
  const [ocrDocId, setOcrDocId] = useState<string | null>(null)
  const [ocrDocName, setOcrDocName] = useState<string | null>(null)
  const [ocrDoc, setOcrDoc] = useState<DocumentBrief | null>(null)
  const [ocrVisible, setOcrVisible] = useState(false)
  const [ocrLoading, setOcrLoading] = useState(false)

  const ocrRequestRef = useRef(0)

  // ============ 抽屉控制 ============

  const openDetailDrawer = useCallback((item: ReviewResultItem) => {
    setDetailItem(item)
    setDetailVisible(true)
  }, [])

  const closeDetailDrawer = useCallback(() => {
    // 保留 detailItem，直到下一次打开，避免关闭动画期间抽屉内容被卸载
    setDetailVisible(false)
  }, [])

  const openOcrDrawer = useCallback(async (docId: string, fileName?: string | null) => {
    const requestId = ++ocrRequestRef.current
    setOcrDocId(docId)
    setOcrDocName(fileName || null)
    setOcrVisible(true)
    setOcrLoading(true)
    setOcrDoc(null)
    try {
      const doc = await contractsApi.getOcr(docId)
      // 不用 mountedRef：StrictMode 假 unmount 会误置为 false，导致内容永不渲染
      if (requestId === ocrRequestRef.current) {
        setOcrDoc(doc)
        setOcrDocName(doc.file_name)
      }
    } catch (e) {
      if (requestId === ocrRequestRef.current) {
        message.error('加载 OCR 详情失败: ' + getErrorMessage(e))
      }
    } finally {
      if (requestId === ocrRequestRef.current) {
        setOcrLoading(false)
      }
    }
  }, [])

  const closeOcrDrawer = useCallback(() => {
    ++ocrRequestRef.current
    setOcrVisible(false)
  }, [])

  // ============ 加载任务 ============

  // 注意：React.StrictMode 在开发模式下会双调用 effect 模拟 unmount/remount，
  // 这会把 mountedRef.current 误置为 false，导致 setTasks/setTasksLoading 被跳过。
  // 因此 loadTasks/loadResult 不再用 mountedRef 判空，依赖 React 18 自身对已卸载组件的静默忽略。

  const loadTasks = async (preserveSelection = true) => {
    if (!currentId) return
    setTasksLoading(true)
    try {
      const list = await reviewsApi.list(currentId, { limit: 100 })
      setTasks(list)
      // 决定选中项
      const fromUrl = searchParams.get('task_id')
      let target: string | undefined
      if (fromUrl && list.some((t) => t.id === fromUrl)) {
        target = fromUrl
      } else if (preserveSelection && selectedId && list.some((t) => t.id === selectedId)) {
        target = selectedId
      } else if (list.length > 0) {
        // 默认选中第一个已完成的，否则第一个
        const firstDone = list.find((t) => t.status === 'completed')
        target = (firstDone || list[0]).id
      }
      if (target && target !== selectedId) {
        setSelectedId(target)
        loadResult(target)
      } else if (!target) {
        setSelectedId(undefined)
        setByRule(null)
        setByDoc(null)
      }
    } catch (e) {
      message.error('加载任务列表失败: ' + getErrorMessage(e))
    } finally {
      setTasksLoading(false)
    }
  }

  const loadResult = async (id: string) => {
    setLoading(true)
    try {
      const [r, d] = await Promise.all([
        reviewsApi.byRule(id),
        reviewsApi.byDoc(id),
      ])
      setByRule(r)
      setByDoc(d)
    } catch (e) {
      message.error('加载结果失败: ' + getErrorMessage(e))
      setByRule(null)
      setByDoc(null)
    } finally {
      setLoading(false)
    }
  }

  // 初始加载：从 URL ?task_id= 读取，或加载列表
  useEffect(() => {
    loadTasks(false)
    // 切换规则集时重新加载(App.tsx 已通过 key 强制重挂载,这里加依赖更稳妥)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId])

  const onSelect = (id: string) => {
    setSelectedId(id)
    setSearchParams({ task_id: id })
    loadResult(id)
  }

  const resultTag = (r: string) => (
    <Tag color={RESULT_COLOR[r]}>{RESULT_LABEL[r] || r}</Tag>
  )

  // 批次 9：严重度标签（high/medium/low）
  const severityTag = (s?: string | null) =>
    s && SEVERITY_COLOR[s] ? <Tag color={SEVERITY_COLOR[s]}>{SEVERITY_LABEL[s]}</Tag> : null

  // 批次 10 Phase C：结果来源标签（图谱确定性引擎 / LLM 语义审查 / 旧逻辑）
  const sourceTag = (s?: string | null, confidence?: number | null) => {
    if (!s) return null
    const label: Record<string, string> = { graph: '图谱', llm: 'LLM', legacy: '旧逻辑' }
    const color: Record<string, string> = { graph: 'blue', llm: 'purple', legacy: 'default' }
    const tip = confidence != null ? `置信度 ${Math.round(confidence * 100)}%` : label[s] || s
    return (
      <Tooltip title={tip}>
        <Tag color={color[s] || 'default'} style={{ fontSize: 10 }}>
          {label[s] || s}
        </Tag>
      </Tooltip>
    )
  }

  // 批次 9：问题状态标签（open/confirmed/fixed/closed）
  const statusLabel: Record<string, string> = {
    open: '打开', confirmed: '已确认', fixed: '已修复', closed: '已关闭',
  }

  // 一条审查结果可能涉及多个文档。优先使用后端解析好的 related_docs，
  // 兼容旧数据只有单一 doc_id 的情况。
  const getRelatedDocs = useCallback((record: ReviewResultItem) => {
    const docs = Array.isArray(record.related_docs) ? record.related_docs : []
    if (docs.length > 0) return docs
    if (record.doc_id) {
      return [{
        doc_id: record.doc_id,
        file_name: record.doc_name || '原件',
        doc_type: record.doc_type,
      }]
    }
    return []
  }, [])

  const renderDocButtons = useCallback((record: ReviewResultItem) => {
    const docs = getRelatedDocs(record)
    if (docs.length === 0) {
      return (
        <Tooltip title="当前检查项未绑定具体文件，无法查看原件">
          <Text type="secondary" style={{ fontSize: 12 }}>无原件</Text>
        </Tooltip>
      )
    }
    return (
      <Space size={[2, 2]} direction="vertical" style={{ width: '100%', alignItems: 'flex-start' }}>
        {docs.map((d) => (
          <Button
            key={d.doc_id}
            type="link"
            size="small"
            title={d.file_name}
            onClick={() => openOcrDrawer(d.doc_id, d.file_name)}
            style={{
              padding: 0,
              fontSize: 12,
              maxWidth: 150,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              display: 'block',
              textAlign: 'left',
            }}
          >
            {d.file_name}
          </Button>
        ))}
      </Space>
    )
  }, [getRelatedDocs, openOcrDrawer])

  // ============ 按规则视图列 ============
  // 列宽设计目标:1280px 视口下左 5/24 + 右 19/24,表格不需横向滚动即可看全主要列
  // 文件类型 字段已移至详情抽屉,避免挤压其它信息列

  const ruleColumns = useMemo<ColumnsType<ReviewResultItem>>(() => [
    {
      title: '结果', dataIndex: 'result', key: 'result', width: 80,
      render: (_: unknown, record: ReviewResultItem) => (
        <Space size={4} wrap>
          {resultTag(record.result)}
          {severityTag(record.severity)}
          {sourceTag(record.source, record.confidence)}
        </Space>
      ),
      filters: [
        { text: '不通过', value: 'fail' as const },
        { text: '无法核验', value: 'unverifiable' as const },
        { text: '通过', value: 'pass' as const },
      ],
      onFilter: (val: React.Key | boolean, row: ReviewResultItem) => row.result === val,
    },
    { title: '检查项', dataIndex: 'check_category', key: 'check_category', width: 90 },
    {
      title: '规则', dataIndex: 'rule_text', key: 'rule_text', width: 150, ellipsis: true,
      render: (v: string | null) =>
        v ? <Tooltip title={v}><Text>{v}</Text></Tooltip> : <Text type="secondary">-</Text>,
    },
    {
      title: '问题描述', dataIndex: 'issue_desc', key: 'issue_desc', width: 220,
      render: (v: string | null, record: ReviewResultItem) => {
        if (!v) return <Text type="secondary">通过</Text>
        const isDanger = ['缺失', '大于', '不一致', '违反', '异常'].some((kw) => v.includes(kw))
        return (
          <Space align="start" size={2}>
            <Paragraph
              ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}
              style={{
                marginBottom: 0,
                flex: '1 1 auto',
                minWidth: 0,
                maxWidth: 170,
                whiteSpace: 'pre-wrap',
                fontSize: 12,
                color: isDanger ? '#ff4d4f' : undefined,
              }}
            >
              {v}
            </Paragraph>
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              aria-label="查看完整问题详情"
              onClick={() => openDetailDrawer(record)}
              style={{ flexShrink: 0, paddingInline: 2 }}
            >
              详情
            </Button>
          </Space>
        )
      },
    },
    {
      title: '修正建议', dataIndex: 'suggestion', key: 'suggestion', width: 180,
      render: (v: string | null, record: ReviewResultItem) => {
        if (!v) return <Text type="secondary">-</Text>
        return (
          <Space align="start" size={2}>
            <Paragraph
              ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}
              style={{ marginBottom: 0, flex: '1 1 auto', minWidth: 0, maxWidth: 130, fontSize: 12 }}
              type="warning"
            >
              {v}
            </Paragraph>
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              aria-label="查看完整修正建议"
              onClick={() => openDetailDrawer(record)}
              style={{ flexShrink: 0, paddingInline: 2 }}
            >
              详情
            </Button>
          </Space>
        )
      },
    },
    {
      title: '操作', key: 'action', width: 180, fixed: 'right',
      render: (_: unknown, record: ReviewResultItem) => (
        <Space size={[2, 2]} direction="vertical" style={{ alignItems: 'flex-start' }}>
          <Button
            type="link"
            size="small"
            style={{ padding: 0, fontSize: 12 }}
            onClick={() => openDetailDrawer(record)}
          >
            详情
          </Button>
          {renderDocButtons(record)}
        </Space>
      ),
    },
  ], [openDetailDrawer, renderDocButtons])

  // 批次 5-12：按文档维度表格列定义 memo（避免 render 内重建）
  const docColumns = useMemo<ColumnsType<ReviewResultItem>>(
    () => [
      {
        title: '结果', dataIndex: 'result', key: 'result', width: 120,
        render: (v: string, record: ReviewResultItem) => (
          <Space size={4} wrap>
            {resultTag(v)}
            {sourceTag(record.source, record.confidence)}
          </Space>
        ),
      },
      { title: '检查项', dataIndex: 'check_category', key: 'check_category', width: 90 },
      {
        title: '问题描述', dataIndex: 'issue_desc', key: 'issue_desc', width: 220,
        render: (v: string | null) => {
          if (!v) return <Text type="secondary">通过</Text>
          return (
            <Paragraph
              ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}
              style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
            >
              {v}
            </Paragraph>
          )
        },
      },
      {
        title: '修正建议', dataIndex: 'suggestion', key: 'suggestion', width: 180,
        render: (v: string | null) => {
          if (!v) return <Text type="secondary">-</Text>
          return (
            <Paragraph
              ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}
              style={{ marginBottom: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
              type="warning"
            >
              {v}
            </Paragraph>
          )
        },
      },
      {
        title: '操作', key: 'action', width: 180, fixed: 'right',
        render: (_: unknown, record: ReviewResultItem) => (
          <Space size={[2, 2]} direction="vertical" style={{ alignItems: 'flex-start' }}>
            <Button
              type="link"
              size="small"
              style={{ padding: 0, fontSize: 12 }}
              onClick={() => openDetailDrawer(record)}
            >
              详情
            </Button>
            {renderDocButtons(record)}
          </Space>
        ),
      },
    ],
    [openDetailDrawer, renderDocButtons],
  )

  // ============ 问题详情抽屉 ============

  const renderDetailDrawer = () => {
    const item = detailItem
    if (!item) return null
    const relatedDocs = getRelatedDocs(item)

    const detailEntries = item.detail && typeof item.detail === 'object'
      ? Object.entries(item.detail).filter(([, v]) => v != null && v !== '')
      : []

    return (
      <Drawer
        title={
          <Space>
            {resultTag(item.result)}
            {severityTag(item.severity)}
            {sourceTag(item.source, item.confidence)}
            {item.status && statusLabel[item.status] ? (
              <Tag>{statusLabel[item.status]}</Tag>
            ) : null}
            <span style={{ fontWeight: 600 }}>{item.check_category || '检查项'}</span>
          </Space>
        }
        placement="right"
        width={560}
        open={detailVisible}
        onClose={closeDetailDrawer}
        footer={
          relatedDocs.length > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
              {relatedDocs.map((d) => (
                <Button
                  key={d.doc_id}
                  type="primary"
                  icon={<FileTextOutlined />}
                  onClick={() => {
                    closeDetailDrawer()
                    openOcrDrawer(d.doc_id, d.file_name)
                  }}
                >
                  {d.file_name}
                </Button>
              ))}
            </div>
          ) : undefined
        }
      >
        {/* 基本信息 */}
        <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
          {item.check_category && (
            <Descriptions.Item label="检查项">{item.check_category}</Descriptions.Item>
          )}
          {item.doc_type && (
            <Descriptions.Item label="文件类型">{item.doc_type}</Descriptions.Item>
          )}
          {relatedDocs.length > 0 && (
            <Descriptions.Item label="涉及文件">
              <Space size={[4, 4]} wrap>
                {relatedDocs.map((d) => (
                  <Button
                    key={d.doc_id}
                    type="link"
                    size="small"
                    onClick={() => { closeDetailDrawer(); openOcrDrawer(d.doc_id, d.file_name) }}
                  >
                    {d.file_name}
                  </Button>
                ))}
              </Space>
            </Descriptions.Item>
          )}
        </Descriptions>

        {/* 规则原文 */}
        <Divider orientation="left" plain>规则</Divider>
        <div style={{
          background: '#f5f5f5',
          padding: 12,
          borderRadius: 6,
          whiteSpace: 'pre-wrap',
          fontSize: 13,
          lineHeight: 1.6,
        }}>
          {item.rule_text || '-'}
        </div>

        {/* 问题描述 */}
        <Divider orientation="left" plain>问题描述</Divider>
        <Paragraph style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
          {item.issue_desc || '无'}
        </Paragraph>

        {/* 修正建议 */}
        <Divider orientation="left" plain>修正建议</Divider>
        <div style={{
          background: '#fffbe6',
          padding: 12,
          borderRadius: 6,
          whiteSpace: 'pre-wrap',
          fontSize: 13,
          lineHeight: 1.6,
          color: '#ad6800',
        }}>
          {item.suggestion || '无'}
        </div>

        {/* 证据字段 */}
        {detailEntries.length > 0 && (
          <>
            <Divider orientation="left" plain>证据字段</Divider>
            <Descriptions column={1} size="small" bordered>
              {detailEntries.map(([k, v]) => (
                <Descriptions.Item label={detailLabelMap[k] || k} key={k}>
                  {formatDetailValue(v)}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </>
        )}

        {relatedDocs.length === 0 && (
          <Paragraph type="secondary" style={{ marginTop: 16, textAlign: 'center', fontSize: 13 }}>
            {item.detail?.skipped_rule_count
              ? `本条为 ${item.detail.skipped_rule_count} 条规则核验项汇总，关联 ${(Array.isArray(item.detail?.doc_types) ? item.detail.doc_types.length : 0)} 类文档`
              : '当前检查项未绑定文件，无法查看原件对照'
            }
          </Paragraph>
        )}
      </Drawer>
    )
  }

  // ============ 原件/OCR 对照抽屉 ============

  const renderOcrDrawer = () => (
    <Drawer
      title={
        <Space>
          <FileSearchOutlined />
          <span>原件 / OCR 对照</span>
          {(ocrDoc?.file_name || ocrDocName || ocrDocId) && (
            <Text strong style={{ fontSize: 13, maxWidth: 360 }} ellipsis>
              {ocrDoc?.file_name || ocrDocName || `文档 ${ocrDocId?.slice(0, 8)}`}
            </Text>
          )}
        </Space>
      }
      placement="right"
      width="88%"
      open={ocrVisible}
      onClose={closeOcrDrawer}
      afterOpenChange={(open) => {
        // 批次 5-2：关闭动画结束后再清空内容，避免关闭动画期间内容闪烁
        if (!open) {
          setOcrDocId(null)
          setOcrDocName(null)
          setOcrDoc(null)
          setOcrLoading(false)
        }
      }}
      destroyOnClose
      styles={{ body: { padding: 16 } }}
    >
      {ocrLoading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" tip="加载 OCR 识别结果中..."><div style={{ padding: 40 }} /></Spin>
        </div>
      ) : ocrDoc ? (
        <DocumentCompare
          doc={ocrDoc}
          fileUrl={contractsApi.fileUrl(ocrDoc.id)}
          height="calc(100vh - 160px)"
          onSaved={async () => {
            try {
              const fresh = await contractsApi.getOcr(ocrDoc.id)
              setOcrDoc(fresh)
            } catch (e) {
              message.error('刷新 OCR 详情失败: ' + getErrorMessage(e))
            }
          }}
        />
      ) : (
        <Empty description="无法加载文档信息" />
      )}
    </Drawer>
  )

  // ============ 按文档视图 ============

  const renderDocView = () => {
    if (!byDoc || byDoc.docs.length === 0) return <Empty description="暂无结果" />
    return (
      <div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            flexWrap: 'wrap',
            marginBottom: 12,
            padding: '8px 12px',
            background: '#f8fafc',
            border: '1px solid #e2e8f0',
            borderRadius: 6,
            fontSize: 12,
          }}
        >
          <Text type="secondary"><EyeOutlined /> 点击“详情”或“展开”查看完整问题描述</Text>
          <Text type="secondary"><FileTextOutlined /> 点击文档分组右侧“原件对照”查看原件与 OCR</Text>
        </div>
        <Collapse>
        {byDoc.docs.map((d, i) => {
          const failCount = d.results.filter((r) => r.result === 'fail').length
          const unvCount = d.results.filter((r) => r.result === 'unverifiable').length
          const passCount = d.results.filter((r) => r.result === 'pass').length
          return (
            <Collapse.Panel
              key={i}
              header={
                <Space>
                  <Text strong>{d.file_name || '（未绑定文件）'}</Text>
                  {d.doc_type && <Tag>{d.doc_type}</Tag>}
                  {failCount > 0 && <Tag color="red">不通过 {failCount}</Tag>}
                  {unvCount > 0 && <Tag color="gold">无法核验 {unvCount}</Tag>}
                  {passCount > 0 && <Tag color="green">通过 {passCount}</Tag>}
                </Space>
              }
              extra={
                d.doc_id ? (
                  <Button
                    type="link"
                    size="small"
                    icon={<FileTextOutlined />}
                    onClick={(e) => { e.stopPropagation(); openOcrDrawer(d.doc_id!, d.file_name) }}
                  >
                    原件对照
                  </Button>
                ) : (
                  <Tooltip title="未绑定文件，无法查看原件">
                    <Text type="secondary" style={{ fontSize: 12 }}>无原件</Text>
                  </Tooltip>
                )
              }
            >
              <Table
                dataSource={d.results}
                columns={docColumns}
                rowKey="id"
                size="small"
                pagination={false}
                scroll={{ x: 760 }}
              />
            </Collapse.Panel>
          )
        })}
        </Collapse>
      </div>
    )
  }

  // ============ 渲染 ============

  const summary = byRule?.summary || {}
  const selectedTask = tasks.find((t) => t.id === selectedId)

  return (
    <div>
      <PageHeader
        title="审查结果展示"
        subtitle="按规则维度与文档维度查看审查明细，支持任务历史切换"
        icon={<FileSearchOutlined />}
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => loadTasks()} loading={tasksLoading}>
            刷新任务列表
          </Button>
        }
      />

      <Card size="small" style={{ marginTop: 16, marginBottom: 16 }} loading={tasksLoading}>
        <Row gutter={[16, 8]} align="middle">
          <Col flex="auto">
            <Space size={8} wrap>
              <FileSearchOutlined />
              <Text strong>审查任务</Text>
              {selectedTask && (
                <Tag color={statusColor[selectedTask.status]}>
                  {statusLabel[selectedTask.status] || selectedTask.status}
                </Tag>
              )}
            </Space>
          </Col>
          <Col xs={24} sm={14} md={10} lg={8} xl={6}>
            <Select
              value={selectedId}
              placeholder={tasks.length > 0 ? '选择审查任务' : '暂无审查任务'}
              onChange={(id: string) => onSelect(id)}
              style={{ width: '100%' }}
              showSearch
              optionFilterProp="label"
              options={tasks.map((t) => ({
                value: t.id,
                label: `${t.contract_no || t.contract_id.slice(0, 8)} · ${statusLabel[t.status] || t.status} · ${dayjs(t.start_time).format('MM-DD HH:mm')}`,
              }))}
            />
          </Col>
        </Row>
      </Card>

      {!selectedId || !byRule ? (
        <Card><Empty description={selectedId ? '加载中...' : '请选择审查任务查看结果'} /></Card>
      ) : (
        <>
          {/* 任务摘要 + 统计合并到一行,降低纵向信息密度 */}
          <Card size="small" style={{ marginBottom: 16 }} styles={{ body: { padding: '12px 16px' } }}>
            <Row gutter={[24, 8]} align="middle">
              <Col flex="auto">
                <Space size={8} wrap>
                  <Text strong style={{ fontSize: 14 }}>{selectedTask?.contract_no || '合同'}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {dayjs(selectedTask?.start_time).format('YYYY-MM-DD HH:mm:ss')}
                  </Text>
                </Space>
              </Col>
              <Col>
                <Space size={20} wrap>
                  <Statistic title="总检查项" value={summary.total || 0} valueStyle={{ fontSize: 20 }} />
                  <Statistic title="通过" value={summary.pass || 0} valueStyle={{ color: '#52c41a', fontSize: 20 }} />
                  <Statistic title="不通过" value={summary.fail || 0} valueStyle={{ color: '#ff4d4f', fontSize: 20 }} />
                  <Statistic title="无法核验" value={summary.unverifiable || 0} valueStyle={{ color: '#faad14', fontSize: 20 }} />
                </Space>
              </Col>
            </Row>
          </Card>

          {/* 结果 Tabs */}
          <Card>
                <Tabs
                  defaultActiveKey="rule"
                  items={[
                    {
                      key: 'rule',
                      label: `按规则视图 (${byRule.results.length})`,
                      children: (
                        <div>
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 16,
                              flexWrap: 'wrap',
                              marginBottom: 12,
                              padding: '8px 12px',
                              background: '#f8fafc',
                              border: '1px solid #e2e8f0',
                              borderRadius: 6,
                              fontSize: 12,
                            }}
                          >
                            <Text type="secondary">
                              <EyeOutlined /> 点击“详情”或“展开”查看完整问题描述和修正建议
                            </Text>
                            <Text type="secondary">
                              <FileTextOutlined /> 点击操作栏文件名按钮查看原件与 OCR 识别内容
                            </Text>
                          </div>
                          <Table
                            dataSource={byRule.results}
                            columns={ruleColumns}
                            rowKey="id"
                            size="small"
                            loading={loading}
                            pagination={{
                              pageSize: 10,
                              showSizeChanger: true,
                              pageSizeOptions: [10, 20, 50],
                            }}
                            scroll={{ x: 900 }}
                            rowClassName={(row: ReviewResultItem) =>
                              row.result === 'fail'
                                ? 'row-fail'
                                : row.result === 'unverifiable'
                                  ? 'row-unverifiable'
                                  : ''
                            }
                          />
                        </div>
                      ),
                    },
                    {
                      key: 'doc',
                      label: `按文档视图 (${byDoc?.docs.length || 0})`,
                      children: renderDocView(),
                    },
                  ]}
                />
          </Card>
        </>
      )}

      {/* Drawers */}
      {renderDetailDrawer()}
      {renderOcrDrawer()}
    </div>
  )
}
