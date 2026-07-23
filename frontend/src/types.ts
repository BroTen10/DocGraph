/** 共享 TypeScript 类型定义。 */

export interface ContractBrief {
  id: string
  contract_no: string
  alias_list: string[]
  upload_time: string
  status: string
  file_count: number
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
