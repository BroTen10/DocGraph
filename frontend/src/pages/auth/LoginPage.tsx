import { useState } from 'react'
import { Alert, Button, Form, Input } from 'antd'
import { LockOutlined, MailOutlined } from '@ant-design/icons'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { getErrorMessage } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import AuthShell from './AuthShell'

interface LoginValues {
  email: string
  password: string
}

export default function LoginPage() {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const requestedPath = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname

  const handleSubmit = async (values: LoginValues) => {
    setSubmitting(true)
    setError('')
    try {
      await login(values.email.trim(), values.password)
      navigate(requestedPath || '/', { replace: true })
    } catch (e) {
      setError(getErrorMessage(e, '登录失败，请稍后重试'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell
      title="登录"
      subtitle="使用已审核通过的工作邮箱登录审查系统"
      footer={(
        <span>
          还没有账号？<Link to="/register">申请开通租户</Link>
        </span>
      )}
    >
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 18 }} />}
      <Form<LoginValues> layout="vertical" size="large" onFinish={handleSubmit}>
        <Form.Item
          name="email"
          label="工作邮箱"
          rules={[
            { required: true, message: '请输入工作邮箱' },
            { type: 'email', message: '邮箱格式不正确' },
          ]}
        >
          <Input prefix={<MailOutlined />} autoComplete="email" placeholder="name@example.com" />
        </Form.Item>
        <Form.Item
          name="password"
          label="密码"
          rules={[{ required: true, message: '请输入密码' }]}
        >
          <Input.Password
            prefix={<LockOutlined />}
            autoComplete="current-password"
            placeholder="请输入密码"
          />
        </Form.Item>
        <div className="auth-form-actions">
          <Link to="/forgot-password">忘记密码</Link>
          <Button type="primary" htmlType="submit" loading={submitting}>
            登录
          </Button>
        </div>
      </Form>
    </AuthShell>
  )
}
