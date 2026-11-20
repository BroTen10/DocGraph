/**
 * 审查结果「原件 / OCR 对照」切换范围回归测试。
 *
 * 业务约束：抽屉里的「上一张 / 下一张」必须在打开对照时那条判定涉及的文件内切换，
 * 不能把同一审查任务里其它判定的文件也纳入清单（否则用户会被带到无关文件上）。
 *
 * 说明：这里用文本查询 + 关闭 userEvent 的 pointer-events 校验，
 * 避免 antd 页面在 jsdom 里因 getComputedStyle / 可达性名称计算而变得很慢。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import ResultsPage from './ResultsPage'
import type { DocumentBrief, ReviewResultByDoc, ReviewResultByRule, ReviewTaskListItem } from '../types'

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
  reviewList: vi.fn(),
  byRule: vi.fn(),
  byDoc: vi.fn(),
  getOcr: vi.fn(),
  docTypes: vi.fn(),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    reviewsApi: {
      ...actual.reviewsApi,
      list: apiMocks.reviewList,
      byRule: apiMocks.byRule,
      byDoc: apiMocks.byDoc,
    },
    contractsApi: { ...actual.contractsApi, getOcr: apiMocks.getOcr },
    constantsApi: { ...actual.constantsApi, docTypes: apiMocks.docTypes },
  }
})

const task: ReviewTaskListItem = {
  id: 'task-1',
  contract_id: 'contract-1',
  contract_no: 'BX-2026-0312',
  status: 'completed',
  progress: 100,
  stage: null,
  start_time: '2026-11-19T08:00:00Z',
  end_time: '2026-11-19T08:01:00Z',
  error: null,
  summary: { total: 2, pass: 1, fail: 1, unverifiable: 0 },
}

const byRule: ReviewResultByRule = {
  task_id: 'task-1',
  summary: { total: 2, pass: 1, fail: 1, unverifiable: 0 },
  results: [
    {
      id: 'r-1',
      rule_id: 'rule-1',
      rule_text: '增值税发票的购货方应与报销单的所属公司一致',
      doc_type: '增值税发票',
      check_category: '信息准确性',
      doc_id: 'doc-1',
      doc_name: 'A_增值税发票.pdf',
      // 这一条判定涉及两份文件 → 对照抽屉的可切换清单就是这两份
      related_docs: [
        { doc_id: 'doc-1', file_name: 'A_增值税发票.pdf', doc_type: '增值税发票' },
        { doc_id: 'doc-2', file_name: 'B_费用报销单.pdf', doc_type: '报销单' },
      ],
      result: 'fail',
      issue_desc: '两票购货方不一致',
      detail: {},
      suggestion: '请核对购货方名称',
    },
    {
      id: 'r-2',
      rule_id: 'rule-2',
      rule_text: '行程单的乘机人应与报销单的申请人一致',
      doc_type: '行程单',
      check_category: '信息准确性',
      doc_id: 'doc-3',
      doc_name: 'C_行程单.pdf',
      // 另一条判定只涉及第三份文件，不能进入上一条判定的切换清单
      related_docs: [{ doc_id: 'doc-3', file_name: 'C_行程单.pdf', doc_type: '行程单' }],
      result: 'pass',
      issue_desc: null,
      detail: {},
      suggestion: null,
    },
  ],
}

const byDoc: ReviewResultByDoc = {
  task_id: 'task-1',
  summary: { total: 2 },
  docs: [
    {
      doc_id: 'doc-1',
      file_name: 'A_增值税发票.pdf',
      doc_type: '增值税发票',
      results: [byRule.results[0]],
    },
    {
      doc_id: 'doc-3',
      file_name: 'C_行程单.pdf',
      doc_type: '行程单',
      results: [byRule.results[1]],
    },
  ],
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
  ocr_text: '',
  ocr_layout: { pages: [] },
  extracted_at: null,
})

/** 通过按钮文案拿到按钮本身（antd 会在两字按钮里插入空格，用正则兼容） */
const findButton = async (label: string | RegExp): Promise<HTMLButtonElement> => {
  const pattern = typeof label === 'string' ? new RegExp(label) : label
  const el = await screen.findByText(pattern)
  const btn = el.closest('button')
  expect(btn).not.toBeNull()
  return btn as HTMLButtonElement
}

describe('ResultsPage 原件对照切换范围', () => {
  beforeEach(() => {
    apiMocks.reviewList.mockResolvedValue([task])
    apiMocks.byRule.mockResolvedValue(byRule)
    apiMocks.byDoc.mockResolvedValue(byDoc)
    apiMocks.docTypes.mockResolvedValue({ doc_types: [], check_categories: [] })
    apiMocks.getOcr.mockImplementation(async (docId: string) => {
      if (docId === 'doc-2') return docBrief('doc-2', 'B_费用报销单.pdf')
      if (docId === 'doc-3') return docBrief('doc-3', 'C_行程单.pdf')
      return docBrief('doc-1', 'A_增值税发票.pdf')
    })
  })

  const renderPage = () => {
    const user = userEvent.setup({ delay: null, pointerEventsCheck: 0 })
    render(
      <MemoryRouter>
        <ResultsPage />
      </MemoryRouter>,
    )
    return user
  }

  it('只在那条判定涉及的文件内前后切换，不会溢出到其它判定的文件', async () => {
    const user = renderPage()

    // 打开第一条判定里的原件
    await user.click(await screen.findByText('A_增值税发票.pdf'))
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-1'))

    // 边界＝该判定涉及的两份文件：第一张时不能再往前
    const prev = await findButton('上一张')
    expect(prev).toBeDisabled()
    expect(await screen.findByText('1/2')).toBeInTheDocument()

    // 下一张 = 同一条判定里的第二份文件
    await user.click(await findButton('下一张'))
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-2'))
    expect(await screen.findByText('2/2')).toBeInTheDocument()

    // 已到清单末尾：再点也不会走到另一条判定的 doc-3
    const next = await findButton('下一张')
    expect(next).toBeDisabled()
    await user.click(next)
    expect(apiMocks.getOcr).not.toHaveBeenCalledWith('doc-3')
  })

  it('按文档视图从分组入口打开时，可在当前视图的文档之间切换', async () => {
    const user = renderPage()

    await user.click(await screen.findByText(/按文档视图/))
    // 每个文档分组各有一个「原件对照」，取第一个分组（doc-1）
    const groupEntry = await screen.findAllByText('原件对照')
    await user.click(groupEntry[0])
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-1'))

    expect(await screen.findByText('1/2')).toBeInTheDocument()
    await user.click(await findButton('下一张'))
    await waitFor(() => expect(apiMocks.getOcr).toHaveBeenCalledWith('doc-3'))
    expect(await screen.findByText('2/2')).toBeInTheDocument()
  })
})
