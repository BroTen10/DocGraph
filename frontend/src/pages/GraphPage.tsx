/**
 * 图谱确认页面（重构版）
 *
 * 布局参考 MiroFish-Explorer：
 * - 左侧（~65%）：ECharts 力导向图谱
 * - 右侧（~35%）：任务进度 / 工作区 / 规则文档导入
 *
 * 功能：
 * 1. 从规则文档（PDF/EXCEL/WORD/MD）导入规则
 * 2. 异步构建图谱 + 实时进度追踪
 * 3. 图谱可视化（节点按类型着色、按度数缩放、曲线边）
 * 4. 节点/边选中、编辑、删除（保留原有功能）
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Card, Row, Col, Button, Space, message, Typography, Empty, Spin, Tag,
  Modal, Form, Input, Statistic, Descriptions, Popconfirm, Alert,
  Upload, Progress, Timeline, List, Tabs, Badge, Divider, Tooltip,
} from 'antd'
import {
  ReloadOutlined, CheckCircleOutlined, DeleteOutlined, EditOutlined,
  ThunderboltOutlined, UploadOutlined, FileTextOutlined, ClockCircleOutlined,
  BulbOutlined, NodeIndexOutlined, ApartmentOutlined,
} from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { graphApi, rulesApi } from '../api/client'
import type { GraphData, GraphEdge, GraphBuildTaskStatus, RuleImportResponse, ImportTask, Rule, RuleSnapshot } from '../types'
import GraphView from '../components/GraphView'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { useRuleSet } from '../context/RuleSetContext'
import dayjs from 'dayjs'

const { Text, Paragraph } = Typography

/** 构建任务状态颜色 */
const BUILD_STATUS_COLOR: Record<string, string> = {
  running: 'processing',
  completed: 'success',
  failed: 'error',
}

/** 消息级别颜色 */
const MSG_LEVEL_COLOR: Record<string, string> = {
  info: 'blue',
  success: 'green',
  warning: 'orange',
  error: 'red',
}

