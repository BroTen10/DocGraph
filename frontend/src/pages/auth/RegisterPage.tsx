import { useState } from 'react'
import { Alert, Button, Checkbox, Form, Input } from 'antd'
import { Link, useNavigate } from 'react-router-dom'
import { authApi, getErrorMessage } from '../../api/client'
import {
  isValidEmail,
  isValidPassword,
  isValidPhone,
  normalizePhone,
} from '../../auth/validation'
import type { RegisterTenantRequest } from '../../types'
import AuthShell from './AuthShell'

interface RegisterValues {
  organization_name: string
  admin_name: string
  email: string
  phone: string
  password: string
  confirm_password: string
  accept_terms: boolean
}

export default function RegisterPage() {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  const handleSubmit = async (values: RegisterValues) => {
    setSubmitting(true)
    setError('')
    try {
      const payload: RegisterTenantRequest = {
        organization_name: values.organization_name.trim(),
        admin_name: values.admin_name.trim(),
        email: values.email.trim().toLowerCase(),
        phone: normalizePhone(values.phone),
        password: values.password,
        accept_terms: values.accept_terms,
      }
      const result = await authApi.register(payload)
      navigate('/pending', {
        replace: true,
        state: { status: result.status, message: result.message },
      })
    } catch (e) {
      setError(getErrorMessage(e, '提交申请失败，请稍后重试'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell
      wide
      title="申请开通租户"
      subtitle="提交组织和管理员资料，邮箱验证后将由平台管理员审核"
      footer={<span>已有账号？<Link to="/login">返回登录</Link></span>}
    >
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 18 }} />}
      <Form<RegisterValues> layout="vertical" onFinish={handleSubmit} requiredMark="optional">
        <div className="auth-form-grid">
          <Form.Item
            name="organization_name"
            label="组织名称"
            rules={[
              { required: true, message: '请输入组织名称' },
              { min: 2, max: 100, message: '组织名称为 2-100 个字符' },
            ]}
          >
            <Input placeholder="例如：华东贸易有限公司" />
          </Form.Item>
          <Form.Item
            name="admin_name"
            label="管理员姓名"
            rules={[
              { required: true, message: '请输入管理员姓名' },
              { max: 50, message: '姓名最多 50 个字符' },
            ]}
          >
            <Input placeholder="请输入真实姓名" />
          </Form.Item>
          <Form.Item
            name="email"
            label="工作邮箱"
            rules={[
              { required: true, message: '请输入工作邮箱' },
              {
                validator: async (_, value: string) => {
                  if (value && !isValidEmail(value)) throw new Error('邮箱格式不正确')
                },
              },
            ]}
          >
            <Input autoComplete="email" placeholder="name@company.com" />
          </Form.Item>
          <Form.Item
            name="phone"
            label="手机号"
            extra="用于客户回访，本期仅做格式校验"
            rules={[
              { required: true, message: '请输入手机号' },
              {
                validator: async (_, value: string) => {
                  if (value && !isValidPhone(value)) throw new Error('请输入有效的中国大陆手机号')
                },
              },
            ]}
          >
            <Input autoComplete="tel" placeholder="13800138000" />
          </Form.Item>
          <Form.Item
            name="password"
            label="登录密码"
            rules={[
              { required: true, message: '请输入登录密码' },
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
        </div>
        <Form.Item
          name="accept_terms"
          valuePropName="checked"
          rules={[{
            validator: async (_, value: boolean) => {
              if (!value) throw new Error('请先同意服务条款与隐私告知')
            },
          }]}
        >
          <Checkbox>我已阅读并同意服务条款与隐私告知</Checkbox>
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          提交开通申请
        </Button>
      </Form>
    </AuthShell>
  )
}
