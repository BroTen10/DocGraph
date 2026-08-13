# Progress Log

## Session: 2026-08-02

### 批次 8：图谱执行引擎升级（执行中）
- **Status:** complete
- **Started:** 2026-08-02
- Actions taken:
  - 8-1 意图链执行器：graph_review_service._run_intent_chain（条件预检 → 币别预检 → 断言比对 → 例外豁免 → 结果带证据链）；_check_compare_relationships 改为逐关系走意图链
    - 条件求值 _evaluate_condition / _evaluate_criterion：等于/不等于/包含/包含于/大小比较/存在/为空，ANY 语义
    - 例外豁免 _try_exempt：结构化例外（field/operator/value）命中即豁免；纯文本例外保留 fail + exception_manual_review
  - 8-2 服务层聚合下沉：_collect_field_evidence 统一采集（含缺失证据）+ _aggregate_field 求和；聚合语义 Value 节点声明优先、关系/节点名后缀兜底（节点属性 source_props/target_props 已在批次 7 查询返回，本轮启用）
  - 8-3 防空满足：全部比较器空值 → unverifiable（不隐式通过），reason 区分 field_data_missing / unparseable_numeric / unparseable_date / condition_field_missing / condition_text_only / currency_missing；detail.missing_fields 字段级清单（no_docs_of_type / field_missing + doc_files）；review_service 合并摘要聚合 missing_fields，_UNEXTRACTABLE_PATTERN 扩展覆盖新文案
  - 8-4 容差统一：graph_builder_service 两路径（结构化/LLM）把 tolerance_params + tolerance 写入 Value 节点；执行器节点优先、关系兜底；_cmp_total_eq 去除全局 5%（规则声明 → legacy_param 兼容 → 严格 0%）
  - 规则导入 prompt：exceptions 增加可选 field/operator/value，支持程序化豁免
- Verification:
  - py_compile 4 个改动文件（graph_review_service / graph_builder_service / review_service / rule_import_service）全过；backend/app 全量 py_compile 通过
  - 批次 8 测试 37 项全过：条件命中/未命中/字段缺失/纯文本、结构化例外豁免/未命中保留 fail/纯文本人工确认、节点 ANY 覆盖关系 SUM、多单求和、缺失清单（no_docs_of_type/field_missing/文件名）、unparseable、两侧空值防空满足、总额容差三来源、节点容差优先、构建器节点注入；回归批次 7（EQ-ALL/ANY、NUM-ANY/ALL、DATE 同日、币别 ok/fail、节点名解析）+ 批次 1（CONTAINS 合同号归一化）+ review_service 合并缺失清单
- Files created/modified:
  - backend/app/services/graph_review_service.py（8-1/8-2/8-3/8-4 核心）
  - backend/app/services/graph_builder_service.py（8-4 Value 节点注入容差）
  - backend/app/services/review_service.py（8-3 缺失清单合并 + 模式扩展）
  - backend/app/services/rule_import_service.py（8-1 结构化例外 prompt）
  - task_plan.md / progress.md / findings.md（状态与决策更新）
- Risks / notes:
  - 聚合下沉采用服务层路线：文档值存 Postgres（extracted_fields）、图内仅规则实体，真实 Cypher 聚合需文档值入图（远期，research 已注明）
  - 无任何容差声明的旧规则在"总额等于"下从隐式 5% 变为严格 0%——设计如此（防空满足），如样本出现临界偏差需在规则容差中显式声明
  - 纯文本条件/例外无法程序化核验 → unverifiable/fail + 人工确认标记，不隐式放行
  - 端到端（Neo4j+DB）需后端重启后跑 acceptance_run.py；图谱需重建（build-graph）以让新 Value 节点容差属性生效

### 批次 9：结果闭环与解释性（4 项全部完成）
- **Status:** complete
- **Started:** 2026-08-02
- Actions taken:
  - 9-1 问题状态机（C1）：新建 result_meta.py（RESULT_STATUS_FLOW：open→confirmed→fixed→closed，可回退 open/重开，非法流转拒绝）；ReviewResult 新增 status/status_history/severity/deviation/graph_source/graph_target 列（database.py 6 条 ALTER + 存量 pass 回填 closed）；新增 PATCH /api/reviews/results/{id}/status 接口（含审计历史 note/by）；图谱结果携带 source/target 图谱实体节点名（COMPARE_TO/REQUIRED/MUST_STAMP 三路）
  - 9-2 严重度分级（C2）：result_meta.compute_severity——pass→无、unverifiable→low、齐套性缺件/币别不一致→high、金额/数值偏差≥10% 或时间偏差≥30 天→high、其余 fail→medium；deviation 从 detail 提取（percent/abs/days）
  - 9-3 证据链（C3）：_run_intent_chain 所有分支 detail 携带 evidence{rule(rule_id/rule_text/doc_type/check_category), source/target(节点名+文档明细 doc_name/value+缺失清单)}；旧逻辑路径保留 rule_text/doc_name 顶层来源
  - 序列化：review_service._result_to_item 统一输出新字段（get_results_by_rule/by_doc 共用）；schemas.ReviewResultItem 补 status/severity/deviation/graph_*；合并 unverifiable 摘要继承状态并强制 severity=low
  - 前端：types.ts 补字段 + SEVERITY_COLOR/LABEL；ResultsPage 结果列显示严重度标签、详情抽屉显示状态标签
- Verification:
  - py_compile 后端 app 全量通过；批次 9 测试 35 项全过；批次 8 核心回归 9 项全过；前端 npx tsc -b 通过
  - 状态机：pass→closed / fail,unverifiable→open；流转 open→confirmed/fixed/closed、confirmed→open、closed→open 合法，非法目标拒绝
  - 严重度 11 类场景：总额偏差15%→high、3%→medium、数值偏差50%→high、5%→medium、时间59天→high、9天→medium、齐套性→high、币别→high、印章→medium、unverifiable→low、pass→无
  - 证据链：意图链结果 evidence 含规则文本、source/target 文档明细（文件名+值）、条件证据
