import type { ReactNode } from 'react'

interface PageHeaderProps {
  /** 主标题 */
  title: string
  /** 副标题描述(灰色小字) */
  subtitle?: ReactNode
  /** 右侧操作区(按钮组等) */
  extra?: ReactNode
  /** 顶部图标(可选,显示在标题左侧) */
  icon?: ReactNode
  /** 顶部面包屑(可选) */
  breadcrumb?: ReactNode
  /** 底部内容(可选,如统计卡片行) */
  footer?: ReactNode
  /** 底部边距,默认 16 */
  bottomGap?: number
}

/**
 * 统一的页面头部组件
 *
 * 替代各页面散乱的 `<Title level={4}>` + `<Text type="secondary">` 堆叠,
 * 提供一致的视觉层次:面包屑 → 标题行(图标+标题+副标题+操作) → 可选底部内容
 */
export default function PageHeader({
  title,
  subtitle,
  extra,
  icon,
  breadcrumb,
  footer,
  bottomGap = 16,
}: PageHeaderProps) {
  return (
    <div style={{ marginBottom: bottomGap }}>
      {breadcrumb && <div style={{ marginBottom: 8 }}>{breadcrumb}</div>}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0, flex: 1 }}>
          {icon && (
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.12) 0%, rgba(139, 92, 246, 0.12) 100%)',
                color: '#6366f1',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 20,
                flexShrink: 0,
              }}
            >
              {icon}
            </div>
          )}
          <div style={{ minWidth: 0 }}>
            <h1
              style={{
                margin: 0,
                fontSize: 22,
                fontWeight: 700,
                color: '#0f172a',
                lineHeight: 1.3,
                letterSpacing: '-0.01em',
              }}
            >
              {title}
            </h1>
            {subtitle && (
              <div
                style={{
                  marginTop: 4,
                  fontSize: 13,
                  color: '#64748b',
                  lineHeight: 1.5,
                }}
              >
                {subtitle}
              </div>
            )}
          </div>
        </div>
        {extra && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            {extra}
          </div>
        )}
      </div>
      {footer && <div style={{ marginTop: 16 }}>{footer}</div>}
    </div>
  )
}
