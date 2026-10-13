/**
 * 规则图谱可视化组件
 *
 * 用 d3 v7 的力导向仿真把后端返回的规则图谱画成原生 SVG：
 * - 节点按类型着色，半径随连接度增大（完全孤立的节点最小）
 * - 同一对节点之间的多条关系按方向铺开：同方向的多条依次外扩，
 *   方向相反的关系落在连线另一侧，单条关系走直线
 * - 自环关系画成挂在节点下方的环
 * - 关系标签水平放置、带浅色底衬，可在工具栏整体关闭
 *
 * 组件只负责绘制与交互回调，选中态由父组件持有：父组件通过
 * selectedNodeName / selectedEdgeKey 传回来，这里只改描边和滤镜，
 * 不重跑布局，避免选一下节点就抖一次。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import { Button, Space, Switch, Tooltip } from 'antd'
import { AimOutlined, ReloadOutlined } from '@ant-design/icons'
import type { GraphData, GraphEdge, GraphNode } from '../types'

/** 画布与线条配色 */
const CANVAS_BG = '#f5f7fa'
const GRID_DOT = '#d5dae2'
const EDGE_COLOR = '#d8dee9'
const EDGE_ACTIVE = '#2A6F9E'
const NODE_STROKE = '#ffffff'
const NODE_FOCUS = '#0B7285'
const NODE_HOVER = '#1f2933'
const LABEL_COLOR = '#5a6472'
const LABEL_ACTIVE = '#2A6F9E'
const LABEL_BG = '#ffffff'
const LABEL_BORDER = '#dfe4ec'
const NODE_LABEL_COLOR = '#2f3542'

/** 业务语义色：跨页面稳定的节点类型给固定色，方便一眼辨认 */
const TYPE_COLORS: Record<string, string> = {
  CheckRoot: '#5B4B8A',
  RequiredDoc: '#2A6F9E',
  StampRequirement: '#C4622D',
  Field: '#2F7D62',
  Rule: '#A63A4B',
  DocumentType: '#0F7A73',
  CheckIntent: '#D2691E',
}

/** 未登记类型按出现顺序取色 */
const FALLBACK_COLORS = [
  '#8E6C88', '#3B7EA1', '#B08968', '#4C956C', '#7D5BA6', '#D08C60',
  '#5F7A9B', '#9C6644', '#6A994E', '#A44A3F', '#556B8D', '#7F6A93',
]

/** 缩放范围：再小节点就只剩色点，再大空白太多 */
const MIN_SCALE = 0.15
const MAX_SCALE = 3
/** 指针位移超过这个像素数就按拖动处理，不再触发点击 */
const CLICK_SLOP = 4
const LABEL_FONT_SIZE = 10
const NODE_R_MIN = 7
const NODE_R_MAX = 18
/** 布局参数：斥力随节点数放大，避免大图挤成一团 */
const REPEL_BASE = -240
const REPEL_PER_NODE = 4
const REPEL_NODE_CAP = 50
const LINK_DISTANCE_BASE = 104
const LINK_DISTANCE_PER_EXTRA_EDGE = 22
const COLLIDE_PADDING = 14
const CENTER_PULL = 0.045
/** 平行关系铺开：间隔随关系条数略增，设上限防止弧太鼓 */
const LANE_GAP_BASE = 16
const LANE_GAP_PER_EDGE = 5
const LANE_GAP_MAX = 34
/** 同对节点的多条关系，标签沿弧线错开取样，避免几个算子挤在一个点上叠字 */
const LABEL_STAGGER = 0.18

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

interface Point {
  x: number
  y: number
}

/** 预处理后的边：额外带同对节点内的铺开位置 */
interface PlacedEdge extends GraphEdge {
  /** 同一对节点（无序）里的关系总条数 */
  laneCount: number
  /** 相对连线的垂直偏移像素，0 表示走直线 */
  laneOffset: number
  /** 标签在曲线上的取样位置（0~1），多条平行关系时错开摆放 */
  labelT: number
  /** 该边方向与节点对的规范方向（字典序小 → 大）相反 */
  reversed: boolean
  isSelfLoop: boolean
}

