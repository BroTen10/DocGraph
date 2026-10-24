import type { ReactNode } from 'react'
import { Card, Typography } from 'antd'

const { Title, Text } = Typography

interface AuthShellProps {
  title: string
  subtitle: ReactNode
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
}

export default function AuthShell({
  title,
  subtitle,
  children,
  footer,
  wide = false,
}: AuthShellProps) {
  return (
    <main className="auth-page">
      <div className={`auth-panel${wide ? ' auth-panel-wide' : ''}`}>
        <div className="auth-brand">
          <div className="auth-brand-mark">审</div>
          <div>
            <div className="auth-brand-name">文档审查智能体</div>
            <div className="auth-brand-caption">Knowledge Graph Reviewer</div>
          </div>
        </div>
        <Card bordered={false} className="auth-card">
          <div className="auth-heading">
            <Title level={2}>{title}</Title>
            <Text type="secondary">{subtitle}</Text>
          </div>
          {children}
        </Card>
        {footer && <div className="auth-footer">{footer}</div>}
      </div>
    </main>
  )
}
