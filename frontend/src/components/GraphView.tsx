/**
 * 知识图谱可视化组件（D3.js 实现）
 *
 * 1:1 模仿 MiroFish-Explorer 的 GraphPanel.vue 风格：
 * - D3.js v7 forceSimulation + 原生 SVG
 * - 浅色主题 + 点阵网格背景
 * - 节点按类型着色（10色固定调色板），固定半径 r=10
 * - 多边曲率分散算法（同一对节点间的多条边按曲率均匀分散）
 * - 自环合并为圆弧显示
 * - 边标签带白色 rect 背景，水平不旋转
 * - 缩放 scaleExtent [0.1, 4] + 拖拽带 3px 阈值
 * - 节点点击高亮相邻边 + glow 滤镜
 * - 边标签开关 / 重置视图 / 刷新 工具栏
 * - 图例（左下角，按节点类型）
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import type { GraphData, GraphNode, GraphEdge } from '../types'
import { Button, Space, Switch, Tooltip } from 'antd'
import {
  ReloadOutlined, FullscreenOutlined, ColumnHeightOutlined, AimOutlined,
} from '@ant-design/icons'

/** MiroFish 10色固定调色板 */
const CATEGORY_COLORS = [
  '#FF6B35', '#004E89', '#7B2D8E', '#1A936F', '#C5283D',
  '#E9724C', '#3498db', '#9b59b6', '#27ae60', '#f39c12',
]

/** 节点类型颜色映射（优先使用业务语义色，回退到 CATEGORY_COLORS 顺序色） */
const NODE_TYPE_COLOR_MAP: Record<string, string> = {
  CheckRoot: '#7B2D8E',
  RequiredDoc: '#004E89',
  StampRequirement: '#FF6B35',
  Field: '#1A936F',
  Rule: '#C5283D',
}

const DEFAULT_NODE_COLOR = '#9a60b4'
const DEFAULT_EDGE_COLOR = '#e4e7ed'
const HIGHLIGHT_COLOR = '#E91E63'
const HOVER_COLOR = '#333'

/** 取节点颜色：先查业务映射，再按类型首次出现顺序分配 CATEGORY_COLORS */
function useTypeColorMap(nodes: GraphNode[]) {
  const typeOrder = useMemo(() => {
    const seen = new Set<string>()
    const order: string[] = []
    nodes.forEach((n) => {
      const t = n.type || 'Field'
      if (!seen.has(t)) {
        seen.add(t)
        order.push(t)
      }
    })
    return order
  }, [nodes])

  return useMemo(() => {
    const m: Record<string, string> = { ...NODE_TYPE_COLOR_MAP }
    let colorIdx = 0
    typeOrder.forEach((t) => {
      if (!m[t]) {
        m[t] = CATEGORY_COLORS[colorIdx % CATEGORY_COLORS.length]
        colorIdx++
      }
    })
    return m
  }, [typeOrder])
}

/** 取节点显示名（截断长名） */
function getDisplayName(name: string, maxLen = 8): string {
  if (!name) return ''
  return name.length > maxLen ? name.slice(0, maxLen) + '…' : name
}

interface GraphViewProps {
  graph: GraphData | null
  selectedNodeName?: string | null
  selectedEdgeKey?: string | null
  onNodeClick?: (nodeName: string) => void
  onEdgeClick?: (source: string, target: string) => void
  onBackgroundClick?: () => void
  height?: number | string
  onRefresh?: () => void
}

/** 预处理边：为每条边计算 pairIndex / pairTotal / isSelfLoop / isReversed / curvature */
interface ProcessedEdge extends GraphEdge {
  pairIndex: number
  pairTotal: number
  isSelfLoop: boolean
  isReversed: boolean
  curvature: number
}

