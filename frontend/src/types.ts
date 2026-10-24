/** 共享 TypeScript 类型定义。 */

export type TenantRole = 'tenant_admin' | 'reviewer' | 'viewer'
export type UserRole = TenantRole | 'platform_admin'

export type Permission =
  | 'profile.read'
  | 'profile.update'
  | 'review.view'
  | 'review.execute'
  | 'rules.view'
  | 'rules.manage'
  | 'doc_types.view'
  | 'doc_types.manage'
  | 'graph.view'
  | 'graph.manage'
  | 'settings.read'
  | 'settings.update'
  | 'members.read'
  | 'members.manage'
  | 'audit.read'
  | 'platform.registrations.read'
  | 'platform.registrations.manage'
  | 'platform.tenants.read'
  | 'platform.tenants.manage'

export interface AuthUser {
  id: string
  name: string
  email: string
  phone_masked: string
  role: UserRole | null
}

export interface TenantInfo {
  id: string
  name: string
}

export interface AuthSessionResponse {
  message: string
  csrf_token: string
  user: AuthUser
  tenant: TenantInfo | null
  role: UserRole | null
  permissions: Permission[]
}

export interface RegisterTenantRequest {
  organization_name: string
  admin_name: string
  email: string
  phone: string
  password: string
  accept_terms: boolean
}

export interface RegisterTenantResponse {
  message: string
  status: string
}

export interface VerifyEmailResponse {
  message: string
  status: string
}

export interface ForgotPasswordResponse {
  message: string
}

export interface ResetPasswordResponse {
  message: string
}

export interface AcceptInviteResponse {
  message: string
}

export interface Member {
  id: string
  name: string
  email: string
  phone_masked: string
  role: TenantRole
  status: 'active' | 'disabled'
  joined_at: string | null
  created_at: string
  updated_at: string
}

export interface Invitation {
  id: string
  name: string
  email: string
  phone_masked: string
  role: TenantRole
  status: 'invited' | 'resent' | 'accepted' | 'revoked'
  expires_at: string | null
  created_at: string
}

export interface MemberListResponse {
  members: Member[]
  invitations: Invitation[]
}

export interface InviteMemberRequest {
  name: string
  email: string
  phone: string
  role: TenantRole
}

export interface MemberUpdateRequest {
  role?: TenantRole
  status?: 'active' | 'disabled'
}

export interface RevealedPhoneResponse {
  phone: string
}

export interface RegistrationReview {
  id: string
  organization_name: string
  admin_name: string
  email: string
  phone_masked: string
  status: string
  email_verified_at: string | null
  terms_accepted_at: string | null
  review_note: string | null
  rejection_reason: string | null
  created_at: string
}

export interface RegistrationListResponse {
  registrations: RegistrationReview[]
  total: number
}

export interface ReviewActionResponse {
  message: string
  tenant_id: string | null
  tenant_status: string | null
  admin_role: TenantRole | null
  status: string
}

export interface PlatformTenant {
  id: string
  name: string
  status: string
  approved_at: string | null
  created_at: string
  member_count: number
}

export interface TenantListResponse {
  tenants: PlatformTenant[]
  total: number
}

export interface TenantStatusResponse {
  id: string
  status: string
}

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
  /** OCR 文本行坐标（相对页面 0-1，扫描 PDF/图片定位用） */
  ocr_layout: OcrLayout
  /** 字段提取时间 */
  extracted_at: string | null
}

export interface OcrLayoutLine {
  text: string
  /** [left, top, right, bottom]，均为相对页面宽高的 0-1 比例 */
  bbox: [number, number, number, number]
}

export interface OcrLayoutPage {
  page: number
  lines: OcrLayoutLine[]
}

export interface OcrLayout {
  version?: number
  pages?: OcrLayoutPage[]
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
  rule_code?: string | null
  related_rule_ids?: string[] | null
  related_rule_codes?: string[] | null
  /** 处理状态：resolved = 用户/系统已认可（确认规则即视为认可）；缺省 = 未处理 */
  resolution?: 'open' | 'resolved' | null
  /** 处理时间（ISO8601） */
  resolved_at?: string | null
  /** 处理来源：用户确认 / 导入自动确认 / 冲突消除 / 历史迁移 */
  resolved_by?: string | null
  /** 处理说明 */
  resolution_note?: string | null
}

