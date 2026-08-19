/**
 * 可缩放平移的文档预览容器
 *
 * - 滚轮：以光标所在位置为中心放大/缩小（0.2x ~ 6x）
 * - 按住鼠标左键拖动：平移可视范围
 * - 右上角：缩放比例 + 放大/缩小/重置按钮
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, ReactNode } from 'react'
import { Button, Tooltip } from 'antd'
import { ReloadOutlined, ZoomInOutlined, ZoomOutOutlined } from '@ant-design/icons'

export interface ZoomPanApi {
  /** 将指定元素平移到可视区中央（点击右侧高亮定位用） */
  centerOn: (el: HTMLElement) => void
  /** 以视口中心为基准缩放（factor > 1 放大，< 1 缩小） */
  zoomBy: (factor: number) => void
  /** 恢复 100% 与初始位置 */
  reset: () => void
}

interface ZoomableStageProps {
  children: ReactNode
  height: number | string
  /** 外部可通过它调用 centerOn 等能力 */
  apiRef?: { current: ZoomPanApi | null }
  /** 视口宽度变化时回调（图片/DOCX 用它按视口宽度铺满内容） */
  onViewportWidth?: (width: number) => void
  /** 视口背景色 */
  background?: string
}

const MIN_SCALE = 0.2
const MAX_SCALE = 6
const WHEEL_STEP = 1.15

