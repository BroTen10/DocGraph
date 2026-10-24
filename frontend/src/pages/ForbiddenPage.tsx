import { Button, Result } from 'antd'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function ForbiddenPage() {
  const { role } = useAuth()
  const destination = role === 'platform_admin' ? '/platform/registrations' : '/upload'
  return (
    <main className="auth-page">
      <div className="auth-panel">
        <Result
          status="403"
          title="无权访问此页面"
          subTitle="当前账号没有所需权限，或该功能不属于当前工作空间。"
          extra={<Link to={destination}><Button type="primary">返回工作台</Button></Link>}
        />
      </div>
    </main>
  )
}
