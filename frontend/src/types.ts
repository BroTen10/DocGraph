/** 共享 TypeScript 类型定义。 */

/** 规则集：多套规则、合同、图谱的命名空间 */
export interface RuleSet {
  id: string
  name: string
  description: string | null
  doc_types: string[]
  check_categories: string[]
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface RuleSetCreate {
  name: string
  description?: string
  doc_types?: string[]
  is_default?: boolean
}

export interface RuleSetUpdate {
  name?: string
  description?: string
  doc_types?: string[]
  is_default?: boolean
}

export interface ContractBrief {
  id: string
  contract_no: string
  alias_list: string[]
  upload_time: string
  status: string
  file_count: number
  rule_set_id: string
}

export interface DocumentBrief {
  id: string
  file_name: string
  file_type: string
  doc_type: string
  is_required: boolean
  ocr_status: string
  ocr_confidence: number | null
  has_stamp: boolean | null
  extracted_fields: Record<string, unknown>
  /** OCR 识别的原始文本 */
  ocr_text: string | null
  /** 字段提取时间 */
  extracted_at: string | null
}

export interface ContractDetail extends ContractBrief {
  documents: DocumentBrief[]
  note: string | null
}

export interface ContractUploadResponse {
  contract_id: string
  contract_no: string
  alias_list: string[]
  file_count: number
  classified: Array<{
    file_name: string
    doc_type: string
    is_required: boolean
    file_type: string
  }>
  message: string
}

export interface Rule {
  id: string
  doc_type: string
  check_category: string
  rule_text: string
  tolerance: Record<string, unknown>
  enabled: boolean
  priority: number
  rule_set_id: string
  confidence: number | null
  status: string
  confirmed_at: string | null
  confirmed_by: string | null
  updated_at: string
  created_at: string
}

export interface RuleSnapshot {
  id: string
  snapshot_time: string
  rule_count: number
  graph_id: string | null
  node_count: number | null
  edge_count: number | null
  operator: string | null
  note: string | null
  rule_set_id: string
}

export interface RuleImportResponse {
  total: number
  imported: number
  skipped: number
  rules: Array<Record<string, unknown>>
  errors: string[]
}

export interface GraphNode {
  id: string | number
  name: string
  type: string
  properties: Record<string, unknown>
}

export interface GraphEdge {
  id: string | number
  source: string
  target: string
  type: string
  properties: Record<string, unknown>
}

export interface GraphData {
  graph_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  node_count: number
  edge_count: number
}

export interface GraphBuildResponse {
  snapshot_id: string
  graph_id: string
  node_count: number
  edge_count: number
  rule_count: number
  auto_confirmed_count: number
  manual_pending_count: number
  message: string
}

/** 异步图谱构建任务状态 */
export interface GraphBuildTaskStatus {
  task_id: string
  status: 'running' | 'completed' | 'failed'
  progress: number
  stage: string
  operator: string
  auto_confirm_all: boolean
  started_at: string
  completed_at: string | null
  error: string | null
  messages: Array<{
    time: string
    level: 'info' | 'success' | 'warning' | 'error'
    stage: string
    message: string
  }>
  snapshot_id: string | null
  graph_id: string | null
  node_count: number
  edge_count: number
  rule_count: number
  auto_confirmed_count: number
  manual_pending_count: number
}

/** 规则文档导入响应 */
export interface RuleDocumentImportResponse {
  total: number
  imported: number
  skipped: number
  rules: Array<Record<string, unknown>>
  errors: string[]
  extracted_text_preview: string
  extracted_text_length: number
  source_filename: string
}

export interface ReviewTaskSummary {
  id: string
  contract_id: string
  status: string
  progress: number
  stage: string | null
  start_time: string
  end_time: string | null
  error: string | null
  summary: {
    total?: number
    pass?: number
    fail?: number
    unverifiable?: number
  }
}

export interface ReviewTaskListItem {
  id: string
  contract_id: string
  contract_no: string | null
  status: string
  progress: number
  stage: string | null
  start_time: string
  end_time: string | null
  error: string | null
  summary: {
    total?: number
    pass?: number
    fail?: number
    unverifiable?: number
  }
}

export interface ReviewResultItem {
  id: string
  rule_id: string | null
  rule_text: string | null
  doc_type: string | null
  check_category: string | null
  doc_id: string | null
  doc_name: string | null
  result: 'pass' | 'fail' | 'unverifiable'
  issue_desc: string | null
  detail: Record<string, unknown>
  suggestion: string | null
}

export interface ReviewResultByRule {
  task_id: string
  results: ReviewResultItem[]
  summary: Record<string, number>
}

export interface ReviewResultByDoc {
  task_id: string
  docs: Array<{
    doc_id?: string
    file_name: string | null
    doc_type: string | null
    results: ReviewResultItem[]
  }>
  summary: Record<string, number>
}

export interface DocTypeMeta {
  name: string
  is_required: boolean
  is_optional: boolean
  stamp_required: string | null
}

export interface ConstantsResponse {
  doc_types: DocTypeMeta[]
  check_categories: string[]
}

/** OCR 任务:单文档或合同级批量 */
export interface OcrTask {
  id: string
  rule_set_id: string
  scope: 'single_doc' | 'contract_batch'
  doc_id: string | null
  contract_id: string | null
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  stage: string | null
  total_count: number
  done_count: number
  success_count: number
  failed_count: number
  failures: Array<{ doc_id: string; file_name: string; error: string }>
  start_time: string
  end_time: string | null
  error: string | null
  created_at: string
}

/** OCR 任务精简版(列表用) */
export interface OcrTaskBrief {
  id: string
  scope: 'single_doc' | 'contract_batch'
  contract_id: string | null
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  stage: string | null
  total_count: number
  done_count: number
  success_count: number
  failed_count: number
  start_time: string
  end_time: string | null
}

/** 三态结果颜色映射。 */
export const RESULT_COLOR: Record<string, string> = {
  pass: 'green',
  fail: 'red',
  unverifiable: 'gold',
}

export const RESULT_LABEL: Record<string, string> = {
  pass: '通过',
  fail: '不通过',
  unverifiable: '无法核验',
}
