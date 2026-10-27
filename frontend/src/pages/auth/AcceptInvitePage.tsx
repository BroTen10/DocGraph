import { useState } from 'react'
import { Alert, Button, Form, Input, Result } from 'antd'
import { Link } from 'react-router-dom'
import { authApi, getErrorMessage } from '../../api/client'
import { isValidPassword } from '../../auth/validation'
import { useOneTimeToken } from '../../auth/useOneTimeToken'
import AuthShell from './AuthShell'

interface InviteValues {
  password: string
  confirm_password: string
}

export default function AcceptInvitePage() {
  const token = useOneTimeToken()
  const [submitting, setSubmitting] = useState(false)
  const [complete, setComplete] = useState(false)
  const [error, setError] = useState('')

  if (complete) {
    return (
      <main className="auth-page">
        <div className="auth-panel">
          <Result
            status="success"
            title="邀请已接受"
            subTitle="账号已创建，请使用工作邮箱和刚设置的密码登录。"
            extra={<Link to="/login">返回登录</Link>}
          />
        </div>
      </main>
    )
  }

  if (!token) {
    return (
      <main className="auth-page">
        <div className="auth-panel">
          <Result
            status="error"
            title="邀请链接无效"
            subTitle="链接缺少邀请令牌，请联系租户管理员重新发送邀请。"
            extra={<Link to="/login">返回登录</Link>}
          />
        </div>
      </main>
    )
  }

  const handleSubmit = async (values: InviteValues) => {
    setSubmitting(true)
    setError('')
    try {
      await authApi.acceptInvite(token, values.password)
      setComplete(true)
    } catch (e) {
      setError(getErrorMessage(e, '接受邀请失败'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell title="接受租户邀请" subtitle="设置登录密码后即可加入租户" footer={<Link to="/login">返回登录</Link>}>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 18 }} />}
      <Form<InviteValues> layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          name="password"
          label="登录密码"
          rules={[
            { required: true, message: '请输入登录密码' },
            {
              validator: async (_, value: string) => {
                if (value && !isValidPassword(value)) {
                  throw new Error('密码需为 6-64 位，并同时包含字母和数字')
                }
              },
            },
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm_password"
          label="确认密码"
          dependencies={['password']}
          rules={[
            { required: true, message: '请再次输入密码' },
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
          接受邀请
        </Button>
      </Form>
    </AuthShell>
  )
}