- Files created/modified:
  - backend/app/services/result_meta.py（新建：状态机 + 严重度/偏离度）
  - backend/app/models/review_result.py（新增 6 列）
  - backend/app/database.py（增量迁移）
  - backend/app/services/graph_review_service.py（9-1 结果元数据 + 9-3 证据链）
  - backend/app/services/review_service.py（_make_result/_result_to_item/update_result_status/合并摘要）
  - backend/app/schemas/review.py、backend/app/routers/reviews.py（状态流转接口）
  - frontend/src/types.ts、frontend/src/pages/ResultsPage.tsx（严重度/状态展示）
  - task_plan.md / progress.md / findings.md（状态更新）
- Risks / notes:
  - DB 迁移重启自动执行；存量结果 status 回填（pass→closed，fail/unverifiable 保持 open）
  - 状态流转 UI（打开/确认/修复/关闭按钮）仅后端接口 + 状态标签展示，按钮交互留待前端批次
  - severity 阈值（10%/30 天）为业务默认值，可按需调整
  - 图谱实体关联仅存节点名（graph_source/target），未建 Neo4j 违规节点（远期可做持久化 violation 对象）

### 批次 3：规则管理体验优化（6 项全部完成）
- **Status:** complete
- **Started:** 2026-08-02
- Actions taken:
  - 现状核对：3-1/3-2 主体已在 v0.7.0（07-30 未提交内容随 commit 一并入仓）落地——矩阵单元格显示"生效 X / 总数 Y 条"+ 三态色 + Popover 逐条 active 标注；空格"添加"已可预填新增
  - 3-1 补强：无（现有显示已区分 confirmed/enabled，计数不误导）
  - 3-2 补强：空格整格可点击新增（td onClick 替代仅链接可点，移除重复 onClick）
  - 3-3 补强：新增 essentialMissingCells useMemo + 全局 Alert 报警（必备文档×检查项=0 清单，红字提示点击「必检」格补充）；行末齐套率列 v0.7.0 已有
  - 3-4 补强：行悬停 tooltip 补"印章要求（stamp_required）"，凑齐 4 个闲置字段 + 行头 name
  - 3-5：后端 /api/constants/doc-types 新增 completeness_category（constants.CHECK_COMPLETENESS 单源）；前端 FALLBACK 仅作兜底，`'齐套性'` 字面量改由状态引用
  - 3-6：前端 importSkillIds 下拉过滤 enabled 且显示"已停用 N 个不参与导入"；后端 import_rules_with_skills 校验 skill_ids——不存在 → ValueError(400)，停用 → 显式报错（文本 /api/rules/import-batch 走 400，文件导入 async task.error 可见），消除静默丢弃
- Verification:
  - 前端 npm run build 通过（tsc -b + vite build）
  - 后端 main.py / rule_import_service.py py_compile 通过
  - 无产品级逻辑测试需求（本轮改动为 UI 交互 + 后端参数校验）
- Files created/modified:
  - frontend/src/pages/RulesPage.tsx（3-2/3-3/3-4/3-5/3-6）
  - frontend/src/types.ts（ConstantsResponse.completeness_category）
  - backend/app/main.py（3-5 completeness_category 下发）
  - backend/app/services/rule_import_service.py（3-6 skill 校验）
  - task_plan.md / progress.md / findings.md（状态更新）
- Risks / notes:
  - skill 校验为破坏性变更：此前选停用 skill 静默降级为默认配置，现在显式报错（符合"消除静默丢弃"目标）
  - SkillTab 内置 Skill YAML 模板中的检查项文字为内容性说明，不属于类别枚举硬编码，未动
  - 前端改动用例需重启前端 dev/build 后人工目验（矩阵 Alert、tooltip、空格点击、skill 下拉）

### 批次 4：LLM 解析健壮性（3 项全部完成）
- **Status:** complete
- **Started:** 2026-08-02
- Actions taken:
  - 现状核对：规则导入 max_tokens 已为 8192（原 4096）、2000 字符分段按段落/行切（批次 7 前后已调整），4-1 主体已满足
  - 4-1 补强：_split_text 超长表格行优先按单元格 '|' 切分（不切断单元格内容），纯超长无分隔符行仍按字符硬切
  - 4-2：llm_client.py 新增 _repair_truncated_json + _last_closing_index/_close_trailing_string/_balance_brackets 辅助；chat_json JSONDecodeError 时先尝试修复再抛 LLMError，惠及所有 chat_json 调用方（规则导入/图谱构建/冲突检测/OCR）
    - 修复策略：①回退到字符串外最后一个完整 '}'/']' 丢弃截断尾部 ②闭合未闭合字符串 + 补齐缺失括号 ③仅补括号
  - 4-3：_SYSTEM_PROMPT 规则 9 强化（defects 无缺陷省略数组）+ 新增规则 10（rules 每行一条、无空行注释、structure/tolerance 仅在有内容时输出）
- Verification:
  - py_compile llm_client.py / rule_import_service.py 通过
  - 批次 4 测试 21 项全过：截断修复 8 类场景（尾部字段截断/字符串未闭合/尾部元素不完整回退/嵌套对象缺括号/尾部逗号/空数组/完整 JSON 回归/垃圾输入 None）、markdown 代码块提取回归、分块 3 类（表格行按单元格切/超长行硬切/短文本整段）、提示词 3 项
  - 测试暴露并修复 bug：_close_trailing_string 与 _balance_brackets 调用顺序（先补括号会把 '}]}' 吞进未闭合字符串），改为先闭合字符串再补括号
- Files created/modified:
  - backend/app/llm_client.py（4-2 截断 JSON 修复）
  - backend/app/services/rule_import_service.py（4-1 表格行切分 + 4-3 紧凑输出提示）
  - task_plan.md / progress.md / findings.md（状态更新）
- Risks / notes:
  - 修复会丢弃被截断的尾部不完整规则/字段并记录 warning 日志；极端情况（数字中途截断如 "0."）无法安全修复，保持原 LLMError 行为
  - 真实 API 端到端效果需重启后端后跑规则导入验证（含长清单触发截断场景）

