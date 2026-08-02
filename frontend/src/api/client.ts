/** API 客户端：封装所有后端调用。所有数据查询均带 rule_set_id 做隔离。 */

import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import type {
  AnalyzeSampleResult,
  ConflictDetectionResponse,
  ConstantsResponse,
  ContractBrief,
  ContractDetail,
  ContractUploadResponse,
  DetectNewTypesResponse,
  DocTypeCreate,
  DocTypeItem,
  DocTypeListResponse,
  DocTypeUpdate,
  DocumentBrief,
  GraphBuildTaskStatus,
  GraphData,
  GraphOntology,
  ImportTask,
  OcrTask,
  OcrTaskBrief,
  ReviewResultByDoc,
  ReviewResultByRule,
  RuleParseSkill,
  RuleParseSkillCreate,
  RuleParseSkillUpdate,
  ReviewTaskListItem,
  ReviewTaskSummary,
  Rule,
  RuleImportResponse,
  RuleSet,
  RuleSetCreate,
  RuleSetUpdate,
  RuleSnapshot,
  SkillLearnRequest,
  SkillLearnResponse,
} from '../types'

const http = axios.create({
  baseURL: '/api',
  // 批次 5-15：默认超时收紧到 2 分钟；上传/解析类长链路按调用覆盖（见各上传函数）
  timeout: 120000,
})

/** 批次 5-15：GET 请求超时/网络错误自动重试一次（幂等安全；POST 不重试防重复提交） */
const retriedGet = new WeakSet<InternalAxiosRequestConfig>()
http.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const cfg = error.config
    const isGet = (cfg?.method ?? '').toLowerCase() === 'get'
    if (
      cfg &&
      !retriedGet.has(cfg) &&
      isGet &&
      !error.response &&
      (error.code === 'ECONNABORTED' || error.code === 'ERR_NETWORK' || error.code === 'ETIMEDOUT')
    ) {
      retriedGet.add(cfg)
      await new Promise((r) => setTimeout(r, 600))
      return http.request(cfg)
    }
    return Promise.reject(error)
  },
)

/** 批次 5-15：上传/解析类长链路超时（单位 ms） */
const LONG_TIMEOUT = 600000

/** 从任意异常中提取用户可读消息（后端 FastAPI 错误结构优先）。批次 5-9。 */
export function getErrorMessage(e: unknown, fallback = '操作失败'): string {
  if (e && typeof e === 'object') {
    const maybe = e as {
      response?: { data?: { detail?: unknown } }
      message?: unknown
      error?: unknown
    }
    if (maybe.response?.data?.detail) return String(maybe.response.data.detail)
    if (typeof maybe.message === 'string') return maybe.message
    if (typeof maybe.error === 'string') return maybe.error
  }
  if (typeof e === 'string') return e
  return fallback
}