export interface ConflictReport {
  total_defects: number
  by_severity: Record<string, number>
  defects: DefectItem[]
}

export interface ConflictItem {
  rule_ids: string[]
  rule_codes: string[]
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
  rule_no: number
  rule_code: string
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
  import_warnings?: string[]
  source_coverage?: {
    expected_rows: number
    covered_rows: number
    missing_rows: string[]
    unexpected_rows: string[]
  } | null
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
  import_warnings?: string[]
  source_coverage?: RuleImportResponse['source_coverage']
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
  /** 本条审查结果涉及的所有可打开原件（单文档规则 1 项，跨文件比对 2 项及以上） */
  related_docs?: Array<{
    doc_id: string
    file_name: string
    doc_type: string | null
  }>
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
  aliases?: string[]
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
  /** 一期:本次识别的生效快照(使用的增强点与提示词口径,profile=type 表示走了增强点) */
  applied_config?: {
    profile?: 'type' | 'base'
    doc_types?: Record<
      string,
      {
        base_fields?: string[]
        extra_fields?: string[]
        instructions?: string | null
        field_list?: string[]
      }
    >
  }
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
  aliases: string[]
  field_aliases?: Record<string, unknown>
  stamp_required: string | null
  business_meaning: string | null
  /** 一期 OCR 增强点:类型专属抽取指令(追加到该类型的 OCR 提示词) */
  ocr_extract_instructions?: string | null
  /** 一期 OCR 增强点:备注内关键字段清单(以「备注-」开头) */
  ocr_extra_fields?: string[]
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
  aliases?: string[]
  field_aliases?: Record<string, unknown>
  stamp_required?: string | null
  business_meaning?: string
  ocr_extract_instructions?: string | null
  ocr_extra_fields?: string[]
  source?: string
}

export interface DocTypeUpdate {
  name?: string
  description?: string
  key_fields?: string[]
  aliases?: string[]
  field_aliases?: Record<string, unknown>
  stamp_required?: string | null
  business_meaning?: string
  ocr_extract_instructions?: string | null
  ocr_extra_fields?: string[]
}

/** 某文档类型的 OCR 增强点与最终字段清单(基础字段 + 备注类补充字段) */
export interface OcrProfileItem {
  doc_type: string
  base_fields: string[]
  extra_fields: string[]
  instructions: string | null
  field_list: string[]
}

export interface AnalyzeSampleResult {
  detected_name: string
  description: string
  key_fields: string[]
  stamp_required: string | null
  business_meaning: string
}

// ============ 系统设置 ============

export interface SettingsItem {
  key: string
  label: string
  group: string
  description: string
  kind: 'text' | 'number' | 'boolean'
  value: unknown
  is_default: boolean
}

export interface SettingsResponse {
  settings: SettingsItem[]
}

export interface PromptOptimizeResult {
  key: string
  suggested: string
  reasoning: string
  error?: string
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

/**
 * 版本信息：后端 `GET /api/version` 与前端构建期 `__BUILD_INFO__` 共用同一结构，
 * 便于把"本机开发环境"与"服务器正式环境"放在一起对比。
 */
export interface BuildInfo {
  /** 来源：backend | frontend */
  kind: string
  name: string
  /** 人类版本号，来自仓库根 VERSION 文件 */
  version: string
  /** 版本号 + 提交 + dirty，如 0.3.0+4b00ac1.dirty */
  label: string
  /** git describe 输出，如 v0.2.0-36-g4b00ac1 */
  describe: string | null
  /** 最近一次发版 tag */
  tag?: string | null
  /** 短提交号（7 位） */
  commit: string | null
  /** 完整提交号（后端 runtime-git 时提供） */
  commit_full?: string | null
  /** 提交时间（ISO 8601） */
  commit_date?: string | null
  branch: string | null
  /** 工作区是否有未提交改动；无法判断时为 null */
  dirty: boolean | null
  /** dev=从 git 工作区运行；prod=deploy.sh 构建的镜像；unknown=都没有 */
  build_env: string
  /** 构建时间（ISO 8601）；本机开发时前端值等于 dev server 启动时间 */
  built_at: string | null
  /** 版本信息来源：build-args | runtime-git | version-file | unknown */
  source: string
}