type SimNode = GraphNode & d3.SimulationNodeDatum
type SimLink = d3.SimulationLinkDatum<SimNode> & PlacedEdge

/** 构建期交给后续 effect 的场景控制器 */
interface SceneController {
  focusNode: (name: string | null) => void
  focusEdge: (key: string | null) => void
  clearFocus: () => void
  setLabelsVisible: (visible: boolean) => void
  resetView: () => void
}

/** 无序节点对做 key；用 \u0000 分隔，节点名里基本不会出现 */
function pairKey(a: string, b: string): string {
  return a <= b ? `${a}\u0000${b}` : `${b}\u0000${a}`
}

function endName(end: string | number | SimNode): string {
  return typeof end === 'object' && end !== null ? end.name : String(end)
}

function endPoint(end: string | number | SimNode): Point {
  return typeof end === 'object' && end !== null ? { x: end.x ?? 0, y: end.y ?? 0 } : { x: 0, y: 0 }
}

/** 节点半径：按连接度开方缩放，孤立节点最小 */
function buildRadii(graph: GraphData): Map<string, number> {
  const degree = new Map<string, number>()
  const bump = (name: string) => degree.set(name, (degree.get(name) ?? 0) + 1)
  graph.edges.forEach((e) => {
    bump(e.source)
    if (e.source !== e.target) bump(e.target)
  })
  const radii = new Map<string, number>()
  graph.nodes.forEach((n) => {
    const d = degree.get(n.name) ?? 0
    radii.set(n.name, Math.min(NODE_R_MAX, NODE_R_MIN + Math.sqrt(d) * 2.2))
  })
  return radii
}

/** 颜色表：先查业务语义色，未登记的按首见顺序兜底 */
function buildColorMap(nodes: GraphNode[]): Record<string, string> {
  const map: Record<string, string> = { ...TYPE_COLORS }
  let next = 0
  nodes.forEach((n) => {
    const t = n.type || 'Field'
    if (!map[t]) {
      map[t] = FALLBACK_COLORS[next % FALLBACK_COLORS.length]
      next += 1
    }
  })
  return map
}

/**
 * 给每条边算铺开位置。
 * 单条关系走直线；一对节点之间有多条时，同方向的按序号依次外扩。
 * 法线方向取自边自身的方向，所以方向相反的关系会自动落在连线另一侧，
 * 这里不要再按方向翻转符号，否则两次翻转抵消、正反两条会压在一起。
 */
function placeEdges(edges: GraphEdge[]): PlacedEdge[] {
  const pairTotal = new Map<string, number>()

  edges.forEach((e) => {
    if (e.source === e.target) return
    const k = pairKey(e.source, e.target)
    pairTotal.set(k, (pairTotal.get(k) ?? 0) + 1)
  })

  const dirSeen = new Map<string, number>()
  const pairSeen = new Map<string, number>()

  return edges.map((e) => {
    if (e.source === e.target) {
      return { ...e, laneCount: 1, laneOffset: 0, labelT: 0.5, reversed: false, isSelfLoop: true }
    }
    const k = pairKey(e.source, e.target)
    const total = pairTotal.get(k) ?? 1
    // 反向边的曲线参数方向与规范方向相反，取标签点时要把参数翻回来
    const reversed = e.source > e.target
    const idxInPair = pairSeen.get(k) ?? 0
    pairSeen.set(k, idxInPair + 1)
    const labelT =
      total === 1
        ? 0.5
        : Math.min(0.75, Math.max(0.25, 0.5 + (idxInPair - (total - 1) / 2) * LABEL_STAGGER))
    if (total === 1) {
      return { ...e, laneCount: 1, laneOffset: 0, labelT, reversed, isSelfLoop: false }
    }
    const dirKey = `${k}\u0001${e.source}`
    const index = dirSeen.get(dirKey) ?? 0
    dirSeen.set(dirKey, index + 1)
    const gap = Math.min(LANE_GAP_MAX, LANE_GAP_BASE + LANE_GAP_PER_EDGE * total)
    return {
      ...e,
      laneCount: total,
      laneOffset: (index + 0.5) * gap,
      labelT,
      reversed,
      isSelfLoop: false,
    }
  })
}

