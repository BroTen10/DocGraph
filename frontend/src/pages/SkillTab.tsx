import { useState, useEffect, useMemo } from 'react'
import {
  Card, Table, Tag, Button, Modal, Form, Input, Space, message, Typography, Tooltip,
  Popconfirm, Switch, Alert, Row, Col, Divider,
} from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, CodeOutlined, WarningOutlined } from '@ant-design/icons'
import { skillsApi } from '../api/client'
import type { RuleParseSkill, RuleParseSkillCreate, RuleParseSkillUpdate, RuleParseSkillContent } from '../types'

const { Text, Title } = Typography
const { TextArea } = Input

interface Props {
  ruleSetId: string
}

export default function SkillTab({ ruleSetId }: Props) {
  const [skills, setSkills] = useState<RuleParseSkill[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<RuleParseSkill | null>(null)
  const [saving, setSaving] = useState(false)
  const [jsonEdit, setJsonEdit] = useState('')
  const [jsonError, setJsonError] = useState('')

  const load = async () => {
    if (!ruleSetId) return
    setLoading(true)
    try {
      const data = await skillsApi.list(ruleSetId)
      setSkills(data)
    } catch (e: any) {
      message.error('加载 Skill 失败: ' + (e?.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [ruleSetId])

  const openCreate = () => {
    setEditing(null)
    setJsonEdit(JSON.stringify({
      prompt_instructions: [],
      field_mappings: {},
      defaults: {
        tolerance: { amount_percent: 5.0, weight_kg: 0.5 },
        priority: { "齐套性": 10, "基础判断": 20, "信息准确性": 30, "时间逻辑": 40 },
      },
      validations: [],
      text_preprocessing: [],
      term_normalization: {},
      domain_context: { glossary: {}, common_patterns: [] },
    }, null, 2))
    setJsonError('')
    setModalOpen(true)
  }

  const openEdit = (skill: RuleParseSkill) => {
    setEditing(skill)
    setJsonEdit(JSON.stringify(skill.content, null, 2))
    setJsonError('')
    setModalOpen(true)
  }

  const handleSave = async () => {
    // 校验 JSON
    let content: RuleParseSkillContent
    try {
      content = JSON.parse(jsonEdit)
    } catch (e: any) {
      setJsonError('JSON 格式错误: ' + e.message)
      return
    }
    setJsonError('')

    setSaving(true)
    try {
      if (editing) {
        await skillsApi.update(ruleSetId, editing.id, { content } as RuleParseSkillUpdate)
        message.success('Skill 已更新')
      } else {
        await skillsApi.create(ruleSetId, {
          name: '自定义 Skill',
          description: '',
          content,
        } as RuleParseSkillCreate)
        message.success('Skill 已创建')
      }
      setModalOpen(false)
      await load()
    } catch (e: any) {
      message.error('保存失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (skill: RuleParseSkill) => {
    try {
      await skillsApi.delete(ruleSetId, skill.id)
      message.success('已删除')
      await load()
    } catch (e: any) {
      message.error('删除失败: ' + (e?.response?.data?.detail || e?.message || e))
    }
  }

  const handleToggleEnabled = async (skill: RuleParseSkill, enabled: boolean) => {
    try {
      await skillsApi.update(ruleSetId, skill.id, { enabled } as RuleParseSkillUpdate)
      await load()
    } catch (e: any) {
      message.error('更新失败: ' + (e?.message || e))
    }
  }

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 160 },
    {
      title: '来源', key: 'type', width: 80,
      render: (_: unknown, row: RuleParseSkill) => (
        row.is_builtin ? <Tag color="blue">内置</Tag> : <Tag>自定义</Tag>
      ),
    },
    {
      title: '启用', key: 'enabled', width: 60,
      render: (_: unknown, row: RuleParseSkill) => (
        <Switch size="small" checked={row.enabled} onChange={(v) => handleToggleEnabled(row, v)} />
      ),
    },
    {
      title: '版本', dataIndex: 'version', key: 'version', width: 60,
    },
    {
      title: '描述', dataIndex: 'description', key: 'description', ellipsis: true,
      render: (v: string | null) => v || <Text type="secondary">-</Text>,
    },
    {
      title: '能力概况', key: 'summary', width: 200,
      render: (_: unknown, row: RuleParseSkill) => {
        const c = row.content || {}
        const tags: string[] = []
        if ((c.prompt_instructions || []).length) tags.push(`指令×${c.prompt_instructions.length}`)
        if (Object.keys(c.field_mappings || {}).length) tags.push('映射')
        if (Object.keys(c.defaults || {}).length) tags.push('默认值')
        if ((c.validations || []).length) tags.push(`校验×${c.validations.length}`)
        if ((c.text_preprocessing || []).length) tags.push(`预处理×${c.text_preprocessing.length}`)
        if (Object.keys(c.term_normalization || {}).length) tags.push('术语归一')
        if (Object.keys(c.domain_context?.glossary || {}).length || (c.domain_context?.common_patterns || []).length) tags.push('领域')
        return tags.length ? <Space size={4} wrap>{tags.map((t) => <Tag key={t} style={{ fontSize: 11 }}>{t}</Tag>)}</Space> : <Text type="secondary">空</Text>
      },
    },
    {
      title: '操作', key: 'action', width: 120,
      render: (_: unknown, row: RuleParseSkill) => (
        <Space>
          <Tooltip title="编辑">
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
          </Tooltip>
          {!row.is_builtin && (
            <Popconfirm title="确定删除此 Skill？" onConfirm={() => handleDelete(row)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建 Skill</Button>
          <Button icon={<CodeOutlined />} onClick={load}>刷新</Button>
        </Space>
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 8 }}
          message={
            <span>
              Skill 控制大模型解析规则的过程。内置默认 Skill 始终生效；
              编辑内置默认会自动创建自定义副本。
              导入规则时系统自动应用已启用的 Skill。
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                编辑时直接修改 JSON 即可，保存后立即生效。
              </Text>
            </span>
          }
        />
      </div>

      <Table
        dataSource={skills}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={false}
        loading={loading}
      />

      <Modal
        title={editing ? `编辑 Skill${editing.is_builtin ? '（将创建自定义副本）' : ''}` : '新建 Skill'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={800}
        confirmLoading={saving}
      >
        {editing?.is_builtin && (
          <Alert
            type="warning"
            showIcon
            icon={<WarningOutlined />}
            style={{ marginBottom: 12 }}
            message="编辑内置默认 Skill 将自动创建该规则集下的自定义副本，原始内置不受影响。"
          />
        )}
        <Form layout="vertical">
          {!editing && (
            <>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="名称">
                    <Input defaultValue="自定义 Skill" onChange={(e) => { /* name handled on save */ }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="描述">
                    <Input placeholder="Skill 用途说明" />
                  </Form.Item>
                </Col>
              </Row>
              <Divider />
            </>
          )}
          <Form.Item
            label={
              <Space>
                <span>Skill 内容 (JSON)</span>
                {jsonError && <Text type="danger" style={{ fontSize: 12 }}>{jsonError}</Text>}
              </Space>
            }
          >
            <TextArea
              rows={20}
              value={jsonEdit}
              onChange={(e) => { setJsonEdit(e.target.value); setJsonError('') }}
              placeholder="输入 JSON 格式的 Skill 内容..."
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