export default function GraphPage() {
  const { currentId } = useRuleSet()
  // 图谱数据
  const [graph, setGraph] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(false)

  // 选中状态
  const [selectedNodeName, setSelectedNodeName] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null)

  // 编辑
  const [editOpen, setEditOpen] = useState(false)
  const [editForm] = Form.useForm()
  const [editTarget, setEditTarget] = useState<{
    kind: 'node' | 'edge'
    name?: string
    source?: string
    target?: string
  } | null>(null)

  // 异步构建
  const [buildTask, setBuildTask] = useState<GraphBuildTaskStatus | null>(null)
  const [buildTasks, setBuildTasks] = useState<GraphBuildTaskStatus[]>([])
  const [building, setBuilding] = useState(false)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 规则文档导入
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<RuleImportResponse | null>(null)
  const [importTask, setImportTask] = useState<ImportTask | null>(null)

  // 工作区
  const [rules, setRules] = useState<Rule[]>([])
  const [snapshots, setSnapshots] = useState<RuleSnapshot[]>([])
  const [activeTab, setActiveTab] = useState('progress')

  // 选中的节点详情
  const selectedNode = graph?.nodes.find((n) => n.name === selectedNodeName) || null

  // ============ 数据加载 ============
  const loadGraph = useCallback(async () => {
    if (!currentId) return
    setLoading(true)
    try {
      const g = await graphApi.getLatest(currentId)
      setGraph(g)
    } catch (e: any) {
      if (e?.response?.status === 404) {
        setGraph(null)
      } else {
        message.error('加载图谱失败: ' + (e?.message || e))
      }
    } finally {
      setLoading(false)
    }
  }, [currentId])

  const loadWorkspace = useCallback(async () => {
    if (!currentId) return
    try {
      const [rulesData, snapsData] = await Promise.all([
        rulesApi.list(currentId, { enabled_only: true }),
        rulesApi.listSnapshots(currentId),
      ])
      setRules(rulesData)
      setSnapshots(snapsData)
    } catch {
      // 静默
    }
  }, [currentId])

  const loadBuildTasks = useCallback(async () => {
    try {
      const tasks = await graphApi.listBuildTasks(10)
      setBuildTasks(tasks)
      // 如果有正在运行的任务，自动选中它
      const running = tasks.find((t) => t.status === 'running')
      if (running) {
        setBuildTask(running)
        setBuilding(true)
      }
    } catch {
      // 静默
    }
  }, [])

  useEffect(() => {
    loadGraph()
    loadWorkspace()
    loadBuildTasks()
  }, [loadGraph, loadWorkspace, loadBuildTasks])

  // ============ 异步构建轮询 ============
  const startPolling = useCallback((taskId: string) => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)

    const poll = async () => {
      try {
        const status = await graphApi.getBuildStatus(taskId)
        setBuildTask(status)

        if (status.status === 'completed') {
          setBuilding(false)
          if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current)
            pollTimerRef.current = null
          }
          message.success(`图谱构建完成：${status.node_count} 节点 / ${status.edge_count} 关系`)
          // 刷新图谱和工作区
          await loadGraph()
          await loadWorkspace()
          await loadBuildTasks()
        } else if (status.status === 'failed') {
          setBuilding(false)
          if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current)
            pollTimerRef.current = null
          }
          message.error('图谱构建失败: ' + (status.error || '未知错误'))
        }
      } catch {
        // 忽略轮询错误
      }
    }

    // 立即执行一次
    poll()
    // 每 1.5 秒轮询
    pollTimerRef.current = setInterval(poll, 1500)
  }, [loadGraph, loadWorkspace, loadBuildTasks])

  // 清理轮询
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [])

  // ============ 异步构建图谱 ============
  const handleBuildAsync = async (autoConfirmAll = false) => {
    if (building) {
      message.warning('已有构建任务正在运行')
      return
    }
    setBuilding(true)
    setBuildTask(null)
    setActiveTab('progress')
    try {
      const resp = await graphApi.buildAsync(currentId!, autoConfirmAll)
      message.info('图谱构建已启动，进度请看右侧面板')
      startPolling(resp.task_id)
    } catch (e: any) {
      setBuilding(false)
      message.error('启动构建失败: ' + (e?.response?.data?.detail || e?.message || e))
    }
  }

  // ============ 规则文档导入 ============
  const importUploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    showUploadList: false,
    accept: '.pdf,.xlsx,.xls,.docx,.md,.txt',
    beforeUpload: (file) => {
      // 校验文件大小（10MB 限制）
      const isUnderLimit = (file.size || 0) < 10 * 1024 * 1024
      if (!isUnderLimit) {
        message.error('文件大小不能超过 10MB')
        return false
      }
      if (!currentId) {
        message.error('尚未选择规则集')
        return false
      }

      setImporting(true)
      setImportResult(null)
      setImportTask(null)
      rulesApi
        .importDocument(currentId, file)
        .then(async (task: ImportTask) => {
          setImportTask(task)
          // 轮询任务进度，直到 done / error
          const poll = async (): Promise<void> => {
            const t = await rulesApi.getImportTask(task.task_id)
            setImportTask(t)
            if (t.status === 'done') {
              setImportResult(t.result)
              if (t.result && t.result.imported > 0) {
                message.success(
                  `导入完成：共 ${t.result.total} 条，成功 ${t.result.imported} 条，跳过 ${t.result.skipped} 条`,
                )
                loadWorkspace()
              } else {
                message.warning(`未导入任何规则${t.result ? `，跳过 ${t.result.skipped} 条` : ''}`)
              }
              setImporting(false)
              return
            }
            if (t.status === 'error') {
              message.error('导入失败: ' + (t.error || '未知错误'))
              setImporting(false)
              return
            }
            await new Promise((r) => setTimeout(r, 1500))
            return poll()
          }
          return poll()
        })
        .catch((e: any) => {
          message.error('导入失败: ' + (e?.response?.data?.detail || e?.message || e))
          setImporting(false)
        })

      return false // 阻止自动上传
    },
  }

  // ============ 图谱编辑（保留原有功能） ============
  const openEditNode = (nodeName: string) => {
    const node = graph?.nodes.find((n) => n.name === nodeName)
    if (!node) return
    setEditTarget({ kind: 'node', name: nodeName })
    editForm.setFieldsValue({
      properties_json: JSON.stringify(node.properties || {}, null, 2),
    })
    setEditOpen(true)
  }

  const openEditEdge = (source: string, target: string) => {
    const edge = graph?.edges.find((e) => e.source === source && e.target === target)
    if (!edge) return
    setSelectedEdge(edge)
    setEditTarget({ kind: 'edge', source, target })
    editForm.setFieldsValue({
      properties_json: JSON.stringify(edge.properties || {}, null, 2),
    })
    setEditOpen(true)
  }

  const saveEdit = async () => {
    if (!graph || !editTarget) return
    try {
      const values = await editForm.validateFields()
      const props = JSON.parse(values.properties_json)
      const op =
        editTarget.kind === 'node'
          ? { op: 'update_node', node_name: editTarget.name, properties: props }
          : { op: 'update_edge', source: editTarget.source, target: editTarget.target, properties: props }
      const updated = await graphApi.confirm(graph.graph_id, [op as any])
      setGraph(updated)
      setEditOpen(false)
      message.success('已更新并写入 Neo4j')
    } catch (e: any) {
      if (e?.errorFields) return
      message.error('保存失败: ' + (e?.message || e))
    }
  }

  const deleteSelected = async () => {
    if (!graph) return
    try {
      const edits: any[] = []
      if (selectedNodeName) edits.push({ op: 'delete_node', node_name: selectedNodeName })
      if (selectedEdge) edits.push({ op: 'delete_edge', source: selectedEdge.source, target: selectedEdge.target })
      if (edits.length === 0) return
      const updated = await graphApi.confirm(graph.graph_id, edits)
      setGraph(updated)
      setSelectedNodeName(null)
      setSelectedEdge(null)
      message.success('已删除')
    } catch (e: any) {
      message.error('删除失败: ' + (e?.message || e))
    }
  }

  const confirmGraph = async () => {
    if (!graph) return
    try {
      await graphApi.confirm(graph.graph_id, [])
      message.success('已确认生效')
    } catch (e: any) {
      message.error('确认失败: ' + (e?.message || e))
    }
  }

  // ============ 渲染 ============
  return (
    <div>
      <PageHeader
        title="知识图谱"
        subtitle="从规则文档导入并构建图谱，可视化节点关系，确认后写入 Neo4j 生效"
        icon={<ApartmentOutlined />}
        extra={
          <Space>
            <Upload {...importUploadProps}>
              <Button icon={<UploadOutlined />} loading={importing}>
                导入规则文档
              </Button>
            </Upload>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={building}
              onClick={() => handleBuildAsync(false)}
            >
              构建图谱
            </Button>
            <Popconfirm
              title="一键自动确认全部（忽略置信度）？"
              onConfirm={() => handleBuildAsync(true)}
              disabled={building}
            >
              <Button disabled={building}>自动确认构建</Button>
            </Popconfirm>
            <Button icon={<ReloadOutlined />} onClick={loadGraph} loading={loading}>
              刷新图谱
            </Button>
            <Popconfirm title="确认将当前图谱写入 Neo4j 生效？" onConfirm={confirmGraph}>
              <Button icon={<CheckCircleOutlined />}>确认生效</Button>
            </Popconfirm>
          </Space>
        }
      />

      <Row gutter={16}>
        {/* 左侧：图谱可视化 */}
        <Col span={16}>
          <Card
            title={
              <Space>
                <NodeIndexOutlined />
                <span>规则图谱可视化</span>
                {graph && (
                  <Tag color="blue">
                    {graph.node_count} 节点 / {graph.edge_count} 关系
                  </Tag>
                )}
              </Space>
            }
            bodyStyle={{ padding: 0 }}
            style={{ minHeight: 600 }}
          >
            {loading ? (
              <div style={{ textAlign: 'center', padding: 120 }}>
                <Spin size="large" tip="加载图谱中..." />
              </div>
            ) : !graph ? (
              <EmptyState
                description={
                  <>
                    <div style={{ marginBottom: 4 }}>暂无图谱</div>
                    <Paragraph type="secondary" style={{ margin: 0 }}>
                      请先导入规则文档或直接构建图谱
                    </Paragraph>
                  </>
                }
                padding={80}
                action={
                  <Space>
                    <Upload {...importUploadProps}>
                      <Button icon={<UploadOutlined />} type="primary">
                        导入规则文档
                      </Button>
                    </Upload>
                    <Button
                      icon={<ThunderboltOutlined />}
                      loading={building}
                      onClick={() => handleBuildAsync(false)}
                    >
                      构建图谱
                    </Button>
                  </Space>
                }
              />
            ) : (
              <>
                <Alert
                  type="info"
                  showIcon
                  style={{ borderRadius: 0, marginBottom: 0 }}
                  message="点击节点或边查看详情。黄色边框节点为低置信度（需人工确认）。"
                />
                <GraphView
                  graph={graph}
                  selectedNodeName={selectedNodeName}
                  selectedEdgeKey={selectedEdge ? `${selectedEdge.source}->${selectedEdge.target}` : null}
                  onNodeClick={(name) => {
                    setSelectedNodeName(name)
                    setSelectedEdge(null)
                  }}
                  onEdgeClick={(source, target) => {
                    const e = graph.edges.find((e) => e.source === source && e.target === target)
                    if (e) {
                      setSelectedEdge(e)
                      setSelectedNodeName(null)
                    }
                  }}
                  onBackgroundClick={() => {
                    setSelectedNodeName(null)
                    setSelectedEdge(null)
                  }}
                  onRefresh={() => loadGraph()}
                  height={560}
                />
              </>
            )}
          </Card>
        </Col>

        {/* 右侧：任务进度 / 工作区 / 规则导入 */}
        <Col span={8}>
          <Card bodyStyle={{ padding: 0 }}>
            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              type="card"
              size="small"
              items={[
                // ============ 构建进度 ============
                {
                  key: 'progress',
                  label: (
                    <span>
                      <ThunderboltOutlined /> 构建进度
                      {buildTask?.status === 'running' && (
                        <Badge status="processing" style={{ marginLeft: 4 }} />
                      )}
                    </span>
                  ),
                  children: (
                    <BuildProgressPanel
                      buildTask={buildTask}
                      building={building}
                      buildTasks={buildTasks}
                      onSelectTask={(t) => {
                        setBuildTask(t)
                        if (t.status === 'running') {
                          startPolling(t.task_id)
                        }
                      }}
                    />
                  ),
                },
                // ============ 工作区 ============
                {
                  key: 'workspace',
                  label: (
                    <span>
                      <FileTextOutlined /> 工作区
                      <Badge count={rules.length} style={{ marginLeft: 4 }} size="small" />
                    </span>
                  ),
                  children: (
                    <WorkspacePanel
                      rules={rules}
                      snapshots={snapshots}
                      onRefresh={loadWorkspace}
                    />
                  ),
                },
                // ============ 节点/边详情 ============
                {
                  key: 'detail',
                  label: (
                    <span>
                      <BulbOutlined /> 详情
                      {(selectedNode || selectedEdge) && (
                        <Badge status="success" style={{ marginLeft: 4 }} />
                      )}
                    </span>
                  ),
                  children: (
                    <DetailPanel
                      selectedNode={selectedNode}
                      selectedEdge={selectedEdge}
                      onEditNode={() => selectedNodeName && openEditNode(selectedNodeName)}
                      onEditEdge={() => selectedEdge && openEditEdge(selectedEdge.source, selectedEdge.target)}
                      onDelete={deleteSelected}
                    />
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      {/* 编辑弹窗 */}
      <Modal
        title={editTarget?.kind === 'node' ? '编辑节点属性' : '编辑关系属性'}
        open={editOpen}
        onOk={saveEdit}
        onCancel={() => setEditOpen(false)}
        width={560}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item name="properties_json" label="属性 JSON" rules={[{ required: true }]}>
            <Input.TextArea rows={8} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// ============ 子组件：构建进度面板 ============
function BuildProgressPanel({
  buildTask,
  building,
  buildTasks,
  onSelectTask,
}: {
  buildTask: GraphBuildTaskStatus | null
  building: boolean
  buildTasks: GraphBuildTaskStatus[]
  onSelectTask: (t: GraphBuildTaskStatus) => void
}) {
  if (!buildTask && !building && buildTasks.length === 0) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Empty description="暂无构建任务" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        <Paragraph type="secondary" style={{ marginTop: 8 }}>
          点击顶部"构建图谱"按钮启动异步构建
        </Paragraph>
      </div>
    )
  }

  return (
    <div style={{ padding: 12 }}>
      {/* 当前任务进度 */}
      {buildTask && (
        <div style={{ marginBottom: 16 }}>
          <Row gutter={8} style={{ marginBottom: 8 }}>
            <Col span={12}>
              <Statistic
                title="进度"
                value={buildTask.progress}
                suffix="%"
                valueStyle={{ fontSize: 20 }}
              />
            </Col>
            <Col span={12}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>状态</div>
              <Badge status={BUILD_STATUS_COLOR[buildTask.status] as any} text={buildTask.stage} />
            </Col>
          </Row>

          <Progress
            percent={buildTask.progress}
            status={
              buildTask.status === 'failed'
                ? 'exception'
                : buildTask.status === 'completed'
                ? 'success'
                : 'active'
            }
            size="small"
          />

          {/* 构建结果统计 */}
          {buildTask.status === 'completed' && (
            <Row gutter={8} style={{ marginTop: 12 }}>
              <Col span={8}>
                <Statistic title="节点" value={buildTask.node_count} valueStyle={{ fontSize: 16 }} />
              </Col>
              <Col span={8}>
                <Statistic title="关系" value={buildTask.edge_count} valueStyle={{ fontSize: 16 }} />
              </Col>
              <Col span={8}>
                <Statistic title="规则" value={buildTask.rule_count} valueStyle={{ fontSize: 16 }} />
              </Col>
            </Row>
          )}

          {buildTask.error && (
            <Alert
              type="error"
              message={buildTask.error}
              style={{ marginTop: 8, fontSize: 12 }}
            />
          )}
        </div>
      )}

      {/* 进度日志 */}
      {buildTask && buildTask.messages.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>
            <ClockCircleOutlined /> 构建日志
          </div>
          <div
            style={{
              maxHeight: 240,
              overflowY: 'auto',
              background: '#0f172a',
              padding: 8,
              borderRadius: 4,
            }}
          >
            <Timeline
              items={buildTask.messages.slice(-30).reverse().map((m) => ({
                color: MSG_LEVEL_COLOR[m.level] === 'green' ? 'green' : MSG_LEVEL_COLOR[m.level] === 'red' ? 'red' : 'blue',
                children: (
                  <div style={{ fontSize: 11 }}>
                    <div style={{ color: '#cbd5e1' }}>{m.message}</div>
                    <div style={{ color: '#64748b', fontSize: 10 }}>
                      [{m.stage}] {dayjs(m.time).format('HH:mm:ss')}
                    </div>
                  </div>
                ),
              }))}
            />
          </div>
        </div>
      )}

      {/* 历史任务 */}
      {buildTasks.length > 0 && (
        <div>
          <Divider style={{ margin: '8px 0' }} />
          <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>历史构建任务</div>
          <List
            size="small"
            dataSource={buildTasks.slice(0, 10)}
            renderItem={(task) => (
              <List.Item
                style={{ padding: '6px 0', cursor: 'pointer' }}
                onClick={() => onSelectTask(task)}
              >
                <div style={{ width: '100%' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Badge
                      status={BUILD_STATUS_COLOR[task.status] as any}
                      text={
                        <Text style={{ fontSize: 12 }}>
                          {task.stage}
                        </Text>
                      }
                    />
                    <Text type="secondary" style={{ fontSize: 10 }}>
                      {dayjs(task.started_at).format('MM-DD HH:mm')}
                    </Text>
                  </div>
                  {task.status === 'completed' && (
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {task.node_count} 节点 / {task.edge_count} 关系
                    </Text>
                  )}
                </div>
              </List.Item>
            )}
          />
        </div>
      )}
    </div>
  )
}

// ============ 子组件：工作区面板 ============
function WorkspacePanel({
  rules,
  snapshots,
  onRefresh,
}: {
  rules: Rule[]
  snapshots: RuleSnapshot[]
  onRefresh: () => void
}) {
  return (
    <div style={{ padding: 12 }}>
      <Tabs
        size="small"
        items={[
          {
            key: 'rules',
            label: `启用规则 (${rules.length})`,
            children: (
              <List
                size="small"
                dataSource={rules.slice(0, 50)}
                renderItem={(rule) => (
                  <List.Item style={{ padding: '4px 0' }}>
                    <div style={{ width: '100%' }}>
                      <div style={{ display: 'flex', gap: 4, marginBottom: 2 }}>
                        <Tag color="blue" style={{ fontSize: 10 }}>{rule.doc_type}</Tag>
                        <Tag style={{ fontSize: 10 }}>{rule.check_category}</Tag>
                      </div>
                      <Text style={{ fontSize: 12 }}>{rule.rule_text}</Text>
                    </div>
                  </List.Item>
                )}
                locale={{ emptyText: '暂无启用规则' }}
                style={{ maxHeight: 400, overflow: 'auto' }}
              />
            ),
          },
          {
            key: 'snapshots',
            label: `快照 (${snapshots.length})`,
            children: (
              <List
                size="small"
                dataSource={snapshots.slice(0, 20)}
                renderItem={(snap) => (
                  <List.Item style={{ padding: '4px 0' }}>
                    <div style={{ width: '100%' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <Text style={{ fontSize: 12 }}>
                          {dayjs(snap.snapshot_time).format('MM-DD HH:mm')}
                        </Text>
                        {snap.graph_id && <Tag color="green" style={{ fontSize: 10 }}>有图谱</Tag>}
                      </div>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        规则 {snap.rule_count} 条
                        {snap.node_count != null && ` / 节点 ${snap.node_count}`}
                        {snap.edge_count != null && ` / 关系 ${snap.edge_count}`}
                      </Text>
                    </div>
                  </List.Item>
                )}
                locale={{ emptyText: '暂无快照' }}
                style={{ maxHeight: 400, overflow: 'auto' }}
              />
            ),
          },
        ]}
      />
    </div>
  )
}

// ============ 子组件：详情面板 ============
function DetailPanel({
  selectedNode,
  selectedEdge,
  onEditNode,
  onEditEdge,
  onDelete,
}: {
  selectedNode: GraphData['nodes'][0] | null
  selectedEdge: GraphEdge | null
  onEditNode: () => void
  onEditEdge: () => void
  onDelete: () => void
}) {
  if (!selectedNode && !selectedEdge) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Empty description="点击图谱中的节点或边查看详情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    )
  }

  return (
    <div style={{ padding: 12 }}>
      {selectedNode && (
        <div>
          <Statistic title="名称" value={selectedNode.name} valueStyle={{ fontSize: 14 }} />
          <Descriptions column={1} size="small" bordered style={{ marginTop: 8 }}>
            <Descriptions.Item label="类型">{selectedNode.type}</Descriptions.Item>
            {Object.entries(selectedNode.properties || {}).map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>
                {String(v).slice(0, 100)}
              </Descriptions.Item>
            ))}
          </Descriptions>
          <Space style={{ marginTop: 12 }}>
            <Button size="small" icon={<EditOutlined />} onClick={onEditNode}>
              编辑
            </Button>
            <Popconfirm title="删除该节点及其所有边？" onConfirm={onDelete}>
              <Button size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        </div>
      )}

      {selectedEdge && !selectedNode && (
        <div>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="起点">{selectedEdge.source}</Descriptions.Item>
            <Descriptions.Item label="终点">{selectedEdge.target}</Descriptions.Item>
            <Descriptions.Item label="类型">{selectedEdge.type}</Descriptions.Item>
            {Object.entries(selectedEdge.properties || {}).map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>
                {String(v).slice(0, 100)}
              </Descriptions.Item>
            ))}
          </Descriptions>
          <Space style={{ marginTop: 12 }}>
            <Button size="small" icon={<EditOutlined />} onClick={onEditEdge}>
              编辑
            </Button>
            <Popconfirm title="删除该关系？" onConfirm={onDelete}>
              <Button size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        </div>
      )}
    </div>
  )
}
