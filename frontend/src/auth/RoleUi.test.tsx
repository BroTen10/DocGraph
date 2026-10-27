import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider, type AuthContextValue } from './AuthContext'
import { hasPermission } from './permissions'
import type { UserRole } from '../types'
import UploadPage from '../pages/UploadPage'
import RulesPage from '../pages/RulesPage'
import DocTypesPage from '../pages/DocTypesPage'
import GraphPage from '../pages/GraphPage'
import ReviewPage from '../pages/ReviewPage'
import ResultsPage from '../pages/ResultsPage'
import { RuleSetSwitcher } from '../components/RuleSetSwitcher'
import DocumentCompare from '../components/DocumentCompare'

vi.mock('react-pdf', () => ({
  Document: () => null,
  Page: () => null,
  pdfjs: { GlobalWorkerOptions: { workerSrc: '' } },
}))

vi.mock('../components/GraphView', () => ({
  default: () => <div>mock graph</div>,
}))

vi.mock('../context/RuleSetContext', () => {
  const ruleSet = {
    id: 'rs-1',
    name: '审查规则集',
    description: null,
    doc_types: [],
    check_categories: [],
    use_default_skill: true,
    is_default: true,
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
  }
  return {
    useRuleSet: () => ({
      ruleSets: [ruleSet],
      current: ruleSet,
      currentId: 'rs-1',
      loading: false,
      error: null,
      switchTo: vi.fn(),
      create: vi.fn(),
      remove: vi.fn(),
      refresh: vi.fn(),
    }),
  }
})

const apiMocks = vi.hoisted(() => ({
  contractsList: vi.fn(),
  contractsGet: vi.fn(),
  rulesList: vi.fn(),
  snapshots: vi.fn(),
  defectSummary: vi.fn(),
  docTypes: vi.fn(),
  ruleSetGet: vi.fn(),
  docTypeList: vi.fn(),
  graphLatest: vi.fn(),
  ontology: vi.fn(),
  buildTasks: vi.fn(),
  reviewList: vi.fn(),
  skillList: vi.fn(),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    contractsApi: {
      ...actual.contractsApi,
      list: apiMocks.contractsList,
      get: apiMocks.contractsGet,
    },
    rulesApi: {
      ...actual.rulesApi,
      list: apiMocks.rulesList,
      listSnapshots: apiMocks.snapshots,
      getDefectSummary: apiMocks.defectSummary,
    },
    constantsApi: {
      ...actual.constantsApi,
      docTypes: apiMocks.docTypes,
    },
    ruleSetsApi: {
      ...actual.ruleSetsApi,
      get: apiMocks.ruleSetGet,
    },
    docTypesApi: {
      ...actual.docTypesApi,
      list: apiMocks.docTypeList,
    },
    graphApi: {
      ...actual.graphApi,
      getLatest: apiMocks.graphLatest,
      getOntology: apiMocks.ontology,
      listBuildTasks: apiMocks.buildTasks,
    },
    reviewsApi: {
      ...actual.reviewsApi,
      list: apiMocks.reviewList,
    },
    skillsApi: {
      ...actual.skillsApi,
      list: apiMocks.skillList,
    },
  }
})

const VIEWER_PERMISSIONS = [
  'profile.read',
  'profile.update',
  'review.view',
  'rules.view',
  'doc_types.view',
  'graph.view',
]
const REVIEWER_PERMISSIONS = [
  ...VIEWER_PERMISSIONS,
  'review.execute',
  'rules.manage',
  'doc_types.manage',
  'graph.manage',
]
const TENANT_ADMIN_PERMISSIONS = [
  ...REVIEWER_PERMISSIONS,
  'settings.read',
  'settings.update',
  'members.read',
  'members.manage',
  'audit.read',
]

const contractBrief = {
  id: 'contract-1',
  rule_set_id: 'rs-1',
  contract_no: 'CN-2026-001',
  alias_list: [],
  upload_time: '2026-10-24T00:00:00Z',
  status: 'uploaded',
  file_count: 2,
}

