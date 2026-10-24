import { useState } from 'react'
import { Layout, Menu, Space, Typography } from 'antd'
import {
  AuditOutlined,
  BankOutlined,
} from '@ant-design/icons'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import VersionBadge from '../components/VersionBadge'
import { visibleMenuKeys } from '../auth/permissions'
import { useAuth } from '../auth/AuthContext'
import { AccountMenu } from './AppLayout'

const { Header, Sider, Content } = Layout
const { Text } = Typography

const PLATFORM_MENU_ITEMS = [
  { key: '/platform/registrations', icon: <AuditOutlined />, label: '注册审核' },
  { key: '/platform/tenants', icon: <BankOutlined />, label: '租户管理' },
]

const PLATFORM_TITLES = Object.fromEntries(
  PLATFORM_MENU_ITEMS.map((item) => [item.key, item.label]),
) as Record<string, string>

export default function PlatformLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const { role } = useAuth()
  const [collapsed, setCollapsed] = useState(false)
  const allowed = new Set(visibleMenuKeys(role))
  const title = PLATFORM_TITLES[location.pathname] || '平台管理'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        theme="dark"
        width={220}
        collapsedWidth={72}
        collapsed={collapsed}
        collapsible
        style={{ position: 'sticky', top: 0, height: '100vh' }}
      >
        <div
          style={{
            height: 60,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: collapsed ? '0 16px' : '0 20px',
            borderBottom: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)',
              display: 'grid',
              placeItems: 'center',
              color: '#fff',
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            平
          </div>
          {!collapsed && (
            <div style={{ lineHeight: 1.25 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#f8fafc' }}>DocGraph 平台</div>
              <div style={{ fontSize: 11, color: '#94a3b8' }}>Platform Administration</div>
            </div>
          )}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={PLATFORM_MENU_ITEMS.filter((item) => allowed.has(item.key))}
          onClick={({ key }) => navigate(key)}
          style={{ paddingTop: 8 }}
        />
        {!collapsed && (
          <div style={{ position: 'absolute', left: 10, right: 10, bottom: 10 }}>
            <AccountMenu dark />
          </div>
        )}
      </Sider>

      <Layout>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #e2e8f0',
            position: 'sticky',
            top: 0,
            zIndex: 10,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 16, fontWeight: 600, color: '#0f172a' }}>{title}</span>
            <Text type="secondary" style={{ fontSize: 12 }}>
              仅展示平台元数据，不访问租户业务内容
            </Text>
          </div>
          <Space>
            <VersionBadge />
          </Space>
        </Header>
        <Content style={{ margin: 0, padding: 20, overflow: 'auto' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
