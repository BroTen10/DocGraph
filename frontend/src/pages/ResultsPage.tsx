import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Card, Row, Col, Tabs, Table, Tag, Button, message, Typography,
  Empty, Statistic, Space, Tooltip, Collapse, List,
} from 'antd'
import { ReloadOutlined, FileSearchOutlined } from '@ant-design/icons'
import { reviewsApi } from '../api/client'
import type { ReviewResultItem, ReviewResultByRule, ReviewResultByDoc, ReviewTaskListItem } from '../types'
import { RESULT_COLOR, RESULT_LABEL } from '../types'
import dayjs from 'dayjs'

const { Title, Text } = Typography

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

export default function ResultsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [tasks, setTasks] = useState<ReviewTaskListItem[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined)
  const [byRule, setByRule] = useState<ReviewResultByRule | null>(null)
  const [byDoc, setByDoc] = useState<ReviewResultByDoc | null>(null)
  const [loading, setLoading] = useState(false)

  const loadTasks = async (preserveSelection = true) => {
    setTasksLoading(true)
    try {
      const list = await reviewsApi.list({ limit: 100 })
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
    } catch (e: any) {
      message.error('加载任务列表失败: ' + (e?.message || e))
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
    } catch (e: any) {
      message.error('加载结果失败: ' + (e?.message || e))
      setByRule(null)
      setByDoc(null)
    } finally {
      setLoading(false)
    }
  }

  // 初始加载：从 URL ?task_id= 读取，或加载列表
  useEffect(() => {
    loadTasks(false)
    // 仅在 mount 时执行
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSelect = (id: string) => {
    setSelectedId(id)
    setSearchParams({ task_id: id })
    loadResult(id)
  }

  const resultTag = (r: string) => (
    <Tag color={RESULT_COLOR[r]}>{RESULT_LABEL[r] || r}</Tag>
  )

  // ============ 按规则视图列 ============
  const ruleColumns = [
    {
      title: '结果', dataIndex: 'result', key: 'result', width: 100,
      render: (v: string) => resultTag(v),
      filters: [
        { text: '不通过', value: 'fail' },
        { text: '无法核验', value: 'unverifiable' },
        { text: '通过', value: 'pass' },
      ],
      onFilter: (val: React.Key | boolean, row: ReviewResultItem) => row.result === val,
    },
    { title: '检查项', dataIndex: 'check_category', key: 'check_category', width: 110 },
    { title: '文件类型', dataIndex: 'doc_type', key: 'doc_type', width: 140 },
    {
      title: '规则', dataIndex: 'rule_text', key: 'rule_text', ellipsis: true,
      render: (v: string | null) => v ? <Tooltip title={v}><Text>{v}</Text></Tooltip> : <Text type="secondary">-</Text>,
    },
    {
      title: '涉及文件', dataIndex: 'doc_name', key: 'doc_name', width: 180, ellipsis: true,
      render: (v: string | null) => v || <Text type="secondary">（未绑定文件）</Text>,
    },
    {
      title: '问题描述', dataIndex: 'issue_desc', key: 'issue_desc', ellipsis: true,
      render: (v: string | null) => v ? <Text type={v.includes('缺失') || v.includes('大于') || v.includes('不一致') || v.includes('违反') || v.includes('异常') ? 'danger' : undefined}>{v}</Text> : <Text type="secondary">通过</Text>,
    },
    {
      title: '修正建议', dataIndex: 'suggestion', key: 'suggestion', width: 320, ellipsis: true,
      render: (v: string | null) => v ? <Tooltip title={v}><Text type="warning">{v}</Text></Tooltip> : <Text type="secondary">-</Text>,
    },
  ]

  // ============ 按文档视图 ============
  const renderDocView = () => {
    if (!byDoc || byDoc.docs.length === 0) return <Empty description="暂无结果" />
    return (
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
            >
              <Table
                dataSource={d.results}
                columns={[
                  { title: '结果', dataIndex: 'result', key: 'result', width: 100, render: (v: string) => resultTag(v) },
                  { title: '检查项', dataIndex: 'check_category', key: 'check_category', width: 110 },
                  { title: '问题描述', dataIndex: 'issue_desc', key: 'issue_desc', ellipsis: true },
                  { title: '修正建议', dataIndex: 'suggestion', key: 'suggestion', ellipsis: true, render: (v: string | null) => v ? <Text type="warning">{v}</Text> : null },
                ]}
                rowKey="id"
                size="small"
                pagination={false}
              />
            </Collapse.Panel>
          )
        })}
      </Collapse>
    )
  }

  const summary = byRule?.summary || {}
  const selectedTask = tasks.find((t) => t.id === selectedId)

  return (
    <div>
      <Row justify="space-between" align="middle">
        <Col><Title level={4}>审查结果展示</Title></Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => loadTasks()} loading={tasksLoading}>刷新任务列表</Button>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={8}>
          <Card title="审查任务列表" size="small" loading={tasksLoading}>
            {tasks.length === 0 ? (
              <Empty description="暂无审查任务" />
            ) : (
              <List
                dataSource={tasks}
                rowKey="id"
                style={{ maxHeight: 560, overflowY: 'auto' }}
                renderItem={(t) => (
                  <List.Item
                    onClick={() => onSelect(t.id)}
                    style={{
                      cursor: 'pointer',
                      padding: '10px 12px',
                      background: t.id === selectedId ? '#e6f4ff' : undefined,
                      borderLeft: t.id === selectedId ? '3px solid #1677ff' : '3px solid transparent',
                    }}
                  >
                    <div style={{ width: '100%' }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Text strong>{t.contract_no || t.contract_id.slice(0, 8)}</Text>
                        <Tag color={statusColor[t.status]}>{statusLabel[t.status] || t.status}</Tag>
                      </Space>
                      <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
                        {dayjs(t.start_time).format('YYYY-MM-DD HH:mm:ss')}
                        {t.end_time && (() => {
                          const durMs = dayjs(t.end_time).diff(dayjs(t.start_time))
                          if (durMs < 0) return null
                          let durText: string
                          if (durMs < 1000) durText = `${durMs}ms`
                          else if (durMs < 60000) durText = `${(durMs / 1000).toFixed(1)}s`
                          else {
                            const m = Math.floor(durMs / 60000)
                            const s = Math.floor((durMs % 60000) / 1000)
                            durText = `${m}分${s}秒`
                          }
                          return <Text type="secondary" style={{ marginLeft: 6 }}>耗时 {durText}</Text>
                        })()}
                      </div>
                      {t.status === 'completed' && t.summary && (
                        <div style={{ fontSize: 12, marginTop: 4 }}>
                          <Space size="small">
                            <Text type="secondary">共 {t.summary.total || 0}</Text>
                            <Text type="success">通过 {t.summary.pass || 0}</Text>
                            <Text type="danger">不通过 {t.summary.fail || 0}</Text>
                            <Text type="warning">未核验 {t.summary.unverifiable || 0}</Text>
                          </Space>
                        </div>
                      )}
                      {t.status === 'failed' && t.error && (
                        <div style={{ fontSize: 12, color: '#ff4d4f', marginTop: 4 }}>错误: {t.error}</div>
                      )}
                      {t.status === 'running' && (
                        <div style={{ fontSize: 12, color: '#1677ff', marginTop: 4 }}>
                          {t.stage || '执行中'} · {t.progress}%
                        </div>
                      )}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>

        <Col span={16}>
          {!selectedId || !byRule ? (
            <Card><Empty description={selectedId ? '加载中...' : '请从左侧选择一个审查任务查看结果'} /></Card>
          ) : (
            <>
              <Card size="small" style={{ marginBottom: 16 }}>
                <Space>
                  <FileSearchOutlined />
                  <Text strong>{selectedTask?.contract_no || '合同'}</Text>
                  <Tag color={statusColor[selectedTask?.status || '']}>{statusLabel[selectedTask?.status || ''] || selectedTask?.status}</Tag>
                  <Text type="secondary">{dayjs(selectedTask?.start_time).format('YYYY-MM-DD HH:mm:ss')}</Text>
                </Space>
              </Card>

              <Card style={{ marginBottom: 16 }}>
                <Row gutter={16}>
                  <Col span={6}><Statistic title="总检查项" value={summary.total || 0} /></Col>
                  <Col span={6}><Statistic title="通过" value={summary.pass || 0} valueStyle={{ color: '#52c41a' }} /></Col>
                  <Col span={6}><Statistic title="不通过" value={summary.fail || 0} valueStyle={{ color: '#ff4d4f' }} /></Col>
                  <Col span={6}><Statistic title="无法核验" value={summary.unverifiable || 0} valueStyle={{ color: '#faad14' }} /></Col>
                </Row>
              </Card>

              <Card>
                <Tabs
                  defaultActiveKey="rule"
                  items={[
                    {
                      key: 'rule',
                      label: `按规则视图 (${byRule.results.length})`,
                      children: (
                        <Table
                          dataSource={byRule.results}
                          columns={ruleColumns as any}
                          rowKey="id"
                          size="small"
                          loading={loading}
                          pagination={{ pageSize: 20 }}
                          rowClassName={(row: ReviewResultItem) => row.result === 'fail' ? 'row-fail' : row.result === 'unverifiable' ? 'row-unverifiable' : ''}
                        />
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
        </Col>
      </Row>

      <style>{`
        .row-fail { background: #fff1f0 !important; }
        .row-unverifiable { background: #fffbe6 !important; }
      `}</style>
    </div>
  )
}
