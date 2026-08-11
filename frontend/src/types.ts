/** 共享 TypeScript 类型定义。 */

/** 规则集：多套规则、合同、图谱的命名空间 */
export interface RuleSet {
  id: string
  name: string
  description: string | null
  doc_types: string[]
  check_categories: string[]
  use_default_skill: boolean
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface RuleSetCreate {
  name: string
  description?: string
  doc_types?: string[]
  use_default_skill?: boolean
  is_default?: boolean
}

export interface RuleSetUpdate {
  name?: string
  description?: string
  doc_types?: string[]
  use_default_skill?: boolean
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

export interface DefectItem {
  type: string
  severity: 'error' | 'warning' | 'info'
  description: string
  rule_index?: number | null
  related_rule_ids?: string[] | null
}

export interface ConflictReport {
  total_defects: number
  by_severity: Record<string, number>
  defects: DefectItem[]
}

export interface ConflictItem {
  rule_ids: string[]
  type: string
  severity: string
  description: string
}

export interface ConflictDetectionResponse {
  total_conflicts: number
  affected_rules: number
  conflicts: ConflictItem[]
}

export interface Rule {
  id: string
  doc_type: string | null
  check_category: string | null
  rule_text: string
  tolerance: Record<string, unknown>
  enabled: boolean
  priority: number
  rule_set_id: string
  confidence: number | null
  status: string
  defects: DefectItem[]
  confirmed_at: string | null
  confirmed_by: string | null
  updated_at: string
  created_at: string
  structure?: Record<string, unknown> | null
  scope?: Record<string, unknown> | null
  intents?: string[]
  provenance?: Record<string, unknown> | null
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
  conflict_report: ConflictReport | null
  conflict_detected?: number
  new_doc_types?: string[]
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

/** 批次 10 Phase D：图谱本体层（文档类型/检查意图/规则） */
export interface GraphOntology {
  graph_id: string
  doc_types: Array<{ name: string; props: Record<string, unknown>; fields: string[] }>
  check_intents: Array<{ name: string; props: Record<string, unknown>; rule_count: number }>
  rules: Array<{ name: string; props: Record<string, unknown> }>
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
  conflict_report: ConflictReport | null
  extracted_text_preview: string
  extracted_text_length: number
  source_filename: string
}

/** 异步导入任务状态与进度（前端轮询用） */
export interface ImportTask {
  task_id: string
  rule_set_id: string
  status: 'pending' | 'extracting' | 'parsing' | 'importing' | 'conflict' | 'done' | 'error'
  message: string
  file_name: string
  total_chunks: number
  parsed_chunks: number
  total_rules: number
  imported_rules: number
  import_errors: number
  conflict_total: number
  conflict_done: number
  conflict_found: number
  result: RuleImportResponse | null
  error: string | null
  created_at: string
  updated_at: string
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
  /** 批次 9：问题状态机（open/confirmed/fixed/closed）、严重度（high/medium/low）、偏离度、图谱实体关联 */
  status?: string
  status_history?: Array<{ status: string; at: string; by?: string | null; note?: string | null }>
  severity?: 'high' | 'medium' | 'low' | null
  deviation?: { kind: 'percent' | 'days'; value?: number | null; abs?: number; src?: unknown; tgt?: unknown } | null
  graph_source?: string | null
  graph_target?: string | null
  /** 批次 10 Phase C：结果来源（graph/llm/legacy）与 LLM 置信度 */
  source?: string | null
  confidence?: number | null
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
  stamp_required: string | null
  key_fields: string[]
  business_meaning: string | null
  has_sample: boolean
}

export interface ConstantsResponse {
  doc_types: DocTypeMeta[]
  check_categories: string[]
  /** 批次 3-5：齐套性检查项名称由后端单源下发 */
  completeness_category?: string
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

/** 批次 9：问题严重度颜色与文案。 */
export const SEVERITY_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'blue',
}

export const SEVERITY_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

// ============ 规则解析 Skill 类型 ============

export interface RuleParseSkillContent {
  prompt_instructions: string[]
  field_mappings: Record<string, Record<string, string>>
  defaults: Record<string, unknown>
  validations: Array<{ field: string; rule: string; severity?: string; message?: string }>
  text_preprocessing: Array<{ type: string; pattern?: string; replacement?: string; extraction?: string; description?: string }>
  term_normalization: Record<string, string[]>
  domain_context: { glossary?: Record<string, string>; common_patterns?: string[] }
}

export interface RuleParseSkill {
  id: string
  rule_set_id: string | null
  parent_id: string | null
  name: string
  description: string | null
  is_builtin: boolean
  enabled: boolean
  priority: number
  content: RuleParseSkillContent
  content_yaml: string
  version: number
  created_at: string
  updated_at: string
}

export interface RuleParseSkillCreate {
  name: string
  description?: string
  enabled?: boolean
  priority?: number
  content_yaml?: string
  content?: RuleParseSkillContent
}

export interface RuleParseSkillUpdate {
  name?: string
  description?: string
  enabled?: boolean
  priority?: number
  content_yaml?: string
  content?: RuleParseSkillContent
}

export interface SkillLearnRequest {
  rule_id?: string
  skill_id?: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  note?: string
}

export interface SkillLearnResponse {
  success: boolean
  skill: RuleParseSkill
  added_instructions: string[]
}

// ============ 文档类型管理 ============

export interface DocTypeItem {
  id: string
  name: string
  description: string | null
  key_fields: string[]
  stamp_required: string | null
  business_meaning: string | null
  has_sample: boolean
  source: string
  status: string
  created_at: string | null
  updated_at: string | null
}

export interface DocTypeListResponse {
  doc_types: DocTypeItem[]
  total: number
  pending_count: number
}

export interface DocTypeCreate {
  name: string
  description?: string
  key_fields?: string[]
  stamp_required?: string | null
  business_meaning?: string
  source?: string
}

export interface DocTypeUpdate {
  name?: string
  description?: string
  key_fields?: string[]
  stamp_required?: string | null
  business_meaning?: string
}

export interface AnalyzeSampleResult {
  detected_name: string
  description: string
  key_fields: string[]
  stamp_required: string | null
  business_meaning: string
}

export interface NewDocTypeInfo {
  name: string
  id: string | null
  is_new: boolean
}

export interface DetectNewTypesResponse {
  new_types: NewDocTypeInfo[]
  total: number
}