export default function ZoomableStage({
  children,
  height,
  apiRef,
  onViewportWidth,
  background = '#525659',
}: ZoomableStageProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 })
  const transformRef = useRef(transform)
  const [dragging, setDragging] = useState(false)
  const dragStartRef = useRef<{ px: number; py: number; x: number; y: number } | null>(null)

  const apply = useCallback((next: { scale: number; x: number; y: number }) => {
    transformRef.current = next
    setTransform(next)
  }, [])

  /** 限制平移范围：内容不能完全拖出视口（保留少量余量） */
  const clampPos = useCallback((scale: number, x: number, y: number) => {
    const vp = viewportRef.current
    const stage = stageRef.current
    if (!vp || !stage) return { x, y }
    const slack = 80
    const minX = Math.min(0, vp.clientWidth - stage.scrollWidth * scale) - slack
    const maxX = Math.max(0, slack)
    const minY = Math.min(0, vp.clientHeight - stage.scrollHeight * scale) - slack
    const maxY = Math.max(0, slack)
    return {
      x: Math.min(maxX, Math.max(minX, x)),
      y: Math.min(maxY, Math.max(minY, y)),
    }
  }, [])

  /** 以屏幕坐标 (clientX, clientY) 为锚点缩放，锚点下的内容保持不动 */
  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number) => {
      const vp = viewportRef.current
      if (!vp) return
      const t = transformRef.current
      const nextScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale * factor))
      if (nextScale === t.scale) return
      const rect = vp.getBoundingClientRect()
      const px = clientX - rect.left
      const py = clientY - rect.top
      // 光标对应的内容坐标（scale=1 坐标系）
      const cx = (px - t.x) / t.scale
      const cy = (py - t.y) / t.scale
      const next = clampPos(nextScale, px - cx * nextScale, py - cy * nextScale)
      apply({ scale: nextScale, x: next.x, y: next.y })
    },
    [apply, clampPos],
  )

  const zoomBy = useCallback(
    (factor: number) => {
      const vp = viewportRef.current
      if (!vp) return
      const rect = vp.getBoundingClientRect()
      zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor)
    },
    [zoomAt],
  )

  const centerOn = useCallback(
    (el: HTMLElement) => {
      const vp = viewportRef.current
      if (!vp) return
      const elRect = el.getBoundingClientRect()
      const vpRect = vp.getBoundingClientRect()
      const t = transformRef.current
      const dx = vpRect.width / 2 - (elRect.left - vpRect.left + elRect.width / 2)
      const dy = vpRect.height / 2 - (elRect.top - vpRect.top + elRect.height / 2)
      const next = clampPos(t.scale, t.x + dx, t.y + dy)
      apply({ scale: t.scale, x: next.x, y: next.y })
    },
    [apply, clampPos],
  )

  const reset = useCallback(() => {
    apply({ scale: 1, x: 0, y: 0 })
  }, [apply])

  // 滚轮缩放：React 合成事件默认是 passive 的，preventDefault 无效，必须用原生非被动监听
  useEffect(() => {
    const vp = viewportRef.current
    if (!vp) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP)
    }
    vp.addEventListener('wheel', onWheel, { passive: false })
    return () => vp.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  // 左键拖拽平移
  const handleMouseDown = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest('[data-zoom-controls]')) return
    e.preventDefault()
    dragStartRef.current = {
      px: e.clientX,
      py: e.clientY,
      x: transformRef.current.x,
      y: transformRef.current.y,
    }
    setDragging(true)
  }

  useEffect(() => {
    if (!dragging) return
    // 拖拽期间禁止文本/图片被选中
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'grabbing'
    const onMove = (e: MouseEvent) => {
      const ds = dragStartRef.current
      if (!ds) return
      e.preventDefault()
      const t = transformRef.current
      const next = clampPos(t.scale, ds.x + (e.clientX - ds.px), ds.y + (e.clientY - ds.py))
      apply({ scale: t.scale, x: next.x, y: next.y })
    }
    const onUp = () => {
      dragStartRef.current = null
      setDragging(false)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
  }, [dragging, apply, clampPos])

  // 暴露缩放平移 API
  useEffect(() => {
    if (!apiRef) return
    apiRef.current = { centerOn, zoomBy, reset }
  }, [apiRef, centerOn, zoomBy, reset])

  // 视口宽度变化上报（图片/DOCX 用铺满宽度）
  useEffect(() => {
    const vp = viewportRef.current
    if (!vp || !onViewportWidth) return
    const report = () => onViewportWidth(vp.clientWidth)
    report()
    const observer = new ResizeObserver(report)
    observer.observe(vp)
    return () => observer.disconnect()
  }, [onViewportWidth])

  return (
    <div
      ref={viewportRef}
      style={{
        position: 'relative',
        height: typeof height === 'number' ? `${height}px` : height,
        overflow: 'hidden',
        background,
        borderRadius: 6,
        touchAction: 'none',
        cursor: dragging ? 'grabbing' : 'grab',
      }}
      onMouseDown={handleMouseDown}
    >
      <div
        ref={stageRef}
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
          transformOrigin: '0 0',
          willChange: 'transform',
        }}
      >
        {children}
      </div>

      {/* 右上角缩放控制条 */}
      <div
        data-zoom-controls
        style={{
          position: 'absolute',
          top: 8,
          right: 8,
          zIndex: 10,
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          background: 'rgba(15, 23, 42, 0.55)',
          borderRadius: 6,
          padding: '2px 4px',
        }}
      >
        <Button
          size="small"
          type="text"
          icon={<ZoomOutOutlined />}
          style={{ color: '#fff' }}
          onClick={() => zoomBy(1 / 1.25)}
        />
        <span style={{ color: '#fff', fontSize: 12, minWidth: 44, textAlign: 'center' }}>
          {Math.round(transform.scale * 100)}%
        </span>
        <Button
          size="small"
          type="text"
          icon={<ZoomInOutlined />}
          style={{ color: '#fff' }}
          onClick={() => zoomBy(1.25)}
        />
        <Tooltip title="重置缩放">
          <Button
            size="small"
            type="text"
            icon={<ReloadOutlined />}
            style={{ color: '#fff' }}
            onClick={reset}
          />
        </Tooltip>
      </div>

      {/* 操作提示 */}
      <div
        style={{
          position: 'absolute',
          bottom: 8,
          left: 12,
          zIndex: 10,
          pointerEvents: 'none',
          color: 'rgba(255, 255, 255, 0.8)',
          fontSize: 11,
          background: 'rgba(15, 23, 42, 0.4)',
          borderRadius: 4,
          padding: '2px 6px',
        }}
      >
        滚轮缩放 · 按住左键拖动
      </div>
    </div>
  )
}
