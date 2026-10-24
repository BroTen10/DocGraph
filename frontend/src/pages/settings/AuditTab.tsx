import { useEffect, useState } from 'react'
import { Button, Card, Empty, Input, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { getErrorMessage, tenantAuditApi } from '../../api/client'
import type { AuditLogItem } from '../../types'

const { Text } = Typography

export default function AuditTab() {
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [action, setAction] = useState('')
  const [loading, setLoading] = useState(false)

  const load = async (offset = 0) => {
    setLoading(true)
    try {
      const response = await tenantAuditApi.list({
        action: action.trim() || undefined,
        offset,
        limit: 50,
      })
      setItems(response.items)
      setTotal(response.total)
    } catch (error) {
      message.error('加载审计日志失败: ' + getErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <Card
      size="small"
      title="审计日志"
      extra={
        <Space>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="精确筛选 action"
            value={action}
            onChange={(event) => setAction(event.target.value)}
            onPressEnter={() => void load()}
            style={{ width: 240 }}
          />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
        </Space>
      }
    >
      <Table<AuditLogItem>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: <Empty description="暂无审计记录" /> }}
        pagination={{
          total,
          pageSize: 50,
          showSizeChanger: false,
          onChange: (page) => void load((page - 1) * 50),
        }}
        columns={[
          {
            title: '时间',
            dataIndex: 'created_at',
            width: 170,
            render: (value: string) => dayjs(value).format('YYYY-MM-DD HH:mm:ss'),
          },
          { title: '操作', dataIndex: 'action', width: 240 },
          {
            title: '结果',
            dataIndex: 'result',
            width: 90,
            render: (value: string | null) => (
              <Tag color={value === 'success' ? 'green' : 'red'}>{value || '-'}</Tag>
            ),
          },
          {
            title: '对象',
            key: 'target',
            width: 240,
            render: (_: unknown, row: AuditLogItem) => (
              <Text type="secondary">
                {row.target_type || '-'} {row.target_id || ''}
              </Text>
            ),
          },
          { title: 'Request ID', dataIndex: 'request_id', width: 180 },
          {
            title: '详情',
            dataIndex: 'metadata',
            render: (value: Record<string, unknown>) => (
              <Text code style={{ whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(value)}
              </Text>
            ),
          },
        ]}
        scroll={{ x: 1100 }}
      />
    </Card>
  )
}