const contractDetail = {
  ...contractBrief,
  note: null,
  documents: [],
}

const documentBrief = {
  id: 'doc-1',
  file_name: '报关单.pdf',
  file_type: 'pdf',
  doc_type: '出口报关单',
  is_required: true,
  ocr_status: 'done',
  ocr_confidence: 0.98,
  has_stamp: true,
  extracted_fields: { 合同号: 'CN-2026-001' },
  ocr_text: '合同号 CN-2026-001',
  ocr_layout: { version: 1, pages: [] },
  extracted_at: '2026-10-24T00:00:00Z',
}

const ruleSet = {
  id: 'rs-1',
  name: '审查规则集',
  description: null,
  doc_types: ['出口报关单'],
  check_categories: ['信息准确性'],
  use_default_skill: true,
  is_default: true,
  created_at: '2026-10-24T00:00:00Z',
  updated_at: '2026-10-24T00:00:00Z',
}

function authFor(role: UserRole, permissions: string[]): AuthContextValue {
  return {
    loading: false,
    initializationError: null,
    user: {
      id: 'u-1',
      name: '测试用户',
      email: 'user@example.com',
      phone_masked: '138****8000',
      role,
    },
    tenant: { id: 'tenant-1', name: '测试租户' },
    role,
    permissions: new Set(permissions),
    login: async () => role,
    logout: async () => undefined,
    refresh: async () => undefined,
    hasPermission: (permission) => permissions.includes(permission),
  }
}

function renderWithAuth(
  ui: ReactNode,
  role: UserRole,
  permissions: string[],
) {
  return render(
    <MemoryRouter>
      <AuthProvider initialAuth={authFor(role, permissions)}>
        {ui}
      </AuthProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.contractsList.mockResolvedValue([contractBrief])
  apiMocks.contractsGet.mockResolvedValue(contractDetail)
  apiMocks.rulesList.mockResolvedValue([])
  apiMocks.snapshots.mockResolvedValue([])
  apiMocks.defectSummary.mockResolvedValue({
    total_rules: 0,
    healthy: 0,
    conflict: 0,
    error: 0,
    warning: 0,
    info: 0,
  })
  apiMocks.docTypes.mockResolvedValue({
    doc_types: [{
      name: '出口报关单',
      stamp_required: '报关行用印',
      key_fields: [],
      aliases: [],
      business_meaning: null,
      has_sample: false,
    }],
    check_categories: ['信息准确性'],
    completeness_category: '齐套性',
  })
  apiMocks.ruleSetGet.mockResolvedValue(ruleSet)
  apiMocks.docTypeList.mockResolvedValue({
    doc_types: [],
    total: 0,
    pending_count: 0,
  })
  apiMocks.graphLatest.mockResolvedValue({
    graph_id: 'graph-1',
    nodes: [],
    edges: [],
    node_count: 0,
    edge_count: 0,
  })
  apiMocks.ontology.mockResolvedValue({
    graph_id: 'graph-1',
    doc_types: [],
    check_intents: [],
    rules: [],
  })
  apiMocks.buildTasks.mockResolvedValue([])
  apiMocks.reviewList.mockResolvedValue([])
  apiMocks.skillList.mockResolvedValue([])
})