function processEdges(edges: GraphEdge[]): ProcessedEdge[] {
  // 按 (source,target) 分组（source 字典序在前）
  const pairKey = (s: string, t: string) => (s < t ? `${s}||${t}` : `${t}||${s}`)
  const pairCount = new Map<string, number>()
  const pairDirection = new Map<string, [string, string]>()

  edges.forEach((e) => {
    if (e.source === e.target) return // 自环单独处理
    const k = pairKey(e.source, e.target)
    pairCount.set(k, (pairCount.get(k) || 0) + 1)
    if (!pairDirection.has(k)) pairDirection.set(k, [e.source, e.target])
  })

  const counters = new Map<string, number>()
  return edges.map((e) => {
    if (e.source === e.target) {
      return {
        ...e,
        pairIndex: 0,
        pairTotal: 1,
        isSelfLoop: true,
        isReversed: false,
        curvature: 0,
      }
    }
    const k = pairKey(e.source, e.target)
    const total = pairCount.get(k) || 1
    const idx = counters.get(k) || 0
    counters.set(k, idx + 1)
    const [firstSrc] = pairDirection.get(k) || [e.source, e.target]
    const isReversed = e.source !== firstSrc
    // MiroFish 公式
    const curvatureRange = Math.min(1.2, 0.6 + total * 0.15)
    const curvature = total === 1
      ? 0
      : ((idx / (total - 1)) - 0.5) * curvatureRange * 2
    return {
      ...e,
      pairIndex: idx,
      pairTotal: total,
      isSelfLoop: false,
      isReversed,
      curvature,
    }
  })
}

/** 生成二次贝塞尔曲线路径 */
function arcPath(sx: number, sy: number, tx: number, ty: number, curvature: number, pairTotal: number): string {
  const dx = tx - sx
  const dy = ty - sy
  const dist = Math.sqrt(dx * dx + dy * dy) || 1
  const offsetRatio = 0.25 + pairTotal * 0.05
  const baseOffset = Math.max(35, dist * offsetRatio)
  const cx = (sx + tx) / 2 + (-dy / dist) * curvature * baseOffset
  const cy = (sy + ty) / 2 + (dx / dist) * curvature * baseOffset
  return `M${sx},${sy} Q${cx},${cy} ${tx},${ty}`
}

/** 自环圆弧路径 */
function selfLoopPath(cx: number, cy: number): string {
  const r = 30
  // 从节点上方出发的圆弧
  return `M${cx},${cy - 10} A${r},${r} 0 1,1 ${cx + 0.01},${cy - 10.01}`
}

