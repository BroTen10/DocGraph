import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  getApiErrorCode,
  getErrorMessage,
  tenantMembersApi,
} from '../../api/client'
import { isValidEmail, normalizePhone } from '../../auth/validation'
import type { Invitation, Member, TenantRole } from '../../types'

const { Text } = Typography

const ROLE_OPTIONS: Array<{ value: TenantRole; label: string }> = [
  { value: 'tenant_admin', label: '租户管理员' },
  { value: 'reviewer', label: '审查员' },
  { value: 'viewer', label: '只读查看者' },
]

const ROLE_LABEL = Object.fromEntries(
  ROLE_OPTIONS.map((option) => [option.value, option.label]),
) as Record<TenantRole, string>

type MemberStatus = 'active' | 'disabled'
type StatusFilter = 'all' | MemberStatus | 'invited'

export default function MembersTab() {
  const [members, setMembers] = useState<Member[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [loading, setLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [searchText, setSearchText] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [inviteOpen, setInviteOpen] = useState(false)
  const [inviting, setInviting] = useState(false)
  const [revealedPhones, setRevealedPhones] = useState<Record<string, string>>({})
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const response = await tenantMembersApi.list()
      setMembers(response.members)
      setInvitations(response.invitations)
    } catch (error) {
      message.error('加载成员失败: ' + getErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const keyword = searchText.trim().toLowerCase()
  const visibleMembers = useMemo(
    () =>
      members.filter((member) => {
        if (statusFilter !== 'all' && statusFilter !== 'invited' && member.status !== statusFilter) {
          return false
        }
        if (statusFilter === 'invited') return false
        const haystack = [
          member.name,
          member.email,
          member.phone_masked,
          ROLE_LABEL[member.role],
        ]
          .join(' ')
          .toLowerCase()
        return !keyword || haystack.includes(keyword)
      }),
    [keyword, members, statusFilter],
  )

  const visibleInvitations = useMemo(
    () =>
      invitations.filter((invitation) => {
        if (statusFilter !== 'all' && statusFilter !== 'invited') return false
        const haystack = [
          invitation.name,
          invitation.email,
          invitation.phone_masked,
          ROLE_LABEL[invitation.role],
        ]
          .join(' ')
          .toLowerCase()
        return !keyword || haystack.includes(keyword)
      }),
    [invitations, keyword, statusFilter],
  )

  const showBusinessError = (error: unknown, fallback: string) => {
    const code = getApiErrorCode(error)
    const text =
      code === 'LAST_TENANT_ADMIN'
        ? '至少需要保留一名有效的租户管理员'
        : code === 'ACCOUNT_EXISTS'
          ? '该邮箱或手机号已经被注册'
          : code === 'INVITE_EXISTS'
            ? '该邮箱已有待接受邀请'
            : getErrorMessage(error, fallback)
    setActionError(text)
    message.error(text)
  }

  const updateMember = async (
    member: Member,
    payload: { role?: TenantRole; status?: MemberStatus },
  ) => {
    setActionLoading(member.id)
    setActionError(null)
    try {
      await tenantMembersApi.update(member.id, payload)
      message.success('成员权限已更新')
      await load()
    } catch (error) {
      showBusinessError(error, '保存成员权限失败')
    } finally {
      setActionLoading(null)
    }
  }

  const revealPhone = async (kind: 'member' | 'invitation', id: string) => {
    setActionLoading(id)
    setActionError(null)
    try {
      const response =
        kind === 'member'
          ? await tenantMembersApi.revealPhone(id)
          : await tenantMembersApi.revealInvitationPhone(id)
      setRevealedPhones((current) => ({ ...current, [id]: response.phone }))
      message.info('已记录本次手机号查看审计')
    } catch (error) {
      message.error('查看手机号失败: ' + getErrorMessage(error))
    } finally {
      setActionLoading(null)
    }
  }

  const invite = async (values: {
    name: string
    email: string
    phone: string
    role: TenantRole
  }) => {
    setInviting(true)
    setActionError(null)
    try {
      await tenantMembersApi.invite({
        name: values.name.trim(),
        email: values.email.trim().toLowerCase(),
        phone: normalizePhone(values.phone),
        role: values.role,
      })
      message.success('邀请已发送')
      setInviteOpen(false)
      form.resetFields()
      await load()
    } catch (error) {
      showBusinessError(error, '发送邀请失败')
    } finally {
      setInviting(false)
    }
  }

  const resendInvitation = async (invitation: Invitation) => {
    setActionLoading(invitation.id)
    setActionError(null)
    try {
      await tenantMembersApi.resend(invitation.id)
      message.success('邀请已重新发送')
      await load()
    } catch (error) {
      showBusinessError(error, '重发邀请失败')
    } finally {
      setActionLoading(null)
    }
  }

  const revokeInvitation = async (invitation: Invitation) => {
    setActionLoading(invitation.id)
    setActionError(null)
    try {
      await tenantMembersApi.revoke(invitation.id)
      message.success('邀请已撤销')
      await load()
    } catch (error) {
      showBusinessError(error, '撤销邀请失败')
    } finally {
      setActionLoading(null)
    }
  }

  const memberColumns = [
    {
      title: '成员',
      key: 'member',
      render: (_: unknown, member: Member) => (
        <Space direction="vertical" size={0}>
          <Text strong>{member.name}</Text>
          <Text type="secondary">{member.email}</Text>
        </Space>
      ),
    },
    {
      title: '手机号',
      key: 'phone',
      width: 210,
      render: (_: unknown, member: Member) => {
        const revealed = revealedPhones[member.id]
        return (
          <Space>
            <span>{revealed ?? member.phone_masked}</span>
            {!revealed && (
              <Popconfirm
                title="查看完整手机号"
                description="完整号码属于个人信息，本次查看将记录操作审计。"
                okText="确认查看"
                cancelText="取消"
                onConfirm={() => revealPhone('member', member.id)}
              >
                <Button
                  type="link"
                  size="small"
                  icon={<EyeOutlined />}
                  loading={actionLoading === member.id}
                >
                  查看完整手机号
                </Button>
              </Popconfirm>
            )}
          </Space>
        )
      },
    },
    {
      title: '角色',
      key: 'role',
      width: 170,
      render: (_: unknown, member: Member) => (
        <Select
          aria-label={`角色-${member.name}`}
          value={member.role}
          options={ROLE_OPTIONS}
          loading={actionLoading === member.id}
          style={{ width: 145 }}
          onChange={(role: TenantRole) => updateMember(member, { role })}
        />
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 90,
      render: (_: unknown, member: Member) => (
        <Tag color={member.status === 'active' ? 'green' : 'default'}>
          {member.status === 'active' ? '正常' : '已停用'}
        </Tag>
      ),
    },
    {
      title: '加入时间',
      key: 'joined_at',
      width: 170,
      render: (_: unknown, member: Member) =>
        member.joined_at ? dayjs(member.joined_at).format('YYYY-MM-DD HH:mm') : '待接受邀请',
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, member: Member) => (
        <Popconfirm
          title={member.status === 'active' ? '停用该成员？' : '恢复该成员？'}
          description={
            member.status === 'active'
              ? '停用后该成员的现有登录会话会立即失效。'
              : '恢复后该成员可以使用原角色重新登录。'
          }
          okText={member.status === 'active' ? '确认停用' : '确认恢复'}
          onConfirm={() =>
            updateMember(member, {
              status: member.status === 'active' ? 'disabled' : 'active',
            })
          }
        >
          <Button
            danger={member.status === 'active'}
            size="small"
            loading={actionLoading === member.id}
          >
            {member.status === 'active' ? '停用' : '恢复'}
          </Button>
        </Popconfirm>
      ),
    },
  ]

  const invitationColumns = [
    {
      title: '受邀人',
      key: 'invitee',
      render: (_: unknown, invitation: Invitation) => (
        <Space direction="vertical" size={0}>
          <Text strong>{invitation.name}</Text>
          <Text type="secondary">{invitation.email}</Text>
        </Space>
      ),
    },
    {
      title: '手机号',
      key: 'phone',
      render: (_: unknown, invitation: Invitation) => {
        const revealed = revealedPhones[invitation.id]
        return (
          <Space>
            <span>{revealed ?? invitation.phone_masked}</span>
            {!revealed && (
              <Popconfirm
                title="查看完整手机号"
                description="完整号码属于个人信息，本次查看将记录操作审计。"
                okText="确认查看"
                onConfirm={() => revealPhone('invitation', invitation.id)}
              >
                <Button type="link" size="small" icon={<EyeOutlined />}>
                  查看完整手机号
                </Button>
              </Popconfirm>
            )}
          </Space>
        )
      },
    },
    {
      title: '角色',
      key: 'role',
      render: (role: TenantRole) => ROLE_LABEL[role],
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={status === 'resent' ? 'gold' : 'blue'}>
          {status === 'resent' ? '已重发' : '待接受'}
        </Tag>
      ),
    },
    {
      title: '有效期至',
      key: 'expires_at',
      render: (_: unknown, invitation: Invitation) =>
        invitation.expires_at
          ? dayjs(invitation.expires_at).format('YYYY-MM-DD HH:mm')
          : '无',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, invitation: Invitation) => (
        <Space>
          <Button
            size="small"
            onClick={() => resendInvitation(invitation)}
            loading={actionLoading === invitation.id}
          >
            重发
          </Button>
          <Popconfirm
            title="撤销该邀请？"
            okText="确认撤销"
            onConfirm={() => revokeInvitation(invitation)}
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={actionLoading === invitation.id}
            >
              撤销
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      {actionError && (
        <Alert
          type="error"
          showIcon
          closable
          message={actionError}
          onClose={() => setActionError(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      <Card
        title="租户成员"
        size="small"
        extra={
          <Space wrap>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索姓名、邮箱或手机号"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              style={{ width: 220 }}
            />
            <Select
              value={statusFilter}
              onChange={setStatusFilter}
              style={{ width: 130 }}
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'active', label: '正常成员' },
                { value: 'disabled', label: '已停用' },
                { value: 'invited', label: '待接受邀请' },
              ]}
            />
            <Tooltip title="刷新成员列表">
              <Button icon={<ReloadOutlined />} onClick={load} loading={loading} />
            </Tooltip>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setInviteOpen(true)}
            >
              邀请成员
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          size="middle"
          loading={loading}
          dataSource={visibleMembers}
          columns={memberColumns}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无成员" /> }}
          scroll={{ x: 900 }}
        />
      </Card>

      {statusFilter === 'all' || statusFilter === 'invited' ? (
        <Card
          title={`待接受邀请（${visibleInvitations.length}）`}
          size="small"
          style={{ marginTop: 16 }}
        >
          <Table
            rowKey="id"
            size="middle"
            loading={loading}
            dataSource={visibleInvitations}
            columns={invitationColumns}
            pagination={false}
            locale={{ emptyText: <Empty description="暂无待接受邀请" /> }}
            scroll={{ x: 860 }}
          />
        </Card>
      ) : null}

      <Modal
        title="邀请成员"
        open={inviteOpen}
        onCancel={() => {
          setInviteOpen(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        okText="发送邀请"
        confirmLoading={inviting}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={invite}
          initialValues={{ role: 'reviewer' }}
        >
          <Form.Item
            name="name"
            label="姓名"
            rules={[{ required: true, message: '请输入成员姓名' }]}
          >
            <Input maxLength={50} placeholder="请输入真实姓名" />
          </Form.Item>
          <Form.Item
            name="email"
            label="工作邮箱"
            rules={[
              { required: true, message: '请输入工作邮箱' },
              {
                validator: async (_, value: string) => {
                  if (value && !isValidEmail(value)) {
                    throw new Error('邮箱格式不正确')
                  }
                },
              },
            ]}
          >
            <Input maxLength={254} placeholder="name@company.com" />
          </Form.Item>
          <Form.Item
            name="phone"
            label="手机号"
            extra="用于业务回访；默认脱敏展示，查看完整号码会记录审计。"
            rules={[
              { required: true, message: '请输入手机号' },
              {
                validator: async (_, value: string) => {
                  try {
                    normalizePhone(value)
                  } catch (error) {
                    throw new Error(
                      error instanceof Error ? error.message : '手机号格式不正确',
                    )
                  }
                },
              },
            ]}
          >
            <Input maxLength={20} placeholder="13800138000" />
          </Form.Item>
          <Form.Item name="role" label="初始角色" rules={[{ required: true }]}>
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
