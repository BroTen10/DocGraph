import { Layout, Menu } from 'antd'
import { Routes, Route, useLocation, useNavigate, Navigate } from 'react-router-dom'
import {
  UploadOutlined,
  SettingOutlined,
  ApartmentOutlined,
  FileSearchOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons'
import UploadPage from './pages/UploadPage'
import RulesPage from './pages/RulesPage'
import GraphPage from './pages/GraphPage'
import ReviewPage from './pages/ReviewPage'
import ResultsPage from './pages/ResultsPage'

const { Header, Sider, Content } = Layout

const menuItems = [
  { key: '/upload', icon: <UploadOutlined />, label: '文档上传' },
  { key: '/rules', icon: <SettingOutlined />, label: '规则管理' },
  { key: '/graph', icon: <ApartmentOutlined />, label: '图谱确认' },
  { key: '/review', icon: <FileSearchOutlined />, label: '审查执行' },
  { key: '/results', icon: <CheckCircleOutlined />, label: '结果展示' },
]

function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const selectedKey = '/' + (location.pathname.split('/')[1] || 'upload')

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={200}>
        <div style={{ height: 56, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, fontWeight: 600, padding: '0 12px' }}>
          文档审查智能体
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', fontSize: 18, fontWeight: 500, borderBottom: '1px solid #f0f0f0' }}>
          基于知识图谱的自动文档审查智能体
        </Header>
        <Content style={{ margin: 16, padding: 24, background: '#fff', borderRadius: 8, overflow: 'auto' }}>
          <Routes>
            <Route path="/" element={<Navigate to="/upload" replace />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/rules" element={<RulesPage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/results" element={<ResultsPage />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default App