/** 带垂直偏移的连线：用三次贝塞尔模拟单侧弧，控制点取在弦中点外侧 */
function curvePath(s: Point, t: Point, offset: number): string {
  if (offset === 0) return `M${s.x},${s.y} L${t.x},${t.y}`
  const control = curveControl(s, t, offset)
  const c1x = s.x + ((control.x - s.x) * 2) / 3
  const c1y = s.y + ((control.y - s.y) * 2) / 3
  const c2x = t.x + ((control.x - t.x) * 2) / 3
  const c2y = t.y + ((control.y - t.y) * 2) / 3
  return `M${s.x},${s.y} C${c1x},${c1y} ${c2x},${c2y} ${t.x},${t.y}`
}

function curveControl(s: Point, t: Point, offset: number): Point {
  const dx = t.x - s.x
  const dy = t.y - s.y
  const len = Math.hypot(dx, dy) || 1
  return {
    x: (s.x + t.x) / 2 + (-dy / len) * offset,
    y: (s.y + t.y) / 2 + (dx / len) * offset,
  }
}

/** 取曲线上参数 u 处的位置，用来放关系标签 */
function curvePointAt(s: Point, t: Point, offset: number, u: number): Point {
  if (offset === 0) return { x: s.x + (t.x - s.x) * u, y: s.y + (t.y - s.y) * u }
  const control = curveControl(s, t, offset)
  const c1x = s.x + ((control.x - s.x) * 2) / 3
  const c1y = s.y + ((control.y - s.y) * 2) / 3
  const c2x = t.x + ((control.x - t.x) * 2) / 3
  const c2y = t.y + ((control.y - t.y) * 2) / 3
  const w0 = (1 - u) ** 3
  const w1 = 3 * (1 - u) ** 2 * u
  const w2 = 3 * (1 - u) * u ** 2
  const w3 = u ** 3
  return {
    x: w0 * s.x + w1 * c1x + w2 * c2x + w3 * t.x,
    y: w0 * s.y + w1 * c1y + w2 * c2y + w3 * t.y,
  }
}

/** 自环：从节点右下绕到左下，标签放在环的最低处 */
function selfLoopPath(p: Point, radius: number): { d: string; label: Point } {
  const half = radius * 0.62
  const depth = radius + 30
  const d =
    `M${p.x - half},${p.y + radius * 0.78} ` +
    `C${p.x - radius * 2.4},${p.y + depth} ${p.x + radius * 2.4},${p.y + depth} ${p.x + half},${p.y + radius * 0.78}`
  return { d, label: { x: p.x, y: p.y + depth * 0.86 } }
}

/**
 * 估算文本宽度给标签底衬用。
 * 中日韩字符按一个字号宽算，西文按 0.6 个字号宽算，够用且不用等测量。
 */
function estimateTextWidth(text: string, fontSize: number): number {
  let width = 0
  for (const ch of text) {
    width += /[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60]/.test(ch)
      ? fontSize
      : fontSize * 0.6
  }
  return width
}

/** 节点显示名：优先 display_name，过长截断 */
function shortName(node: GraphNode, limit = 9): string {
  const raw =
    (node.properties && typeof node.properties.display_name === 'string' && node.properties.display_name) || node.name
  if (!raw) return ''
  return raw.length > limit ? `${raw.slice(0, limit)}…` : raw
}

