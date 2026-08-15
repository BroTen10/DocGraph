import { useState, useEffect, useRef } from 'react'
import {
  Card, Row, Col, Upload, Button, Table, Tag, message, Modal, Input,
  Select, Space, Typography, Popconfirm, Drawer, Spin, Progress, Tooltip,
} from 'antd'
import { InboxOutlined, DeleteOutlined, ReloadOutlined, EditOutlined, FileSearchOutlined, UploadOutlined, ThunderboltOutlined, ReloadOutlined as ReRunIcon } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { contractsApi, constantsApi, getErrorMessage, ocrApi } from '../api/client'
import type { ContractBrief, ContractDetail, ContractUploadResponse, DocTypeMeta, DocumentBrief, OcrTask } from '../types'
import DocumentCompare from '../components/DocumentCompare'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { useRuleSet } from '../context/RuleSetContext'
import dayjs from 'dayjs'

const { Dragger } = Upload
const { Text } = Typography

export default function UploadPage() {
  const { currentId } = useRuleSet()
  const [contracts, setContracts] = useState<ContractBrief[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [detail, setDetail] = useState<ContractDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [docTypes, setDocTypes] = useState<DocTypeMeta[]>([])
  const [aliasModal, setAliasModal] = useState<{ open: boolean; contract?: ContractBrief; contractNo: string; aliases: string }>({ open: false, contractNo: '', aliases: '' })
  const pendingFilesRef = useRef<File[]>([])
  const [pendingFileNames, setPendingFileNames] = useState<string[]>([])
  // OCR 对照查看
  const [compareOpen, setCompareOpen] = useState(false)
  const [compareDoc, setCompareDoc] = useState<DocumentBrief | null>(null)
  const [compareLoading, setCompareLoading] = useState(false)
  // OCR 任务状态:当前正在运行的 OCR 任务(选中合同时显示进度)
  const [ocrTask, setOcrTask] = useState<OcrTask | null>(null)
  const [ocrTriggering, setOcrTriggering] = useState(false) // 批量触发按钮 loading
  const [docOcrLoading, setDocOcrLoading] = useState<Record<string, boolean>>({}) // 单文档触发 loading(按文档粒度,支持并行触发)
  const ocrPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // 当前展示的合同 id,供 OCR 轮询回调对比:仅当用户仍停留在启动轮询的那个合同时才刷新详情,
  // 避免切到新合同后被旧合同轮询的 setDetail 覆盖(张冠李戴)
  const currentDetailIdRef = useRef<string | null>(null)

  const load = async () => {
    if (!currentId) return
    setLoading(true)
    try {
      setContracts(await contractsApi.list(currentId))
    } catch (e) {
      message.error('加载合同列表失败: ' + getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  const loadConstants = async () => {
    try {
      const c = await constantsApi.docTypes()
      setDocTypes(c.doc_types)
    } catch (e) {
      console.warn('加载文件类型清单失败:', e)
    }
  }

  useEffect(() => {
    load()
    loadConstants()
    // 切换规则集时重新加载(App.tsx 已通过 key 强制重挂载,这里加依赖更稳妥)
  }, [currentId])

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
        (f) => !(f.name === file.name && f.size === file.size && f.lastModified === file.lastModified),
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
      const resp: ContractUploadResponse = await contractsApi.upload(currentId!, files)
      message.success(`上传成功：${resp.message}`)
      clearPending()
      await load()
      if (resp.contract_id) {
        viewDetail(resp.contract_id)
      }
    } catch (e) {
      message.error('上传失败: ' + getErrorMessage(e))
    } finally {
      setUploading(false)
    }
  }

  const viewDetail = async (id: string) => {
    currentDetailIdRef.current = id
    setDetailLoading(true)
    setOcrTask(null) // 切换合同时清空旧任务
    try {
      const d = await contractsApi.get(id)
      setDetail(d)
      // 切到新合同后,查询该合同最近一次 OCR 任务状态(可能正在运行)
      if (currentId) {
        try {
          const tasks = await ocrApi.listTasks(currentId, id)
          const last = tasks[0]
          if (last && (last.status === 'running' || last.status === 'pending')) {
            pollOcrTask(last.id, id)
          } else if (last) {
            // 显示历史任务摘要(便于看上次结果)
            const full = await ocrApi.getTask(last.id)
            setOcrTask(full)
          }
        } catch (e) {
          console.warn('查询 OCR 任务状态失败:', e)
        }
      }
    } catch (e) {
      message.error('加载详情失败: ' + getErrorMessage(e))
    } finally {
      setDetailLoading(false)
    }
  }

  // ============ OCR 触发与轮询 ============
  const stopOcrPoll = () => {
    if (ocrPollRef.current) {
      clearInterval(ocrPollRef.current)
      ocrPollRef.current = null
    }
  }

  const pollOcrTask = (taskId: string, contractId: string) => {
    stopOcrPoll()
    ocrPollRef.current = setInterval(async () => {
      try {
        const t = await ocrApi.getTask(taskId)
        setOcrTask(t)
        if (t.status === 'completed' || t.status === 'failed') {
          stopOcrPoll()
          // 完成后刷新详情(更新文档列表的 ocr_status):
          // 仅当用户仍停留在启动轮询的那个合同时才刷新,避免切走后被覆盖
          if (currentDetailIdRef.current === contractId) {
            try {
              setDetail(await contractsApi.get(contractId))
            } catch (e) {
              console.warn('OCR 完成后刷新详情失败:', e)
            }
          }
          if (t.status === 'completed') {
            message.success(`OCR 完成:成功 ${t.success_count}/${t.total_count},失败 ${t.failed_count}`)
          } else {
            message.error('OCR 任务失败: ' + (t.error || '未知错误'))
          }
        }
      } catch (e) {
        // 轮询失败,停止
        stopOcrPoll()
      }
    }, 2000)
  }

  const triggerContractOcr = async () => {
    if (!currentId || !detail) return
    setOcrTriggering(true)
    try {
      const t = await ocrApi.triggerContract(currentId, detail.id)
      setOcrTask(t)
      message.info(`已触发 OCR,共 ${t.total_count} 个待识别文档`)
      pollOcrTask(t.id, detail.id)
    } catch (e) {
      message.error('触发 OCR 失败: ' + getErrorMessage(e))
    } finally {
      setOcrTriggering(false)
    }
  }

  const triggerDocOcr = async (docId: string, fileName: string) => {
    if (!currentId) return
    setDocOcrLoading((m) => ({ ...m, [docId]: true }))
    try {
      const t = await ocrApi.triggerDoc(currentId, docId)
      setOcrTask(t)
      message.info(`已触发「${fileName}」OCR 识别`)
      pollOcrTask(t.id, currentDetailIdRef.current || '')
    } catch (e) {
      message.error('触发 OCR 失败: ' + getErrorMessage(e))
    } finally {
      setDocOcrLoading((m) => ({ ...m, [docId]: false }))
    }
  }

  // 组件卸载时停止轮询
  useEffect(() => {
    return () => stopOcrPoll()
  }, [])

  const handleDelete = async (id: string) => {
    try {
      await contractsApi.delete(id)
      message.success('删除成功')
      if (detail?.id === id) setDetail(null)
      await load()
    } catch (e) {
      message.error('删除失败: ' + getErrorMessage(e))
    }
  }

  const handleDocTypeChange = async (docId: string, docType: string) => {
    try {
      await contractsApi.updateDocType(docId, docType)
      message.success('已修正文件类型')
      if (detail) await viewDetail(detail.id)
    } catch (e) {
      message.error('修正失败: ' + getErrorMessage(e))
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
    } catch (e) {
      message.error('更新失败: ' + getErrorMessage(e))
    }
  }

  /** 打开 OCR 对照查看抽屉 */
  const openCompare = async (doc: DocumentBrief) => {
    setCompareDoc(doc)
    setCompareOpen(true)
    // 若文档已有 OCR 文本则直接展示；否则请求后端获取最新 OCR
    if (!doc.ocr_text && doc.ocr_status === 'done') {
      setCompareLoading(true)
      try {
        const fresh = await contractsApi.getOcr(doc.id)
        setCompareDoc(fresh)
    } catch (e) {
      message.error('加载 OCR 详情失败: ' + getErrorMessage(e))
      } finally {
        setCompareLoading(false)
      }
    }
  }

  const closeCompare = () => {
    setCompareOpen(false)
    // 延迟清空避免动画期间空白
    setTimeout(() => setCompareDoc(null), 300)
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
      render: (v: string, row: any) => {
        const inferred = (row.extracted_fields as Record<string, unknown>)?.['__inferred_doc_type__'] as string | undefined
        return (
          <div>
            <Select
              size="small"
              value={v}
              style={{ width: '100%' }}
              onChange={(val) => handleDocTypeChange(row.id, val)}
              options={docTypes.map((d) => ({ value: d.name, label: d.name }))}
            />
            {inferred && inferred !== v && (
              <div style={{ fontSize: 11, color: '#8B5CF6', marginTop: 2, lineHeight: '16px' }}>
                模型推测: {inferred}
              </div>
            )}
          </div>
        )
      },
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
    {
      title: '操作', key: 'action', width: 180, align: 'center' as const,
      render: (_: unknown, row: DocumentBrief) => (
        <Space size={4}>
          {row.ocr_status !== 'done' && (
            <Tooltip title={row.ocr_status === 'failed' ? '重新识别' : '触发 OCR 识别'}>
              <Button
                size="small"
                type="link"
                icon={row.ocr_status === 'failed' ? <ReRunIcon /> : <ThunderboltOutlined />}
                onClick={() => triggerDocOcr(row.id, row.file_name)}
                disabled={docOcrLoading[row.id]}
              >
                {row.ocr_status === 'failed' ? '重试' : '识别'}
              </Button>
            </Tooltip>
          )}
          {row.ocr_status === 'done' && (
            <Button
              size="small"
              type="link"
              icon={<FileSearchOutlined />}
              onClick={() => openCompare(row)}
            >
              OCR对照
            </Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="文档上传与合同识别"
        subtitle="选择本地合同文件夹上传（支持 PDF / PNG / JPG / DOCX），系统自动识别文件类型与合同号归一化"
        icon={<UploadOutlined />}
      />

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        {/* 第一段:上传区(全宽,左 Dragger + 右已选文件与操作) */}
        <Col span={24}>
          <Card
            title="上传合同文件夹"
            extra={
              <Space>
                <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新列表</Button>
                <Button
                  type="primary"
                  onClick={doUpload}
                  loading={uploading}
                  disabled={pendingFileNames.length === 0}
                >
                  开始上传
                </Button>
              </Space>
            }
          >
            <Row gutter={24} align="stretch">
              <Col flex="1 1 0%">
                <Dragger {...uploadProps} disabled={uploading} style={{ minHeight: 200 }}>
                  <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                  <p className="ant-upload-text">{uploading ? '上传中...' : '点击或拖拽文件到此区域'}</p>
                  <p className="ant-upload-hint">支持同时选择多个文件（一个合同的所有文件），选择后点击右上角"开始上传"</p>
                </Dragger>
              </Col>
              <Col flex="360px">
                {pendingFileNames.length > 0 ? (
                  <div
                    style={{
                      height: '100%',
                      padding: 16,
                      background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.06) 0%, rgba(139, 92, 246, 0.06) 100%)',
                      borderRadius: 10,
                      border: '1px solid #e2e8f0',
                      display: 'flex',
                      flexDirection: 'column',
                    }}
                  >
                    <Space style={{ marginBottom: 10, justifyContent: 'space-between', width: '100%' }}>
                      <Text strong style={{ color: '#475569' }}>
                        已选择 {pendingFileNames.length} 个文件
                      </Text>
                      <Button size="small" type="text" onClick={clearPending}>清空</Button>
                    </Space>
                    <div style={{ flex: 1, overflowY: 'auto', maxHeight: 140, fontSize: 12, color: '#64748b', lineHeight: 1.8 }}>
                      {pendingFileNames.map((n, i) => (
                        <div key={i} style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          · {n}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div
                    style={{
                      height: '100%',
                      minHeight: 200,
                      padding: 16,
                      background: '#f8fafc',
                      borderRadius: 10,
                      border: '1px dashed #e2e8f0',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: '#94a3b8',
                      fontSize: 13,
                      textAlign: 'center',
                    }}
                  >
                    选择文件后,此处显示待上传清单
                  </div>
                )}
              </Col>
            </Row>
          </Card>
        </Col>

        {/* 第二段:合同列表(全宽表格) */}
        <Col span={24}>
          <Card title="合同列表" size="small">
            <Table
              dataSource={contracts}
              columns={columns}
              rowKey="id"
              size="middle"
              loading={loading}
              pagination={false}
              scroll={{ y: 280 }}
              onRow={(row) => ({
                onClick: () => viewDetail(row.id),
                style: { cursor: 'pointer' },
              })}
            />
          </Card>
        </Col>

        {/* 第三段:合同详情(全宽,选中后展示) */}
        <Col span={24}>
          <Card
            title={
              detail ? (
                <Space>
                  <Text strong>{detail.contract_no}</Text>
                  <Tag>{detail.status}</Tag>
                </Space>
              ) : (
                '合同详情'
              )
            }
            extra={
              detail && (
                <Tooltip title="对该合同下所有未识别文档批量触发 OCR">
                  <Button
                    type="primary"
                    icon={<ThunderboltOutlined />}
                    onClick={triggerContractOcr}
                    loading={ocrTriggering}
                    disabled={ocrTask?.status === 'running' || detail.documents.every((d) => d.ocr_status === 'done')}
                  >
                    全部 OCR
                  </Button>
                </Tooltip>
              )
            }
            loading={detailLoading}
          >
            {!detail ? (
              <EmptyState
                description="点击上方合同列表中的任意一行,查看合同详情与文档清单"
                padding={48}
              />
            ) : (
              <>
                <Row gutter={24} style={{ marginBottom: 16 }}>
                  <Col span={6}>
                    <div style={{ padding: 12, background: '#f8fafc', borderRadius: 8 }}>
                      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>合同号</div>
                      <Text strong style={{ fontSize: 15 }}>{detail.contract_no}</Text>
                    </div>
                  </Col>
                  <Col span={12}>
                    <div style={{ padding: 12, background: '#f8fafc', borderRadius: 8 }}>
                      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>别名</div>
                      <Space size={[4, 4]} wrap>
                        {(detail.alias_list || []).length ? detail.alias_list.map((a) => <Tag key={a}>{a}</Tag>) : <Text type="secondary">无</Text>}
                      </Space>
                    </div>
                  </Col>
                  <Col span={6}>
                    <div style={{ padding: 12, background: '#f8fafc', borderRadius: 8 }}>
                      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>状态</div>
                      <Tag color={detail.status === 'reviewed' ? 'green' : detail.status === 'reviewing' ? 'blue' : 'default'}>
                        {detail.status}
                      </Tag>
                    </div>
                  </Col>
                </Row>

                {/* OCR 任务进度面板(运行中或刚完成时显示) */}
                {ocrTask && (
                  <div
                    style={{
                      marginBottom: 16,
                      padding: 12,
                      background:
                        ocrTask.status === 'running'
                          ? 'linear-gradient(135deg, rgba(99, 102, 241, 0.06) 0%, rgba(139, 92, 246, 0.06) 100%)'
                          : ocrTask.status === 'completed'
                            ? '#f0fdf4'
                            : '#fef2f2',
                      borderRadius: 8,
                      border: `1px solid ${ocrTask.status === 'running' ? '#c7d2fe' : ocrTask.status === 'completed' ? '#bbf7d0' : '#fecaca'}`,
                    }}
                  >
                    <Row gutter={16} align="middle">
                      <Col flex="auto">
                        <Space size={12}>
                          {ocrTask.status === 'running' && <Spin size="small" />}
                          <Text strong style={{ fontSize: 13 }}>
                            {ocrTask.stage || '准备中'}
                          </Text>
                          <Tag
                            color={
                              ocrTask.status === 'running'
                                ? 'processing'
                                : ocrTask.status === 'completed'
                                  ? 'success'
                                  : 'error'
                            }
                          >
                            {ocrTask.scope === 'single_doc' ? '单文档' : '批量'} · {ocrTask.status}
                          </Tag>
                        </Space>
                        <div style={{ marginTop: 8, fontSize: 12, color: '#64748b' }}>
                          进度 {ocrTask.done_count}/{ocrTask.total_count} ·
                          成功 <Text type="success">{ocrTask.success_count}</Text> ·
                          失败 <Text type="danger">{ocrTask.failed_count}</Text>
                        </div>
                      </Col>
                      <Col flex="200px">
                        <Progress
                          percent={ocrTask.progress}
                          size="small"
                          status={
                            ocrTask.status === 'failed'
                              ? 'exception'
                              : ocrTask.status === 'completed'
                                ? 'success'
                                : 'active'
                          }
                        />
                      </Col>
                    </Row>
                    {ocrTask.failures.length > 0 && (
                      <div style={{ marginTop: 8, fontSize: 11, color: '#dc2626' }}>
                        {ocrTask.failures.slice(0, 3).map((f, i) => (
                          <div key={i}>· {f.file_name}: {f.error}</div>
                        ))}
                        {ocrTask.failures.length > 3 && (
                          <div>... 还有 {ocrTask.failures.length - 3} 条</div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                <Table
                  dataSource={detail.documents}
                  columns={docColumns}
                  rowKey="id"
                  size="middle"
                  pagination={false}
                  scroll={{ y: 360 }}
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

      <Drawer
        title={
          <Space>
            <FileSearchOutlined />
            <span>OCR 对照查看</span>
            {compareDoc && (
              <Tag color="blue" style={{ marginLeft: 8 }}>
                {compareDoc.file_name}
              </Tag>
            )}
          </Space>
        }
        placement="right"
        open={compareOpen}
        onClose={closeCompare}
        width="86%"
        destroyOnClose
        styles={{ body: { padding: 12, background: '#f5f5f5' } }}
      >
        {compareLoading ? (
          <div style={{ textAlign: 'center', padding: 80 }}>
            <Spin size="large" tip="加载 OCR 识别结果中..."><div style={{ padding: 40 }} /></Spin>
          </div>
        ) : compareDoc ? (
          <DocumentCompare
            doc={compareDoc}
            fileUrl={contractsApi.fileUrl(compareDoc.id)}
            height="calc(100vh - 120px)"
            onSaved={async () => {
              try {
                const fresh = await contractsApi.getOcr(compareDoc.id)
                setCompareDoc(fresh)
                if (detail) await viewDetail(detail.id)
              } catch (e) {
                message.error('刷新文档详情失败: ' + getErrorMessage(e))
              }
            }}
          />
        ) : (
          <EmptyState description="未选择文档" padding={48} />
        )}
      </Drawer>
    </div>
  )
}
