import { useEffect, useState, useCallback } from 'react'
import {
  Card, Table, Button, Modal, Form, Input, Tag, Space, message, Typography,
  Popconfirm, Tooltip, Alert, Tabs, Select,
} from 'antd'
import { PlusOutlined, EditOutlined, MergeCellsOutlined, ReloadOutlined, InfoCircleOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { checkIntentsApi, getErrorMessage } from '../api/client'
import type { CheckIntentItem } from '../types'
import { useAuth } from '../auth/AuthContext'
import PageHeader from '../components/PageHeader'

const { Text } = Typography

const SOURCE_LABEL: Record<string, string> = {
  seed: '内置',
  rule_import: '规则导入发现',
  manual: '手动创建',
}

/**
 * 检查项管理页。
 *
 * 检查项（检查意图）过去只是 rule_sets.check_categories 里的一个字符串，
 * 大模型写出什么就记什么，既没有"户口本"也没有确认环节。
 * 本页把检查项升级为与「文档类型」对称的注册表：
 * 待确认 → 确认后才参与规则矩阵分列；可改名（同步改写存量规则）、合并、启停。
 */
export default function CheckIntentsPage() {
  const { hasPermission } = useAuth()
  const canManage = hasPermission('rules.manage')

  const [activeTab, setActiveTab] = useState('active')
  const [items, setItems] = useState<CheckIntentItem[]>([])
  const [loading, setLoading] = useState(false)

  // 新建 / 编辑
  const [editOpen, setEditOpen] = useState(false)
  const [editing, setEditing] = useState<CheckIntentItem | null>(null)
  const [form] = Form.useForm()

  // 合并
  const [mergeOpen, setMergeOpen] = useState(false)
  const [mergeSource, setMergeSource] = useState<CheckIntentItem | null>(null)
  const [mergeTargetId, setMergeTargetId] = useState<string | undefined>(undefined)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const rows = await checkIntentsApi.list()
      setItems(rows)
    } catch (e) {
      message.error('加载检查项失败: ' + getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const pending = items.filter((x) => x.status === 'pending_review')
  const active = items.filter((x) => x.status === 'active')
  const rejected = items.filter((x) => x.status === 'rejected')
  const current = activeTab === 'pending' ? pending : activeTab === 'rejected' ? rejected : active

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ sort_order: 100, status: 'active', aliases: [] })
    setEditOpen(true)
  }

  const openEdit = (row: CheckIntentItem) => {
    setEditing(row)
    form.setFieldsValue({
      name: row.name,
      description: row.description || '',
      aliases: row.aliases || [],
      sort_order: row.sort_order,
      status: row.status,
    })
    setEditOpen(true)
  }

  const submitEdit = async () => {
    const values = await form.validateFields()
    try {
      if (editing) {
        await checkIntentsApi.update(editing.id, {
          name: values.name,
          description: values.description || null,
          aliases: values.aliases || [],
          sort_order: values.sort_order,
          status: values.status,
        })
        message.success(
          values.name !== editing.name
            ? `已改名为「${values.name}」，存量规则引用已同步改写`
            : '已保存',
        )
      } else {
        await checkIntentsApi.create({
          name: values.name,
          description: values.description || null,
          aliases: values.aliases || [],
          sort_order: values.sort_order,
          status: values.status,
        })
        message.success('已创建')
      }
      setEditOpen(false)
      await load()
    } catch (e) {
      message.error('保存失败: ' + getErrorMessage(e))
    }
  }

  const setStatus = async (row: CheckIntentItem, status: string) => {
    try {
      await checkIntentsApi.update(row.id, { status })
      message.success(status === 'active' ? `已确认「${row.name}」` : `已停用「${row.name}」`)
      await load()
    } catch (e) {
      message.error('操作失败: ' + getErrorMessage(e))
    }
  }

  const submitMerge = async () => {
    if (!mergeSource || !mergeTargetId) return
    try {
      await checkIntentsApi.merge(mergeSource.id, mergeTargetId)
      message.success('已合并，存量规则引用已改写到目标检查项')
      setMergeOpen(false)
      setMergeSource(null)
      setMergeTargetId(undefined)
      await load()
    } catch (e) {
      message.error('合并失败: ' + getErrorMessage(e))
    }
  }

  const remove = async (row: CheckIntentItem) => {
    try {
      await checkIntentsApi.delete(row.id)
      message.success('已删除')
      await load()
    } catch (e) {
      message.error('删除失败: ' + getErrorMessage(e))
    }
  }

  const columns: ColumnsType<CheckIntentItem> = [
    {
      title: '检查项',
      dataIndex: 'name',
      key: 'name',
      render: (v: string, row) => (
        <Space size={6}>
          <Text strong>{v}</Text>
          {row.source === 'seed' && <Tag color="blue">内置</Tag>}
          {row.source === 'rule_import' && <Tag color="orange">规则发现</Tag>}
        </Space>
      ),
    },
    {
      title: '别名（导入时按别名归一到本检查项）',
      dataIndex: 'aliases',
      key: 'aliases',
      render: (aliases: string[]) =>
        aliases?.length ? (
          <Space size={4} wrap>
            {aliases.map((a) => <Tag key={a}>{a}</Tag>)}
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: '说明',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (v: string | null) => v || <Text type="secondary">—</Text>,
    },
    {
      title: (
        <Tooltip title="有多少条规则在使用这个检查项；改名/合并会同步改写这些规则">
          <span>使用规则数 <InfoCircleOutlined /></span>
        </Tooltip>
      ),
      dataIndex: 'rule_count',
      key: 'rule_count',
      width: 110,
      render: (v: number) => (v > 0 ? <Tag color="geekblue">{v}</Tag> : <Text type="secondary">0</Text>),
    },
    { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 70 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) =>
        v === 'active' ? <Tag color="green">已确认</Tag>
          : v === 'pending_review' ? <Tag color="orange">待确认</Tag>
            : <Tag>已弃用</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_, row) => (
        <Space size={4} wrap>
          {row.status === 'active' ? (
            <Button size="small" onClick={() => setStatus(row, 'pending_review')} disabled={!canManage}>
              停用
            </Button>
          ) : (
            <Button size="small" type="primary" onClick={() => setStatus(row, 'active')} disabled={!canManage}>
              确认
            </Button>
          )}
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} disabled={!canManage}>
            编辑
          </Button>
          <Button
            size="small"
            icon={<MergeCellsOutlined />}
            disabled={!canManage || row.status === 'rejected'}
            onClick={() => { setMergeSource(row); setMergeTargetId(undefined); setMergeOpen(true) }}
          >
            合并
          </Button>
          <Popconfirm
            title="删除该检查项？"
            description={row.rule_count > 0 ? `仍有 ${row.rule_count} 条规则在使用，需先改名或合并` : '删除后不可恢复'}
            onConfirm={() => remove(row)}
            disabled={!canManage}
          >
            <Button size="small" danger disabled={!canManage}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="检查项"
        subtitle="检查项（检查意图）注册表：规则导入发现的检查项先进入待确认，确认后才参与规则矩阵分列与执行分派；支持改名、合并、别名归一"
      />

      {pending.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`有 ${pending.length} 个检查项待确认`}
          description={
            <span style={{ fontSize: 12 }}>
              这些检查项是规则导入时由大模型新发现并登记的（{pending.map((p) => p.name).join('、')}）。
              未确认前它们不会成为规则列表里的独立检查项列，相关规则会归入「未分类」。
              如果它们只是已有检查项的另一种叫法，请用「合并」把它并到规范检查项上——
              合并会把存量规则的引用一起改写，并把旧名保留为别名，后续导入自动归一。
            </span>
          }
        />
      )}

      <Card
        size="small"
        title={<Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            { key: 'active', label: `已确认 (${active.length})` },
            { key: 'pending', label: `待确认 (${pending.length})` },
            { key: 'rejected', label: `已弃用 (${rejected.length})` },
          ]}
        />}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} disabled={!canManage}>
              新建检查项
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={current}
          pagination={false}
        />
      </Card>

      <Modal
        title={editing ? `编辑检查项：${editing.name}` : '新建检查项'}
        open={editOpen}
        onOk={submitEdit}
        onCancel={() => setEditOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="检查项名称"
            tooltip="规范名称，规则矩阵与执行分派都用它；改名会同步改写存量规则的引用"
            rules={[{ required: true, message: '请输入检查项名称' }]}
          >
            <Input placeholder="如：金额一致性" />
          </Form.Item>
          <Form.Item
            name="aliases"
            label="别名"
            tooltip="导入时把别名映射到本检查项，避免同一件事出现多个名字"
          >
            <Select mode="tags" placeholder="回车添加，如：金额校验、金额核对" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} placeholder="可选：这个检查项查什么" />
          </Form.Item>
          <Space size={16}>
            <Form.Item name="sort_order" label="排序" tooltip="数字小的排前面">
              <Input type="number" style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="status" label="状态">
              <Select
                style={{ width: 140 }}
                options={[
                  { value: 'active', label: '已确认' },
                  { value: 'pending_review', label: '待确认' },
                ]}
              />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={`合并检查项：${mergeSource?.name ?? ''}`}
        open={mergeOpen}
        onOk={submitMerge}
        onCancel={() => setMergeOpen(false)}
        okButtonProps={{ disabled: !mergeTargetId }}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="合并后：存量规则里对「本检查项」的引用会改写成目标检查项，本项旧名转成目标的别名（后续导入自动归一），本项标记为已弃用。"
        />
        <Form layout="vertical">
          <Form.Item label="合并到" required>
            <Select
              value={mergeTargetId}
              onChange={setMergeTargetId}
              placeholder="选择目标检查项"
              options={items
                .filter((x) => x.id !== mergeSource?.id && x.status === 'active')
                .map((x) => ({ value: x.id, label: `${x.name}（${x.rule_count} 条规则）` }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
