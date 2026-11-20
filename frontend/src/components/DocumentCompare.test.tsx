/**
 * DocumentCompare 回归测试
 *
 * 背景：DOCX 预览用 mammoth 转成 HTML 后直接写进容器 innerHTML，
 * 如果该容器同时承载 React 托管的「加载中」节点，React 在 setLoading(false)
 * 时会去删除已被 innerHTML 覆盖掉的加载中节点，抛出
 * `NotFoundError: Failed to execute 'removeChild' on 'Node'`，
 * 没有错误边界时整棵组件树卸载 → 整个页面白屏（文档上传 → 文件列表 → OCR 对照）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import DocumentCompare from './DocumentCompare'
import type { DocumentBrief } from '../types'

vi.mock('react-pdf', () => ({
  Document: () => null,
  Page: () => null,
  pdfjs: { GlobalWorkerOptions: { workerSrc: '' } },
}))

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: 'pdf.worker.mjs' }))

vi.mock('mammoth', () => ({
  convertToHtml: vi.fn(async () => ({
    value: '<p>合同编号 BX-2026-0312</p><p>价税合计 1280.00</p>',
    messages: [],
  })),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    constantsApi: {
      ...actual.constantsApi,
      docTypes: vi.fn().mockResolvedValue({ doc_types: [], check_categories: [] }),
    },
  }
})

/** jsdom 没有 ResizeObserver，ZoomableStage 会用到 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const docxDoc: DocumentBrief = {
  id: 'doc-1',
  file_name: 'BX-2026-0312_费用报销单.docx',
  file_type: 'docx',
  doc_type: '报销单',
  is_required: false,
  ocr_status: 'done',
  ocr_confidence: 0.9,
  has_stamp: null,
  extracted_fields: { 合同编号: 'BX-2026-0312' },
  ocr_text: '合同编号 BX-2026-0312\n价税合计 1280.00',
  ocr_layout: { pages: [] },
  extracted_at: null,
}

describe('DocumentCompare DOCX 预览', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub)
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      arrayBuffer: async () => new ArrayBuffer(8),
    })) as unknown as typeof fetch
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    globalThis.fetch = originalFetch
  })

  it('mammoth 注入 HTML 后不会因 React 删除加载中节点而崩溃，且正文可见', async () => {
    render(<DocumentCompare doc={docxDoc} fileUrl="/api/contracts/documents/doc-1/file" />)

    // 崩溃时 React 会卸载整棵树，下面的断言会失败
    await waitFor(() => {
      expect(screen.getByText(/合同编号 BX-2026-0312/)).toBeInTheDocument()
    })
    expect(screen.getByText(/价税合计 1280\.00/)).toBeInTheDocument()
    // 加载提示应当已被替换掉，而不是残留
    expect(screen.queryByText('加载 Word 文档中...')).not.toBeInTheDocument()
    // 右侧 OCR 结果面板仍在（说明整棵树没有被卸载）
    expect(screen.getByText('OCR 识别结果')).toBeInTheDocument()
  })
})
