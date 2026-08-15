/**
 * 文档对照查看组件
 *
 * 布局：
 * - 左侧：原始文档预览（PDF/图片/DOCX）
 * - 右侧：OCR 识别结果（文本 + 结构化字段）
 *
 * 交互：
 * - 点击右侧 OCR 文本/字段 → 在左侧原始文档中高亮对应位置
 * - PDF：通过 react-pdf 文本层 + customTextRenderer 实现高亮
 * - 图片：无坐标信息，点击时显示视觉提示
 * - DOCX：通过 mammoth 转 HTML 后，JS 搜索高亮
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { Document as PdfDocument, Page as PdfPage } from 'react-pdf'
import {
  Card, Tabs, Table, Tag, Empty, Spin, Typography, Button, Tooltip, message, Input, Space, Popconfirm,
} from 'antd'
import {
  SearchOutlined, FileTextOutlined, CheckCircleOutlined, WarningOutlined,
  EditOutlined, SaveOutlined, PlusOutlined, DeleteOutlined, CloseOutlined,
} from '@ant-design/icons'
import { contractsApi, getErrorMessage } from '../api/client'
import type { DocumentBrief } from '../types'
import '../pdf-setup'

const { Text, Paragraph } = Typography

interface DocumentCompareProps {
  doc: DocumentBrief
  fileUrl: string
  height?: number | string
  /** 字段修正保存成功后的回调（父级可刷新文档详情） */
  onSaved?: () => void
}

/** 高亮 CSS 类名 */
const HIGHLIGHT_CLASS = 'doc-highlight-mark'
const HIGHLIGHTED_ITEM_CLASS = 'ocr-item-selected'

/**
 * 在 DOM 容器中高亮指定文本
 * 使用 TreeWalker 遍历文本节点，找到匹配项并包裹 <mark> 标签
 */
function highlightTextInContainer(container: HTMLElement, searchText: string): number {
  if (!searchText || searchText.trim().length < 2) return 0

  // 清除之前的高亮
  container.querySelectorAll(`mark.${HIGHLIGHT_CLASS}`).forEach((mark) => {
    const parent = mark.parentNode
    if (parent) {
      parent.replaceChild(document.createTextNode(mark.textContent || ''), mark)
      parent.normalize()
    }
  })

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      if (!node.nodeValue || node.nodeValue.trim().length < 1) return NodeFilter.FILTER_REJECT
      // 跳过 script/style
      const parent = node.parentElement
      if (parent && ['SCRIPT', 'STYLE', 'MARK'].includes(parent.tagName)) {
        return NodeFilter.FILTER_REJECT
      }
      return NodeFilter.FILTER_ACCEPT
    },
  })

  const textNodes: Text[] = []
  let node: Node | null
  while ((node = walker.nextNode())) {
    textNodes.push(node as Text)
  }

  let highlightCount = 0
  // 搜索关键词：取前 20 个字符作为搜索词（避免太长的文本匹配不到）
  const searchKey = searchText.trim().slice(0, 30)

  textNodes.forEach((textNode) => {
    const text = textNode.nodeValue || ''
    const lowerText = text.toLowerCase()
    const lowerKey = searchKey.toLowerCase()

    const idx = lowerText.indexOf(lowerKey)
    if (idx === -1) {
      // 尝试部分匹配（取关键词的前 8 个字符）
      const shortKey = searchKey.slice(0, 8).toLowerCase()
      const shortIdx = lowerText.indexOf(shortKey)
      if (shortIdx === -1) return
      // 找到部分匹配，高亮该片段
      const range = document.createRange()
      range.setStart(textNode, shortIdx)
      range.setEnd(textNode, Math.min(shortIdx + shortKey.length, text.length))
      const mark = document.createElement('mark')
      mark.className = HIGHLIGHT_CLASS
      mark.style.backgroundColor = 'rgba(255, 235, 59, 0.7)'
      mark.style.padding = '1px 0'
      mark.style.borderRadius = '2px'
      range.surroundContents(mark)
      highlightCount++
    } else {
      const range = document.createRange()
      range.setStart(textNode, idx)
      range.setEnd(textNode, Math.min(idx + searchKey.length, text.length))
      const mark = document.createElement('mark')
      mark.className = HIGHLIGHT_CLASS
      mark.style.backgroundColor = 'rgba(255, 235, 59, 0.7)'
      mark.style.padding = '1px 0'
      mark.style.borderRadius = '2px'
      range.surroundContents(mark)
      highlightCount++
    }
  })

  // 滚动到第一个高亮项
  if (highlightCount > 0) {
    const firstMark = container.querySelector(`mark.${HIGHLIGHT_CLASS}`)
    if (firstMark) {
      firstMark.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }

  return highlightCount
}

