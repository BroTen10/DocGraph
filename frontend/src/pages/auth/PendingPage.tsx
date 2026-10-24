import { useEffect, useRef, useState } from 'react'
import { Alert, Result, Spin } from 'antd'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { authApi, getErrorMessage } from '../../api/client'

type PendingStatus = 'pending_email' | 'pending_review' | 'rejected' | 'verified' | 'error'

const COPY: Record<PendingStatus, { title: string; subTitle: string }> = {
  pending_email: {
    title: '请完成邮箱验证',
    subTitle: '验证邮件已发送，请点击邮件中的链接完成邮箱验证。',
  },
  pending_review: {
    title: '申请已提交',
    subTitle: '邮箱验证已完成，平台管理员审核通过后即可登录。',
  },
  rejected: {
    title: '申请未通过',
    subTitle: '请联系平台管理员了解审核结果。',
  },
  verified: {
    title: '邮箱验证成功',
    subTitle: '申请已进入平台审核队列，请耐心等待审核结果。',
  },
  error: {
    title: '邮箱验证失败',
    subTitle: '验证链接可能已失效或已被使用，请联系平台管理员。',
  },
}

export default function PendingPage() {
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const token = searchParams.get('token')
  const initialStatus = token
    ? 'verified'
    : (location.state as { status?: PendingStatus } | null)?.status ?? 'pending_email'
  const [status, setStatus] = useState<PendingStatus>(initialStatus)
  const [message, setMessage] = useState(
    (location.state as { message?: string } | null)?.message ?? '',
  )
  const [verifying, setVerifying] = useState(Boolean(token))
  const verificationStarted = useRef(false)

  useEffect(() => {
    if (!token || verificationStarted.current) return
    verificationStarted.current = true
    let active = true
    authApi.verifyEmail(token)
      .then((result) => {
        if (!active) return
        setStatus(result.status === 'pending_review' ? 'verified' : 'pending_review')
        setMessage(result.message)
      })
      .catch((error) => {
        if (!active) return
        setStatus('error')
        setMessage(getErrorMessage(error, '邮箱验证失败'))
      })
      .finally(() => {
        if (active) setVerifying(false)
      })
    return () => {
      active = false
    }
  }, [token])

  if (verifying) {
    return <div className="auth-loading"><Spin size="large" tip="正在验证邮箱..." /></div>
  }

  const copy = COPY[status]
  return (
    <main className="auth-page">
      <div className="auth-panel">
        <Result
          status={status === 'error' || status === 'rejected' ? 'warning' : 'success'}
          title={copy.title}
          subTitle={copy.subTitle}
          extra={<Link to="/login">返回登录</Link>}
        />
        {message && <Alert type="info" showIcon message={message} />}
      </div>
    </main>
  )
}
