import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { BankOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { getErrorMessage, platformApi } from '../../api/client'
import type { PlatformTenant } from '../../types'

const { Paragraph, Text, Title } = Typography

type TenantTargetStatus = 'active' | 'suspended' | 'archived'

const STATUS_META: Record<string, { label: string; color: string }> = {
  active: { label: '正常', color: 'green' },
  suspended: { label: '已暂停', color: 'orange' },
  archived: { label: '已归档', color: 'default' },
  pending: { label: '待审核', color: 'gold' },
  rejected: { label: '已驳回', color: 'red' },
}

const ACTION_COPY: Record<TenantTargetStatus, {
  title: string
  button: string
  description: string
}> = {
  active: {
    title: '恢复租户',
    button: '确认恢复',
    description: '恢复后租户成员可以重新登录并继续使用系统。',
  },
  suspended: {
    title: '暂停租户',
    button: '确认暂停',
    description: '暂停后现有成员会话将立即失效，所有成员将无法进入系统。',
  },
  archived: {
    title: '归档租户',
    button: '确认归档',
    description: '归档后租户成员将无法进入系统。归档前请确认已完成数据备份和留存安排。',
  },
}

function formatTime(value: string | null): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—'
}

export default function TenantListPage() {
  const [rows, setRows] = useState<PlatformTenant[]>([])
  const [loading, setLoading] = useState(false)
  const [action, setAction] = useState<{
    tenant: PlatformTenant
    status: TenantTargetStatus
  } | null>(null)
  const [actionError, setActionError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await platformApi.listTenants()
      setRows(response.tenants)
    } catch (error) {
      message.error('加载租户列表失败：' + getErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openAction = (tenant: PlatformTenant, status: TenantTargetStatus) => {
    setAction({ tenant, status })
    setActionError('')
  }

  const closeAction = () => {
    if (submitting) return
    setAction(null)
    setActionError('')
  }

  const submitAction = async () => {
    if (!action) return
    setSubmitting(true)
    setActionError('')
    try {
      await platformApi.setTenantStatus(action.tenant.id, action.status)
      message.success(
        action.status === 'active'
          ? '租户已恢复'
          : action.status === 'suspended'
            ? '租户已暂停'
            : '租户已归档',
      )
      setAction(null)
      await load()
    } catch (error) {
      setActionError(getErrorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  const columns = useMemo<ColumnsType<PlatformTenant>>(
    () => [
      {
        title: '租户名称',
        dataIndex: 'name',
        key: 'name',
        render: (value: string) => <Text strong>{value}</Text>,
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 120,
        render: (value: string) => {
          const meta = STATUS_META[value] ?? { label: value, color: 'default' }
          return <Tag color={meta.color}>{meta.label}</Tag>
        },
      },
      {
        title: '成员数',
        dataIndex: 'member_count',
        key: 'member_count',
        width: 100,
        align: 'center',
      },
      {
        title: '创建时间',
        dataIndex: 'created_at',
        key: 'created_at',
        width: 160,
        render: formatTime,
      },
      {
        title: '最近审核时间',
        dataIndex: 'approved_at',
        key: 'approved_at',
        width: 160,
        render: formatTime,
      },
      {
        title: '操作',
        key: 'actions',
        width: 230,
        fixed: 'right',
        render: (_, row) => {
          if (row.status === 'archived') {
            return <Text type="secondary">已归档</Text>
          }
          return (
            <Space wrap>
              {row.status === 'active' ? (
                <Button
                  size="small"
                  autoInsertSpace={false}
                  onClick={() => openAction(row, 'suspended')}
                >
                  暂停
                </Button>
              ) : (
                <Button
                  size="small"
                  type="primary"
                  autoInsertSpace={false}
                  onClick={() => openAction(row, 'active')}
                >
                  恢复
                </Button>
              )}
              <Button
                size="small"
                danger
                autoInsertSpace={false}
                onClick={() => openAction(row, 'archived')}
              >
                归档
              </Button>
            </Space>
          )
        },
      },
    ],
    [],
  )

  const copy = action ? ACTION_COPY[action.status] : null

  return (
    <div>
      <Space align="start" style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>
            <BankOutlined style={{ color: '#6366f1', marginRight: 8 }} />
            租户管理
          </Title>
          <Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
            仅展示租户元数据、成员数量和访问状态，不提供任何租户业务内容入口。
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
        scroll={{ x: 950 }}
        locale={{ emptyText: '暂无租户' }}
      />

      <Modal
        title={copy?.title ?? '更新租户状态'}
        open={Boolean(action)}
        onCancel={closeAction}
        destroyOnHidden
        footer={
          <Space>
            <Button onClick={closeAction} disabled={submitting}>
              取消
            </Button>
            <Button
              type={action?.status === 'active' ? 'primary' : 'primary'}
              danger={action?.status !== 'active'}
              loading={submitting}
              onClick={submitAction}
            >
              {copy?.button ?? '确认'}
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Text>
            租户：<Text strong>{action?.tenant.name}</Text>
          </Text>
          <Text type="secondary">{copy?.description}</Text>
          {actionError && <Text type="danger">{actionError}</Text>}
        </Space>
      </Modal>
    </div>
  )
}