describe('role-aware review UI', () => {
  it('renders upload as read-only for viewers', async () => {
    renderWithAuth(<UploadPage />, 'viewer', VIEWER_PERMISSIONS)

    expect(await screen.findByText('合同文档浏览')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '开始上传' })).not.toBeInTheDocument()
    expect(screen.queryByText('全部 OCR')).not.toBeInTheDocument()
    expect(screen.getByText('合同详情')).toBeInTheDocument()
  })

  it.each([
    ['reviewer', REVIEWER_PERMISSIONS],
    ['tenant_admin', TENANT_ADMIN_PERMISSIONS],
  ] as const)('allows %s to use upload actions', async (role, permissions) => {
    renderWithAuth(<UploadPage />, role, permissions)

    expect(await screen.findByRole('button', { name: '开始上传' })).toBeInTheDocument()
    expect(screen.getByText('上传合同文件夹')).toBeInTheDocument()
  })

  it('hides rule mutation controls from viewers but keeps read navigation', async () => {
    renderWithAuth(<RulesPage />, 'viewer', VIEWER_PERMISSIONS)

    expect(await screen.findByText('规则管理')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新增规则' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '批量导入' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '检测冲突' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /前往构建图谱/ })).toBeInTheDocument()
  })

  it('allows reviewers to manage rules', async () => {
    renderWithAuth(<RulesPage />, 'reviewer', REVIEWER_PERMISSIONS)

    expect(await screen.findByRole('button', { name: /新增规则/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /批量导入/ })).toBeInTheDocument()
  })

  it('keeps the skill tab read-only for viewers', async () => {
    renderWithAuth(<RulesPage />, 'viewer', VIEWER_PERMISSIONS)

    await screen.findByText('规则管理')
    await userEvent.click(screen.getByRole('tab', { name: '解析 Skill' }))
    expect(screen.queryByRole('button', { name: /新建 Skill/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /纠偏引导/ })).not.toBeInTheDocument()
  })

  it('hides document type writes from viewers', async () => {
    renderWithAuth(<DocTypesPage />, 'viewer', VIEWER_PERMISSIONS)

    expect(await screen.findByText('文档类型管理')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /新建文档类型/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /上传样例分析/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /刷新/ })).toBeInTheDocument()
  })

  it('hides graph mutations from viewers and shows them to reviewers', async () => {
    const { unmount } = renderWithAuth(<GraphPage />, 'viewer', VIEWER_PERMISSIONS)
    expect(await screen.findByText('知识图谱')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /构建图谱/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /确认生效/ })).not.toBeInTheDocument()
    unmount()

    renderWithAuth(<GraphPage />, 'reviewer', REVIEWER_PERMISSIONS)
    expect(await screen.findByRole('button', { name: /构建图谱/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认生效/ })).toBeInTheDocument()
  })

  it('hides review execution from viewers', async () => {
    renderWithAuth(<ReviewPage />, 'viewer', VIEWER_PERMISSIONS)

    expect(await screen.findByText('审查执行')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /开始审查/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /刷新合同/ })).toBeInTheDocument()
  })

  it('lets reviewers start review and keeps result pages viewable', async () => {
    const { unmount } = renderWithAuth(<ReviewPage />, 'reviewer', REVIEWER_PERMISSIONS)
    expect(await screen.findByRole('button', { name: /开始审查/ })).toBeInTheDocument()
    unmount()

    renderWithAuth(<ResultsPage />, 'viewer', VIEWER_PERMISSIONS)
    expect(await screen.findByText('暂无审查任务')).toBeInTheDocument()
  })

  it('hides rule-set creation and deletion from viewers', async () => {
    renderWithAuth(<RuleSetSwitcher />, 'viewer', VIEWER_PERMISSIONS)

    expect(await screen.findByText('审查规则集')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新建规则集' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除当前规则集' })).not.toBeInTheDocument()
  })

  it('renders OCR field editing as read-only for viewers', async () => {
    render(
      <DocumentCompare
        doc={documentBrief}
        fileUrl="/api/contracts/documents/doc-1/file"
        editable={false}
      />,
    )

    expect(await screen.findByText('OCR 识别结果')).toBeInTheDocument()
    expect(screen.queryByText('修正字段')).not.toBeInTheDocument()
    expect(screen.queryByText('保存类型')).not.toBeInTheDocument()
  })

  it('uses execute permission for review mutations', () => {
    expect(hasPermission('viewer', 'review.execute')).toBe(false)
    expect(hasPermission('reviewer', 'review.execute')).toBe(true)
    expect(hasPermission('tenant_admin', 'review.execute')).toBe(true)
  })
})
