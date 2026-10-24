import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { AuditOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { getErrorMessage, platformApi } from '../../api/client'
import type { RegistrationReview } from '../../types'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input

type ReviewMode = 'approve' | 'reject'

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending_email: { label: '待验证邮箱', color: 'default' },
  pending_review: { label: '待审核', color: 'gold' },
  approved: { label: '已开通', color: 'green' },
  rejected: { label: '已驳回', color: 'red' },
}

function formatTime(value: string | null): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

export default function RegistrationReviewPage() {
  const [rows, setRows] = useState<RegistrationReview[]>([])
  const [loading, setLoading] = useState(false)
  const [action, setAction] = useState<{
    mode: ReviewMode
    registration: RegistrationReview
  } | null>(null)
  const [note, setNote] = useState('')
  const [actionError, setActionError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await platformApi.listRegistrations()
      setRows(response.registrations)
    } catch (error) {
      message.error('加载注册申请失败：' + getErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openAction = (mode: ReviewMode, registration: RegistrationReview) => {
    setAction({ mode, registration })
    setNote('')
    setActionError('')
  }

  const closeAction = () => {
    if (submitting) return
    setAction(null)
    setNote('')
    setActionError('')
  }

  const submitAction = async () => {
    if (!action) return
    const trimmed = note.trim()
    if (action.mode === 'reject' && trimmed.length < 2) {
      setActionError('请填写驳回原因')
      return
    }

    setSubmitting(true)
    setActionError('')
    try {
      if (action.mode === 'approve') {
        await platformApi.approveRegistration(action.registration.id, trimmed || undefined)
        message.success('注册申请已通过，租户已开通')
      } else {
        await platformApi.rejectRegistration(action.registration.id, trimmed)
        message.success('注册申请已驳回')
      }
      setAction(null)
      setNote('')
      await load()
    } catch (error) {
      setActionError(getErrorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  const columns = useMemo<ColumnsType<RegistrationReview>>(
    () => [
      {
        title: '组织',
        dataIndex: 'organization_name',
        key: 'organization_name',
        width: 190,
        render: (value: string) => <Text strong>{value}</Text>,
      },
      {
        title: '申请人',
        dataIndex: 'admin_name',
        key: 'admin_name',
        width: 120,
      },
      {
        title: '联系方式',
        key: 'contact',
        width: 230,
        render: (_, row) => (
          <Space direction="vertical" size={0}>
            <Text>{row.email}</Text>
            <Text type="secondary">{row.phone_masked}</Text>
          </Space>
        ),
      },
      {
        title: '提交时间',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 155,
        render: formatTime,
      },
      {
        title: '邮箱验证',
        dataIndex: 'email_verified_at',
        key: 'email_verified_at',
        width: 120,
        render: (value: string | null) =>
          value ? <Tag color="green">已验证</Tag> : <Tag>未验证</Tag>,
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 110,
        render: (value: string) => {
          const meta = STATUS_META[value] ?? { label: value, color: 'default' }
          return <Tag color={meta.color}>{meta.label}</Tag>
        },
      },
      {
        title: '操作',
        key: 'actions',
        width: 170,
        fixed: 'right',
        render: (_, row) =>
          row.status === 'pending_review' ? (
            <Space>
              <Button
                type="primary"
                size="small"
                autoInsertSpace={false}
                onClick={() => openAction('approve', row)}
              >
                批准
              </Button>
              <Button
                danger
                size="small"
                autoInsertSpace={false}
                onClick={() => openAction('reject', row)}
              >
                驳回
              </Button>
            </Space>
          ) : (
            <Text type="secondary">已处理</Text>
          ),
      },
    ],
    [],
  )

  const isReject = action?.mode === 'reject'

  return (
    <div>
      <Space align="start" style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>
            <AuditOutlined style={{ color: '#6366f1', marginRight: 8 }} />
            注册审核
          </Title>
          <Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
            审核已通过邮箱验证的新租户申请。批准后创建租户及首个租户管理员。
          </Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
          刷新
        </Button>
      </Space>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        scroll={{ x: 1100 }}
        locale={{ emptyText: '暂无待审核注册申请' }}
      />

      <Modal
        title={isReject ? '驳回租户申请' : '批准租户申请'}
        open={Boolean(action)}
        onCancel={closeAction}
        destroyOnHidden
        footer={
          <Space>
            <Button onClick={closeAction} disabled={submitting}>
              取消
            </Button>
            <Button
              type={isReject ? 'primary' : 'primary'}
              danger={isReject}
              loading={submitting}
              onClick={submitAction}
            >
              {isReject ? '确认驳回' : '确认批准'}
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Text>
            申请组织：<Text strong>{action?.registration.organization_name}</Text>
          </Text>
          {isReject ? (
            <Alert
              type="warning"
              showIcon
              message="驳回后将通知申请人，且需要填写原因。"
            />
          ) : (
            <Alert
              type="info"
              showIcon
              message="批准后将立即创建租户、管理员账号和默认配置。"
            />
          )}
          {actionError && <Alert type="error" showIcon message={actionError} />}
          <TextArea
            value={note}
            onChange={(event) => {
              setNote(event.target.value)
              if (actionError) setActionError('')
            }}
            maxLength={500}
            showCount
            rows={4}
            placeholder={isReject ? '请填写驳回原因' : '可填写审核备注'}
          />
        </Space>
      </Modal>
    </div>
  )
}