/** 提取后端返回的 detail 字段（原始值），供需要按内容判断的调用方使用。 */
export function getErrorDetail(e: unknown): unknown {
  if (e && typeof e === 'object') {
    return (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  }
  return undefined
}

/** 判断是否为 antd Form 校验错误（含 errorFields）。 */
export function isFormValidationError(e: unknown): boolean {
  return !!e && typeof e === 'object' && Array.isArray((e as { errorFields?: unknown }).errorFields)
}

// ============ 规则集 ============
export const ruleSetsApi = {
  list: () => http.get<RuleSet[]>('/rule-sets').then((r) => r.data),
  get: (id: string) => http.get<RuleSet>(`/rule-sets/${id}`).then((r) => r.data),
  create: (data: RuleSetCreate) => http.post<RuleSet>('/rule-sets', data).then((r) => r.data),
  update: (id: string, data: RuleSetUpdate) =>
    http.put<RuleSet>(`/rule-sets/${id}`, data).then((r) => r.data),
  delete: (id: string) => http.delete(`/rule-sets/${id}`).then((r) => r.data),
  setDefault: (id: string) =>
    http.post<RuleSet>(`/rule-sets/${id}/set-default`).then((r) => r.data),
}

// ============ 合同 ============
export const contractsApi = {
  upload: (ruleSetId: string, files: File[], signal?: AbortSignal) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    return http
      .post<ContractUploadResponse>('/contracts/upload', form, {
        params: { rule_set_id: ruleSetId },
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: LONG_TIMEOUT,
        signal,
      })
      .then((r) => r.data)
  },
  list: (ruleSetId: string) =>
    http.get<ContractBrief[]>('/contracts', { params: { rule_set_id: ruleSetId } }).then((r) => r.data),
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
  list: (ruleSetId: string, params?: { doc_type?: string; check_category?: string; enabled_only?: boolean; defect_severity?: string; only_confirmed?: boolean }) =>
    http.get<Rule[]>('/rules', { params: { rule_set_id: ruleSetId, ...params } }).then((r) => r.data),
  create: (ruleSetId: string, data: Partial<Rule>) =>
    http.post<Rule>('/rules', data, { params: { rule_set_id: ruleSetId } }).then((r) => r.data),
  importBatch: (ruleSetId: string, raw_text: string, skill_ids?: string[]) =>
    http
      .post<RuleImportResponse>('/rules/import-batch', { raw_text, skill_ids: skill_ids || [] }, { params: { rule_set_id: ruleSetId } })
      .then((r) => r.data),
  /** 从上传的规则描述文档（PDF/EXCEL/WORD/MD）导入规则（异步任务模式，返回 task_id） */
  importDocument: (ruleSetId: string, file: File, skill_ids?: string[], signal?: AbortSignal) => {
    const form = new FormData()
    form.append('file', file)
    const params: Record<string, string> = { rule_set_id: ruleSetId }
    if (skill_ids && skill_ids.length) {
      params.skill_ids = skill_ids.join(',')
    }
    return http
      .post<ImportTask>('/rules/import-document', form, {
        params,
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: LONG_TIMEOUT,
        signal,
      })
      .then((r) => r.data)
  },
  /** 轮询导入任务进度与结果 */
  getImportTask: (taskId: string) =>
    http.get<ImportTask>(`/rules/import-tasks/${taskId}`).then((r) => r.data),
  update: (id: string, data: Partial<Rule>) => http.put<Rule>(`/rules/${id}`, data).then((r) => r.data),
  delete: (id: string) => http.delete(`/rules/${id}`).then((r) => r.data),
  /** 批量删除规则：传 ids 仅删指定；不传则清空该规则集全部规则 */
  batchDelete: (ruleSetId: string, ids?: string[]) =>
    http
      .delete<{ success: boolean; deleted: number }>('/rules', {
        params: {
          rule_set_id: ruleSetId,
          ...(ids && ids.length ? { ids: ids.join(',') } : {}),
        },
      })
      .then((r) => r.data),
  /** 批量确认规则：传 ids 仅确认指定；不传则确认该规则集所有 pending 规则 */
  confirmBatch: (ruleSetId: string, ids?: string[]) =>
    http
      .post<{ success: boolean; confirmed: number; message: string }>(
        '/rules/confirm',
        { ids: ids || null },
        { params: { rule_set_id: ruleSetId } },
      )
      .then((r) => r.data),
  listSnapshots: (ruleSetId: string) =>
    http.get<RuleSnapshot[]>('/rules/snapshots', { params: { rule_set_id: ruleSetId } }).then((r) => r.data),
  getSnapshot: (id: string) => http.get<RuleSnapshot>(`/rules/snapshots/${id}`).then((r) => r.data),
  /** 语义冲突检测 */
  detectConflicts: (ruleSetId: string) =>
    http.post<ConflictDetectionResponse>('/rules/detect-conflicts', null, { params: { rule_set_id: ruleSetId } }).then((r) => r.data),
  /** 缺陷概览统计 */
  getDefectSummary: (ruleSetId: string) =>
    http.get<{ total_rules: number; healthy: number; conflict: number; error: number; warning: number; info: number }>(
      '/rules/defect-summary', { params: { rule_set_id: ruleSetId } },
    ).then((r) => r.data),
}

// ============ 图谱 ============
export const graphApi = {
  /** 异步构建图谱（后台线程），返回 task_id */
  buildAsync: (ruleSetId: string, auto_confirm_all = false, operator = 'system') =>
    http
      .post<{ task_id: string; message: string }>('/rules/build-graph-async', null, {
        params: { auto_confirm_all, operator, rule_set_id: ruleSetId },
      })
      .then((r) => r.data),
  /** 查询异步构建进度 */
  getBuildStatus: (taskId: string) =>
    http.get<GraphBuildTaskStatus>(`/rules/build-graph-status/${taskId}`).then((r) => r.data),
  /** 列出最近的构建任务 */
  listBuildTasks: (limit = 20) =>
    http.get<GraphBuildTaskStatus[]>('/rules/build-graph-tasks', { params: { limit } }).then((r) => r.data),
  getLatest: (ruleSetId: string) =>
    http.get<GraphData>('/rules/graph', { params: { rule_set_id: ruleSetId } }).then((r) => r.data),
  get: (graphId: string) => http.get<GraphData>(`/rules/graph/${graphId}`).then((r) => r.data),
  /** 批次 10 Phase D：查询图谱本体层（文档类型/检查意图/规则） */
  getOntology: (graphId: string) =>
    http.get<GraphOntology>('/rules/graph/ontology', { params: { graph_id: graphId } }).then((r) => r.data),
  confirm: (graph_id: string, edits: Array<{ op: string; node_name?: string; source?: string; target?: string; properties?: Record<string, unknown> }>) =>
    http.put<GraphData>('/rules/graph/confirm', { graph_id, edits }).then((r) => r.data),
}

