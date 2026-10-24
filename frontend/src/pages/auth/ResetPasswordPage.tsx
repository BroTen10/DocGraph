import { useState } from 'react'
import { Alert, Button, Form, Input, Result } from 'antd'
import { Link, useSearchParams } from 'react-router-dom'
import { authApi, getErrorMessage } from '../../api/client'
import { isValidPassword } from '../../auth/validation'
import AuthShell from './AuthShell'

interface ResetValues {
  password: string
  confirm_password: string
}

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const [submitting, setSubmitting] = useState(false)
  const [complete, setComplete] = useState(false)
  const [error, setError] = useState('')

  if (!token) {
    return (
      <main className="auth-page">
        <div className="auth-panel">
          <Result
            status="error"
            title="重置链接无效"
            subTitle="链接缺少必要令牌，请重新申请密码重置。"
            extra={<Link to="/forgot-password">重新申请</Link>}
          />
        </div>
      </main>
    )
  }

  if (complete) {
    return (
      <main className="auth-page">
        <div className="auth-panel">
          <Result
            status="success"
            title="密码已重置"
            subTitle="其他设备上的会话已失效，请重新登录。"
            extra={<Link to="/login">前往登录</Link>}
          />
        </div>
      </main>
    )
  }

  const handleSubmit = async (values: ResetValues) => {
    setSubmitting(true)
    setError('')
    try {
      await authApi.resetPassword(token, values.password)
      setComplete(true)
    } catch (e) {
      setError(getErrorMessage(e, '密码重置失败'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell title="设置新密码" subtitle="重置成功后需要重新登录" footer={<Link to="/login">返回登录</Link>}>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 18 }} />}
      <Form<ResetValues> layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          name="password"
          label="新密码"
          rules={[
            { required: true, message: '请输入新密码' },
            {
              validator: async (_, value: string) => {
                if (value && !isValidPassword(value)) {
                  throw new Error('密码需为 10-64 位，并同时包含字母和数字')
                }
              },
            },
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm_password"
          label="确认新密码"
          dependencies={['password']}
          rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator: async (_, value: string) => {
                if (value && value !== getFieldValue('password')) {
                  throw new Error('两次输入的密码不一致')
                }
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          确认重置
        </Button>
      </Form>
    </AuthShell>
  )
}