/** 关系标签文本：优先显示比对算子，退化到关系类型 */
function edgeText(edge: GraphEdge): string {
  const op = edge.properties?.operator
  return typeof op === 'string' && op ? op : edge.type
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
  const [container, setContainer] = useState<HTMLDivElement | null>(null)
  const [box, setBox] = useState({ width: 0, height: 0 })
  const [showEdgeLabels, setShowEdgeLabels] = useState(true)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const sceneRef = useRef<SceneController | null>(null)

  // 回调放进 ref：父组件每次渲染都会传新函数，进依赖数组会导致反复重建整张图
  const handlersRef = useRef({ onNodeClick, onEdgeClick, onBackgroundClick })
  useEffect(() => {
    handlersRef.current = { onNodeClick, onEdgeClick, onBackgroundClick }
  }, [onNodeClick, onEdgeClick, onBackgroundClick])

  // 容器尺寸：等布局完成再画，否则首帧拿到 0×0 会画出空图
  useEffect(() => {
    if (!container) return
    const measure = () => {
      const width = container.clientWidth
      const heightPx = typeof height === 'number' ? height : container.clientHeight
      setBox((prev) => (prev.width === width && prev.height === heightPx ? prev : { width, height: heightPx }))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(container)
    return () => observer.disconnect()
  }, [container, height])

  useEffect(() => {
    const svgEl = svgRef.current
    if (!svgEl || !graph || box.width === 0 || box.height === 0) return

    const width = box.width
    const heightPx = box.height
    const radii = buildRadii(graph)
    const colorMap = buildColorMap(graph.nodes)
    const radiusOf = (name: string) => radii.get(name) ?? NODE_R_MIN
    const nodes: SimNode[] = graph.nodes.map((n) => ({ ...n }))
    const links: SimLink[] = placeEdges(graph.edges)

    // 确定性初值：先沿圆环摊开，避免从 d3 默认的螺旋初值开始，
    // 既不出现"开局挤在左上角"的过渡态，刷新同一张图位置也基本稳定
    const ringRadius = Math.min(width, heightPx) * 0.32
    nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(nodes.length, 1)
      n.x = width / 2 + ringRadius * Math.cos(angle)
      n.y = heightPx / 2 + ringRadius * Math.sin(angle)
    })

    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()
    svg.attr('viewBox', `0 0 ${width} ${heightPx}`)

    // ===== 定义：聚焦光晕 + 背景点阵 =====
    const defs = svg.append('defs')
    const halo = defs
      .append('filter')
      .attr('id', 'graph-node-focus')
      .attr('x', '-60%')
      .attr('y', '-60%')
      .attr('width', '220%')
      .attr('height', '220%')
    halo.append('feGaussianBlur').attr('stdDeviation', 2.4).attr('result', 'halo')
    const haloMerge = halo.append('feMerge')
    haloMerge.append('feMergeNode').attr('in', 'halo')
    haloMerge.append('feMergeNode').attr('in', 'SourceGraphic')

    const grid = defs
      .append('pattern')
      .attr('id', 'graph-grid')
      .attr('width', 28)
      .attr('height', 28)
      .attr('patternUnits', 'userSpaceOnUse')
    grid.append('circle').attr('cx', 14).attr('cy', 14).attr('r', 1.2).attr('fill', GRID_DOT)

    svg.append('rect').attr('width', width).attr('height', heightPx).attr('fill', 'url(#graph-grid)')
    svg.style('background-color', CANVAS_BG)

    // 缩放只作用于这一层，背景点阵保持不动
    const viewport = svg.append('g').attr('class', 'graph-viewport')

    // ===== 关系层 =====
    const linkLayer = viewport.append('g').attr('class', 'graph-links')
    const linkPaths = linkLayer
      .selectAll<SVGPathElement, SimLink>('path')
      .data(links)
      .enter()
      .append('path')
      .attr('fill', 'none')
      .attr('stroke', EDGE_COLOR)
      .attr('stroke-width', 1.4)
      .style('cursor', 'pointer')

    // ===== 关系标签层（每条关系一个 g，整体平移到位） =====
    const labelLayer = viewport.append('g').attr('class', 'graph-edge-labels')
    const labelGroups = labelLayer
      .selectAll<SVGGElement, SimLink>('g')
      .data(links)
      .enter()
      .append('g')
    if (!showEdgeLabels) labelGroups.style('display', 'none')
    labelGroups
      .append('rect')
      .attr('rx', 3)
      .attr('fill', LABEL_BG)
      .attr('stroke', LABEL_BORDER)
      .attr('stroke-width', 0.6)
      .style('pointer-events', 'all')
    labelGroups
      .append('text')
      .attr('font-size', LABEL_FONT_SIZE)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('fill', LABEL_COLOR)
      .style('pointer-events', 'none')
    labelGroups.each(function (d) {
      const g = d3.select(this)
      const text = edgeText(d)
      const w = estimateTextWidth(text, LABEL_FONT_SIZE) + 8
      const h = LABEL_FONT_SIZE + 6
      g.select('text').text(text)
      g.select('rect').attr('x', -w / 2).attr('y', -h / 2).attr('width', w).attr('height', h)
    })

    // ===== 节点层 =====
    const nodeLayer = viewport.append('g').attr('class', 'graph-nodes')
    const nodeGroups = nodeLayer
      .selectAll<SVGGElement, SimNode>('g')
      .data(nodes)
      .enter()
      .append('g')
      .attr('class', 'graph-node')
      .style('cursor', 'pointer')
    const nodeCircles = nodeGroups
      .append('circle')
      .attr('r', (d) => radiusOf(d.name))
      .attr('fill', (d) => colorMap[d.type || 'Field'] || NODE_STROKE)
      .attr('stroke', NODE_STROKE)
      .attr('stroke-width', 2)
    nodeGroups
      .append('text')
      .attr('x', (d) => radiusOf(d.name) + 5)
      .attr('y', 4)
      .attr('font-size', 11)
      .attr('fill', NODE_LABEL_COLOR)
      .style('pointer-events', 'none')
      .text((d) => shortName(d))

    // ===== 仿真 =====
    const nodeCount = nodes.length
    const repel = REPEL_BASE - Math.min(nodeCount, REPEL_NODE_CAP) * REPEL_PER_NODE
    const simulation = d3
      .forceSimulation<SimNode>(nodes)
      .force(
        'link',
        d3
          .forceLink<SimNode, SimLink>(links)
          .id((d) => d.name)
          .distance((d) => LINK_DISTANCE_BASE + LINK_DISTANCE_PER_EXTRA_EDGE * Math.min(d.laneCount, 4))
          .strength(0.32),
      )
      .force('repel', d3.forceManyBody<SimNode>().strength(repel).distanceMax(620))
      .force('collide', d3.forceCollide<SimNode>().radius((d) => radiusOf(d.name) + COLLIDE_PADDING))
      .force('center', d3.forceCenter<SimNode>(width / 2, heightPx / 2))
      .force('pull-x', d3.forceX<SimNode>(width / 2).strength(CENTER_PULL))
      .force('pull-y', d3.forceY<SimNode>(heightPx / 2).strength(CENTER_PULL))

    let focusedName: string | null = null

    const clearFocus = () => {
      focusedName = null
      nodeCircles.attr('stroke', NODE_STROKE).attr('stroke-width', 2).style('filter', null)
      linkPaths.attr('stroke', EDGE_COLOR).attr('stroke-width', 1.4)
      labelGroups.select('rect').attr('stroke', LABEL_BORDER)
      labelGroups.select('text').attr('fill', LABEL_COLOR)
    }

    const touches = (d: SimLink, name: string) => endName(d.source) === name || endName(d.target) === name

    const highlightNode = (name: string) => {
      focusedName = name
      nodeCircles
        .attr('stroke', (d) => (d.name === name ? NODE_FOCUS : NODE_STROKE))
        .attr('stroke-width', (d) => (d.name === name ? 3.2 : 2))
        .style('filter', (d) => (d.name === name ? 'url(#graph-node-focus)' : null))
      linkPaths
        .attr('stroke', (d) => (touches(d, name) ? NODE_FOCUS : EDGE_COLOR))
        .attr('stroke-width', (d) => (touches(d, name) ? 2.2 : 1.4))
      labelGroups.select('rect').attr('stroke', (d) => (touches(d as SimLink, name) ? NODE_FOCUS : LABEL_BORDER))
    }

    const highlightEdge = (key: string) => {
      clearFocus()
      const isTarget = (d: SimLink) => `${endName(d.source)}->${endName(d.target)}` === key
      linkPaths.attr('stroke', (d) => (isTarget(d) ? EDGE_ACTIVE : EDGE_COLOR)).attr('stroke-width', (d) => (isTarget(d) ? 2.6 : 1.4))
      labelGroups.select('rect').attr('stroke', (d) => (isTarget(d as SimLink) ? EDGE_ACTIVE : LABEL_BORDER))
      labelGroups.select('text').attr('fill', (d) => (isTarget(d as SimLink) ? LABEL_ACTIVE : LABEL_COLOR))
    }

    // ===== 拖拽：位移小于阈值算点击 =====
    let pressFrom: Point | null = null
    let dragged = false
    const drag = d3
      .drag<SVGGElement, SimNode>()
      .on('start', (event, d) => {
        pressFrom = { x: event.x, y: event.y }
        dragged = false
        if (!event.active) simulation.alphaTarget(0.25).restart()
        d.fx = d.x
        d.fy = d.y
      })
      .on('drag', (event, d) => {
        if (pressFrom && Math.hypot(event.x - pressFrom.x, event.y - pressFrom.y) > CLICK_SLOP) dragged = true
        d.fx = event.x
        d.fy = event.y
      })
      .on('end', (event, d) => {
        if (!event.active) simulation.alphaTarget(0)
        if (!dragged) {
          highlightNode(d.name)
          handlersRef.current.onNodeClick?.(d.name)
        }
        d.fx = null
        d.fy = null
      })
    nodeGroups.call(drag)

    // ===== 悬停 =====
    nodeGroups
      .on('mouseenter', (event, d) => {
        if (d.name === focusedName) return
        d3.select(event.currentTarget as SVGGElement).select('circle').attr('stroke', NODE_HOVER).attr('stroke-width', 2.6)
      })
      .on('mouseleave', (event, d) => {
        if (d.name === focusedName) return
        d3.select(event.currentTarget as SVGGElement).select('circle').attr('stroke', NODE_STROKE).attr('stroke-width', 2)
      })

    // ===== 点击关系 =====
    linkPaths.on('click', (event, d) => {
      event.stopPropagation()
      highlightEdge(`${endName(d.source)}->${endName(d.target)}`)
      handlersRef.current.onEdgeClick?.(endName(d.source), endName(d.target))
    })
    labelGroups.on('click', (event, d) => {
      event.stopPropagation()
      handlersRef.current.onEdgeClick?.(endName(d.source), endName(d.target))
    })

    // ===== 点击留白：取消选中 =====
    svg.on('click', (event) => {
      const target = event.target as Element
      if (target === svgEl || target.tagName === 'rect') {
        clearFocus()
        handlersRef.current.onBackgroundClick?.()
      }
    })

    // ===== 缩放与平移 =====
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([MIN_SCALE, MAX_SCALE])
      .on('zoom', (event) => {
        viewport.attr('transform', event.transform.toString())
      })
    svg.call(zoom).on('dblclick.zoom', null)

    // ===== 每帧更新 =====
    simulation.on('tick', () => {
      linkPaths.attr('d', (d) => {
        const s = endPoint(d.source)
        const t = endPoint(d.target)
        if (d.isSelfLoop) return selfLoopPath(s, radiusOf(endName(d.source))).d
        return curvePath(s, t, d.laneOffset)
      })
      labelGroups.attr('transform', (d) => {
        const s = endPoint(d.source)
        const t = endPoint(d.target)
        const at = d.isSelfLoop
          ? selfLoopPath(s, radiusOf(endName(d.source))).label
          : curvePointAt(s, t, d.laneOffset, d.reversed ? 1 - d.labelT : d.labelT)
        return `translate(${at.x},${at.y})`
      })
      nodeGroups.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`)
    })

    sceneRef.current = {
      focusNode: (name) => {
        if (name) highlightNode(name)
        else clearFocus()
      },
      focusEdge: (key) => {
        if (key) highlightEdge(key)
        else clearFocus()
      },
      clearFocus,
      setLabelsVisible: (visible) => {
        // null 走 removeProperty 分支，等同恢复默认显示
        if (visible) labelGroups.style('display', null)
        else labelGroups.style('display', 'none')
      },
      resetView: () => {
        svg.transition().duration(420).call(zoom.transform, d3.zoomIdentity)
      },
    }

    return () => {
      simulation.stop()
      svg.on('click', null)
      svg.selectAll('*').remove()
      sceneRef.current = null
    }
    // showEdgeLabels 不进依赖：标签显隐由下面的 effect 单独控制，避免切换时重跑布局
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, box.width, box.height])

  // 关系标签显隐：只切换可见性，不动布局
  useEffect(() => {
    sceneRef.current?.setLabelsVisible(showEdgeLabels)
  }, [showEdgeLabels, graph, box.width, box.height])

  // 父组件的选中态回灌：只改描边与滤镜
  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return
    if (selectedNodeName) scene.focusNode(selectedNodeName)
    else if (selectedEdgeKey) scene.focusEdge(selectedEdgeKey)
    else scene.clearFocus()
  }, [selectedNodeName, selectedEdgeKey, graph, box.width, box.height])

  const legend = useMemo(() => {
    if (!graph) return [] as Array<{ type: string; color: string; count: number }>
    const colorMap = buildColorMap(graph.nodes)
    const order: string[] = []
    const counts = new Map<string, number>()
    graph.nodes.forEach((n) => {
      const t = n.type || 'Field'
      if (!counts.has(t)) order.push(t)
      counts.set(t, (counts.get(t) ?? 0) + 1)
    })
    return order.map((t) => ({ type: t, color: colorMap[t], count: counts.get(t) ?? 0 }))
  }, [graph])

  const frameStyle: React.CSSProperties = {
    position: 'relative',
    width: '100%',
    height: typeof height === 'number' ? `${height}px` : height,
    // 显式 border-box：边框不再撑出 2px，height 传入多少就是多少
    boxSizing: 'border-box',
    background: CANVAS_BG,
    borderRadius: 6,
    overflow: 'hidden',
    border: '1px solid #e4e7ed',
  }

  const panelStyle: React.CSSProperties = {
    position: 'absolute',
    background: 'rgba(255, 255, 255, 0.95)',
    borderRadius: 6,
    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.08)',
    border: '1px solid #e4e7ed',
  }

  return (
    <div ref={setContainer} style={frameStyle}>
      <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />

      <div style={{ ...panelStyle, top: 12, right: 12, padding: '6px 10px' }}>
        <Space size="small">
          <Tooltip title="重置视图">
            <Button size="small" icon={<AimOutlined />} onClick={() => sceneRef.current?.resetView()} />
          </Tooltip>
          {onRefresh && (
            <Tooltip title="刷新图谱">
              <Button size="small" icon={<ReloadOutlined />} onClick={onRefresh} />
            </Tooltip>
          )}
          <Tooltip title="显示关系标签">
            <span style={{ display: 'inline-flex', alignItems: 'center' }}>
              <Switch size="small" checked={showEdgeLabels} onChange={setShowEdgeLabels} />
            </span>
          </Tooltip>
        </Space>
      </div>

      {legend.length > 0 && (
        <div style={{ ...panelStyle, left: 12, bottom: 12, padding: '8px 12px', fontSize: 12, maxWidth: 220 }}>
          {legend.map((item) => (
            <div key={item.type} style={{ display: 'flex', alignItems: 'center', gap: 6, lineHeight: '18px' }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: item.color,
                  flex: '0 0 auto',
                }}
              />
              <span style={{ color: '#4b5563', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.type}
              </span>
              <span style={{ color: '#9aa4b2' }}>{item.count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