/** PDF 文本层自定义渲染器：为每个文本项添加 data-text 属性 */
function pdfTextRenderer(textItem: { str?: string }): string {
  const str = textItem.str || ''
  if (!str.trim()) return str
  // 用 data-text 存储原文，便于后续搜索高亮
  return `<span data-pdf-text="${str.replace(/"/g, '&quot;')}">${str}</span>`
}

/** PDF 渲染器 */
function PdfViewer({
  fileUrl,
  onReady,
  height,
}: {
  fileUrl: string
  onReady: (container: HTMLElement | null) => void
  height: number | string
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [numPages, setNumPages] = useState<number>(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const handleDocumentLoad = useCallback(({ numPages }: { numPages: number }) => {
    setNumPages(numPages)
    setLoading(false)
    setError(null)
  }, [])

  const handleError = useCallback((err: Error) => {
    setLoading(false)
    setError(err.message || 'PDF 加载失败')
  }, [])

  // 当文档加载完成后，通知父组件容器已就绪
  useEffect(() => {
    if (!loading && containerRef.current) {
      onReady(containerRef.current)
    }
  }, [loading, onReady])

  return (
    <div
      ref={containerRef}
      style={{
        height: typeof height === 'number' ? `${height}px` : height,
        overflow: 'auto',
        background: '#525659',
        padding: 16,
        borderRadius: 6,
      }}
    >
      {error && (
        <div style={{ textAlign: 'center', padding: 40, color: '#fff' }}>
          <WarningOutlined style={{ fontSize: 32, marginBottom: 12 }} />
          <div>PDF 加载失败: {error}</div>
          <div style={{ marginTop: 8 }}>
            <a href={fileUrl} target="_blank" rel="noreferrer" style={{ color: '#60a5fa' }}>
              点击直接下载查看
            </a>
          </div>
        </div>
      )}
      {loading && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" tip="加载 PDF 中..."><div style={{ padding: 40 }} /></Spin>
        </div>
      )}
      <PdfDocument
        file={fileUrl}
        onLoadSuccess={handleDocumentLoad}
        onLoadError={handleError}
        loading={null}
        error={null}
      >
        {Array.from(new Array(numPages), (_, i) => (
          <PdfPage
            key={i + 1}
            pageNumber={i + 1}
            width={600}
            customTextRenderer={pdfTextRenderer}
            renderAnnotationLayer={false}
          />
        ))}
      </PdfDocument>
    </div>
  )
}

/** 图片渲染器 */
function ImageViewer({ fileUrl, height }: { fileUrl: string; height: number | string }) {
  return (
    <div
      style={{
        height: typeof height === 'number' ? `${height}px` : height,
        overflow: 'auto',
        background: '#525659',
        padding: 16,
        borderRadius: 6,
        textAlign: 'center',
      }}
    >
      <img
        src={fileUrl}
        alt="原始文档"
        style={{ maxWidth: '100%', border: '1px solid #374151', borderRadius: 4 }}
      />
    </div>
  )
}

/** DOCX 渲染器（通过 mammoth 转 HTML） */
function DocxViewer({
  fileUrl,
  onReady,
  height,
}: {
  fileUrl: string
  onReady: (container: HTMLElement | null) => void
  height: number | string
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    setLoading(true)
    setError(null)

    fetch(fileUrl)
      .then((res) => res.arrayBuffer())
      .then(async (arrayBuffer) => {
        const mammoth = await import('mammoth')
        const result = await mammoth.convertToHtml({ arrayBuffer })
        if (mounted && containerRef.current) {
          containerRef.current.innerHTML = result.value
          setLoading(false)
          onReady(containerRef.current)
        }
      })
      .catch((err) => {
        if (mounted) {
          setError(err.message || 'DOCX 加载失败')
          setLoading(false)
        }
      })

    return () => {
      mounted = false
    }
  }, [fileUrl, onReady])

  return (
    <div
      ref={containerRef}
      style={{
        height: typeof height === 'number' ? `${height}px` : height,
        overflow: 'auto',
        background: '#fff',
        padding: 24,
        borderRadius: 6,
        lineHeight: 1.8,
        fontSize: 14,
      }}
    >
      {loading && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" tip="加载 Word 文档中..."><div style={{ padding: 40 }} /></Spin>
        </div>
      )}
      {error && (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <WarningOutlined style={{ fontSize: 32, marginBottom: 12, color: '#faad14' }} />
          <div>Word 文档加载失败: {error}</div>
          <div style={{ marginTop: 8 }}>
            <a href={fileUrl} target="_blank" rel="noreferrer">点击下载查看</a>
          </div>
        </div>
      )}
    </div>
  )
}

