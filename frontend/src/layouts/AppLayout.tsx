import { useState } from 'react'
import {
  Alert,
  Avatar,
  Button,
  Dropdown,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Result,
  Space,
  Spin,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { MenuProps } from 'antd'
import {
  ApartmentOutlined,
  CheckCircleOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SettingOutlined,
  UploadOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { authApi, getErrorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { visibleMenuKeys } from '../auth/permissions'
import { normalizePhone } from '../auth/validation'
import { RuleSetSwitcher } from '../components/RuleSetSwitcher'
import VersionBadge from '../components/VersionBadge'
import { useRuleSet } from '../context/RuleSetContext'
import type { UserRole } from '../types'

const { Header, Sider, Content } = Layout
const { Text } = Typography

export const ALL_MENU_ITEMS = [
  { key: '/upload', icon: <UploadOutlined />, label: '文档上传' },
  { key: '/rules', icon: <SettingOutlined />, label: '规则管理' },
  { key: '/doc-types', icon: <FileTextOutlined />, label: '文档类型' },
  { key: '/graph', icon: <ApartmentOutlined />, label: '图谱确认' },
  { key: '/review', icon: <FileSearchOutlined />, label: '审查执行' },
  { key: '/results', icon: <CheckCircleOutlined />, label: '结果展示' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
]

const pageTitleMap = Object.fromEntries(
  ALL_MENU_ITEMS.map((item) => [item.key, item.label]),
) as Record<string, string>

const ROLE_LABELS: Record<UserRole, string> = {
  tenant_admin: '租户管理员',
  reviewer: '审查员',
  viewer: '只读查看者',
  platform_admin: '平台管理员',
}

export function AccountMenu({
  dark = false,
  compact = false,
}: {
  dark?: boolean
  compact?: boolean
}) {
  const navigate = useNavigate()
  const { user, tenant, role, logout, refresh } = useAuth()
  const [profileOpen, setProfileOpen] = useState(false)
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileError, setProfileError] = useState('')
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [passwordError, setPasswordError] = useState('')
  const [form] = Form.useForm<{
    current_password: string
    new_password: string
    confirm_password: string
  }>()
  const [profileForm] = Form.useForm<{
    name: string
    phone?: string
  }>()

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  const changePassword = async () => {
    try {
      const values = await form.validateFields()
      if (values.new_password !== values.confirm_password) {
        setPasswordError('两次输入的新密码不一致')
        return
      }
      setSubmitting(true)
      setPasswordError('')
      await authApi.changePassword(values.current_password, values.new_password)
      setPasswordOpen(false)
      message.success('密码已修改，请重新登录')
      window.dispatchEvent(new Event('docgraph:session-expired'))
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      setPasswordError(getErrorMessage(error, '修改密码失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const saveProfile = async () => {
    try {
      const values = await profileForm.validateFields()
      setProfileSaving(true)
      setProfileError('')
      await authApi.updateProfile({
        name: values.name.trim(),
        ...(values.phone?.trim()
          ? { phone: normalizePhone(values.phone) }
          : {}),
      })
      await refresh()
      setProfileOpen(false)
      message.success('个人资料已更新')
    } catch (error) {
      if (error && typeof error === 'object' && 'errorFields' in error) return
      setProfileError(getErrorMessage(error, '更新个人资料失败'))
    } finally {
      setProfileSaving(false)
    }
  }

  const items: MenuProps['items'] = [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人资料',
      onClick: () => {
        profileForm.setFieldsValue({
          name: user?.name || '',
          phone: '',
        })
        setProfileError('')
        setProfileOpen(true)
      },
    },
    {
      key: 'password',
      label: '修改密码',
      onClick: () => {
        form.resetFields()
        setPasswordError('')
        setPasswordOpen(true)
      },
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      danger: true,
      label: '退出登录',
      onClick: () => void handleLogout(),
    },
  ]

  return (
    <>
      <Dropdown menu={{ items }} trigger={['click']} placement="bottomRight">
        <button
          type="button"
          aria-label="用户菜单"
          style={{
            width: '100%',
            border: 0,
            background: dark ? 'rgba(255,255,255,0.06)' : 'transparent',
            color: dark ? '#e2e8f0' : '#0f172a',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '8px 10px',
            borderRadius: 8,
            textAlign: 'left',
          }}
        >
          <Avatar
            size={34}
            style={{ background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)' }}
            icon={<UserOutlined />}
          />
          {!compact && <span style={{ flex: 1, minWidth: 0, lineHeight: 1.3 }}>
            <span
              style={{
                display: 'block',
                fontSize: 13,
                fontWeight: 600,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {user?.name || '当前用户'}
            </span>
            <span
              style={{
                display: 'block',
                fontSize: 11,
                color: dark ? '#94a3b8' : '#64748b',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {tenant?.name || (role ? ROLE_LABELS[role] : '')}
            </span>
          </span>}
        </button>
      </Dropdown>

      <Modal
        title="个人资料"
        open={profileOpen}
        okText="保存资料"
        confirmLoading={profileSaving}
        onOk={() => void saveProfile()}
        footer={(
          <Space>
            <Button onClick={() => setProfileOpen(false)}>取消</Button>
            <Button type="primary" loading={profileSaving} onClick={() => void saveProfile()}>
              保存资料
            </Button>
          </Space>
        )}
        onCancel={() => setProfileOpen(false)}
      >
        {profileError && (
          <Alert type="error" showIcon message={profileError} style={{ marginBottom: 16 }} />
        )}
        <Form form={profileForm} layout="vertical">
          <Form.Item
            name="name"
            label="姓名"
            rules={[{ required: true, message: '请输入姓名' }]}
          >
            <Input maxLength={50} />
          </Form.Item>
          <Form.Item label="登录邮箱">
            <Input value={user?.email} disabled />
          </Form.Item>
          <Form.Item
            name="phone"
            label={`手机号（当前 ${user?.phone_masked || '未登记'}）`}
            extra="留空表示不修改；修改后必须是有效的中国大陆手机号。"
            rules={[
              {
                validator: async (_, value: string | undefined) => {
                  if (!value?.trim()) return
                  normalizePhone(value)
                },
              },
            ]}
          >
            <Input maxLength={20} placeholder="输入新手机号" />
          </Form.Item>
        </Form>
        <Space direction="vertical" size={4}>
          <div><Text type="secondary">租户：</Text>{tenant?.name || '平台管理'}</div>
          <div><Text type="secondary">角色：</Text>{role ? ROLE_LABELS[role] : '—'}</div>
        </Space>
      </Modal>

      <Modal
        title="修改密码"
        open={passwordOpen}
        okText="确认修改"
        confirmLoading={submitting}
        onOk={() => void changePassword()}
        onCancel={() => setPasswordOpen(false)}
        destroyOnHidden
      >
        {passwordError && (
          <Alert type="error" showIcon message={passwordError} style={{ marginBottom: 16 }} />
        )}
        <Form form={form} layout="vertical">
          <Form.Item
            name="current_password"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码至少 6 位' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            rules={[{ required: true, message: '请再次输入新密码' }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

export default function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const { role } = useAuth()
  const { current, currentId, loading } = useRuleSet()

  const selectedKey = '/' + (location.pathname.split('/')[1] || 'upload')
  const currentTitle = pageTitleMap[selectedKey] || '文档审查智能体'
  const allowedKeys = new Set(visibleMenuKeys(role))
  const menuItems = ALL_MENU_ITEMS.filter((item) => allowedKeys.has(item.key))

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        theme="light"
        width={220}
        collapsedWidth={72}
        collapsible
        collapsed={collapsed}
        trigger={null}
        style={{
          borderRight: '1px solid #e2e8f0',
          position: 'sticky',
          top: 0,
          height: '100vh',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: 60,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: collapsed ? '0 16px' : '0 20px',
            borderBottom: '1px solid #f1f5f9',
          }}
        >
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            审
          </div>
          {!collapsed && (
            <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
              <span className="brand-gradient-text" style={{ fontSize: 15, fontWeight: 700 }}>
                文档审查智能体
              </span>
              <span style={{ fontSize: 11, color: '#94a3b8' }}>Knowledge Graph Reviewer</span>
            </div>
          )}
        </div>

        <Menu
          theme="light"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none', paddingTop: 8 }}
        />

        <div
          style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            padding: collapsed ? '10px 8px' : 10,
            borderTop: '1px solid #f1f5f9',
            background: '#fafbfc',
          }}
        >
          <AccountMenu compact={collapsed} />
        </div>
      </Sider>

      <Layout>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #e2e8f0',
            boxShadow: '0 1px 2px rgba(15, 23, 42, 0.03)',
            position: 'sticky',
            top: 0,
            zIndex: 10,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Tooltip title={collapsed ? '展开菜单' : '收起菜单'} placement="bottom">
              <Button
                type="text"
                aria-label={collapsed ? '展开菜单' : '收起菜单'}
                icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={() => setCollapsed((value) => !value)}
              />
            </Tooltip>
            <span style={{ fontSize: 16, fontWeight: 600, color: '#0f172a' }}>{currentTitle}</span>
          </div>
          <Space size={12}>
            <RuleSetSwitcher />
            <VersionBadge />
          </Space>
        </Header>

        <Content style={{ margin: 0, padding: 20, overflow: 'auto' }}>
          {loading ? (
            <div style={{ minHeight: 360, display: 'grid', placeItems: 'center' }}>
              <Space direction="vertical" align="center">
                <Spin size="large" />
                <Text type="secondary">加载规则集...</Text>
              </Space>
            </div>
          ) : !current ? (
            <Result
              status="info"
              title="欢迎使用文档审查智能体"
              subTitle="使用前请先创建一套审查规则集。规则集是规则、合同、图谱和审查结果的业务分组，所有数据仍然受当前租户边界保护。"
              extra={<RuleSetSwitcher />}
            />
          ) : (
            <div key={currentId} className="page-fade-in">
              <Outlet />
            </div>
          )}
        </Content>
      </Layout>
    </Layout>
  )
}
