import { useState, useEffect, useMemo } from 'react'
import type { UploadProps, UploadFile } from 'antd'
import {
  Card, Row, Col, Table, Tag, Button, Modal, Form, Input, InputNumber, Switch,
  Select, Space, message, Typography, Tooltip, Popconfirm, Tabs, List, Spin, Alert,
  Upload,
} from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, ThunderboltOutlined, HistoryOutlined, ImportOutlined, InboxOutlined, FileTextOutlined } from '@ant-design/icons'
import { rulesApi, graphApi, constantsApi } from '../api/client'
import type { Rule, RuleSnapshot, DocTypeMeta, ConstantsResponse, RuleImportResponse, RuleDocumentImportResponse } from '../types'
import dayjs from 'dayjs'

const { Title, Text } = Typography
const { Dragger } = Upload
const CHECK_CATEGORIES = ['齐套性', '基础判断', '信息准确性', '时间逻辑']
const FILE_ACCEPT = '.pdf,.xlsx,.xls,.docx,.md,.txt'

export default function RulesPage() {
  const [rules, setRules] = useState<Rule[]>([])
  const [snapshots, setSnapshots] = useState<RuleSnapshot[]>([])
  const [docTypes, setDocTypes] = useState<DocTypeMeta[]>([])
  const [loading, setLoading] = useState(false)
  const [building, setBuilding] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Rule | null>(null)
  const [form] = Form.useForm()
  const [importOpen, setImportOpen] = useState(false)
  const [importMode, setImportMode] = useState<'text' | 'file'>('text')
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<RuleImportResponse | null>(null)
  // 文件导入
  const [importFile, setImportFile] = useState<File | null>(null)
  const [fileImportResult, setFileImportResult] = useState<RuleDocumentImportResponse | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const [r, s, c] = await Promise.all([
        rulesApi.list(),
        rulesApi.listSnapshots(),
        constantsApi.docTypes(),
      ])
      setRules(r)
      setSnapshots(s)
      setDocTypes(c.doc_types)
    } catch (e: any) {
      message.error('加载失败: ' + (e?.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // 二维表格：行=文件类型，列=检查项
  const matrix = useMemo(() => {
    const m: Record<string, Record<string, Rule[]>> = {}
    rules.forEach((r) => {
      if (!m[r.doc_type]) m[r.doc_type] = {}
      if (!m[r.doc_type][r.check_category]) m[r.doc_type][r.check_category] = []
      m[r.doc_type][r.check_category].push(r)
    })
    return m
  }, [rules])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ enabled: true, priority: 100, tolerance: {} })
    setModalOpen(true)
  }

  const openEdit = (rule: Rule) => {
    setEditing(rule)
    form.setFieldsValue({
      ...rule,
      tolerance_amount: rule.tolerance?.amount_percent,
      tolerance_weight: rule.tolerance?.weight_kg,
      tolerance_time: rule.tolerance?.time_days,
      allow_same_day: rule.tolerance?.allow_same_day,
    })
    setModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      // 组装容差参数
      const tolerance: Record<string, unknown> = {}
      if (values.tolerance_amount != null) tolerance.amount_percent = values.tolerance_amount
      if (values.tolerance_weight != null) tolerance.weight_kg = values.tolerance_weight
      if (values.tolerance_time != null) tolerance.time_days = values.tolerance_time
      if (values.allow_same_day != null) tolerance.allow_same_day = values.allow_same_day

      const payload = {
        doc_type: values.doc_type,
        check_category: values.check_category,
        rule_text: values.rule_text,
        tolerance,
        enabled: values.enabled,
        priority: values.priority,
      }
      if (editing) {
        await rulesApi.update(editing.id, payload)
        message.success('规则已更新')
      } else {
        await rulesApi.create(payload as any)
        message.success('规则已新增')
      }
      setModalOpen(false)
      await load()
    } catch (e: any) {
      if (e?.errorFields) return // 表单校验错误
      message.error('保存失败: ' + (e?.message || e))
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await rulesApi.delete(id)
      message.success('已删除')
      await load()
    } catch (e: any) {
      message.error('删除失败: ' + (e?.message || e))
    }
  }

  const handleBuild = async (autoAll = false) => {
    setBuilding(true)
    try {
      const resp = await graphApi.build(autoAll)
      message.success(`${resp.message}（节点 ${resp.node_count} / 关系 ${resp.edge_count}）`)
      await load()
    } catch (e: any) {
      message.error('图谱构建失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setBuilding(false)
    }
  }

  const openImport = () => {
    setImportText('')
    setImportResult(null)
    setImportFile(null)
    setFileImportResult(null)
    setImportMode('text')
    setImportOpen(true)
  }

  const handleImport = async () => {
    if (!importText.trim()) {
      message.warning('请粘贴规则清单文本')
      return
    }
    setImporting(true)
    setImportResult(null)
    try {
      const resp = await rulesApi.importBatch(importText)
      setImportResult(resp)
      if (resp.imported > 0) {
        message.success(`导入完成：成功 ${resp.imported} 条，跳过 ${resp.skipped} 条`)
        await load()
      } else {
        message.warning(`未导入任何规则，跳过 ${resp.skipped} 条`)
      }
    } catch (e: any) {
      message.error('导入失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setImporting(false)
    }
  }

  /** 文件导入：支持 PDF/Excel/Word/MD/TXT，后端解析为文本后调用 LLM 转规则 */
  const handleImportFile = async () => {
    if (!importFile) {
      message.warning('请先选择要导入的规则文档')
      return
    }
    setImporting(true)
    setFileImportResult(null)
    try {
      const resp = await rulesApi.importDocument(importFile)
      setFileImportResult(resp)
      if (resp.imported > 0) {
        message.success(`导入完成：成功 ${resp.imported} 条，跳过 ${resp.skipped} 条`)
        await load()
      } else {
        message.warning(`未导入任何规则，跳过 ${resp.skipped} 条`)
      }
    } catch (e: any) {
      message.error('文件导入失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setImporting(false)
    }
  }

  // 文件上传组件 props：单文件，手动触发上传
  const fileUploadProps: UploadProps = {
    accept: FILE_ACCEPT,
    multiple: false,
    maxCount: 1,
    showUploadList: true,
    fileList: importFile
      ? [
          {
            uid: '-1',
            name: importFile.name,
            size: importFile.size,
            type: importFile.type,
            status: 'done',
            originFileObj: importFile as any,
          } as UploadFile,
        ]
      : [],
    beforeUpload: (file) => {
      setImportFile(file as File)
      setFileImportResult(null)
      return false // 阻止自动上传
    },
    onRemove: () => {
      setImportFile(null)
      setFileImportResult(null)
    },
  }

  // 二维表格渲染
  const renderMatrix = () => {
    const requiredTypes = docTypes.filter((d) => d.is_required)
    const optionalTypes = docTypes.filter((d) => d.is_optional)
    const otherTypes = docTypes.filter((d) => !d.is_required && !d.is_optional)
    const ordered = [...requiredTypes, ...optionalTypes, ...otherTypes]

    return (
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={thStyle}>文件类型 \ 检查项</th>
            {CHECK_CATEGORIES.map((c) => <th key={c} style={thStyle}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {ordered.map((dt) => (
            <tr key={dt.name}>
              <td style={tdStyle}>
                <Text strong>{dt.name}</Text>
                {dt.is_required && <Tag color="red" style={{ marginLeft: 4 }}>必备</Tag>}
                {dt.is_optional && <Tag color="blue" style={{ marginLeft: 4 }}>非必备</Tag>}
              </td>
              {CHECK_CATEGORIES.map((cc) => {
                const cellRules = matrix[dt.name]?.[cc] || []
                return (
                  <td key={cc} style={tdStyle} onClick={() => cellRules.length > 0 && openEdit(cellRules[0])}>
                    {cellRules.length === 0 ? (
                      <Text type="secondary">-</Text>
                    ) : (
                      <Tooltip title={cellRules.map((r) => r.rule_text).join('\n')}>
                        <Tag color={cellRules[0].enabled ? 'blue' : 'default'} style={{ cursor: 'pointer' }}>
                          {cellRules.length} 条
                        </Tag>
                      </Tooltip>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  const thStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: 8, background: '#fafafa', textAlign: 'center' }
  const tdStyle: React.CSSProperties = { border: '1px solid #f0f0f0', padding: 8, textAlign: 'center', cursor: 'pointer' }

  const ruleColumns = [
    { title: '文件类型', dataIndex: 'doc_type', key: 'doc_type', width: 140 },
    { title: '检查项', dataIndex: 'check_category', key: 'check_category', width: 120 },
    { title: '规则文本', dataIndex: 'rule_text', key: 'rule_text', ellipsis: true },
    {
      title: '容差', key: 'tolerance', width: 200,
      render: (_: unknown, row: Rule) => {
        const t = row.tolerance || {}
        const parts: string[] = []
        if (t.amount_percent != null) parts.push(`金额±${t.amount_percent}%`)
        if (t.weight_kg != null) parts.push(`重量±${t.weight_kg}kg`)
        if (t.allow_same_day != null) parts.push(t.allow_same_day ? '允许同日' : '不允许同日')
        if (t.time_days != null) parts.push(`时间±${t.time_days}天`)
        return parts.length ? parts.join(' / ') : <Text type="secondary">无</Text>
      },
    },
    {
      title: '启用', dataIndex: 'enabled', key: 'enabled', width: 80,
      render: (v: boolean, row: Rule) => (
        <Switch
          size="small"
          checked={v}
          onChange={async (checked) => {
            try {
              await rulesApi.update(row.id, { enabled: checked })
              await load()
            } catch (e: any) {
              message.error('更新失败: ' + (e?.message || e))
            }
          }}
        />
      ),
    },
    {
      title: '操作', key: 'action', width: 120,
      render: (_: unknown, row: Rule) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
          <Popconfirm title="确定删除该规则？" onConfirm={() => handleDelete(row.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Row justify="space-between" align="middle">
        <Col><Title level={4}>规则管理</Title></Col>
        <Col>
          <Space>
            <Button icon={<PlusOutlined />} onClick={openCreate}>新增规则</Button>
            <Button icon={<ImportOutlined />} onClick={openImport}>批量导入</Button>
            <Button type="primary" icon={<ThunderboltOutlined />} loading={building} onClick={() => handleBuild(false)}>生成图谱</Button>
            <Popconfirm title="一键自动确认全部规则（忽略置信度）？" onConfirm={() => handleBuild(true)}>
              <Button icon={<ThunderboltOutlined />} loading={building}>一键自动确认</Button>
            </Popconfirm>
            <Button icon={<HistoryOutlined />} onClick={load}>刷新</Button>
          </Space>
        </Col>
      </Row>

      <Tabs
        defaultActiveKey="matrix"
        items={[
          {
            key: 'matrix',
            label: '二维表格视图',
            children: (
              <Card loading={loading}>
                {renderMatrix()}
              </Card>
            ),
          },
          {
            key: 'list',
            label: '规则列表',
            children: (
              <Card loading={loading}>
                <Table dataSource={rules} columns={ruleColumns} rowKey="id" size="small" pagination={{ pageSize: 20 }} />
              </Card>
            ),
          },
          {
            key: 'snapshots',
            label: '规则快照历史',
            children: (
              <Card loading={loading}>
                <List
                  dataSource={snapshots}
                  renderItem={(s) => (
                    <List.Item>
                      <List.Item.Meta
                        title={`${dayjs(s.snapshot_time).format('YYYY-MM-DD HH:mm:ss')} - ${s.rule_count} 条规则`}
                        description={
                          <Space>
                            {s.graph_id && <Tag color="blue">graph: {s.graph_id.slice(0, 24)}...</Tag>}
                            <Tag>节点 {s.node_count ?? '-'}</Tag>
                            <Tag>关系 {s.edge_count ?? '-'}</Tag>
                            {s.operator && <Tag>操作人: {s.operator}</Tag>}
                            {s.note && <Text type="secondary">{s.note}</Text>}
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              </Card>
            ),
          },
        ]}
      />

      <Modal
        title={editing ? '编辑规则' : '新增规则'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={640}
        confirmLoading={loading}
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="doc_type" label="文件类型" rules={[{ required: true }]}>
                <Select options={docTypes.map((d) => ({ value: d.name, label: d.name }))} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="check_category" label="检查项" rules={[{ required: true }]}>
                <Select options={CHECK_CATEGORIES.map((c) => ({ value: c, label: c }))} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="rule_text" label="规则文本（自然语言）" rules={[{ required: true }]}>
            <Input.TextArea rows={3} placeholder="如：报关单数量应不大于委托单数量..." />
          </Form.Item>
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item name="tolerance_amount" label="金额容差(%)">
                <InputNumber min={0} max={100} style={{ width: '100%' }} placeholder="如 5" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="tolerance_weight" label="重量容差(kg)">
                <InputNumber min={0} style={{ width: '100%' }} placeholder="如 0.5" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="tolerance_time" label="时间容差(天)">
                <InputNumber min={0} style={{ width: '100%' }} placeholder="如 0" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="allow_same_day" label="允许同日" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="priority" label="优先级（数字越小越先）">
                <InputNumber min={1} max={999} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="enabled" label="启用" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title="批量导入规则"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        width={760}
        footer={null}
        destroyOnClose
      >
        <Tabs
          activeKey={importMode}
          onChange={(k) => setImportMode(k as 'text' | 'file')}
          items={[
            {
              key: 'text',
              label: <span><ImportOutlined /> 文本导入</span>,
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="粘贴自然语言规则清单，系统会调用大模型解析为结构化规则并自动入库"
                    description={
                      <span style={{ fontSize: 12 }}>
                        可用文件类型：{docTypes.map((d) => d.name).join('、')}<br />
                        可用检查项：{CHECK_CATEGORIES.join('、')}
                      </span>
                    }
                  />
                  <Input.TextArea
                    rows={10}
                    value={importText}
                    onChange={(e) => setImportText(e.target.value)}
                    placeholder={`请粘贴规则清单，例如：\n1. 代理协议的协议方应与委托出口确认单的委托方一致\n2. 出口报关单数量应不大于委托出口确认单数量（金额容差5%）\n3. 代理协议必须双方回签用印\n4. 委托出口确认单签订日期应在代理协议有效期内`}
                    disabled={importing}
                  />
                  <div style={{ marginTop: 12, textAlign: 'right' }}>
                    <Space>
                      <Button onClick={() => setImportOpen(false)}>关闭</Button>
                      <Button type="primary" loading={importing} onClick={handleImport}>
                        开始解析并导入
                      </Button>
                    </Space>
                  </div>
                  {importResult && (
                    <div style={{ marginTop: 12 }}>
                      <Space>
                        <Tag color="blue">解析 {importResult.total} 条</Tag>
                        <Tag color="green">成功 {importResult.imported} 条</Tag>
                        {importResult.skipped > 0 && <Tag color="orange">跳过 {importResult.skipped} 条</Tag>}
                      </Space>
                      {importResult.errors.length > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message="跳过的规则及原因"
                          description={
                            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
                              {importResult.errors.map((err, i) => <li key={i}>{err}</li>)}
                            </ul>
                          }
                        />
                      )}
                    </div>
                  )}
                </>
              ),
            },
            {
              key: 'file',
              label: <span><FileTextOutlined /> 文件导入</span>,
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="上传规则描述文档，后端会先解析为文本，再调用大模型解析为结构化规则"
                    description={
                      <span style={{ fontSize: 12 }}>
                        支持格式：PDF、Excel(.xlsx/.xls)、Word(.docx)、Markdown(.md)、文本(.txt)<br />
                        可用文件类型：{docTypes.map((d) => d.name).join('、')} · 可用检查项：{CHECK_CATEGORIES.join('、')}
                      </span>
                    }
                  />
                  <Dragger {...fileUploadProps} disabled={importing}>
                    <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                    <p className="ant-upload-text">{importing ? '解析中...' : '点击或拖拽文件到此区域'}</p>
                    <p className="ant-upload-hint">单文件上传，解析后可查看提取的文本预览</p>
                  </Dragger>
                  <div style={{ marginTop: 12, textAlign: 'right' }}>
                    <Space>
                      <Button onClick={() => setImportOpen(false)}>关闭</Button>
                      <Button
                        type="primary"
                        loading={importing}
                        onClick={handleImportFile}
                        disabled={!importFile}
                      >
                        开始解析并导入
                      </Button>
                    </Space>
                  </div>
                  {fileImportResult && (
                    <div style={{ marginTop: 12 }}>
                      <Space wrap>
                        <Tag color="blue">解析 {fileImportResult.total} 条</Tag>
                        <Tag color="green">成功 {fileImportResult.imported} 条</Tag>
                        {fileImportResult.skipped > 0 && <Tag color="orange">跳过 {fileImportResult.skipped} 条</Tag>}
                        <Tag color="purple">提取文本长度 {fileImportResult.extracted_text_length} 字符</Tag>
                        <Tag>来源: {fileImportResult.source_filename}</Tag>
                      </Space>
                      {fileImportResult.extracted_text_preview && (
                        <Card
                          size="small"
                          type="inner"
                          title={<Text type="secondary" style={{ fontSize: 12 }}>提取文本预览（前 500 字符）</Text>}
                          style={{ marginTop: 8 }}
                        >
                          <pre style={{
                            maxHeight: 160,
                            overflow: 'auto',
                            margin: 0,
                            padding: 8,
                            background: '#f6f8fa',
                            borderRadius: 4,
                            fontSize: 12,
                            fontFamily: 'monospace',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                          }}>
                            {fileImportResult.extracted_text_preview}
                          </pre>
                        </Card>
                      )}
                      {fileImportResult.errors.length > 0 && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message="跳过的规则及原因"
                          description={
                            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
                              {fileImportResult.errors.map((err, i) => <li key={i}>{err}</li>)}
                            </ul>
                          }
                        />
                      )}
                    </div>
                  )}
                </>
              ),
            },
          ]}
        />
      </Modal>
    </div>
  )
}