export default function GraphView({
  graph,
  selectedNodeName,
  selectedEdgeKey,
  onNodeClick,
  onEdgeClick,
  onBackgroundClick,
  height = 560,
  onRefresh,
}: GraphViewProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [showEdgeLabels, setShowEdgeLabels] = useState(true)
  const [containerReady, setContainerReady] = useState(0) // 用于触发重渲
  const typeColorMap = useTypeColorMap(graph?.nodes || [])

  // 暴露给重置按钮的 zoom 行为
  const zoomBehaviorRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null)

  // 主渲染逻辑
  useEffect(() => {
    if (!svgRef.current || !graph) return

    const svgEl = svgRef.current
    const containerEl = containerRef.current
    if (!containerEl) return

    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()

    // 等待容器有尺寸再渲染（防止初次挂载时 clientWidth=0 导致 viewBox=0×0）
    const width = containerEl.clientWidth
    const heightVal = typeof height === 'number' ? height : containerEl.clientHeight
    if (width === 0 || heightVal === 0) {
      // 容器还没布局好，用 ResizeObserver 等
      const ro = new ResizeObserver((entries) => {
        const rect = entries[0].contentRect
        if (rect.width > 0 && rect.height > 0) {
          ro.disconnect()
          // 重新触发本 effect
          setContainerReady((v) => v + 1)
        }
      })
      ro.observe(containerEl)
      return () => ro.disconnect()
    }

    svg.attr('viewBox', `0 0 ${width} ${heightVal}`)
    svg.attr('width', '100%')
    svg.attr('height', typeof height === 'number' ? `${height}px` : height)

    // ===== 定义 =====
    const defs = svg.append('defs')

    // glow 滤镜（高亮节点用）
    const glowFilter = defs.append('filter')
      .attr('id', 'glow-filter')
      .attr('x', '-50%').attr('y', '-50%')
      .attr('width', '200%').attr('height', '200%')
    glowFilter.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'coloredBlur')
    const feMerge = glowFilter.append('feMerge')
    feMerge.append('feMergeNode').attr('in', 'coloredBlur')
    feMerge.append('feMergeNode').attr('in', 'SourceGraphic')

    // 点阵 pattern（必须在 rect 引用前定义）
    const pattern = defs.append('pattern')
      .attr('id', 'dot-pattern')
      .attr('width', 24)
      .attr('height', 24)
      .attr('patternUnits', 'userSpaceOnUse')
    pattern.append('circle')
      .attr('cx', 12).attr('cy', 12).attr('r', 1.5)
      .attr('fill', '#D0D0D0')

    // ===== 背景层（点阵网格） =====
    svg.append('rect')
      .attr('width', width)
      .attr('height', heightVal)
      .attr('fill', 'url(#dot-pattern)')
    svg.style('background-color', '#f5f7fa')

    // ===== 主图层（应用 zoom transform） =====
    const g = svg.append('g').attr('class', 'main-group')

    // ===== 数据准备 =====
    // 后端边的 source/target 用节点 name 引用，所以 D3 link id accessor 也用 name
    const nodes = graph.nodes.map((n) => ({
      ...n,
      id: String(n.id ?? n.name),
    } as GraphNode & { x?: number; y?: number; vx?: number; vy?: number; fx?: number; fy?: number }))

    const edges = processEdges(graph.edges)

    // ===== 仿真 =====
    const simulation = d3.forceSimulation(nodes as any)
      .force('link', d3.forceLink(edges as any).id((d: any) => d.name).distance((d: any) => {
        const baseDistance = 150
        return baseDistance + (((d.pairTotal as number) || 1) - 1) * 50
      }))
      .force('charge', d3.forceManyBody().strength(-400))
      .force('center', d3.forceCenter(width / 2, heightVal / 2))
      .force('collide', d3.forceCollide(50))
      .force('x', d3.forceX(width / 2).strength(0.04))
      .force('y', d3.forceY(heightVal / 2).strength(0.04))

    // ===== 边层 =====
    const linkGroup = g.append('g').attr('class', 'links')
    const linkPaths = linkGroup.selectAll('path')
      .data(edges)
      .enter()
      .append('path')
      .attr('fill', 'none')
      .attr('stroke', DEFAULT_EDGE_COLOR)
      .attr('stroke-width', 1.5)
      .style('cursor', 'pointer')

    // 边标签背景 + 文本
    const linkLabelGroup = g.append('g').attr('class', 'link-labels')
    const linkLabelBg = linkLabelGroup.selectAll('rect')
      .data(edges)
      .enter()
      .append('rect')
      .attr('rx', 3)
      .attr('ry', 3)
      .attr('fill', 'white')
      .attr('stroke', '#e4e7ed')
      .attr('stroke-width', 0.5)
      .style('pointer-events', 'all')
      .style('display', showEdgeLabels ? 'inline' : 'none')

    const linkLabelText = linkLabelGroup.selectAll('text')
      .data(edges)
      .enter()
      .append('text')
      .attr('font-size', 9)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('fill', '#606266')
      .style('pointer-events', 'none')
      .style('display', showEdgeLabels ? 'inline' : 'none')
      .text((d: ProcessedEdge) => {
        const op = (d.properties as Record<string, unknown>)?.operator
        return String(op || d.type)
      })

    // ===== 节点层 =====
    const nodeGroup = g.append('g').attr('class', 'nodes')
    const nodeG = nodeGroup.selectAll('g')
      .data(nodes)
      .enter()
      .append('g')
      .attr('class', 'node')
      .style('cursor', 'pointer')

    nodeG.append('circle')
      .attr('r', 10)
      .attr('fill', (d) => typeColorMap[d.type || 'Field'] || DEFAULT_NODE_COLOR)
      .attr('stroke', '#fff')
      .attr('stroke-width', 2.5)

    nodeG.append('text')
      .attr('dx', 14)
      .attr('dy', 4)
      .attr('font-size', 11)
      .attr('fill', '#303133')
      .style('pointer-events', 'none')
      .text((d) => getDisplayName(d.name))

    // ===== 交互：拖拽（带 3px 阈值） =====
    let dragStart: [number, number] | null = null
    let dragMoved = false

    const drag = d3.drag<any, any>()
      .on('start', (event, d) => {
        dragStart = [event.x, event.y]
        dragMoved = false
        if (!event.active) simulation.alphaTarget(0.3).restart()
        d.fx = d.x
        d.fy = d.y
      })
      .on('drag', (event, d) => {
        if (dragStart) {
          const dx = event.x - dragStart[0]
          const dy = event.y - dragStart[1]
          if (Math.sqrt(dx * dx + dy * dy) > 3) dragMoved = true
        }
        d.fx = event.x
        d.fy = event.y
      })
      .on('end', (event, d) => {
        if (!event.active) simulation.alphaTarget(0)
        if (!dragMoved) {
          // 未真正拖动 → 视为点击
          handleNodeClickInternal(d)
        }
        d.fx = null
        d.fy = null
      })

    nodeG.call(drag as any)

    // ===== 交互：节点点击高亮 + 外部回调 =====
    let currentSelectedNode: GraphNode | null = null

    function clearHighlight() {
      nodeG.selectAll('circle')
        .attr('stroke', '#fff')
        .attr('stroke-width', 2.5)
        .style('filter', 'none')
      linkPaths
        .attr('stroke', DEFAULT_EDGE_COLOR)
        .attr('stroke-width', 1.5)
      linkLabelBg.attr('fill', 'white').attr('stroke', '#e4e7ed')
      linkLabelText.attr('fill', '#606266')
    }

    function highlightNode(node: GraphNode & { x?: number; y?: number }) {
      clearHighlight()
      currentSelectedNode = node
      // 高亮目标节点
      nodeG.selectAll('circle').attr('stroke', (d: any) =>
        d.id === node.id ? HIGHLIGHT_COLOR : '#fff'
      )
      nodeG.selectAll('circle').attr('stroke-width', (d: any) =>
        d.id === node.id ? 4 : 2.5
      )
      // 高亮相邻边
      const adjacentEdgeKeys = new Set<string>()
      edges.forEach((e, i) => {
        const sid = e.source as any
        const tid = e.target as any
        const sName = typeof sid === 'object' ? sid.name : sid
        const tName = typeof tid === 'object' ? tid.name : tid
        if (sName === node.name || tName === node.name) {
          adjacentEdgeKeys.add(`${i}`)
        }
      })
      linkPaths.each(function (d: any, i: number) {
        const sid = d.source as any
        const tid = d.target as any
        const sName = typeof sid === 'object' ? sid.name : sid
        const tName = typeof tid === 'object' ? tid.name : tid
        if (sName === node.name || tName === node.name) {
          d3.select(this).attr('stroke', HIGHLIGHT_COLOR).attr('stroke-width', 2.5)
        }
      })
      onNodeClick?.(node.name)
    }

    function handleNodeClickInternal(node: GraphNode & { x?: number; y?: number }) {
      highlightNode(node)
    }

    // ===== 交互：节点悬停 =====
    nodeG
      .on('mouseenter', function (_event, d) {
        if (currentSelectedNode?.id !== d.id) {
          d3.select(this).select('circle')
            .attr('stroke', HOVER_COLOR)
            .attr('stroke-width', 3)
        }
      })
      .on('mouseleave', function (_event, d) {
        if (currentSelectedNode?.id !== d.id) {
          d3.select(this).select('circle')
            .attr('stroke', '#fff')
            .attr('stroke-width', 2.5)
        }
      })

    // ===== 交互：边点击 =====
    linkPaths.on('click', function (event, d) {
      event.stopPropagation()
      clearHighlight()
      currentSelectedNode = null
      d3.select(this).attr('stroke', '#3498db').attr('stroke-width', 3)
      const sid = d.source as any
      const tid = d.target as any
      const sName = typeof sid === 'object' ? sid.name : sid
      const tName = typeof tid === 'object' ? tid.name : tid
      onEdgeClick?.(sName, tName)
    })

    linkLabelBg.on('click', function (event, d) {
      event.stopPropagation()
      const sid = d.source as any
      const tid = d.target as any
      const sName = typeof sid === 'object' ? sid.name : sid
      const tName = typeof tid === 'object' ? tid.name : tid
      onEdgeClick?.(sName, tName)
    })

    // ===== 交互：空白点击 =====
    svg.on('click', (event) => {
      if (event.target === svg.node() || (event.target as SVGRectElement).tagName === 'rect') {
        clearHighlight()
        currentSelectedNode = null
        onBackgroundClick?.()
      }
    })

    // ===== 交互：缩放/平移 =====
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => {
        g.attr('transform', event.transform.toString())
      })
    svg.call(zoom as any)
    zoomBehaviorRef.current = zoom

    // ===== 仿真 tick 更新 =====
    simulation.on('tick', () => {
      // 边路径
      linkPaths.attr('d', (d: any) => {
        const s = d.source as any
        const t = d.target as any
        const sx = typeof s === 'object' ? s.x : 0
        const sy = typeof s === 'object' ? s.y : 0
        const tx = typeof t === 'object' ? t.x : 0
        const ty = typeof t === 'object' ? t.y : 0
        if (d.isSelfLoop) {
          return selfLoopPath(sx, sy)
        }
        return arcPath(sx, sy, tx, ty, d.curvature, d.pairTotal)
      })

      // 边标签位置（中点 + 曲线偏移）
      const labelOffset = 0
      linkLabelText
        .attr('x', (d: any) => {
          const s = d.source as any
          const t = d.target as any
          const sx = typeof s === 'object' ? s.x : 0
          const tx = typeof t === 'object' ? t.x : 0
          return (sx + tx) / 2 + labelOffset
        })
        .attr('y', (d: any) => {
          const s = d.source as any
          const t = d.target as any
          const sy = typeof s === 'object' ? s.y : 0
          const ty = typeof t === 'object' ? t.y : 0
          return (sy + ty) / 2
        })

      // 边标签背景跟随
      linkLabelBg.each(function (d: any) {
        const s = d.source as any
        const t = d.target as any
        const sx = typeof s === 'object' ? s.x : 0
        const tx = typeof t === 'object' ? t.x : 0
        const sy = typeof s === 'object' ? s.y : 0
        const ty = typeof t === 'object' ? t.y : 0
        const cx = (sx + tx) / 2
        const cy = (sy + ty) / 2
        const sel = d3.select(this)
        // 根据文本宽度调整 rect 尺寸（简化：用固定 padding）
        const text = String((d.properties as Record<string, unknown>)?.operator || d.type)
        const w = Math.max(20, text.length * 6 + 8)
        sel.attr('x', cx - w / 2).attr('y', cy - 7).attr('width', w).attr('height', 14)
      })

      // 节点位置
      nodeG.attr('transform', (d: any) => `translate(${d.x},${d.y})`)
    })

    // ===== 外部 selectedNodeName 变化时联动高亮（独立 effect，避免重跑仿真） =====
    // 见下方 useEffect

    // ===== 清理 =====
    return () => {
      simulation.stop()
      svg.selectAll('*').remove()
      svg.on('click', null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, showEdgeLabels, containerReady])

  // ===== 外部 selectedNodeName 变化时联动高亮（不重跑仿真） =====
  useEffect(() => {
    if (!svgRef.current || !graph || !selectedNodeName) return
    const svg = d3.select(svgRef.current)
    const target = graph.nodes.find((n) => n.name === selectedNodeName)
    if (!target) return
    const targetId = String(target.id ?? target.name)
    svg.selectAll('.node circle')
      .attr('stroke', (d: any) => (d.id === targetId ? HIGHLIGHT_COLOR : '#fff'))
      .attr('stroke-width', (d: any) => (d.id === targetId ? 4 : 2.5))
      .style('filter', (d: any) => (d.id === targetId ? 'url(#glow-filter)' : 'none'))
  }, [selectedNodeName, graph])

  // ===== 重置视图 =====
  const handleResetView = () => {
    const svg = d3.select(svgRef.current!)
    if (zoomBehaviorRef.current) {
      svg.transition().duration(500).call(zoomBehaviorRef.current.transform as any, d3.zoomIdentity)
    }
  }

  // ===== 图例数据 =====
  const legendItems = useMemo(() => {
    if (!graph) return []
    const seen = new Map<string, string>()
    graph.nodes.forEach((n) => {
      const t = n.type || 'Field'
      if (!seen.has(t)) seen.set(t, typeColorMap[t] || DEFAULT_NODE_COLOR)
    })
    return Array.from(seen.entries()).map(([type, color]) => ({ type, color }))
  }, [graph, typeColorMap])

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        width: '100%',
        height: typeof height === 'number' ? `${height}px` : height,
        background: '#f5f7fa',
        borderRadius: 6,
        overflow: 'hidden',
        border: '1px solid #e4e7ed',
      }}
    >
      <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />

      {/* 工具栏（右上角） */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          right: 12,
          background: 'rgba(255, 255, 255, 0.95)',
          borderRadius: 6,
          padding: '6px 10px',
          boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
          border: '1px solid #e4e7ed',
        }}
      >
        <Space size="small">
          <Tooltip title="重置视图">
            <Button
              size="small"
              icon={<AimOutlined />}
              onClick={handleResetView}
            />
          </Tooltip>
          {onRefresh && (
            <Tooltip title="刷新图谱">
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={onRefresh}
              />
            </Tooltip>
          )}
          <Tooltip title="显示边标签">
            <span style={{ display: 'inline-flex', alignItems: 'center' }}>
              <Switch
                size="small"
                checked={showEdgeLabels}
                onChange={setShowEdgeLabels}
              />
            </span>
          </Tooltip>
        </Space>
      </div>

      {/* 图例（左下角） */}
      {legendItems.length > 0 && (
        <div
          style={{
            position: 'absolute',
            left: 12,
            bottom: 12,
            background: 'rgba(255, 255, 255, 0.95)',
            borderRadius: 6,
            padding: '8px 12px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
            border: '1px solid #e4e7ed',
            fontSize: 12,
            maxWidth: 220,
          }}
        >
          <div style={{ fontWeight: 600, color: HIGHLIGHT_COLOR, marginBottom: 6 }}>
            Entity Types
          </div>
          {legendItems.map((item) => (
            <div
              key={item.type}
              style={{ display: 'flex', alignItems: 'center', marginBottom: 2 }}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  background: item.color,
                  marginRight: 8,
                }}
              />
              <span style={{ color: '#303133' }}>{item.type}</span>
            </div>
          ))}
        </div>
      )}

      {/* 统计信息（右下角） */}
      {graph && (
        <div
          style={{
            position: 'absolute',
            right: 12,
            bottom: 12,
            background: 'rgba(255, 255, 255, 0.95)',
            borderRadius: 6,
            padding: '6px 10px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
            border: '1px solid #e4e7ed',
            fontSize: 11,
            color: '#606266',
          }}
        >
          <ColumnHeightOutlined /> 节点 {graph.node_count} · 关系 {graph.edge_count}
        </div>
      )}
    </div>
  )
}
