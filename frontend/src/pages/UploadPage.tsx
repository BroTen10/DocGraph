import { useState, useEffect, useRef } from 'react'
import {
  Card, Row, Col, Upload, Button, Table, Tag, message, Modal, Input,
  Select, Space, Typography, Empty, Popconfirm, Divider,
} from 'antd'
import { InboxOutlined, DeleteOutlined, ReloadOutlined, EditOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { contractsApi, constantsApi } from '../api/client'
import type { ContractBrief, ContractDetail, ContractUploadResponse, DocTypeMeta } from '../types'
import dayjs from 'dayjs'

const { Dragger } = Upload
const { Title, Text } = Typography

export default function UploadPage() {
  const [contracts, setContracts] = useState<ContractBrief[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [detail, setDetail] = useState<ContractDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [docTypes, setDocTypes] = useState<DocTypeMeta[]>([])
  const [aliasModal, setAliasModal] = useState<{ open: boolean; contract?: ContractBrief; contractNo: string; aliases: string }>({ open: false, contractNo: '', aliases: '' })
  const pendingFilesRef = useRef<File[]>([])
  const [pendingFileNames, setPendingFileNames] = useState<string[]>([])

  const load = async () => {
    setLoading(true)
    try {
      setContracts(await contractsApi.list())
    } catch (e: any) {
      message.error('加载合同列表失败: ' + (e?.message || e))
    } finally {
      setLoading(false)
    }
  }

  const loadConstants = async () => {
    try {
      const c = await constantsApi.docTypes()
      setDocTypes(c.doc_types)
    } catch (e) {
      // 静默
    }
  }

  useEffect(() => {
    load()
    loadConstants()
  }, [])

  const uploadProps: UploadProps = {
    name: 'files',
    multiple: true,
    showUploadList: false,
    accept: '.pdf,.png,.jpg,.jpeg,.docx',
    beforeUpload: (file, fileList) => {
      // antd 在多文件选择时会对每个文件调用一次 beforeUpload；
      // 只有当本文件是"本次新增批次"的最后一个时才合并，避免重复添加。
      // 判断方式：fileList 中本文件之后是否还有同批次文件（通过 uid 识别）。
      // 简单做法：每次都追加去重。
      const existing = pendingFilesRef.current
      if (!existing.some((f) => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified)) {
        existing.push(file)
      }
      setPendingFileNames(existing.map((f) => f.name))
      return false // 阻止自动上传
    },
    onRemove: (file) => {
      pendingFilesRef.current = pendingFilesRef.current.filter(
        (f) => !(f.name === file.name && f.size === file.size && f.lastModified === (file as any).lastModified),
      )
      setPendingFileNames(pendingFilesRef.current.map((f) => f.name))
    },
    fileList: [],
  }

  const clearPending = () => {
    pendingFilesRef.current = []
    setPendingFileNames([])
  }

  const doUpload = async () => {
    const files = pendingFilesRef.current
    if (files.length === 0) {
      message.warning('请先选择要上传的文件')
      return
    }
    setUploading(true)
    try {
      const resp: ContractUploadResponse = await contractsApi.upload(files)
      message.success(`上传成功：${resp.message}`)
      clearPending()
      await load()
      if (resp.contract_id) {
        viewDetail(resp.contract_id)
      }
    } catch (e: any) {
      message.error('上传失败: ' + (e?.response?.data?.detail || e?.message || e))
    } finally {
      setUploading(false)
    }
  }

  const viewDetail = async (id: string) => {
    setDetailLoading(true)
    try {
      setDetail(await contractsApi.get(id))
    } catch (e: any) {
      message.error('加载详情失败: ' + (e?.message || e))
    } finally {
      setDetailLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await contractsApi.delete(id)
      message.success('删除成功')
      if (detail?.id === id) setDetail(null)
      await load()
    } catch (e: any) {
      message.error('删除失败: ' + (e?.message || e))
    }
  }

  const handleDocTypeChange = async (docId: string, docType: string) => {
    try {
      await contractsApi.updateDocType(docId, docType)
      message.success('已修正文件类型')
      if (detail) await viewDetail(detail.id)
    } catch (e: any) {
      message.error('修正失败: ' + (e?.message || e))
    }
  }

  const saveAliases = async () => {
    if (!aliasModal.contract) return
    try {
      const aliases = aliasModal.aliases.split(/[,，\s]+/).filter(Boolean)
      await contractsApi.updateAliases(aliasModal.contract.id, aliasModal.contractNo, aliases)
      message.success('合同号归一化已更新')
      setAliasModal({ ...aliasModal, open: false })
      await load()
      if (detail?.id === aliasModal.contract.id) await viewDetail(aliasModal.contract.id)
    } catch (e: any) {
      message.error('更新失败: ' + (e?.message || e))
    }
  }

  const columns = [
    { title: '合同号', dataIndex: 'contract_no', key: 'contract_no', render: (v: string) => <Text strong>{v}</Text> },
    {
      title: '别名',
      dataIndex: 'alias_list',
      key: 'alias_list',
      render: (v: string[]) => v?.length ? v.map((a) => <Tag key={a}>{a}</Tag>) : <Text type="secondary">无</Text>,
    },
    { title: '文件数', dataIndex: 'file_count', key: 'file_count', align: 'center' as const },
    { title: '上传时间', dataIndex: 'upload_time', key: 'upload_time', render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm') },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v: string) => <Tag color={v === 'reviewed' ? 'green' : v === 'reviewing' ? 'blue' : 'default'}>{v}</Tag> },
    {
      title: '操作', key: 'action', width: 220,
      render: (_: unknown, row: ContractBrief) => (
        <Space>
          <Button size="small" onClick={() => viewDetail(row.id)}>查看</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => setAliasModal({ open: true, contract: row, contractNo: row.contract_no, aliases: (row.alias_list || []).join(', ') })}>归一化</Button>
          <Popconfirm title="确定删除该合同及其所有文件？" onConfirm={() => handleDelete(row.id)}>
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const docColumns = [
    { title: '文件名', dataIndex: 'file_name', key: 'file_name', ellipsis: true },
    {
      title: '业务类型', dataIndex: 'doc_type', key: 'doc_type', width: 180,
      render: (v: string, row: any) => (
        <Select
          size="small"
          value={v}
          style={{ width: '100%' }}
          onChange={(val) => handleDocTypeChange(row.id, val)}
          options={docTypes.map((d) => ({ value: d.name, label: `${d.name}${d.is_required ? '（必备）' : d.is_optional ? '（非必备）' : ''}` }))}
        />
      ),
    },
    {
      title: '必备', dataIndex: 'is_required', key: 'is_required', width: 80, align: 'center' as const,
      render: (v: boolean) => v ? <Tag color="red">必备</Tag> : <Tag>非必备</Tag>,
    },
    { title: '格式', dataIndex: 'file_type', key: 'file_type', width: 80, align: 'center' as const, render: (v: string) => <Tag>{v.toUpperCase()}</Tag> },
    {
      title: 'OCR', dataIndex: 'ocr_status', key: 'ocr_status', width: 100, align: 'center' as const,
      render: (v: string) => <Tag color={v === 'done' ? 'green' : v === 'failed' ? 'red' : v === 'skipped' ? 'default' : 'blue'}>{v}</Tag>,
    },
    {
      title: '印章', dataIndex: 'has_stamp', key: 'has_stamp', width: 80, align: 'center' as const,
      render: (v: boolean | null) => v === true ? <Tag color="green">有</Tag> : v === false ? <Tag color="red">无</Tag> : <Tag color="gold">未验</Tag>,
    },
  ]

  return (
    <div>
      <Title level={4}>文档上传与合同识别</Title>
      <Text type="secondary">选择本地合同文件夹上传（支持 PDF / PNG / JPG / DOCX）。系统自动识别文件类型与合同号归一化。</Text>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={10}>
          <Card title="上传合同文件夹">
            <Dragger {...uploadProps} disabled={uploading}>
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">{uploading ? '上传中...' : '点击或拖拽文件到此区域'}</p>
              <p className="ant-upload-hint">支持同时选择多个文件（一个合同的所有文件），选择后点击下方"开始上传"</p>
            </Dragger>
            {pendingFileNames.length > 0 && (
              <div style={{ marginTop: 12, padding: 12, background: '#f6f8fa', borderRadius: 6 }}>
                <Space style={{ marginBottom: 8 }}>
                  <Text strong>已选择 {pendingFileNames.length} 个文件</Text>
                  <Button size="small" onClick={clearPending}>清空</Button>
                </Space>
                <div style={{ maxHeight: 120, overflowY: 'auto', fontSize: 12, color: '#666' }}>
                  {pendingFileNames.map((n, i) => <div key={i}>· {n}</div>)}
                </div>
              </div>
            )}
            <Divider />
            <Space>
              <Button
                type="primary"
                onClick={doUpload}
                loading={uploading}
                disabled={pendingFileNames.length === 0}
              >
                开始上传
              </Button>
              <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新列表</Button>
            </Space>
          </Card>

          <Card title="合同列表" size="small" style={{ marginTop: 16 }}>
            <Table
              dataSource={contracts}
              columns={columns}
              rowKey="id"
              size="small"
              loading={loading}
              pagination={false}
              scroll={{ y: 320 }}
            />
          </Card>
        </Col>

        <Col span={14}>
          <Card title="合同详情" loading={detailLoading}>
            {!detail ? (
              <Empty description="选择左侧合同查看详情" />
            ) : (
              <>
                <Row gutter={16}>
                  <Col span={8}><Text type="secondary">合同号：</Text><Text strong>{detail.contract_no}</Text></Col>
                  <Col span={10}><Text type="secondary">别名：</Text>{(detail.alias_list || []).map((a) => <Tag key={a}>{a}</Tag>)}</Col>
                  <Col span={6}><Text type="secondary">状态：</Text><Tag>{detail.status}</Tag></Col>
                </Row>
                <Divider />
                <Table
                  dataSource={detail.documents}
                  columns={docColumns}
                  rowKey="id"
                  size="small"
                  pagination={false}
                  scroll={{ y: 480 }}
                />
              </>
            )}
          </Card>
        </Col>
      </Row>

      <Modal
        title="修正合同号归一化"
        open={aliasModal.open}
        onOk={saveAliases}
        onCancel={() => setAliasModal({ ...aliasModal, open: false })}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <div><Text type="secondary">主合同号：</Text></div>
          <Input value={aliasModal.contractNo} onChange={(e) => setAliasModal({ ...aliasModal, contractNo: e.target.value })} />
          <div><Text type="secondary">别名列表（逗号分隔）：</Text></div>
          <Input.TextArea rows={3} value={aliasModal.aliases} onChange={(e) => setAliasModal({ ...aliasModal, aliases: e.target.value })} />
        </Space>
      </Modal>
    </div>
  )
}