### 批次 5：前端工程质量 P2（15 项全部完成）
- **Status:** complete
- **Started:** 2026-08-02
- Actions taken:
  - 5-1 RulesPage 保存加 saving 状态（try/finally），Modal confirmLoading={saving} + okButtonProps disabled，杜绝双击重复提交
  - 5-2 ResultsPage OCR 抽屉 onClose 只关抽屉，afterOpenChange(false) 动画结束后再清空 ocrDoc/ocrDocId/ocrDocName（UploadPage 300ms 延迟清除与 detail 抽屉保留内容此前已满足）
  - 5-3 ResultsPage 内联 `<style>` 删除，row-fail/row-unverifiable 移入 index.css（无重复定义）
  - 5-4 SkillTab 保存前 js-yaml yamlLoad 预校验（语法错误直接拦截并提示，新增依赖 js-yaml + @types/js-yaml）
  - 5-5 现状核对：GraphPage 已无规则文档导入逻辑（只有提示文案"请先到规则管理页导入"），无重复可抽，标记完成
  - 5-6 RulesPage 缺陷 Drawer error/warning Tab 抽公共 renderDefectTable（severity + rowPrefix），消除两份 IIFE 重复表格；冲突 Tab 的 `_: any` 参数类型化为 ConflictGroup
  - 5-7 现状核对：UploadPage 仅一次 import ReloadOutlined（此前已修复）
  - 5-8 GraphView 主渲染 effect 依赖补 height（height 变化触发重渲）
  - 5-9 `catch (e: any)` 41 处清零：client.ts 新增 getErrorMessage/getErrorDetail/isFormValidationError，8 个文件统一替换（DocTypes/Graph/Results/Review/Rules/SkillTab/Upload）
  - 5-10 client.ts 全部 `import('../types')` 内联类型收拢到顶部 import type 块，删除文件中部重复 import
  - 5-11 RuleSetContext 新增 error 状态（加载失败消息，区分加载中/失败），refresh 时清空
  - 5-12 ResultsPage by-doc 表格列抽 docColumns useMemo（ruleColumns 此前已 memo）
  - 5-13 `as any` 全量清零：GraphView 21 处 d3 类型收敛（SimNode/SimLink 泛型、forceLink<SimNode,SimLink>、drag<SVGGElement,SimNode>、linkEndName/linkEndPoint、selectAll 显式泛型）；RulesPage 8 处（defects 直取类型字段、before[keyof Rule] 直取、payload 去 cast、new_doc_types 走类型、originFileObj 用 RcFile 类型状态）；GraphPage 3 处（op 去 cast、Badge status 用 BadgeProps['status'] 类型映射）；UploadPage 1 处（File.lastModified 直取）
  - 5-14 DocumentCompare 新增 useEffect([doc, fileUrl]) 切换文档时清除 highlightTarget
  - 5-15 client.ts 默认 timeout 600s→120s（上传/导入/样例分析覆盖 600s），GET 幂等请求超时/网络错误自动重试一次（WeakSet 防重复），上传三接口支持 AbortSignal 取消
- Verification:
  - 前端 `npx tsc -b` 通过（修复了 forceLink 泛型顺序、selectAll 显式泛型、ConflictGroup 表格类型三处类型错误）
  - `npm run build`（tsc + vite build）通过
  - grep 确认 `as any` / `catch (e: any)` 全量清零
- Files created/modified:
  - frontend/src/api/client.ts（5-9/5-10/5-15 + 错误工具）
  - frontend/src/context/RuleSetContext.tsx（5-11）
  - frontend/src/components/GraphView.tsx（5-8/5-13）
  - frontend/src/components/DocumentCompare.tsx（5-14）
  - frontend/src/pages/{RulesPage,GraphPage,ResultsPage,ReviewPage,UploadPage,DocTypesPage,SkillTab}.tsx（5-1/5-2/5-3/5-4/5-6/5-9/5-12）
  - frontend/src/index.css（5-3）、frontend/src/types.ts（此前批次）、frontend/package.json + package-lock.json（5-4 新增 js-yaml）
  - task_plan.md / progress.md / findings.md（状态更新）
- Risks / notes:
  - js-yaml 预校验只挡语法错误；语义/字段结构仍由后端校验（后端 YAML 错误照常展示）
  - GraphView 类型收敛涉及 d3 泛型，已通过 tsc；运行期行为未变（as any 仅是类型层面）
  - GET 自动重试仅幂等读请求，POST 不重试防重复提交
  - 需重启前端 dev/build 后人工目验交互改动（抽屉动画、保存按钮、缺陷 Tab、YAML 校验）

