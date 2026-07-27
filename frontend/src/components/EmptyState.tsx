import type { ReactNode } from 'react'
import { Empty, Skeleton, SkeletonProps } from 'antd'

interface EmptyStateProps {
  /** 主要描述文案 */
  description?: ReactNode
  /** 自定义图标(传入 ReactNode;默认使用品牌渐变图标) */
  image?: ReactNode
  /** 操作按钮区 */
  action?: ReactNode
  /** 容器内边距,默认 60 */
  padding?: number
}

/**
 * 统一的空状态组件
 *
 * 替代 AntD 默认的灰色小图 Empty,提供:
 * - 品牌渐变背景圆形图标
 * - 清晰的描述文案
 * - 可选的操作引导(CTA)
 */
export default function EmptyState({
  description = '暂无数据',
  image,
  action,
  padding = 60,
}: EmptyStateProps) {
  return (
    <div
      style={{
        padding,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
      }}
    >
      {image ? (
        image
      ) : (
        <div
          style={{
            width: 72,
            height: 72,
            borderRadius: '50%',
            background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.10) 0%, rgba(139, 92, 246, 0.10) 100%)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: 16,
          }}
        >
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none">
            <path
              d="M12 2L2 7v10c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V7l-10-5z"
              stroke="url(#empty-grad)"
              strokeWidth="1.8"
              strokeLinejoin="round"
              fill="none"
            />
            <path d="M9 12l2 2 4-4" stroke="url(#empty-grad)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            <defs>
              <linearGradient id="empty-grad" x1="0" y1="0" x2="24" y2="24" gradientUnits="userSpaceOnUse">
                <stop stopColor="#6366f1" />
                <stop offset="1" stopColor="#8b5cf6" />
              </linearGradient>
            </defs>
          </svg>
        </div>
      )}
      <div style={{ fontSize: 14, color: '#64748b', marginBottom: action ? 16 : 0 }}>{description}</div>
      {action}
    </div>
  )
}

/** 兼容 AntD Empty 的简写 */
export function AntdEmptyFallback(props: { description?: ReactNode }) {
  return <Empty description={props.description || '暂无数据'} />
}

/**
 * 统一的卡片加载骨架
 * 替代简陋的 `<Spin />`,在卡片内容区显示骨架屏
 */
export function CardSkeleton(props: { active?: boolean; rows?: number } & Pick<SkeletonProps, 'title'>) {
  const { active = true, rows = 4 } = props
  return (
    <div style={{ padding: 16 }}>
      <Skeleton active={active} paragraph={{ rows }} {...props} />
    </div>
  )
}
