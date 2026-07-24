/** API 客户端：封装所有后端调用。 */

import axios from 'axios'
import type {
  ConstantsResponse,
  ContractBrief,
  ContractDetail,
  ContractUploadResponse,
  DocumentBrief,
  GraphBuildResponse,
  GraphBuildTaskStatus,
  GraphData,
  ReviewResultByDoc,
  ReviewResultByRule,
  ReviewTaskListItem,
  ReviewTaskSummary,
  Rule,
  RuleDocumentImportResponse,
  RuleImportResponse,
  RuleSnapshot,
} from '../types'

const http = axios.create({
  baseURL: '/api',
  timeout: 300000, // OCR + LLM 调用较慢，5 分钟超时
})

// ============ 合同 ============
export const contractsApi = {
  upload: (files: File[]) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    return http.post<ContractUploadResponse>('/contracts/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },
  list: () => http.get<ContractBrief[]>('/contracts').then((r) => r.data),
  get: (id: string) => http.get<ContractDetail>(`/contracts/${id}`).then((r) => r.data),
  delete: (id: string) => http.delete(`/contracts/${id}`).then((r) => r.data),
  updateAliases: (id: string, contract_no: string, alias_list: string[]) =>
    http.put<ContractBrief>(`/contracts/${id}/aliases`, { contract_no, alias_list }).then((r) => r.data),
  updateDocType: (docId: string, doc_type: string) =>
    http.put(`/contracts/documents/${docId}/doc-type`, { doc_type }).then((r) => r.data),
  /** 获取原始文件预览 URL（PDF/图片可直接用 iframe/img 展示） */
  fileUrl: (docId: string) => `/api/contracts/documents/${docId}/file`,
  /** 获取单个文档的 OCR 识别详情 */
  getOcr: (docId: string) =>
    http.get<DocumentBrief>(`/contracts/documents/${docId}/ocr`).then((r) => r.data),
}

// ============ 规则 ============
export const rulesApi = {
  list: (params?: { doc_type?: string; check_category?: string; enabled_only?: boolean }) =>
    http.get<Rule[]>('/rules', { params }).then((r) => r.data),
  create: (data: Partial<Rule>) => http.post<Rule>('/rules', data).then((r) => r.data),
  importBatch: (raw_text: string) =>
    http.post<RuleImportResponse>('/rules/import-batch', { raw_text }).then((r) => r.data),
  /** 从上传的规则描述文档（PDF/EXCEL/WORD/MD）导入规则 */
  importDocument: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http.post<RuleDocumentImportResponse>('/rules/import-document', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },
  update: (id: string, data: Partial<Rule>) => http.put<Rule>(`/rules/${id}`, data).then((r) => r.data),
  delete: (id: string) => http.delete(`/rules/${id}`).then((r) => r.data),
  listSnapshots: () => http.get<RuleSnapshot[]>('/rules/snapshots').then((r) => r.data),
  getSnapshot: (id: string) => http.get<RuleSnapshot>(`/rules/snapshots/${id}`).then((r) => r.data),
}

// ============ 图谱 ============
export const graphApi = {
  build: (auto_confirm_all = false, operator = 'system') =>
    http.post<GraphBuildResponse>('/rules/build-graph', null, { params: { auto_confirm_all, operator } }).then((r) => r.data),
  /** 异步构建图谱（后台线程），返回 task_id */
  buildAsync: (auto_confirm_all = false, operator = 'system') =>
    http.post<{ task_id: string; message: string }>('/rules/build-graph-async', null, {
      params: { auto_confirm_all, operator },
    }).then((r) => r.data),
  /** 查询异步构建进度 */
  getBuildStatus: (taskId: string) =>
    http.get<GraphBuildTaskStatus>(`/rules/build-graph-status/${taskId}`).then((r) => r.data),
  /** 列出最近的构建任务 */
  listBuildTasks: (limit = 20) =>
    http.get<GraphBuildTaskStatus[]>('/rules/build-graph-tasks', { params: { limit } }).then((r) => r.data),
  getLatest: () => http.get<GraphData>('/rules/graph').then((r) => r.data),
  get: (graphId: string) => http.get<GraphData>(`/rules/graph/${graphId}`).then((r) => r.data),
  confirm: (graph_id: string, edits: Array<{ op: string; node_name?: string; source?: string; target?: string; properties?: Record<string, unknown> }>) =>
    http.put<GraphData>('/rules/graph/confirm', { graph_id, edits }).then((r) => r.data),
}

// ============ 审查 ============
export const reviewsApi = {
  start: (contract_id: string, snapshot_id?: string) =>
    http.post<ReviewTaskSummary>('/reviews/start', { contract_id, snapshot_id }).then((r) => r.data),
  list: (params?: { contract_id?: string; limit?: number }) =>
    http.get<ReviewTaskListItem[]>('/reviews', { params }).then((r) => r.data),
  getStatus: (taskId: string) => http.get<ReviewTaskSummary>(`/reviews/${taskId}`).then((r) => r.data),
  byRule: (taskId: string) => http.get<ReviewResultByRule>(`/reviews/${taskId}/by-rule`).then((r) => r.data),
  byDoc: (taskId: string) => http.get<ReviewResultByDoc>(`/reviews/${taskId}/by-doc`).then((r) => r.data),
}

// ============ 常量 ============
export const constantsApi = {
  docTypes: () => http.get<ConstantsResponse>('/constants/doc-types').then((r) => r.data),
  health: () => http.get<{ status: string }>('/health').then((r) => r.data),
}
