/** 规则集切换器:Header 右上角下拉切换 + 创建新规则集弹窗。 */

import { useEffect, useMemo, useState } from 'react'
import { Button, Dropdown, Modal, Form, Input, Select, Switch, Space, Tag, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { PlusOutlined, DownOutlined, AppstoreOutlined } from '@ant-design/icons'
import { useRuleSet } from '../context/RuleSetContext'
import { constantsApi } from '../api/client'
import type { DocTypeMeta } from '../types'

const { Text } = Typography

export function RuleSetSwitcher() {
  const { ruleSets, current, currentId, loading, switchTo, create } = useRuleSet()
  const [modalOpen, setModalOpen] = useState(false)
  const [docTypes, setDocTypes] = useState<DocTypeMeta[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()

  // 加载文件类型清单(供"适用文件类型"多选)
  useEffect(() => {
    constantsApi.docTypes().then((res) => setDocTypes(res.doc_types)).catch(() => {})
  }, [])

  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      setSubmitting(true)
      await create({
        name: values.name,
        description: values.description,
        doc_types: values.doc_types || [],
        is_default: values.is_default || false,
      })
      setModalOpen(false)
      form.resetFields()
    } catch (e) {
      // 校验失败或创建失败,message 已由 context/form 处理
    } finally {
      setSubmitting(false)
    }
  }

  const menuItems: MenuProps['items'] = useMemo(() => {
    const items: MenuProps['items'] = ruleSets.map((r) => ({
      key: r.id,
      label: (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, minWidth: 240 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 500, color: r.id === currentId ? '#6366f1' : '#0f172a' }}>
              {r.name}
            </div>
            {r.description && (
              <div style={{ fontSize: 11, color: '#94a3b8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {r.description}
              </div>
            )}
          </div>
          {r.is_default && <Tag color="purple" style={{ margin: 0 }}>默认</Tag>}
        </div>
      ),
      onClick: () => switchTo(r.id),
    }))
    if (ruleSets.length > 0) {
      items.push({ type: 'divider' })
    }
    items.push({
      key: '__create__',
      label: (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#6366f1', fontWeight: 500 }}>
          <PlusOutlined /> 新建规则集
        </div>
      ),
      onClick: () => setModalOpen(true),
    })
    return items
  }, [ruleSets, currentId, switchTo])

  return (
    <>
      <Space size={6}>
        <Dropdown
          menu={{ items: menuItems }}
          trigger={['click']}
          placement="bottomRight"
          getPopupContainer={() => document.body}
          destroyOnHidden={false}
        >
          <Button
            loading={loading}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              borderRadius: 8,
              border: '1px solid #e2e8f0',
              background: '#fff',
              padding: '4px 12px',
              height: 36,
            }}
          >
            <AppstoreOutlined style={{ color: '#6366f1' }} />
            <span style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }}>
              {current?.name || (loading ? '加载中...' : '暂无规则集')}
            </span>
            <DownOutlined style={{ fontSize: 10, color: '#94a3b8' }} />
          </Button>
        </Dropdown>

        {/* 独立的"新建规则集"按钮:品牌紫主色,无需展开下拉即可点击 */}
        <Tooltip title="新建规则集" placement="bottom">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setModalOpen(true)}
            style={{
              borderRadius: 8,
              height: 36,
              width: 36,
              padding: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)',
              border: 'none',
              boxShadow: '0 2px 8px rgba(99, 102, 241, 0.35)',
            }}
          />
        </Tooltip>
      </Space>

      <Modal
        title="新建规则集"
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false)
          form.resetFields()
        }}
        onOk={handleCreate}
        confirmLoading={submitting}
        okText="创建并切换"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label="规则集名称"
            rules={[{ required: true, message: '请输入规则集名称' }]}
          >
            <Input placeholder="如:出口代理默认规则" maxLength={64} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="该规则集适用的审查场景..." maxLength={500} />
          </Form.Item>
          <Form.Item name="doc_types" label="适用文件类型">
            <Select
              mode="multiple"
              placeholder="不选则适用全部文件类型"
              options={docTypes.map((d) => ({ label: d.name, value: d.name }))}
              optionFilterProp="label"
              showSearch
              allowClear
            />
          </Form.Item>
          <Form.Item name="is_default" label="设为默认规则集" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