// ============ 审查 ============
export const reviewsApi = {
  start: (contract_id: string, snapshot_id?: string) =>
    http.post<ReviewTaskSummary>('/reviews/start', { contract_id, snapshot_id }).then((r) => r.data),
  list: (ruleSetId: string, params?: { contract_id?: string; limit?: number }) =>
    http
      .get<ReviewTaskListItem[]>('/reviews', { params: { rule_set_id: ruleSetId, ...params } })
      .then((r) => r.data),
  getStatus: (taskId: string) => http.get<ReviewTaskSummary>(`/reviews/${taskId}`).then((r) => r.data),
  byRule: (taskId: string) => http.get<ReviewResultByRule>(`/reviews/${taskId}/by-rule`).then((r) => r.data),
  byDoc: (taskId: string) => http.get<ReviewResultByDoc>(`/reviews/${taskId}/by-doc`).then((r) => r.data),
}

// ============ OCR ============
export const ocrApi = {
  /** 触发单个文档 OCR(异步,立即返回 task) */
  triggerDoc: (ruleSetId: string, docId: string) =>
    http
      .post<OcrTask>(`/ocr/documents/${docId}`, null, { params: { rule_set_id: ruleSetId } })
      .then((r) => r.data),
  /** 触发合同下所有 pending 文档批量 OCR(异步) */
  triggerContract: (ruleSetId: string, contractId: string) =>
    http
      .post<OcrTask>(`/ocr/contracts/${contractId}`, null, { params: { rule_set_id: ruleSetId } })
      .then((r) => r.data),
  /** OCR 任务列表(按 rule_set_id 过滤,可按 contract_id 进一步过滤) */
  listTasks: (ruleSetId: string, contractId?: string) =>
    http
      .get<OcrTaskBrief[]>('/ocr/tasks', {
        params: { rule_set_id: ruleSetId, contract_id: contractId },
      })
      .then((r) => r.data),
  /** 查询单个 OCR 任务进度 */
  getTask: (taskId: string) => http.get<OcrTask>(`/ocr/tasks/${taskId}`).then((r) => r.data),
}

// ============ 常量 ============
export const constantsApi = {
  docTypes: () => http.get<ConstantsResponse>('/constants/doc-types').then((r) => r.data),
  health: () => http.get<{ status: string }>('/health').then((r) => r.data),
}

export const docTypesApi = {
  /** 获取文档类型列表 */
  list: (params?: { status?: string; source?: string }) =>
    http.get<DocTypeListResponse>('/doc-types', { params }).then((r) => r.data),
  /** 获取单个文档类型 */
  get: (id: string) =>
    http.get<DocTypeItem>(`/doc-types/${id}`).then((r) => r.data),
  /** 创建文档类型 */
  create: (data: DocTypeCreate) =>
    http.post<DocTypeItem>('/doc-types', data).then((r) => r.data),
  /** 更新文档类型 */
  update: (id: string, data: DocTypeUpdate) =>
    http.put<DocTypeItem>(`/doc-types/${id}`, data).then((r) => r.data),
  /** 删除文档类型 */
  delete: (id: string) =>
    http.delete(`/doc-types/${id}`).then((r) => r.data),
  /** 确认新类型（pending → active） */
  confirm: (id: string) =>
    http.post<DocTypeItem>(`/doc-types/${id}/confirm`).then((r) => r.data),
  /** 丢弃新类型 */
  reject: (id: string) =>
    http.post(`/doc-types/${id}/reject`).then((r) => r.data),
  /** 上传样例文档分析 */
  analyzeSample: (file: File, docTypeHint?: string, signal?: AbortSignal) => {
    const formData = new FormData()
    formData.append('file', file)
    const params: Record<string, string> = {}
    if (docTypeHint) params.doc_type_hint = docTypeHint
    return http.post<AnalyzeSampleResult>('/doc-types/analyze-sample', formData, {
      params,
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: LONG_TIMEOUT,
      signal,
    }).then((r) => r.data)
  },
  /** 从规则中检测新文档类型 */
  detectFromRules: (docTypes: string[]) =>
    http.post<DetectNewTypesResponse>('/doc-types/detect-from-rules', { rule_doc_types: docTypes }).then((r) => r.data),
}

// ============ 规则解析 Skill ============
export const skillsApi = {
  list: (ruleSetId: string) =>
    http.get<RuleParseSkill[]>(`/rule-sets/${ruleSetId}/skills`).then((r) => r.data),
  create: (ruleSetId: string, data: RuleParseSkillCreate) =>
    http.post<RuleParseSkill>(`/rule-sets/${ruleSetId}/skills`, data).then((r) => r.data),
  get: (ruleSetId: string, skillId: string) =>
    http.get<RuleParseSkill>(`/rule-sets/${ruleSetId}/skills/${skillId}`).then((r) => r.data),
  update: (ruleSetId: string, skillId: string, data: RuleParseSkillUpdate) =>
    http.put<RuleParseSkill>(`/rule-sets/${ruleSetId}/skills/${skillId}`, data).then((r) => r.data),
  delete: (ruleSetId: string, skillId: string) =>
    http.delete(`/rule-sets/${ruleSetId}/skills/${skillId}`).then((r) => r.data),
  /** 将人工修正规则的经验写回 Skill */
  learn: (ruleSetId: string, data: SkillLearnRequest) =>
    http.post<SkillLearnResponse>(`/rule-sets/${ruleSetId}/skills/learn`, data).then((r) => r.data),
}