### 批次 6：工程卫生与收尾（代码项全部完成，环境动作待用户）
- **Status:** complete
- **Started:** 2026-08-02
- Actions taken:
  - 6-1 删除根目录 `nul`（3602B Docker 重定向垃圾）：Windows 设备名需 `\\?\` 全路径 + [System.IO.File]::Delete（Remove-Item 被安全策略拦截）；扫描工作区无其他 nul 类杂散文件
  - 6-2 CORS 收紧：Settings 新增 cors_origins_raw（逗号分隔白名单，默认 localhost:5173/127.0.0.1:5173/3000/8800，生产同源可留空）+ cors_origins property；main.py `allow_origins=["*"]` → settings.cors_origins（allow_credentials=True 保留）
  - 6-3 seed_rules.py 删除僵尸函数 init_seed_rules 及无用 import（sqlalchemy select/Session/Rule/logging），保留 ALL_SEED_RULES 数据（acceptance_run.py 依赖）；模块 docstring 更新
  - 6-4 cleanup_dupes.py BASE 8801→8800（按计划决策修正），py_compile 通过；实际清理需后端在线执行
  - 6-5 静态回归：backend app 全量 py_compile 通过、cleanup_dupes.py + acceptance_run.py 编译通过、前端 npx tsc -b 通过
- Files created/modified:
  - 根目录 nul（删除）；backend/app/config.py + main.py（6-2 CORS）；backend/app/services/seed_rules.py（6-3 僵尸函数）；cleanup_dupes.py（6-4 端口）
  - task_plan.md / progress.md / findings.md（状态更新）
- Risks / notes:
  - **待环境动作（Postgres/Neo4j/后端当前离线，需用户启动服务后执行）**：
    ① `python cleanup_dupes.py <测试规则集2 id>` 清理 220 条重复（脚本默认 id be2467a2-807f-4fde-a7b4-723c9f6192dd）
    ② `python acceptance_run.py` 端到端验收（图谱路径 + 批次 8/9 迁移与意图链/状态/严重度/证据链）
  - CORS 收紧为白名单：若生产前端域名非默认四项，需在 .env 配置 CORS_ORIGINS_RAW
  - 所有批次累计未提交改动保留在工作区（git status 可见），提交决策待用户

### 批次 6 运行级收尾（2026-08-02 服务重启后执行）
- **Status:** complete
- Actions taken:
  - 服务状态核验：后端 8800 在线（uvicorn run.py + reload），Postgres/Neo4j 由后端验证可达（rule-sets 5 个正常返回）
  - 6-4 实测：cleanup_dupes.py be2467a2（测试规则集2）→ 当前 92 条规则、重复组 0，无重复可清（220 条重复此前已清理，目标达成）
  - 6-5 acceptance_run.py 端到端：两轮全绿（第二轮含全部修复）——30 规则灌入/确认、25 文件上传分类、图谱构建 73 节点/60 关系、审查 21 条结果（13 pass / 5 fail / 3 unverifiable，467s 含 OCR），结果验证含批次 8/9 特性：status(open/closed)、severity(medium/low)、detail.evidence（规则+节点+文档明细）、intent_chain、missing_fields
  - **验收暴露并修复 2 个运行时 bug**：
    ① Neo4j 属性嵌套 Map 报错（tolerance_params/condition/exceptions 直接写入失败，批次 1-4 潜伏至首次全链路）：neo4j_client._sanitize_props 写入侧 JSON 序列化 + graph_review_service._props_dict/_props_list 读取侧反序列化
    ② LLM 转换路径缺 rule_text（结果与证据链无规则文本）：graph_builder_service._convert_one_rule 关系属性补 rule_text
  - 验收脚本自身修复：create_ruleset 名称时间戳唯一化 + is_default=False（幂等可重跑）；upload() 返回完整响应（main 需 contract_id 与 classified）
  - 清理：探测/失败运行的临时规则集 4 个已删除（29516781、5ad10899、280fa9b8、7dd51d8d、63a597fc），保留最新验收规则集 e47a755e 作证据
- Verification:
  - acceptance_run.py 退出码 0；02_by_rule.json 抽查 5 条 fail 均含 rule_text + evidence.rule + intent_chain=assertion_compare + severity=medium
  - py_compile 全量通过；前端 tsc 通过
- Files created/modified:
  - backend/app/neo4j_client.py（_sanitize_props）
  - backend/app/services/graph_review_service.py（_props_dict/_props_list）
  - backend/app/services/graph_builder_service.py（_convert_one_rule 补 rule_text）
  - acceptance_run.py（幂等建规则集 + upload 返回值）
  - task_plan.md / progress.md / findings.md（状态更新）
- Risks / notes:
  - 5 条 fail 均为"协议方/付款方/收款方"字符串比对：代理协议.协议方为"深圳市英卡科技有限公司, 深圳中电投资有限公司"（多主体），水单侧为单一主体，按现有相等语义判不一致——符合当前规则语义，若业务上应为"包含关系"需后续调整规则
  - 全部改动仍未提交（git status 可见），提交决策待用户

### 批次 0: 任务清单整理
- **Status:** complete
- **Started:** 2026-08-02
- Actions taken:
  - 读取 planning-with-files SKILL.md 完整说明，确认三件套落盘规范
  - 确认项目根目录无既有规划文件，无 .planning 目录
  - 收集待办线索：.workbuddy/memory 全量（07-27 ~ 07-31 + MEMORY.md）
  - 阅读验收报告（25 条结果、AC5/AC6 未触发、7 个假阳性、5 条建议）
  - 提取前端体检报告全部 28 项 issue 标题，对照记忆确认已修复项
  - 回顾 2026-08-02 图谱 Cypher 深挖结论（6 个观察点）
  - 复核 duplicate_report.md / cleanup_dupes.py 状态
  - 划分 6 个批次，落盘 task_plan.md / findings.md / progress.md
- Files created/modified:
  - task_plan.md（创建）
  - findings.md（创建）
  - progress.md（创建）

### 批次 0.5: 审查算法与图谱审查模式调研
- **Status:** complete
- Actions taken:
  - 落盘用户 3 项决策：①图谱构建前全清 ②多单汇总为主（分批付款） ③清理测试规则集2 重复
  - 发起 4 轮共 26 个搜索查询，覆盖：图谱审查案例、合同审查 AI、文档一致性校验、图推理/GraphRAG、金融图异常检测、规则引擎/符号验证、中文工程化案例
  - 精读 GRAPH-GRPO-LEX（arxiv 2511.06618）合同语义图本体：9 类节点 / 7 类边，Clause 级元模型
  - 定位关键参照：Neo4j graphrag-contract-review、MMLogicInt（物流单据图谱验证）、GAIJ、Luminance/Kira、昆仑数智合规方案、天津大学合同问答、MedRule-KG、Violation Situation Pattern、RWTH NL→Cypher、SHACL/Stardog ICV
  - 产出 11 个可落地设计模式（R1-R4 意图结构 / E1-E4 执行 / C1-C3 结果闭环）
  - 研究结论转化为 task_plan 批次 7-9（审查意图结构升级 / 图谱执行引擎升级 / 结果闭环与解释性）
- Files created/modified:
  - docs/research_审查算法与图谱审查案例调研.md（创建，完整调研文档）
  - task_plan.md（更新：3 项决策落盘 + 批次 0.5 complete + 新增批次 7/8/9）
  - findings.md（更新：调研摘要 + 资源链接）

### 待办（下一轮）
- 等待用户确认执行顺序后启动批次 1（图谱审查链路修复，6 项子任务）
- 批次 1-3 实施时应用研究结论：1-3 多单汇总语义与 8-2 聚合比对联动设计

### 批次 1: 图谱审查链路修复（6 项全部完成）
- **Status:** complete
- Actions taken:
  - 1-1 旧图谱清理：graph_builder_service.build_graph 保存新快照后，按 rule_set_id 查历史 graph_id 逐个 clear_graph（先写后清，失败可回滚）；新增 sqlalchemy select import
  - 1-2 snapshot_id 透传：review_service.start_review 写入 task.snapshot_id；_run_review_pipeline 加参透传；run_graph_review 支持 snapshot_id 优先 + 跨规则集校验
  - 1-3 多单汇总语义（核心行为变化）：
    - _cmp_eq：两侧全数值→求和比较；否则字符串集合任一匹配
    - _cmp_numeric：求和后比较；容差按字段类型解析（金额→百分比、重量→绝对值）
    - _cmp_date：max(src) vs min(tgt) 集合区间比较（同日规则控制边界）
    - _cmp_contains：全部 src 合同号归一化后 ∈ 别名/合同号集合或 tgt 归一化集合
    - 新增 _collect_fields / _to_numeric_list / _parse_dates / _is_amount_field / _is_weight_field / _resolve_tolerance；删除 _first_field、_current_contract
  - 1-4 容差统一：graph_builder 构建时向 COMPARE_TO 关系注入 tolerance_params=rule.tolerance；_cmp_total_eq 优先规则 amount_percent（detail 带 tolerance_source），_cmp_numeric 走 _resolve_tolerance，全局 settings 仅兜底
  - 1-5 ContextVar 容错：_current_contract_opt 返回 Optional，无上下文不抛 RuntimeError
  - 1-6 cypher_guard：validate_cypher_params 加 check_string_values 参数；neo4j execute_write 支持宽松模式，write_rule_graph 传 False；修正 DETONATE→DETACH 笔误
- Verification:
  - py_compile 5 个改动文件全部通过
  - 18 项纯函数测试全部通过（EQ-SUM/EQ-STR/NUM-LE/NUM-GE/DATE 区间/DATE 同日/TOTAL 容差来源×2/CONTAINS 无上下文/GUARD 严格+宽松+键名校验）
  - 排查确认 4 个初测失败均为测试脚本编码问题（PowerShell 管道中文变乱码）与期望计算错误，非代码 bug；改用 UTF-8 临时文件执行后全过
- Files created/modified:
  - backend/app/services/graph_review_service.py（1-2/1-3/1-4/1-5）
  - backend/app/services/review_service.py（1-2）
  - backend/app/services/graph_builder_service.py（1-1/1-4）
  - backend/app/neo4j_client.py（1-6）
  - backend/app/utils/cypher_guard.py（1-6）
  - task_plan.md / progress.md（状态更新）
- Risks / notes:
  - 行为变化：等于/不大于/不小于/日期/包含 全部从"取第一份文档"改为集合级（分批付款语义）；验收报告中的单文档场景行为不变
  - 需后端重启生效（沙箱无法 kill 宿主机进程，需用户手动重启 backend）
  - 端到端验收（图谱路径 + 多单汇总真实数据）留待批次 2 acceptance_run.py 扩展后验证

### 批次 2: OCR 与审查质量提升（4 项全部完成）
- **Status:** complete
- Actions taken:
  - 2-1 OCR prompt 增强：
    - ocr_client.py：字段提取提示升级（金额/价税纯数字、日期 YYYY-MM-DD、数量/重量纯数字、模板字段完整性、合同号逐位核对防末位误识）
    - ocr_service.py：文本型提取 prompt 同步增强 + max_tokens 2048→4096；多页 OCR 合并跳过 None/空值（防空值覆盖已提取字段）
  - 2-2 合同号交叉校验：
    - field_extraction_service.py 新增 cross_validate_contract_no：文件名 ground truth 优先，OCR 归一化号不属于文件名别名时覆盖并记录 __ocr_raw/__source
    - review_service.py OCR 阶段接入（normalize_fields 之后）
    - **额外根治深层 bug**：测试发现 contract_normalizer._MAIN_WITH_BATCH_PATTERN 用 `[-_\s]*`（零分隔符），贪婪回溯把主号末位数字当成分批号吞掉（24HCSP012260253 → 24HCSP01226025 + 3），导致文件名候选含错误号、normalize 选错 canonical。修复：分隔符改 `[-_\s]+`（显式），main_nos 取最长候选
  - 2-3 验收脚本图谱路径：acceptance_run.py 新增 confirm_all_rules（pending→confirmed+enabled，图谱构建前置要求）、build_graph（异步+轮询）、start_review 支持 snapshot_id；main 重写接住 upload 返回值（修复 up 未定义）、先确认规则再构建图谱再审查
  - 2-4 精确计时：main 记录 start_review 前后耗时，输出"审查耗时 Xs（含 OCR；图谱驱动 snapshot=...）"
- Verification:
  - py_compile 6 文件（ocr_client/ocr_service/field_extraction_service/review_service/contract_normalizer/acceptance_run）全部通过
  - 12 项测试全过：文件名不吞末位、分批号/下划线提取、OCR 缺末位候选保留、多候选取最长、覆盖/不覆盖/别名/无文件名/非合同号格式分支
  - 测试过程暴露 contract_normalizer 正则 bug（首测 FAIL override 缺末位）→ 定位根因并修复后全过
- Files created/modified:
  - backend/app/ocr_client.py（2-1）
  - backend/app/services/ocr_service.py（2-1）
  - backend/app/services/field_extraction_service.py（2-2 新增 cross_validate_contract_no）
  - backend/app/services/review_service.py（2-2 接入）
  - backend/app/services/contract_normalizer.py（2-2 额外根治正则 bug）
  - acceptance_run.py（2-3/2-4）
  - task_plan.md / progress.md（状态更新）
- Risks / notes:
  - OCR prompt 增强的实效依赖真实 API（通义千问 VL），需重启后端后跑 acceptance_run.py 端到端确认 AC5/AC6 触发与假阳性消除
  - contract_normalizer 正则修复改变提取行为：无显式分隔符的"主号+数字"不再被拆分为分批号——符合真实合同号写法，若样本中有"24HCSP01226025310"式紧贴分批号写法会被识别为单个主号（可后续按样本验证）
  - 验收脚本图谱路径需要 Neo4j 与 LLM key 可用；build-graph 失败会显式报错

### 批次 7: 审查意图结构升级（5 项全部完成）
- **Status:** complete
- Actions taken:
  - 7-1 structure 数据链路：
    - models/rule.py：新增 structure JSONB（condition/assertion/exceptions 注释文档化）
    - database.py：_run_migrations 加 ALTER TABLE rules ADD COLUMN IF NOT EXISTS structure JSONB
    - schemas/rule.py：RuleBase/RuleUpdate 加 structure 字段（旧规则为 null 兼容）
    - rule_import_service.py：入库透传 structure（清洗 dict；仅当含 assertion 时保存）
  - 7-2 聚合语义：
    - graph_builder_service：_node_name 生成 "类型.字段|SUM|ANY|ALL" 后缀；aggregate 支持 assertion 顶层或 source/target 两处声明（测试暴露不一致后统一）
    - graph_review_service：_parse_field_node 返回 (doc_type, field, aggregate)；_cmp_eq/_cmp_numeric/_cmp_date 支持 ANY/ALL（SUM/默认保持求和）
  - 7-3 角色建模：Field 节点 attributes.role（source/target 的 role 字段）
  - 7-4 币别/单位：currency/unit 写入节点与关系 attributes；_check_currency 审查前置（两侧币别字段与断言币别一致 → ok；缺失 → unverifiable；不一致 → fail）
  - 7-5 导入引擎与图谱构建：
    - rule_import_service._SYSTEM_PROMPT：输出契约加 structure（condition/assertion/exceptions + aggregate/role/currency/unit 说明）
    - graph_builder_service：新增 _convert_structured_rule（断言→Field 节点 + COMPARE_TO 边，确定性，confidence=1.0）；build_graph 分派：齐套/印章 → 程序化 → 有 assertion → 结构化程序化 → 无 structure → LLM
    - _convert_one_rule（LLM 路径）同步注入 condition/exceptions 属性
- Verification:
  - py_compile 6 文件（rule/database/schemas.rule/rule_import_service/graph_builder_service/graph_review_service）全过
  - 批次 7 测试 21 项全过：节点名解析（SUM/ANY/plain）、EQ-ALL 集合一致、NUM-ANY/ALL 边界、DATE-ANY/ALL、币别 ok/fail/unverifiable、结构化转换产出（节点/边/operator/aggregate/unit/tolerance_params/condition/exceptions/置信度）、顶层与子级 aggregate、RuleCreate 接受 structure
  - 批次 1 回归 5 项全过（默认参数向后兼容）
- Files created/modified:
  - backend/app/models/rule.py（7-1）
  - backend/app/database.py（7-1 迁移）
  - backend/app/schemas/rule.py（7-1）
  - backend/app/services/rule_import_service.py（7-1/7-5 prompt+入库）
  - backend/app/services/graph_builder_service.py（7-2/7-3/7-4/7-5）
  - backend/app/services/graph_review_service.py（7-2/7-4 执行）
  - task_plan.md / progress.md（状态更新）
- Risks / notes:
  - structure 为 LLM 可选输出：无 structure 的旧规则仍走 LLM 图谱转换，完全兼容
  - condition（条件预检）与 exceptions（例外豁免）本批次仅保存到图谱属性，执行逻辑留批次 8（E1 意图链）
  - 币别校验依赖文档提取出"币别"字段（FIELD_TEMPLATES 已含）；OCR 提取不到币别时降级 unverifiable（不误报）
  - 需后端重启 + DB 迁移（structure 列自动 ADD，重启时 _run_migrations 执行）
  - 前端规则编辑暂不展示/维护 structure（后续批次可加）

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| 批次 1 纯函数测试（18 项） | 多单金额求和/字符串集合/日期区间/容差来源/无上下文/宽松校验 | 全部符合设计 | 18/18 通过 | ✓ |
| py_compile（5 文件） | graph_review/review/graph_builder/neo4j_client/cypher_guard | 0 错误 | 0 错误 | ✓ |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-08-02 | 体检报告 P2 卡片正则多次匹配为空 | 1-2 | 改用 Select-String 按行提取 issue-title，成功 |
| 2026-08-02 | 批次 8 测试：聚合 ANY 优先用例失败 | 1 | 原因：_run_intent_chain 聚合优先级写成"关系 or 节点"（节点声明被 SUM 短路） | 
| 2026-08-02 | 同上（续） | 2 | 修正为节点属性优先、关系兜底后通过；另修复条件 unknown 未透传内部 reason（condition_text_only） |
| 2026-08-02 | 批次 9 测试：CHECK_TIME_LOGIC 未导入 | 1 | 测试脚本漏导入常量，补充后 35 项全过（非产品代码问题） |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | 批次 0 完成，等待批次 1（图谱审查链路修复）启动 |
| Where am I going? | 批次 1→6 依次执行 |
| What's the goal? | 完成已识别优化任务，分批次实施并全程记录 |
| What have I learned? | 见 findings.md（6 大来源待办全量汇总） |
| What have I done? | 收集线索、划分批次、落盘三件套 |

---
*Update after completing each phase or encountering errors*

### 批次 10：泛化规则体系重构（2026-08-02，设计提案阶段）
- **Status:** 设计完成，待用户确认（未改产品代码）
- Actions taken:
  - 通读 PRD、补充需求集（20260727-20260730）、规则解析控制架构设计、批次 0-9 规划/验收记录
  - 逐层核查代码：constants.py、models/rule.py+rule_set.py+document_type.py、schemas/rule.py、rule_import_service.py、rule_document_import_service.py、rule_parse_engine.py、graph_builder_service.py、graph_review_service.py、review_service.py、suggestion_service.py、ocr_service.py、routers（rule_sets/doc_types/rules）、frontend RulesPage.tsx
  - 定位偏离根因：规则被强制归入"文件类型×检查项"格子（表示层）+ 常量枚举硬编码（本体层）+ 审查无 LLM（执行层）+ 必检格 Alert（UI 层）
  - 产出设计文档 docs/泛化规则体系重构设计.md：规则自描述 / 本体涌现 / 双引擎审查 / 矩阵投影，分 Phase A-E 落地
- Files created/modified:
  - docs/泛化规则体系重构设计.md（新增，设计提案）
  - task_plan.md（批次 10 落盘，pending）
  - findings.md（偏离根因与目标设计摘要）
- Risks / notes:
  - 待用户确认后再进入 Phase A 编码；Phase A 涉及 rules 表迁移，需重启后端生效
  - 关键取舍：精度 vs 泛化——确定性引擎保持权威，LLM 仅补定性/语义场景且低置信走 unverifiable

### 批次 10-1：Phase A 解除规则-格子强绑定（2026-08-02，已完成）
- **Status:** complete（py_compile / tsc+vite / DB 冒烟全绿）
- Actions taken:
  - 后端：rules 表迁移（doc_type/check_category 可空 + scope/intents/provenance）；schemas/rule.py 同步；models/rule.py 更新
  - 导入 v2：_SYSTEM_PROMPT/_USER_PROMPT_TEMPLATE 重写（枚举降级为建议 + ontology 输出 + scope/intents）；校验放宽；派生标签兜底；语义级去重（_structure_signature）；_merge_into_existing 扩展
  - 存量 bug 修复：document_types 模型补齐 category/is_required（真实库有列、ORM 缺失 → 新类型注册一直静默失败）；新类型 key_fields 由 ontology.fields 预填
  - 兼容处理：graph_builder 跳过无类型齐套/印章规则；review_service 旧回退跳过 None 类型；冲突检测空标签回退
  - 前端：RulesPage 移除必检 Alert/红色必检格；矩阵投影化（空标签→整批/全部、未分类）；编辑表单标签可选；types.ts/GraphPage 同步
- Verification:
  - py_compile 全量通过；npm run build（tsc + vite）通过
  - DB 冒烟（临时规则集 + mock LLM）：3 条规则（空标签 + 结构-only）入库、标签派生正确、scope/intents/provenance 落库、二次导入 0 新增（语义去重）、新类型 pending_review 注册 + key_fields 预填、规则集类型更新；测试数据已清理（含两次失败运行残留，人工复核清空）
- Files created/modified:
  - backend/app/models/rule.py、document_type.py、database.py
  - backend/app/schemas/rule.py
  - backend/app/services/rule_import_service.py、rule_conflict_detector.py、graph_builder_service.py、review_service.py
  - frontend/src/types.ts、pages/RulesPage.tsx、pages/GraphPage.tsx
  - task_plan.md / findings.md / progress.md
- Risks / notes:
  - 迁移已对真实库执行（幂等）；后端进程仍是旧代码，需用户重启 backend 生效（重启时 _run_migrations 会补执行，幂等无副作用）
  - 无类型整批规则（scope=ALL）暂不参与确定性图谱执行，留待 Phase D/E；LLM 语义审查（Phase C）负责这类规则的执行

### 批次 10-2/10-3：Phase B 本体闭环 + Phase C 双引擎审查（2026-08-02，已完成）
- **Status:** complete（py_compile / tsc+vite / LLM 审查单测 / 迁移实测全绿）
- Actions taken:
  - Phase B：
    - ocr_service 新增 resolve_field_template（DocumentType.key_fields 优先，constants 兜底），review_service + ocr_task_service 两处调用接入
    - file_classifier 支持动态注册表（name→is_required），contract_service 上传时从 DocumentType 构建并传入；未识别时按注册类型名子串匹配
    - database._seed_doc_types 补齐 category/is_required（与真实库取值对齐）
    - 新规则集零预设确认（前端空数组 + 后端默认 []）
  - Phase C：
    - 新增 llm_review_service.py：定性规则批量审查 + 字符串相等失败语义复核，含置信度护栏（fail≥0.8/pass≥0.6，不足→unverifiable）
    - review_results 迁移新增 source/confidence 列；图引擎结果 source=graph；旧逻辑 source=legacy；LLM source=llm
    - review_service 阶段 2.5 合并引擎 B，异常不影响确定性结果；_make_result_from_llm 构造结果
    - suggestion_service.build_suggestion_llm：LLM 生成建议（证据链上下文），失败回退模板
    - 前端 ResultsPage 来源标签 + 置信度 tooltip；types.ts ReviewResultItem 扩展
- Verification:
  - py_compile 全量通过；npm run build（tsc + vite）通过
  - LLM 审查引擎单测：规则筛选、批量审查归一化、fail 低置信护栏降级、语义兜底三分支（同义→pass/不同→保持fail/不确定→unverifiable）、建议生成 LLM 优先与异常回退，全部通过
  - 迁移对真实库幂等执行，review_results.source/confidence 列确认存在
- Files created/modified:
  - backend/app/services/llm_review_service.py（新增）、suggestion_service.py、ocr_service.py、ocr_task_service.py、file_classifier.py、contract_service.py、review_service.py、graph_review_service.py、database.py、models/review_result.py、schemas/review.py
  - frontend/src/pages/ResultsPage.tsx、types.ts
  - task_plan.md / findings.md / progress.md
- Risks / notes:
  - 后端需重启生效；LLM 审查仅在存在定性规则或字符串相等失败时调用，成本受控
  - 语义复核把"字符串不一致"判为同义需要置信≥0.8；无法确认时降级 unverifiable（人工确认），宁可多标不可漏标

### 批次 10-4：Phase D 图谱本体化（2026-08-02，已完成）
- **Status:** complete（py_compile / tsc+vite / 真实 Neo4j 冒烟全绿）
- Actions taken:
  - graph_builder_service：写图增加本体层（DocumentType/CheckIntent/Rule 节点 + APPLIES_TO/CHECKS/INVOLVES/HAS_FIELD 边），节点/边标注 layer，R 编号与执行层一致；DocumentType 节点属性带描述/字段/业务含义/印章/必备标记
  - neo4j_client：边类型白名单扩展；新增 get_ontology 本体查询
  - graph.py：新增 GET /api/rules/graph/ontology（声明在 /graph/{graph_id} 之前）
  - 前端：GraphPage 图层过滤 Segmented + "本体概览"Tab；GraphView 本体节点配色 + display_name 显示
- Verification:
  - py_compile 全量通过；npm run build（tsc + vite）通过
  - 真实 Neo4j 冒烟：临时规则集（1 空标签结构化规则 + 1 齐套性规则）→ 10 节点/11 边；get_ontology 返回正确的文件类型/检查意图/规则节点；APPLIES_TO/CHECKS/INVOLVES/HAS_FIELD/COMPARE_TO/REQUIRED 六类边全部存在；测后图谱与规则集已清理
- Files created/modified:
  - backend/app/services/graph_builder_service.py、neo4j_client.py、routers/graph.py
  - frontend/src/pages/GraphPage.tsx、components/GraphView.tsx、api/client.ts、types.ts
  - task_plan.md / findings.md / progress.md
- Risks / notes:
  - 旧图无本体层，需重新构建图谱后本体概览才有数据（前端已做空态提示）
  - 本体层与执行层同 graph_id；历史图清理逻辑（clear_graph/clear_all_rule_graphs）无需改动

### 批次 10-5：Phase E 回归验收（2026-08-02，已完成）
- **Status:** complete（回归验收 + 新流程验收全绿）
- Actions taken:
  - 清理并重建后端进程（旧实例存在 uvicorn 双进程/孤儿 worker，代码版本不一致风险，已全杀重启为单实例新代码）
  - 回归 acceptance_run.py：30/30 规则、25 文件、图谱 125 节点/215 关系、审查 38 条（25/6/7）、双引擎来源 graph=22/llm=16、状态闭环正确、12 条 LLM 建议
  - 新流程 acceptance_phase_e_new.py（保留为仓库验收脚本）：零预设规则集→任意规则文本→自动发现新类型（验收确认单 pending_review+key_fields）→本体层建图→双引擎审查→清理，全绿
  - 修复验收暴露的三个问题：schemas/rule.py 缺 import uuid + model_rebuild（import-batch 500）；RuleImportResponse 缺 new_doc_types 字段；scope=ALL 误推导 doc_type="ALL" 误注册类型
- Verification:
  - py_compile 全量通过；两次验收期间后端日志无审查异常
  - 验收数据清理：3 个验收规则集、pending 类型（验收确认单/ALL）、测试上传目录全部删除，DB 无残留
- Files created/modified:
  - acceptance_phase_e_new.py（新增，新流程验收脚本，与 acceptance_run.py 并列）
  - backend/app/schemas/rule.py（import uuid + model_rebuild + new_doc_types）
  - backend/app/services/rule_import_service.py（scope=ALL 归一化/推导修复）、graph_builder_service.py（过滤 ALL）
  - task_plan.md / findings.md / progress.md
- Risks / notes:
  - 后端已运行新代码（隐藏窗口 + 日志 backend_phaseE.log）；后续如再改代码，热重载会重启 worker，验收脚本需在改动完成后一次跑通
  - 多值字符串相等不做 LLM 语义兜底为已知设计边界（确定性结果保留，防止 LLM 误判）

## Session: 2026-08-13

### 全面体检与安全/冗余清理
- **Status:** complete
- Actions taken:
  - 盘点项目目录、Git 跟踪状态与主要目录体积。
  - 运行后端 secret/pattern 扫描、pip-audit、前端 npm audit、ruff F 类检查、tsc --noEmit。
  - 运行 tests/run_graph_rule_tests.py，14/14 通过。
  - 将可再生成缓存/构建产物移动到系统临时目录：frontend/dist、tsconfig.tsbuildinfo、_extracted_imgs、acceptance_output、backend/backend_phaseE.log、backend/backend_phaseE_err.log、项目内 __pycache__（排除 .venv）。
- Verification:
  - git status 清理后仍为干净状态。
  - 图谱规则测试 PASS=14 / FAIL=0。
- Files created/modified:
  - task_plan.md / findings.md / progress.md（追加本批次记录）
- Risks / notes:
  - 本次未修改业务代码，未删除用户业务文档、依赖目录和 .workbuddy。
  - 待办优先级：前端 react-router 升级 > 后端依赖升级与回归 > ruff 清理（至少修 graph_builder_service.py 缺少 Any）。

### 用户确认后的清理与低风险优化
- **Status:** complete
- Actions taken:
  - 清空临时目录；删除 backend/uploads 和 6 个根目录一次性调试脚本。
  - ruff --select F401 --fix 移除 31 个未使用 import；补 graph_builder_service.py 的 Any。
  - 修复 ocr_service/review_service/rule_parse_engine 的未使用变量与变量遮蔽；修复 vite 兜底端口。
- Verification:
  - py_compile 通过，前端 tsc --noEmit 通过。
  - tests/run_graph_rule_tests.py PASS=14 / FAIL=0。
- Files created/modified:
  - backend/app/services/*、backend/app/routers/*、backend/app/models/*、frontend/vite.config.ts 等低风险代码文件。
  - task_plan.md / findings.md / progress.md。
- Risks / notes:
  - 依赖与安全项未动；backend/uploads 已按用户确认删除，若后续还需要原始上传文件，需从资料样本重新上传。
