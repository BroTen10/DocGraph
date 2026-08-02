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

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  Card, Row, Col, Button, Space, message, Typography, Empty, Spin, Tag,
  Modal, Form, Input, Statistic, Descriptions, Popconfirm, Alert,
  Progress, Timeline, List, Tabs, Badge, Divider, Tooltip, Segmented,
} from 'antd'
import {
  ReloadOutlined, CheckCircleOutlined, DeleteOutlined, EditOutlined,
  ThunderboltOutlined, FileTextOutlined, ClockCircleOutlined,
  BulbOutlined, NodeIndexOutlined, ApartmentOutlined,
} from '@ant-design/icons'
import { graphApi, getErrorMessage, isFormValidationError, rulesApi } from '../api/client'
import type { BadgeProps } from 'antd'
import type { GraphData, GraphEdge, GraphBuildTaskStatus, GraphNode, GraphOntology, Rule, RuleSnapshot } from '../types'
import GraphView from '../components/GraphView'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { useRuleSet } from '../context/RuleSetContext'
import dayjs from 'dayjs'

const { Text, Paragraph } = Typography

/** 构建任务状态颜色（antd Badge 预设状态色） */
const BUILD_STATUS_COLOR: Record<string, NonNullable<BadgeProps['status']>> = {
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
  const [ontology, setOntology] = useState<GraphOntology | null>(null)
  const [loading, setLoading] = useState(false)

  // 批次 10 Phase D：图层过滤（本体 / 规则 / 执行）
  const [layerFilter, setLayerFilter] = useState<'all' | 'ontology' | 'rule' | 'execution'>('all')

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

  // 工作区
  const [rules, setRules] = useState<Rule[]>([])
  const [snapshots, setSnapshots] = useState<RuleSnapshot[]>([])
  const [activeTab, setActiveTab] = useState('progress')

  // 选中的节点详情
  const selectedNode = graph?.nodes.find((n) => n.name === selectedNodeName) || null

  // 节点/边所属层：本体（DocumentType/CheckIntent/Field）、规则（Rule）、执行（其余）
  const nodeLayer = (n: GraphNode): 'ontology' | 'rule' | 'execution' =>
    n.type === 'DocumentType' || n.type === 'CheckIntent' || n.type === 'Field'
      ? 'ontology'
      : n.type === 'Rule' ? 'rule' : 'execution'
  const edgeLayer = (e: GraphEdge): 'ontology' | 'execution' =>
    ['APPLIES_TO', 'CHECKS', 'INVOLVES', 'HAS_FIELD'].includes(e.type) ? 'ontology' : 'execution'

  const filteredGraph = useMemo<GraphData | null>(() => {
    if (!graph || layerFilter === 'all') return graph
    const nodes = graph.nodes.filter((n) => nodeLayer(n) === layerFilter)
    const names = new Set(nodes.map((n) => n.name))
    const edges = graph.edges.filter(
      (e) => edgeLayer(e) === layerFilter && names.has(e.source) && names.has(e.target)
    )
    return { ...graph, nodes, edges, node_count: nodes.length, edge_count: edges.length }
  }, [graph, layerFilter])

  // ============ 数据加载 ============
  const loadGraph = useCallback(async () => {
    if (!currentId) return
    setLoading(true)
    try {
      const g = await graphApi.getLatest(currentId)
      setGraph(g)
      // 批次 10 Phase D：加载本体层概览（旧图无本体节点时返回空清单）
      try {
        setOntology(await graphApi.getOntology(g.graph_id))
      } catch (e) {
        setOntology(null)
      }
    } catch (e) {
      if (e && typeof e === 'object' && (e as { response?: { status?: number } }).response?.status === 404) {
        setGraph(null)
      } else {
        message.error('加载图谱失败: ' + getErrorMessage(e))
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
    } catch (e) {
      console.warn('加载工作区(规则/快照)失败:', e)
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
    } catch (e) {
      console.warn('加载构建任务列表失败:', e)
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
      } catch (e) {
        console.warn('图谱构建轮询失败:', e)
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
    } catch (e) {
      setBuilding(false)
      message.error('启动构建失败: ' + getErrorMessage(e))
    }
  }

  // 规则文档导入功能已统一移至 RulesPage(那里有完整进度展示),此处不再提供入口

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
      const updated = await graphApi.confirm(graph.graph_id, [op])
      setGraph(updated)
      setEditOpen(false)
      message.success('已更新并写入 Neo4j')
    } catch (e) {
      if (isFormValidationError(e)) return
      message.error('保存失败: ' + getErrorMessage(e))
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
    } catch (e) {
      message.error('删除失败: ' + getErrorMessage(e))
    }
  }

  const confirmGraph = async () => {
    if (!graph) return
    try {
      await graphApi.confirm(graph.graph_id, [])
      message.success('已确认生效')
    } catch (e) {
      message.error('确认失败: ' + getErrorMessage(e))
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
            styles={{ body: { padding: 0 } }}
            style={{ minHeight: 600 }}
          >
            {loading ? (
              <div style={{ textAlign: 'center', padding: 120 }}>
                <Spin size="large" tip="加载图谱中..."><div style={{ padding: 40 }} /></Spin>
              </div>
            ) : !graph ? (
              <EmptyState
                description={
                  <>
                    <div style={{ marginBottom: 4 }}>暂无图谱</div>
                    <Paragraph type="secondary" style={{ margin: 0 }}>
                      请先到「规则管理」页导入规则文档,再回到此处构建图谱
                    </Paragraph>
                  </>
                }
                padding={80}
                action={
                  <Button
                    icon={<ThunderboltOutlined />}
                    loading={building}
                    onClick={() => handleBuildAsync(false)}
                  >
                    构建图谱
                  </Button>
                }
              />
            ) : (
              <>
                <div style={{ padding: '10px 12px', borderBottom: '1px solid #f0f0f0' }}>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 0, padding: '4px 12px', flex: '1 1 auto' }}
                      message="点击节点或边查看详情。黄色边框节点为低置信度（需人工确认）。"
                    />
                    <Segmented
                      size="small"
                      value={layerFilter}
                      onChange={(v) => setLayerFilter(v as typeof layerFilter)}
                      options={[
                        { label: '全部', value: 'all' },
                        { label: '本体', value: 'ontology' },
                        { label: '规则', value: 'rule' },
                        { label: '执行', value: 'execution' },
                      ]}
                    />
                  </Space>
                </div>
                <GraphView
                  graph={filteredGraph}
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
          <Card styles={{ body: { padding: 0 } }}>
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
                // ============ 本体概览（批次 10 Phase D） ============
                {
                  key: 'ontology',
                  label: (
                    <span>
                      <NodeIndexOutlined /> 本体概览
                      <Badge
                        count={(ontology?.doc_types.length ?? 0) + (ontology?.check_intents.length ?? 0)}
                        style={{ marginLeft: 4 }}
                        size="small"
                      />
                    </span>
                  ),
                  children: <OntologyPanel ontology={ontology} />,
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
              <Badge status={BUILD_STATUS_COLOR[buildTask.status]} text={buildTask.stage} />
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
                      status={BUILD_STATUS_COLOR[task.status]}
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
                        <Tag color="blue" style={{ fontSize: 10 }}>{rule.doc_type || '整批/全部'}</Tag>
                        <Tag style={{ fontSize: 10 }}>{rule.check_category || '未分类'}</Tag>
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

// ============ 子组件：本体概览面板（批次 10 Phase D） ============
function OntologyPanel({ ontology }: { ontology: GraphOntology | null }) {
  if (!ontology) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Empty description="暂无本体数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        <Paragraph type="secondary" style={{ marginTop: 8 }}>
          构建图谱后，此处展示从规则中抽象出的文档类型、检查意图与规则清单
        </Paragraph>
      </div>
    )
  }
  const { doc_types, check_intents, rules } = ontology
  return (
    <div style={{ padding: 12 }}>
      {doc_types.length === 0 && check_intents.length === 0 && rules.length === 0 ? (
        <Empty description="当前图谱尚未包含本体层（旧图需重新构建）" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Tabs
          size="small"
          items={[
            {
              key: 'doc_types',
              label: `文档类型（${doc_types.length}）`,
              children: (
                <List
                  size="small"
                  dataSource={doc_types}
                  renderItem={(dt) => {
                    const p = dt.props || {}
                    const name = (p.display_name as string) || dt.name
                    const fields = dt.fields || []
                    return (
                      <List.Item style={{ padding: '6px 0' }}>
                        <div style={{ width: '100%' }}>
                          <Space size={4} wrap>
                            <Tag color="cyan" style={{ fontSize: 11 }}>{name}</Tag>
                            {p.is_required === true && <Tag color="red" style={{ fontSize: 10 }}>必备</Tag>}
                            {!!p.status && p.status !== 'active' && (
                              <Tag style={{ fontSize: 10 }}>{p.status === 'pending_review' ? '待确认' : String(p.status)}</Tag>
                            )}
                          </Space>
                          {p.description ? (
                            <div style={{ fontSize: 12, color: '#666', marginTop: 2 }}>{String(p.description)}</div>
                          ) : null}
                          {fields.length > 0 && (
                            <div style={{ marginTop: 4 }}>
                              <Text type="secondary" style={{ fontSize: 11 }}>
                                字段：{fields.map((f) => f.split('|')[0]).join('、')}
                              </Text>
                            </div>
                          )}
                        </div>
                      </List.Item>
                    )
                  }}
                  locale={{ emptyText: '暂无文档类型' }}
                />
              ),
            },
            {
              key: 'intents',
              label: `检查意图（${check_intents.length}）`,
              children: (
                <List
                  size="small"
                  dataSource={check_intents}
                  renderItem={(it) => {
                    const p = it.props || {}
                    const name = (p.display_name as string) || it.name
                    return (
                      <List.Item style={{ padding: '6px 0' }}>
                        <Space>
                          <Tag color="purple" style={{ fontSize: 11 }}>{name}</Tag>
                          <Text type="secondary" style={{ fontSize: 11 }}>{it.rule_count} 条规则</Text>
                        </Space>
                      </List.Item>
                    )
                  }}
                  locale={{ emptyText: '暂无检查意图' }}
                />
              ),
            },
            {
              key: 'rules',
              label: `规则（${rules.length}）`,
              children: (
                <List
                  size="small"
                  dataSource={rules}
                  renderItem={(r) => {
                    const p = r.props || {}
                    return (
                      <List.Item style={{ padding: '6px 0' }}>
                        <Text style={{ fontSize: 12 }}>{String(p.rule_text || r.name)}</Text>
                      </List.Item>
                    )
                  }}
                  locale={{ emptyText: '暂无规则' }}
                  style={{ maxHeight: 380, overflow: 'auto' }}
                />
              ),
            },
          ]}
        />
      )}
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
