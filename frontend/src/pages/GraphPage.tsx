import { useState, useEffect, useMemo, useRef } from 'react'
import {
  Card, Row, Col, Button, Space, message, Typography, Empty, Spin, Tag,
  Modal, Form, Input, Statistic, Drawer, Descriptions, Popconfirm, Alert,
} from 'antd'
import { ReloadOutlined, CheckCircleOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import { graphApi } from '../api/client'
import type { GraphData, GraphNode, GraphEdge } from '../types'

const { Title, Text } = Typography

// 简单的力导向布局：圆形分布 + 轻量迭代
interface PositionedNode {
  id: string | number
  name: string
  type: string
  properties: Record<string, unknown>
  x: number
  y: number
  lowConfidence?: boolean
}

const WIDTH = 760
const HEIGHT = 560
const ITERATIONS = 200

function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): PositionedNode[] {
  if (nodes.length === 0) return []
  const positioned: PositionedNode[] = nodes.map((n, i) => {
    const angle = (i / nodes.length) * 2 * Math.PI
    return {
      id: n.id ?? n.name,
      name: n.name,
      type: n.type,
      properties: n.properties || {},
      x: WIDTH / 2 + Math.cos(angle) * 180,
      y: HEIGHT / 2 + Math.sin(angle) * 180,
      lowConfidence: Boolean((n.properties as any)?.low_confidence),
    }
  })
  const nameToNode = new Map(positioned.map((n) => [n.name, n]))

  // 简单力导向迭代
  const k = 80 // 理想距离
  for (let iter = 0; iter < ITERATIONS; iter++) {
    const disp: Record<string, { x: number; y: number }> = {}
    positioned.forEach((n) => (disp[n.name] = { x: 0, y: 0 }))

    // 斥力
    for (let i = 0; i < positioned.length; i++) {
      for (let j = i + 1; j < positioned.length; j++) {
        const a = positioned[i], b = positioned[j]
        let dx = a.x - b.x, dy = a.y - b.y
        let dist = Math.sqrt(dx * dx + dy * dy) || 0.01
        const force = (k * k) / dist
        dx = (dx / dist) * force
        dy = (dy / dist) * force
        disp[a.name].x += dx
        disp[a.name].y += dy
        disp[b.name].x -= dx
        disp[b.name].y -= dy
      }
    }
    // 引力（边）
    edges.forEach((e) => {
      const a = nameToNode.get(e.source)
      const b = nameToNode.get(e.target)
      if (!a || !b) return
      let dx = a.x - b.x, dy = a.y - b.y
      let dist = Math.sqrt(dx * dx + dy * dy) || 0.01
      const force = (dist * dist) / k
      dx = (dx / dist) * force
      dy = (dy / dist) * force
      disp[a.name].x -= dx
      disp[a.name].y -= dy
      disp[b.name].x += dx
      disp[b.name].y += dy
    })

    // 应用位移（带温度衰减）
    const temperature = Math.max(0.5, 30 * (1 - iter / ITERATIONS))
    positioned.forEach((n) => {
      const d = disp[n.name]
      const dist = Math.sqrt(d.x * d.x + d.y * d.y) || 0.01
      n.x += (d.x / dist) * Math.min(dist, temperature)
      n.y += (d.y / dist) * Math.min(dist, temperature)
      // 边界
      n.x = Math.max(40, Math.min(WIDTH - 40, n.x))
      n.y = Math.max(40, Math.min(HEIGHT - 40, n.y))
    })
  }
  return positioned
}

