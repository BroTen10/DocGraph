import { useState } from 'react'
import { Layout, Menu, Avatar, Tooltip, Result, Button, Spin } from 'antd'
import { Routes, Route, useLocation, useNavigate, Navigate } from 'react-router-dom'
import {
  UploadOutlined,
  SettingOutlined,
  ApartmentOutlined,
  FileSearchOutlined,
  CheckCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  UserOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import UploadPage from './pages/UploadPage'
import RulesPage from './pages/RulesPage'
import GraphPage from './pages/GraphPage'
import ReviewPage from './pages/ReviewPage'
import ResultsPage from './pages/ResultsPage'
import DocTypesPage from './pages/DocTypesPage'
import SettingsPage from './pages/SettingsPage'
import { RuleSetProvider, useRuleSet } from './context/RuleSetContext'
import { RuleSetSwitcher } from './components/RuleSetSwitcher'

const { Header, Sider, Content } = Layout

/** 菜单配置:key 同时作为路由 / 标题查找依据 */
const menuItems = [
  { key: '/upload', icon: <UploadOutlined />, label: '文档上传' },
  { key: '/rules', icon: <SettingOutlined />, label: '规则管理' },
  { key: '/doc-types', icon: <FileTextOutlined />, label: '文档类型' },
  { key: '/graph', icon: <ApartmentOutlined />, label: '图谱确认' },
  { key: '/review', icon: <FileSearchOutlined />, label: '审查执行' },
  { key: '/results', icon: <CheckCircleOutlined />, label: '结果展示' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
]

/** 路径 → 当前页标题映射(给 Header 显示) */
const pageTitleMap: Record<string, string> = menuItems.reduce(
  (acc, item) => ({ ...acc, [item.key]: item.label }),
  {} as Record<string, string>,
)

function AppInner() {
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const { current, currentId, loading: rsLoading } = useRuleSet()

  const selectedKey = '/' + (location.pathname.split('/')[1] || 'upload')
  const currentTitle = pageTitleMap[selectedKey] || '文档审查智能体'

  // 规则集正在加载中:显示全屏 loading
  if (rsLoading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" tip="加载规则集...">
          <div style={{ padding: 50 }} />
        </Spin>
      </div>
    )
  }

  // 没有任何规则集:显示引导(不进入主布局)
  if (!current) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f8fafc' }}>
        <Result
          status="info"
          title="欢迎使用文档审查智能体"
          subTitle="使用前请先创建一套审查规则集。规则集是规则、合同、图谱和审查结果的命名空间,你可以为不同业务场景创建多套规则集。"
          extra={<RuleSetSwitcher />}
        />
      </div>
    )
  }

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
          overflow: 'auto',
        }}
      >
        {/* 品牌 Logo 区 */}
        <div
          style={{
            height: 60,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: collapsed ? '0 16px' : '0 20px',
            borderBottom: '1px solid #f1f5f9',
            transition: 'padding 0.2s',
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
              fontSize: 15,
              flexShrink: 0,
              boxShadow: '0 4px 12px rgba(99, 102, 241, 0.35)',
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

        {/* 菜单 */}
        <Menu
          theme="light"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none', paddingTop: 8 }}
        />

        {/* 底部用户区 */}
        {!collapsed && (
          <div
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              padding: '12px 16px',
              borderTop: '1px solid #f1f5f9',
              background: '#fafbfc',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
            }}
          >
            <Avatar size={32} style={{ background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)' }} icon={<UserOutlined />} />
            <div style={{ flex: 1, minWidth: 0, lineHeight: 1.3 }}>
              <div style={{ fontSize: 13, fontWeight: 500, color: '#0f172a', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                审查员
              </div>
              <div style={{ fontSize: 11, color: '#94a3b8' }}>在线</div>
            </div>
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
            boxShadow: '0 1px 2px rgba(15, 23, 42, 0.03)',
            position: 'sticky',
            top: 0,
            zIndex: 10,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Tooltip title={collapsed ? '展开菜单' : '收起菜单'} placement="bottom">
              <button
                onClick={() => setCollapsed(!collapsed)}
                style={{
                  border: 'none',
                  background: 'transparent',
                  cursor: 'pointer',
                  fontSize: 16,
                  color: '#475569',
                  padding: 6,
                  borderRadius: 6,
                  display: 'flex',
                  alignItems: 'center',
                  transition: 'all 0.2s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#f1f5f9')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              >
                {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              </button>
            </Tooltip>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 16, fontWeight: 600, color: '#0f172a' }}>{currentTitle}</span>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <RuleSetSwitcher />
          </div>
        </Header>

        <Content style={{ margin: 0, padding: 20, overflow: 'auto' }}>
          {/* key=currentId:切换规则集时强制重新挂载所有页面,确保数据刷新;
              切换页面(pathname 变化)时不重挂载,保留页面内状态(编辑表单/勾选/滚动位置) */}
          <div key={currentId || 'no-ruleset'} className="page-fade-in">
            <Routes>
              <Route path="/" element={<Navigate to="/upload" replace />} />
              <Route path="/upload" element={<UploadPage />} />
              <Route path="/rules" element={<RulesPage />} />
              <Route path="/doc-types" element={<DocTypesPage />} />
              <Route path="/graph" element={<GraphPage />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/results" element={<ResultsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}

function App() {
  return (
    <RuleSetProvider>
      <AppInner />
    </RuleSetProvider>
  )
}

export default App
