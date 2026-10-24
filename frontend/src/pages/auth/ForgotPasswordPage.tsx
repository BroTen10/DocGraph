import { useState } from 'react'
import { Alert, Button, Form, Input, Result } from 'antd'
import { MailOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { authApi, getErrorMessage } from '../../api/client'
import AuthShell from './AuthShell'

export default function ForgotPasswordPage() {
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = async ({ email }: { email: string }) => {
    setSubmitting(true)
    setError('')
    try {
      const result = await authApi.forgotPassword(email.trim().toLowerCase())
      setMessage(result.message)
    } catch (e) {
      setError(getErrorMessage(e, '发送重置邮件失败'))
    } finally {
      setSubmitting(false)
    }
  }

  if (message) {
    return (
      <main className="auth-page">
        <div className="auth-panel">
          <Result
            status="success"
            title="请检查邮箱"
            subTitle={message}
            extra={<Link to="/login">返回登录</Link>}
          />
        </div>
      </main>
    )
  }

  return (
    <AuthShell
      title="找回密码"
      subtitle="输入注册邮箱，我们将发送一次性重置链接"
      footer={<Link to="/login">返回登录</Link>}
    >
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 18 }} />}
      <Form layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          name="email"
          label="工作邮箱"
          rules={[
            { required: true, message: '请输入工作邮箱' },
            { type: 'email', message: '邮箱格式不正确' },
          ]}
        >
          <Input prefix={<MailOutlined />} autoComplete="email" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          发送重置邮件
        </Button>
      </Form>
    </AuthShell>
  )
}
