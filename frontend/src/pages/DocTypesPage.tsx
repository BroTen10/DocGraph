import React, { useEffect, useState, useCallback } from 'react'
import {
  Card, Table, Button, Modal, Form, Input, Select, Tag, Space, Upload,
  message, Typography, Descriptions, Divider, Tabs, Badge, Tooltip, Radio, Alert,
} from 'antd'
import {
  PlusOutlined, UploadOutlined, DeleteOutlined, CheckCircleOutlined,
  CloseCircleOutlined, FileTextOutlined, ReloadOutlined, AimOutlined,
  InboxOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { docTypesApi, getErrorMessage } from '../api/client'
import type { DocTypeItem, DocTypeCreate, DocTypeUpdate, AnalyzeSampleResult } from '../types'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input
const { Dragger } = Upload

const SOURCE_LABEL: Record<string, string> = {
  seed: '内置',
  rule_import: '规则发现',
  manual: '手动创建',
}

export default function DocTypesPage() {
  const [activeTab, setActiveTab] = useState('active')
  const [docTypes, setDocTypes] = useState<DocTypeItem[]>([])
  const [pendingCount, setPendingCount] = useState(0)
  const [loading, setLoading] = useState(false)

  // Modal states
  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editingType, setEditingType] = useState<DocTypeItem | null>(null)
  const [analyzeOpen, setAnalyzeOpen] = useState(false)
  const [analyzeTargetName, setAnalyzeTargetName] = useState<string | null>(null)
  const [analyzeTargetId, setAnalyzeTargetId] = useState<string | null>(null)
  const [analyzeTargetStatus, setAnalyzeTargetStatus] = useState<string | null>(null)

  // Analyze modal states
  const [analyzeFile, setAnalyzeFile] = useState<File | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeResult, setAnalyzeResult] = useState<AnalyzeSampleResult | null>(null)

  const [form] = Form.useForm()
  const [editForm] = Form.useForm()

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await docTypesApi.list({ status: activeTab === 'pending' ? 'pending_review' : 'active' })
      setDocTypes(res.doc_types)
      setPendingCount(res.pending_count)
    } catch (e) {
      message.error('加载文档类型失败: ' + getErrorMessage(e, ''))
    }
    setLoading(false)
  }, [activeTab])

  useEffect(() => {
    const timer = setInterval(() => {
      docTypesApi.list({ status: 'pending_review' }).then(r => {
        setPendingCount(r.pending_count)
      }).catch(() => {})
    }, 10000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  // ============ CRUD handlers ============
  const handleCreate = async (values: DocTypeCreate) => {
    try {
      await docTypesApi.create(values)
      message.success('文档类型已创建')
      setCreateOpen(false)
      form.resetFields()
      loadData()
    } catch (e) {
      message.error('创建失败: ' + getErrorMessage(e, ''))
    }
  }

  const handleEdit = async (values: DocTypeUpdate) => {
    if (!editingType) return
    try {
      await docTypesApi.update(editingType.id, values)
      message.success('已更新')
      setEditOpen(false)
      setEditingType(null)
      editForm.resetFields()
      loadData()
    } catch (e) {
      message.error('更新失败: ' + getErrorMessage(e, ''))
    }
  }

  const handleDelete = (item: DocTypeItem) => {
    Modal.confirm({
      title: `确定删除「${item.name}」？`,
      content: '此操作不可恢复。如果该类型已被规则引用，删除可能导致数据不一致。',
      onOk: async () => {
        try {
          await docTypesApi.delete(item.id)
          message.success('已删除')
          loadData()
    } catch (e) {
      message.error('删除失败: ' + getErrorMessage(e, ''))
        }
      },
    })
  }

  const handleConfirm = async (item: DocTypeItem) => {
    try {
      await docTypesApi.confirm(item.id)
      message.success(`「${item.name}」已确认并激活`)
      loadData()
    } catch (e) {
      message.error('确认失败: ' + getErrorMessage(e, ''))
    }
  }

  const handleReject = (item: DocTypeItem) => {
    Modal.confirm({
      title: `丢弃「${item.name}」？`,
      content: '该文档类型将被删除。如果后续规则再提到它，会重新检测到。',
      onOk: async () => {
        try {
          await docTypesApi.reject(item.id)
          message.success('已丢弃')
          loadData()
    } catch (e) {
      message.error('操作失败: ' + getErrorMessage(e, ''))
        }
      },
    })
  }

  // ============ Analyze sample ============
  const openAnalyze = (name?: string, id?: string, status?: string) => {
    setAnalyzeTargetName(name || null)
    setAnalyzeTargetId(id || null)
    setAnalyzeTargetStatus(status || null)
    setAnalyzeFile(null)
    setAnalyzeResult(null)
    setAnalyzeOpen(true)
  }

  const handleAnalyzeFile = async () => {
    if (!analyzeFile) {
      message.warning('请先上传一个样例文档')
      return
    }
    setAnalyzing(true)
    try {
      const result = await docTypesApi.analyzeSample(analyzeFile, analyzeTargetName || undefined)
      setAnalyzeResult(result)
    } catch (e) {
      message.error('分析失败: ' + getErrorMessage(e, ''))
      setAnalyzeResult({
        detected_name: analyzeTargetName || '未知类型',
        description: '',
        key_fields: [],
        stamp_required: null,
        business_meaning: '',
      })
    }
    setAnalyzing(false)
  }

  const handleSaveAnalyzeResult = async () => {
    if (!analyzeResult) return
    if (!analyzeResult.detected_name.trim()) {
      message.warning('类型名称不能为空')
      return
    }
    try {
      if (analyzeTargetId) {
        // 更新已有待确认类型的字段（含 AI 识别出的类型名）
        await docTypesApi.update(analyzeTargetId, {
          name: analyzeResult.detected_name,
          key_fields: analyzeResult.key_fields,
          business_meaning: analyzeResult.business_meaning,
          description: analyzeResult.description,
          stamp_required: analyzeResult.stamp_required,
        })
        // 只有 pending_review 才需要 confirm；已激活类型直接跳过
        if (analyzeTargetStatus === 'pending_review') {
          await docTypesApi.confirm(analyzeTargetId)
          message.success('分析结果已保存并激活')
        } else {
          message.success('分析结果已保存')
        }
      } else {
        // 创建新类型（直接 active，无需 confirm）
        await docTypesApi.create({
          name: analyzeResult.detected_name,
          description: analyzeResult.description,
          key_fields: analyzeResult.key_fields,
          stamp_required: analyzeResult.stamp_required,
          business_meaning: analyzeResult.business_meaning,
          source: analyzeTargetName ? 'rule_import' : 'manual',
        })
        message.success('文档类型已创建')
      }
      setAnalyzeOpen(false)
      setAnalyzeResult(null)
      setAnalyzeFile(null)
      setAnalyzeTargetId(null)
      setAnalyzeTargetName(null)
      setAnalyzeTargetStatus(null)
      loadData()
    } catch (e) {
      message.error('保存失败: ' + getErrorMessage(e, ''))
    }
  }

  // ============ Table columns ============
  const activeColumns: ColumnsType<DocTypeItem> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 160,
      render: (name: string, record) => (
        <Space>
          <FileTextOutlined />
          <strong>{name}</strong>
          <Tag color={SOURCE_LABEL[record.source] ? 'blue' : 'default'}>{SOURCE_LABEL[record.source] || record.source}</Tag>
        </Space>
      ),
    },
    {
      title: '关键字段',
      dataIndex: 'key_fields',
      key: 'key_fields',
      render: (fields: string[]) => (
        fields?.length > 0
          ? <Space size={4} wrap>{fields.map(f => <Tag key={f}>{f}</Tag>)}</Space>
          : <Text type="secondary">-</Text>
      ),
    },
    {
      title: '业务含义',
      dataIndex: 'business_meaning',
      key: 'business_meaning',
      ellipsis: true,
      width: 260,
      render: (v: string | null) => v || <Text type="secondary">-</Text>,
    },
    {
      title: '样例',
      dataIndex: 'has_sample',
      key: 'has_sample',
      width: 60,
      render: (v: boolean) => v ? <Tag color="green">有</Tag> : <Tag>-</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, record: DocTypeItem) => (
        <Space size="small">
          <Button size="small" onClick={() => { setEditingType(record); editForm.setFieldsValue(record); setEditOpen(true) }}>
            编辑
          </Button>
          <Button size="small" icon={<AimOutlined />} onClick={() => openAnalyze(record.name, record.id, record.status)}>
            分析样例
          </Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(record)} />
        </Space>
      ),
    },
  ]

  const pendingColumns: ColumnsType<DocTypeItem> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 160,
      render: (name: string) => <strong>{name}</strong>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      render: (src: string) => <Tag>{SOURCE_LABEL[src] || src}</Tag>,
    },
    {
      title: '检测时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string | null) => v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: DocTypeItem) => (
        <Space size="small">
          <Tooltip title="仅确认，不分析样例">
            <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={() => handleConfirm(record)}>
              直接激活
            </Button>
          </Tooltip>
          <Button size="small" icon={<AimOutlined />} onClick={() => openAnalyze(record.name, record.id, record.status)}>
            上传样例分析
          </Button>
          <Button size="small" danger icon={<CloseCircleOutlined />} onClick={() => handleReject(record)}>
            忽略
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>文档类型管理</Title>
        <Space>
          <Button icon={<PlusOutlined />} type="primary" onClick={() => { form.resetFields(); setCreateOpen(true) }}>
            新建文档类型
          </Button>
          <Button icon={<AimOutlined />} onClick={() => openAnalyze()}>
            上传样例分析
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>
        </Space>
      </div>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'active',
              label: (
                <span>
                  已激活
                  <Badge count={docTypes.length} style={{ marginLeft: 8 }} />
                </span>
              ),
              children: (
                <Table
                  dataSource={docTypes}
                  columns={activeColumns}
                  rowKey="id"
                  loading={loading}
                  pagination={false}
                  size="small"
                />
              ),
            },
            {
              key: 'pending',
              label: (
                <span>
                  待确认
                  <Badge count={pendingCount} style={{ marginLeft: 8 }} overflowCount={99} />
                </span>
              ),
              children: pendingCount === 0 && !loading ? (
                <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                  <InboxOutlined style={{ fontSize: 48, color: '#ddd' }} />
                  <p style={{ marginTop: 12 }}>暂无待确认的文档类型</p>
                  <Paragraph type="secondary">
                    导入新规则时，如规则中提到了系统尚未收录的文档类型，会自动出现在这里。
                  </Paragraph>
                </div>
              ) : (
                <Table
                  dataSource={docTypes}
                  columns={pendingColumns}
                  rowKey="id"
                  loading={loading}
                  pagination={false}
                  size="small"
                />
              ),
            },
          ]}
        />
      </Card>

      {/* Create Modal */}
      <Modal
        title="新建文档类型"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="类型名称" rules={[{ required: true, message: '请输入文档类型名称' }]}>
            <Input placeholder="如：信用证、商业发票" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} placeholder="简要说明该文档类型" />
          </Form.Item>
          <Form.Item name="key_fields" label="关键字段">
            <Select mode="tags" placeholder="输入字段名后回车添加" tokenSeparators={[',']} />
          </Form.Item>
          <Form.Item name="stamp_required" label="用印要求">
            <Input placeholder="如：双方回签用印" />
          </Form.Item>
          <Form.Item name="business_meaning" label="业务含义">
            <TextArea rows={3} placeholder="该文档在贸易流程中扮演的角色" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit Modal */}
      <Modal
        title={`编辑 - ${editingType?.name || ''}`}
        open={editOpen}
        onCancel={() => { setEditOpen(false); setEditingType(null) }}
        onOk={() => editForm.submit()}
        width={600}
      >
        <Form form={editForm} layout="vertical" onFinish={handleEdit}>
          <Form.Item name="name" label="类型名称">
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item name="key_fields" label="关键字段">
            <Select mode="tags" tokenSeparators={[',']} />
          </Form.Item>
          <Form.Item name="stamp_required" label="用印要求">
            <Input />
          </Form.Item>
          <Form.Item name="business_meaning" label="业务含义">
            <TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Analyze Sample Modal */}
      <Modal
        title={analyzeTargetName ? `分析「${analyzeTargetName}」样例文档` : '上传样例文档分析'}
        open={analyzeOpen}
        onCancel={() => {
          setAnalyzeOpen(false)
          setAnalyzeResult(null)
          setAnalyzeFile(null)
          setAnalyzeTargetId(null)
          setAnalyzeTargetName(null)
          setAnalyzeTargetStatus(null)
        }}
        footer={null}
        width={640}
      >
        <Dragger
          accept=".pdf,.png,.jpg,.jpeg,.docx,.txt,.md"
          showUploadList={false}
          beforeUpload={(file) => {
            setAnalyzeFile(file)
            setAnalyzeResult(null)
            return false
          }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽一个文档到此区域</p>
          <p className="ant-upload-hint">
            支持 PDF / DOCX / PNG / JPG / TXT / MD 格式
            {analyzeTargetName && `，建议上传「${analyzeTargetName}」类型的实际文档样例`}
          </p>
        </Dragger>

        {analyzeFile && !analyzeResult && (
          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Text>已选择: {analyzeFile.name}</Text>
            <br />
            <Button
              type="primary"
              loading={analyzing}
              onClick={handleAnalyzeFile}
              style={{ marginTop: 12 }}
              icon={<AimOutlined />}
            >
              {analyzing ? 'AI 分析中...' : '开始 AI 分析'}
            </Button>
          </div>
        )}

        {analyzeResult && (
          <div style={{ marginTop: 16 }}>
            <Divider>分析结果</Divider>
            {analyzeTargetName && analyzeTargetName !== analyzeResult.detected_name && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message={
                  <>
                    AI 识别的类型名「<b>{analyzeResult.detected_name}</b>」与待确认名称「<b>{analyzeTargetName}</b>」不一致。
                    请确认最终名称：保持原名则改成「{analyzeTargetName}」，否则用识别结果「{analyzeResult.detected_name}」。
                  </>
                }
              />
            )}
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="类型名称">
                <Input
                  value={analyzeResult.detected_name}
                  onChange={(e) => setAnalyzeResult({ ...analyzeResult, detected_name: e.target.value })}
                  placeholder="AI 识别结果，可修改"
                  style={{ maxWidth: 360 }}
                />
              </Descriptions.Item>
              <Descriptions.Item label="描述">
                <TextArea
                  rows={2}
                  value={analyzeResult.description}
                  onChange={(e) => setAnalyzeResult({ ...analyzeResult, description: e.target.value })}
                />
              </Descriptions.Item>
              <Descriptions.Item label="关键字段">
                <Select
                  mode="tags"
                  value={analyzeResult.key_fields}
                  onChange={(v) => setAnalyzeResult({ ...analyzeResult, key_fields: v })}
                  tokenSeparators={[',']}
                  placeholder="输入字段名后回车"
                />
              </Descriptions.Item>
              <Descriptions.Item label="用印要求">
                <Input
                  value={analyzeResult.stamp_required || ''}
                  onChange={(e) => setAnalyzeResult({ ...analyzeResult, stamp_required: e.target.value || null })}
                />
              </Descriptions.Item>
              <Descriptions.Item label="业务含义">
                <TextArea
                  rows={3}
                  value={analyzeResult.business_meaning}
                  onChange={(e) => setAnalyzeResult({ ...analyzeResult, business_meaning: e.target.value })}
                />
              </Descriptions.Item>
            </Descriptions>

            <div style={{ marginTop: 16, textAlign: 'right' }}>
              <Space>
                <Button onClick={() => { setAnalyzeResult(null); setAnalyzeFile(null) }}>重新上传</Button>
                <Button type="primary" onClick={handleSaveAnalyzeResult}>
                  {analyzeTargetId
                    ? analyzeTargetStatus === 'pending_review'
                      ? '保存分析结果并激活'
                      : '保存分析结果'
                    : '创建为文档类型'}
                </Button>
              </Space>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
