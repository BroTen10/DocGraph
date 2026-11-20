/**
 * 文档上传 → 文件列表 → OCR 对照的切换范围回归测试。
 *
 * 业务约束：「上一张 / 下一张」的清单＝当前合同（这批上传业务）下的全部文件，
 * 不会把其它合同的文件也纳入进来。
 *
 * 说明：这里用文本查询 + 关闭 userEvent 的 pointer-events 校验，
 * 避免 antd 页面在 jsdom 里因 getComputedStyle / 可达性名称计算而变得很慢。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import UploadPage from './UploadPage'
import type { ContractBrief, ContractDetail, DocumentBrief } from '../types'

vi.mock('react-pdf', () => ({
  Document: () => null,
  Page: () => null,
  pdfjs: { GlobalWorkerOptions: { workerSrc: '' } },
}))

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: 'pdf.worker.mjs' }))

vi.mock('../context/RuleSetContext', () => ({
  useRuleSet: () => ({
    ruleSets: [],
    current: null,
    currentId: 'rs-1',
    loading: false,
    error: null,
    switchTo: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    refresh: vi.fn(),
  }),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: '测试用户' },
    tenant: { id: 't-1', name: '测试租户' },
    role: 'reviewer',
    permissions: new Set(['review.view', 'review.execute']),
    hasPermission: () => true,
  }),
}))

const apiMocks = vi.hoisted(() => ({
  contractsList: vi.fn(),
  contractsGet: vi.fn(),
  getOcr: vi.fn(),
  listTasks: vi.fn(),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    contractsApi: {
      ...actual.contractsApi,
      list: apiMocks.contractsList,
      get: apiMocks.contractsGet,
      getOcr: apiMocks.getOcr,
    },
    ocrApi: { ...actual.ocrApi, listTasks: apiMocks.listTasks },
    constantsApi: {
      ...actual.constantsApi,
      docTypes: async () => ({ doc_types: [], check_categories: [] }),
    },
  }
})

const contractA: ContractBrief = {
  id: 'contract-1',
  rule_set_id: 'rs-1',
  contract_no: 'BX-2026-0312',
  alias_list: [],
  upload_time: '2026-11-19T08:00:00Z',
  status: 'uploaded',
  file_count: 3,
}

/** 另一份合同：它的文件不能出现在第一份合同的切换清单里 */
const contractB: ContractBrief = {
  ...contractA,
  id: 'contract-2',
  contract_no: 'BX-2026-0999',
  file_count: 1,
}

const docBrief = (id: string, fileName: string): DocumentBrief => ({
  id,
  file_name: fileName,
  // 用不支持预览的格式，避免测试里渲染 PDF / 图片查看器
  file_type: 'txt',
  doc_type: '增值税发票',
  is_required: false,
  ocr_status: 'done',
  ocr_confidence: 0.9,
  has_stamp: null,
  extracted_fields: {},
  // 列表数据缺少坐标时，对照抽屉会去拉最新 OCR 详情（这里用来断言切换目标）
  ocr_text: '',
  ocr_layout: { pages: [] },
  extracted_at: null,
})

const detailA: ContractDetail = {
  ...contractA,
  note: null,
  documents: [
    docBrief('doc-1', 'BX-2026-0312_费用报销单.txt'),
    docBrief('doc-2', 'BX-2026-0312_增值税发票.txt'),
    docBrief('doc-3', 'BX-2026-0312_行程单.txt'),
  ],
}

const detailB: ContractDetail = {
  ...contractB,
  note: null,
  documents: [docBrief('doc-9', 'BX-2026-0999_其它合同附件.txt')],
}

/** 通过按钮文案拿到按钮本身（antd 会在两字按钮里插入空格，用正则兼容） */
const findButton = async (label: string | RegExp): Promise<HTMLButtonElement> => {
  const pattern = typeof label === 'string' ? new RegExp(label) : label
  const el = await screen.findByText(pattern)
  const btn = el.closest('button')
  expect(btn).not.toBeNull()
  return btn as HTMLButtonElement
}

describe('UploadPage OCR 对照切换范围', () => {
  beforeEach(() => {
    apiMocks.contractsList.mockResolvedValue([contractA, contractB])
    apiMocks.contractsGet.mockImplementation(async (id: string) =>
      id === 'contract-2' ? detailB : detailA,
    )
    apiMocks.listTasks.mockResolvedValue([])
    apiMocks.getOcr.mockImplementation(async (docId: string) =>
      detailA.documents.concat(detailB.documents).find((d) => d.id === docId) ??
      docBrief(docId, '未知文件.txt'),
    )
  })

  it('在当前合同的文件列表内前后切换，不会串到其它合同', async () => {
    const user = userEvent.setup({ delay: null, pointerEventsCheck: 0 })
    render(<UploadPage />)

    // 打开第一份合同的详情（antd 会把「查看」拆成两个字符节点，只能按按钮名匹配）
    const viewButtons = await screen.findAllByRole('button', { name: /查\s*看/ })
    await user.click(viewButtons[0])
    await waitFor(() => expect(apiMocks.contractsGet).toHaveBeenCalledWith('contract-1'))

    // 从文件列表第一份文件进入 OCR 对照
    const compareEntries = await screen.findAllByText('OCR对照')
    await user.click(compareEntries[0])
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-1'))

    // 范围＝当前合同下的 3 份文件
    const prev = await findButton('上一张')
    expect(prev).toBeDisabled()
    expect(await screen.findByText('1/3')).toBeInTheDocument()

    await user.click(await findButton('下一张'))
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-2'))
    expect(await screen.findByText('2/3')).toBeInTheDocument()

    await user.click(await findButton('下一张'))
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-3'))
    expect(await screen.findByText('3/3')).toBeInTheDocument()

    // 末张：不能继续往后，也不会跳到另一份合同的 doc-9
    const next = await findButton('下一张')
    expect(next).toBeDisabled()
    await user.click(next)
    expect(apiMocks.getOcr).not.toHaveBeenCalledWith('doc-9')

    // 可以回到上一张
    await user.click(await findButton('上一张'))
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-2'))
    expect(await screen.findByText('2/3')).toBeInTheDocument()
  })
})