export default function GraphPage() {
  const [graph, setGraph] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedNode, setSelectedNode] = useState<PositionedNode | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [editForm] = Form.useForm()
  const [editTarget, setEditTarget] = useState<{ kind: 'node' | 'edge'; name?: string; source?: string; target?: string } | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const g = await graphApi.getLatest()
      setGraph(g)
    } catch (e: any) {
      if (e?.response?.status === 404) {
        setGraph(null)
        message.info('暂无图谱，请先到"规则管理"页面构建图谱')
      } else {
        message.error('加载图谱失败: ' + (e?.message || e))
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const positionedNodes = useMemo(() => {
    if (!graph) return []
    return layoutGraph(graph.nodes, graph.edges)
  }, [graph])

  const nodeByName = useMemo(() => new Map(positionedNodes.map((n) => [n.name, n])), [positionedNodes])

  const openEditNode = (n: PositionedNode) => {
    setEditTarget({ kind: 'node', name: n.name })
    editForm.setFieldsValue({
      properties_json: JSON.stringify(n.properties, null, 2),
    })
    setEditOpen(true)
  }

  const openEditEdge = (e: GraphEdge) => {
    setEditTarget({ kind: 'edge', source: e.source, target: e.target })
    editForm.setFieldsValue({
      properties_json: JSON.stringify(e.properties || {}, null, 2),
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
      if (selectedNode) edits.push({ op: 'delete_node', node_name: selectedNode.name })
      if (selectedEdge) edits.push({ op: 'delete_edge', source: selectedEdge.source, target: selectedEdge.target })
      if (edits.length === 0) return
      const updated = await graphApi.confirm(graph.graph_id, edits)
      setGraph(updated)
      setSelectedNode(null)
      setSelectedEdge(null)
      message.success('已删除')
    } catch (e: any) {
      message.error('删除失败: ' + (e?.message || e))
    }
  }

  return (
    <div>
      <Row justify="space-between" align="middle">
        <Col><Title level={4}>图谱确认</Title></Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
            <Popconfirm title="确认将当前图谱（含人工编辑）写入 Neo4j 生效？" onConfirm={async () => {
              if (!graph) return
              try {
                await graphApi.confirm(graph.graph_id, [])
                message.success('已确认生效')
              } catch (e: any) {
                message.error('确认失败: ' + (e?.message || e))
              }
            }}>
              <Button type="primary" icon={<CheckCircleOutlined />}>确认生效</Button>
            </Popconfirm>
          </Space>
        </Col>
      </Row>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
      ) : !graph ? (
        <Empty description="暂无图谱" />
      ) : (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={`当前图谱：${graph.graph_id}（节点 ${graph.node_count} / 关系 ${graph.edge_count}）`}
            description={'点击节点或边查看详情。黄色节点为低置信度（需人工确认）。编辑后点击"确认生效"写入 Neo4j。'}
          />
          <Row gutter={16}>
            <Col span={16}>
              <Card title="规则图谱可视化" bodyStyle={{ padding: 0 }}>
                <svg width={WIDTH} height={HEIGHT} style={{ background: '#fafafa', display: 'block' }}>
                  {/* 边 */}
                  {graph.edges.map((e, i) => {
                    const a = nodeByName.get(e.source)
                    const b = nodeByName.get(e.target)
                    if (!a || !b) return null
                    const isSel = selectedEdge === e
                    return (
                      <g key={i} onClick={() => { setSelectedEdge(e); setSelectedNode(null) }} style={{ cursor: 'pointer' }}>
                        <line
                          x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                          stroke={isSel ? '#ff7a45' : '#bbb'} strokeWidth={isSel ? 2 : 1}
                        />
                        <text
                          x={(a.x + b.x) / 2} y={(a.y + b.y) / 2}
                          fontSize={9} fill="#888" textAnchor="middle"
                          style={{ pointerEvents: 'none' }}
                        >
                          {(e.properties as any)?.operator || e.type}
                        </text>
                      </g>
                    )
                  })}
                  {/* 节点 */}
                  {positionedNodes.map((n) => {
                    const isSel = selectedNode?.name === n.name
                    const fill = n.lowConfidence ? '#faad14' : '#1890ff'
                    return (
                      <g key={n.name} onClick={() => { setSelectedNode(n); setSelectedEdge(null) }} style={{ cursor: 'pointer' }}>
                        <circle
                          cx={n.x} cy={n.y} r={isSel ? 14 : 10}
                          fill={fill} stroke={isSel ? '#000' : '#fff'} strokeWidth={isSel ? 2 : 1}
                        />
                        <text x={n.x} y={n.y + 24} fontSize={10} fill="#333" textAnchor="middle" style={{ pointerEvents: 'none' }}>
                          {n.name.length > 16 ? n.name.slice(0, 16) + '...' : n.name}
                        </text>
                      </g>
                    )
                  })}
                </svg>
              </Card>
            </Col>
            <Col span={8}>
              <Card title="详情">
                <Row gutter={16}>
                  <Col span={12}><Statistic title="节点数" value={graph.node_count} /></Col>
                  <Col span={12}><Statistic title="关系数" value={graph.edge_count} /></Col>
                </Row>
                {selectedNode ? (
                  <div style={{ marginTop: 16 }}>
                    <Descriptions title="节点属性" column={1} size="small" bordered>
                      <Descriptions.Item label="名称">{selectedNode.name}</Descriptions.Item>
                      <Descriptions.Item label="类型">{selectedNode.type}</Descriptions.Item>
                      {Object.entries(selectedNode.properties).map(([k, v]) => (
                        <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                      ))}
                    </Descriptions>
                    <Space style={{ marginTop: 12 }}>
                      <Button size="small" icon={<EditOutlined />} onClick={() => openEditNode(selectedNode)}>编辑</Button>
                      <Popconfirm title="删除该节点及其所有边？" onConfirm={deleteSelected}>
                        <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                      </Popconfirm>
                    </Space>
                  </div>
                ) : selectedEdge ? (
                  <div style={{ marginTop: 16 }}>
                    <Descriptions title="关系属性" column={1} size="small" bordered>
                      <Descriptions.Item label="起点">{selectedEdge.source}</Descriptions.Item>
                      <Descriptions.Item label="终点">{selectedEdge.target}</Descriptions.Item>
                      <Descriptions.Item label="类型">{selectedEdge.type}</Descriptions.Item>
                      {Object.entries(selectedEdge.properties || {}).map(([k, v]) => (
                        <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                      ))}
                    </Descriptions>
                    <Space style={{ marginTop: 12 }}>
                      <Button size="small" icon={<EditOutlined />} onClick={() => openEditEdge(selectedEdge)}>编辑</Button>
                      <Popconfirm title="删除该关系？" onConfirm={deleteSelected}>
                        <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                      </Popconfirm>
                    </Space>
                  </div>
                ) : (
                  <Text type="secondary" style={{ display: 'block', marginTop: 16 }}>点击图谱中的节点或边查看详情</Text>
                )}
              </Card>
            </Col>
          </Row>
        </>
      )}

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