/** 主组件 */
export default function DocumentCompare({ doc, fileUrl, height = 600, onSaved }: DocumentCompareProps) {
  const [highlightTarget, setHighlightTarget] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draftFields, setDraftFields] = useState<Array<{ key: string; value: string }>>([])
  const [saving, setSaving] = useState(false)
  const [freshDoc, setFreshDoc] = useState<DocumentBrief | null>(null)
  const docContainerRef = useRef<HTMLElement | null>(null)

  // 切换文档时清除上一次的高亮目标与编辑状态，避免旧状态残留
  useEffect(() => {
    setHighlightTarget(null)
    setEditing(false)
    setFreshDoc(null)
  }, [doc, fileUrl])

  const displayDoc = freshDoc ?? doc

  const handleDocReady = useCallback((container: HTMLElement | null) => {
    docContainerRef.current = container
  }, [])

  /** 点击 OCR 文本/字段 → 高亮原始文档中对应位置 */
  const handleHighlight = useCallback(
    (text: string) => {
      if (!text || text.trim().length < 2) {
        message.warning('文本太短，无法高亮')
        return
      }

      setHighlightTarget(text)

      const container = docContainerRef.current
      if (!container) {
        message.info('文档尚未加载完成')
        return
      }

      // 对 PDF：搜索 data-pdf-text 属性
      const pdfTextSpans = container.querySelectorAll('[data-pdf-text]')
      if (pdfTextSpans.length > 0) {
        // 先清除之前的高亮
        container.querySelectorAll(`mark.${HIGHLIGHT_CLASS}`).forEach((mark) => {
          const parent = mark.parentNode
          if (parent) {
            parent.replaceChild(document.createTextNode(mark.textContent || ''), mark)
            parent.normalize()
          }
        })

        // 在 data-pdf-text 属性中查找匹配
        let found = false
        pdfTextSpans.forEach((span) => {
          const spanText = span.getAttribute('data-pdf-text') || ''
          if (
            spanText.includes(text.slice(0, 10)) ||
            text.slice(0, 10).includes(spanText)
          ) {
            if (!found) {
              // 只高亮第一个匹配项的样式
              const mark = document.createElement('mark')
              mark.className = HIGHLIGHT_CLASS
              mark.style.backgroundColor = 'rgba(255, 235, 59, 0.7)'
              mark.style.padding = '1px 0'
              mark.style.borderRadius = '2px'
              // 替换 span 内容为 mark
              while (span.firstChild) {
                mark.appendChild(span.firstChild)
              }
              span.appendChild(mark)
              // 滚动到匹配位置
              span.scrollIntoView({ behavior: 'smooth', block: 'center' })
              found = true
            }
          }
        })

        if (found) {
          message.success(`已在原始文档中定位: ${text.slice(0, 30)}${text.length > 30 ? '...' : ''}`)
        } else {
          message.info(`未在 PDF 中找到完全匹配的文本，请尝试在 PDF 中按 Ctrl+F 搜索`)
        }
        return
      }

      // 对 HTML 内容（DOCX）：使用 TreeWalker 高亮
      const count = highlightTextInContainer(container, text)
      if (count > 0) {
        message.success(`找到 ${count} 处匹配，已高亮第一处`)
      } else {
        // 图片：无法高亮文本位置
        const hasImage = container.querySelector('img')
        if (hasImage) {
          message.info('图片文档无法定位文本位置，请在右侧查看 OCR 识别结果')
        } else {
          message.info('未在原始文档中找到匹配文本')
        }
      }
    },
    [],
  )

  // ============ 字段人工修正（OCR 对照界面） ============
  const startEditFields = () => {
    setDraftFields(
      Object.entries(displayDoc.extracted_fields || {})
        .filter(([k]) => k !== '__inferred_doc_type__')
        .map(([k, v]) => ({ key: k, value: v == null ? '' : String(v) })),
    )
    setEditing(true)
  }

  const cancelEditFields = () => {
    setEditing(false)
    setDraftFields([])
  }

  const saveFields = async () => {
    if (saving) return
    const extracted_fields: Record<string, unknown> = {}
    for (const row of draftFields) {
      const k = (row.key || '').trim()
      if (!k) continue
      extracted_fields[k] = row.value
    }
    // 保留模型推断类型（不在编辑表中展示）
    const inferred = (displayDoc.extracted_fields as Record<string, unknown>)?.['__inferred_doc_type__']
    if (inferred) extracted_fields['__inferred_doc_type__'] = inferred

    setSaving(true)
    try {
      const updated = await contractsApi.updateOcrFields(displayDoc.id, { extracted_fields })
      message.success('OCR 字段已保存，重新审查将使用修正后的数据')
      setFreshDoc(updated)
      setEditing(false)
      onSaved?.()
    } catch (e) {
      message.error('保存失败: ' + getErrorMessage(e, ''))
    } finally {
      setSaving(false)
    }
  }

  const updateDraft = (idx: number, patch: Partial<{ key: string; value: string }>) => {
    setDraftFields((prev) => prev.map((row, i) => (i === idx ? { ...row, ...patch } : row)))
  }

  const removeDraft = (idx: number) => {
    setDraftFields((prev) => prev.filter((_, i) => i !== idx))
  }

  const addDraft = () => {
    setDraftFields((prev) => [...prev, { key: '', value: '' }])
  }

  // 字段表格列（查看模式）
  const fieldColumns = [
    {
      title: '字段名',
      dataIndex: 'key',
      key: 'key',
      width: '40%',
      render: (v: string) => <Text strong>{v}</Text>,
    },
    {
      title: '识别值',
      dataIndex: 'value',
      key: 'value',
      render: (v: string) => (
        <Tooltip title="点击在原始文档中高亮">
          <span
            style={{ cursor: 'pointer', padding: '2px 4px', borderRadius: 3 }}
            className={highlightTarget === String(v) ? HIGHLIGHTED_ITEM_CLASS : ''}
            onClick={() => handleHighlight(String(v))}
          >
            {String(v) || <Text type="secondary">（空）</Text>}
          </span>
        </Tooltip>
      ),
    },
  ]

  // 字段表格列（编辑模式）
  const editFieldColumns = [
    {
      title: '字段名',
      dataIndex: 'key',
      key: 'key',
      width: '34%',
      render: (v: string, _: unknown, idx: number) => (
        <Input
          size="small"
          value={v}
          placeholder="字段名"
          onChange={(e) => updateDraft(idx, { key: e.target.value })}
        />
      ),
    },
    {
      title: '值',
      dataIndex: 'value',
      key: 'value',
      render: (v: string, _: unknown, idx: number) => (
        <Input
          size="small"
          value={v}
          placeholder="从左侧识别文本复制实际值"
          onChange={(e) => updateDraft(idx, { value: e.target.value })}
        />
      ),
    },
    {
      title: '',
      key: 'op',
      width: 48,
      render: (_: unknown, __: unknown, idx: number) => (
        <Popconfirm title="删除该字段？" onConfirm={() => removeDraft(idx)}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ]

  const fieldData = Object.entries(displayDoc.extracted_fields || {})
    .filter(([k]) => k !== '__inferred_doc_type__')
    .map(([k, v]) => ({
      key: k,
      value: String(v ?? ''),
    }))

  const inferredDocType = (displayDoc.extracted_fields as Record<string, unknown>)?.['__inferred_doc_type__'] as string | undefined

  // OCR 文本按行分割，每行可点击高亮
  const ocrLines = (displayDoc.ocr_text || '').split('\n').filter((line) => line.trim())

  return (
    <div style={{ display: 'flex', gap: 12, height: typeof height === 'number' ? `${height}px` : height }}>
      {/* 左侧：原始文档 */}
      <div style={{ flex: '1 1 55%', minWidth: 0 }}>
        <Card
          size="small"
          title={
            <span>
              <FileTextOutlined /> 原始文档：{doc.file_name}
            </span>
          }
          styles={{ body: { padding: 0 } }}
        >
          {doc.file_type === 'pdf' && (
            <PdfViewer fileUrl={fileUrl} onReady={handleDocReady} height={height} />
          )}
          {(doc.file_type === 'png' || doc.file_type === 'jpg' || doc.file_type === 'jpeg') && (
            <ImageViewer fileUrl={fileUrl} height={height} />
          )}
          {doc.file_type === 'docx' && (
            <DocxViewer fileUrl={fileUrl} onReady={handleDocReady} height={height} />
          )}
          {!['pdf', 'png', 'jpg', 'jpeg', 'docx'].includes(doc.file_type) && (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Empty description={`不支持预览 ${doc.file_type} 格式`}>
                <a href={fileUrl} target="_blank" rel="noreferrer">
                  <Button icon={<SearchOutlined />}>下载查看</Button>
                </a>
              </Empty>
            </div>
          )}
        </Card>
      </div>

      {/* 右侧：OCR 识别结果 */}
      <div style={{ flex: '1 1 45%', minWidth: 0, overflow: 'auto' }}>
        <Card
          size="small"
          title={
            <span>
              <CheckCircleOutlined /> OCR 识别结果
            </span>
          }
          extra={
            <Tag color={displayDoc.ocr_status === 'done' ? 'green' : displayDoc.ocr_status === 'failed' ? 'red' : 'blue'}>
              {displayDoc.ocr_status}
            </Tag>
          }
        >
          {/* 元信息 */}
          <div style={{ marginBottom: 12, fontSize: 12, color: '#666' }}>
            {inferredDocType && (
              <Tag color="purple" style={{ marginRight: 8 }}>
                模型推测类型: {inferredDocType}
              </Tag>
            )}
            {displayDoc.ocr_confidence != null && (
              <span>置信度: {(displayDoc.ocr_confidence * 100).toFixed(1)}% </span>
            )}
            {displayDoc.has_stamp != null && (
              <Tag color={displayDoc.has_stamp ? 'green' : 'red'} style={{ marginLeft: 8 }}>
                {displayDoc.has_stamp ? '有印章' : '无印章'}
              </Tag>
            )}
          </div>

          <Tabs
            size="small"
            items={[
              {
                key: 'fields',
                label: `结构化字段 (${fieldData.length})`,
                children: (
                  <div>
                    <Space style={{ marginBottom: 8 }}>
                      {!editing ? (
                        <Button size="small" icon={<EditOutlined />} onClick={startEditFields}>
                          修正字段
                        </Button>
                      ) : (
                        <>
                          <Button
                            size="small"
                            type="primary"
                            icon={<SaveOutlined />}
                            loading={saving}
                            onClick={saveFields}
                          >
                            保存
                          </Button>
                          <Button size="small" icon={<CloseOutlined />} onClick={cancelEditFields}>
                            取消
                          </Button>
                        </>
                      )}
                      {editing && (
                        <Button size="small" icon={<PlusOutlined />} onClick={addDraft}>
                          新增字段
                        </Button>
                      )}
                    </Space>
                    {editing ? (
                      <Table
                        dataSource={draftFields}
                        columns={editFieldColumns}
                        pagination={false}
                        size="small"
                        scroll={{ y: 340 }}
                        rowKey={(_, idx) => String(idx)}
                      />
                    ) : fieldData.length > 0 ? (
                      <Table
                        dataSource={fieldData}
                        columns={fieldColumns}
                        pagination={false}
                        size="small"
                        scroll={{ y: 400 }}
                      />
                    ) : (
                      <Empty description="无提取字段" />
                    )}
                    {editing && (
                      <Paragraph type="warning" style={{ marginTop: 8, fontSize: 11, marginBottom: 0 }}>
                        合计类字段（数量/总价/件数等）请填所有明细行的汇总值，可直接从左侧识别文本中取值。
                      </Paragraph>
                    )}
                  </div>
                ),
              },
              {
                key: 'text',
                label: `识别文本 (${ocrLines.length} 行)`,
                children: ocrLines.length > 0 ? (
                  <div
                    style={{
                      maxHeight: 480,
                      overflowY: 'auto',
                      background: '#f6f8fa',
                      padding: 8,
                      borderRadius: 4,
                      fontFamily: 'monospace',
                      fontSize: 12,
                      lineHeight: 1.8,
                    }}
                  >
                    {ocrLines.map((line, i) => (
                      <div
                        key={i}
                        style={{
                          cursor: 'pointer',
                          padding: '2px 4px',
                          borderRadius: 3,
                          transition: 'background 0.2s',
                        }}
                        className={highlightTarget === line ? HIGHLIGHTED_ITEM_CLASS : ''}
                        onClick={() => handleHighlight(line)}
                        onMouseEnter={(e) => {
                          if (line.trim().length >= 2) {
                            (e.currentTarget as HTMLElement).style.background = '#e6f7ff'
                          }
                        }}
                        onMouseLeave={(e) => {
                          (e.currentTarget as HTMLElement).style.background = 'transparent'
                        }}
                      >
                        <Text type="secondary" style={{ fontSize: 10, marginRight: 8 }}>
                          {i + 1}
                        </Text>
                        {line}
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty description="无识别文本" />
                ),
              },
            ]}
          />

          <Paragraph
            type="secondary"
            style={{ marginTop: 8, fontSize: 11, marginBottom: 0 }}
          >
            点击右侧字段或文本行，可在左侧原始文档中高亮对应位置
          </Paragraph>
        </Card>
      </div>
    </div>
  )
}
