# Task Plan: DocGraph 优化开发任务清单

## Goal
把项目已识别的优化/开发任务整理为可执行清单，按批次逐步实施（图谱审查链路 → OCR 质量 → 规则管理 → LLM 健壮性 → 前端工程 → 收尾卫生），每批完成即验证、记录并汇报。

## Current Phase
批次 0-9 完成；新增批次 10（泛化规则体系重构）：设计提案完成（docs/泛化规则体系重构设计.md），待用户确认后实施

## Phases

### 批次 0：任务清单整理
- [x] 收集项目全部待办线索（记忆文件 / 验收报告 / 体检报告 / 图谱深挖 / 根目录杂项）
- [x] 按 ROI 与依赖关系划分批次
- [x] 落盘 task_plan.md / findings.md / progress.md
- **Status:** complete

### 批次 0.5：审查算法与图谱审查模式调研（研究项目）
> 用户提出：现行图谱审查过于粗糙，未还原原始规则文件里复杂、有关联的审查意图。需参考外部优秀审查算法设计与图谱审查案例，先研究，再纳入清单。
- [x] 调研外部审查算法/系统（合同审查 AI、文档一致性校验、审计规则引擎）——20+ 案例/论文
- [x] 调研知识图谱用于审查/核验的案例与论文（GraphRAG、图推理、符号验证器、违规持久化模式等）
- [x] 提炼可落地的设计模式（R1-R4 意图结构 / E1-E4 执行 / C1-C3 结果闭环）
- [x] 产出调研文档 docs/research_审查算法与图谱审查案例调研.md，转化为批次 7-9
- **Status:** complete

### 批次 1：图谱审查链路修复（后端）
> 来源：2026-08-02 图谱 Cypher 深挖。纯后端逻辑，范围清晰，为图谱路径验收铺路。
- [x] 1-1 决策已定：**构建前全清**。每次构建前清除该规则集的旧图谱（按快照 graph_id 定位清理，避免误删其他规则集）——实施：先写新图+快照，成功后再清旧图（失败可回滚）
- [x] 1-2 `snapshot_id` 空转：`start_review` 接收 snapshot_id 但 `run_graph_review` 固定取最新快照。改为传入的 snapshot_id 优先（并写入 task.snapshot_id，校验快照属于当前规则集）
- [x] 1-3 决策已定：**多单汇总为主**（分批付款场景）。数值/金额类比较默认按同合同多单求和后比对；时间类按单据集合处理；具体语义实施时按规则意图细化（与批次 0.5 调研联动）
- [x] 1-4 两套容差并存：`总额等于` 用全局 5% 百分比容差，忽略关系 tolerance。统一容差来源（tolerance_params 注入 + 按字段类型解析 + 全局兜底）
- [x] 1-5 `_cmp_contains` ContextVar 依赖：直接调 `run_graph_review`（无合同上下文）会抛 RuntimeError。改为缺失上下文时返回 unverifiable（_current_contract_opt 容错）
- [x] 1-6 cypher_guard 误伤业务字符串：参数值含 MATCH/CREATE 等词被整体拒绝，规则文本写入图谱可能被挡。评估放宽策略（write_rule_graph 走宽松模式，仅校验键名；修正 DETONATE→DETACH 笔误）
- **Status:** complete

**批次 1 验证**：py_compile 全过；18 项纯函数测试全过（多单求和/字符串集合/日期区间/容差来源/无上下文容错/宽松校验）。需后端重启生效；端到端验收留待批次 2 跑 acceptance_run.py 时一并验证。

### 批次 2：OCR 与审查质量提升（验收核心）
> 来源：验收报告 24HCSP012260253。AC5/AC6 未触发、7 个假阳性，ROI 最高。
- [x] 2-1 OCR 字段提取增强：OCR/LLM prompt 增加金额/日期专项指令 + 字段完整性要求 + 合同号逐位核对；文本提取 max_tokens 2048→4096；多页 OCR 合并跳过空值
- [x] 2-2 合同号文件名交叉校验：`cross_validate_contract_no` 以文件名 ground truth 覆盖 OCR 误识（保留 OCR 原值 + source 标记）；**额外根治 contract_normalizer 分批号正则 bug**（`[-_\s]*`→`[-_\s]+` 贪婪回溯吞主号末位，多候选取最长）
- [x] 2-3 图谱路径正式验收：`acceptance_run.py` 增加 confirm_all_rules + build_graph（异步轮询）+ 带快照审查，修复 `up` 未定义隐患
- [x] 2-4 单合同审查精确计时：main 记录审查起止耗时并输出
- **Status:** complete

**批次 2 验证**：py_compile 6 文件全过；12 项归一化/交叉校验测试全过（含根治前失败的用例）。OCR prompt 增强效果需真实 API 端到端验证（重启后端后跑 acceptance_run.py）。

### 批次 3：规则管理体验优化（前端为主）
> 来源：记忆文件 2026-07-30（5 项硬伤）+ 2026-07-29（skill 耦合）。
- [x] 3-1 二维矩阵入桶未过滤：`RulesPage.tsx:174-182` 不分 enabled/confirmed 全入桶，Tag 计数含待确认/禁用规则
- [x] 3-2 空格快捷新增规则
- [x] 3-3 必备格=0 报警 + 齐套率展示
- [x] 3-4 悬停 tooltip 用上 5 个闲置字段（business_meaning/key_fields 等）
- [x] 3-5 CHECK_CATEGORIES 后端单源化（现前后端双硬编码）
- [x] 3-6 skill 停用项 UX：批量导入下拉过滤停用 skill（推荐）或后端 400 校验，消除静默丢弃
- **Status:** complete

**批次 3 验证**：现状核对——3-1（生效/总数分离计数 + 三态色 + Popover 内逐条 active 标注）、3-2（空格点击预填新增，v0.7.0 已有）已满足，本轮补强：空格整格可点（不再只点链接）；3-3 新增全局 Alert 报警（必备格=0 清单，最多列 8 项）；3-4 tooltip 补"印章要求"（凑齐 key_fields/business_meaning/stamp_required/has_sample 4 闲置字段 + 行头 name）；3-5 后端 `/api/constants/doc-types` 新增 completeness_category 单源下发，前端 `'齐套性'` 字面量改为状态引用；3-6 前端下拉过滤停用 skill + 计数提示，后端 `import_rules_with_skills` 对不存在的 skill 报 400、对停用 skill 显式报错（文本/文件导入两路生效）。前端 `npm run build` 通过、后端 py_compile 通过。

### 批次 4：LLM 解析健壮性
> 来源：记忆文件 2026-07-30 排障（第 7 段 JSON 被 max_tokens=4096 截断）。
- [x] 4-1 提高段落 max_tokens 或按表格行切分限流
- [x] 4-2 `_extract_json` 增加截断 JSON 容错修复
- [x] 4-3 提示词要求紧凑输出（每行一条、省略可选字段）
- **Status:** complete

**批次 4 验证**：现状核对——max_tokens 已从 4096 提到 8192（规则导入）、2000 字符分段按段落/行切（含单段超长按行），4-1 主体已满足；本轮补强：超长表格行优先按单元格 `|` 切分（不切断单元格内容），纯超长行仍按字符硬切。4-2 在 llm_client.py 新增 `_repair_truncated_json`（回退到最后一个完整 `}`/`]` 丢弃截断尾部 → 闭合未闭合字符串 → 补齐缺失括号），接入 chat_json 全部调用方（规则导入/图谱构建/冲突检测/OCR）。4-3 提示词新增紧凑输出规则（rules 每行一条、无空行注释、structure/tolerance 仅在有内容时输出、defects 无缺陷省略数组）。测试 21 项全过（截断场景 8 类 + 分块 3 类 + 提示词 3 项）；修复过程暴露调用顺序 bug（先补括号后闭合字符串会把右括号吞进字符串），已修正。

### 批次 5：前端工程质量 P2 剩余项
> 来源：前端代码体检报告（P0×5 / P1×11 已清完，剩 P2 类）。以下为未修项，执行时以报告为准逐项核对。
- [x] 5-1 表单无防重复提交保护（disabled + in-flight ref）
- [x] 5-2 Drawer 关闭动画期间内容清空闪烁 → `afterOpenChange` 回调
- [x] 5-3 ResultsPage 内联全局 style 注入 + 与 index.css 重复定义
- [x] 5-4 Skill 编辑器无前端 YAML 预校验
- [x] 5-5 RulesPage 与 GraphPage 规则文档导入逻辑重复 → 抽公共 hook/组件
- [x] 5-6 RulesPage 缺陷 Drawer error/warning Tab 渲染重复
- [x] 5-7 UploadPage 重复导入 ReloadOutlined
- [x] 5-8 GraphView useEffect 依赖故意不全（height 变化不重渲）
- [x] 5-9 大量 `catch (e: any)` 滥用 any，类型收紧
- [x] 5-10 client.ts 内联 `import('../types')` 统一到顶部
- [x] 5-11 RuleSetContext 无 error 状态（加载中/失败无法区分）
- [x] 5-12 ResultsPage 表格列定义未 memo（render 内重建）
- [x] 5-13 GraphView 大量 `as any` 收敛
- [x] 5-14 DocumentCompare highlightTarget 设置后从不清除
- [x] 5-15 axios timeout 10 分钟兜底过长 + 无请求取消/重试机制
- **Status:** complete

**批次 5 验证**：`catch (e: any)` 41 处全部收紧（client.ts 新增 getErrorMessage/getErrorDetail/isFormValidationError 工具）；`as any` 全部清除（GraphView 21 处 d3 类型收敛：SimNode/SimLink 泛型 + linkEndName/linkEndPoint 辅助；RulesPage/GraphPage/UploadPage 其余 12 处）；前端 `npm run build`（tsc + vite）通过，新增依赖 js-yaml + @types/js-yaml。逐项：5-1 RulesPage 保存加 saving 状态 + confirmLoading/disabled；5-2 ResultsPage OCR 抽屉改 afterOpenChange 动画结束后清空（UploadPage 已有 300ms 延迟清除、detail 抽屉此前已保留内容）；5-3 内联 `<style>` 移入 index.css；5-4 SkillTab 保存前 yamlLoad 预校验；5-5 现状核对 GraphPage 已无导入逻辑（仅提示文案），无重复可抽；5-6 缺陷 error/warning Tab 抽公共 renderDefectTable；5-7 现状单次导入（已满足）；5-8 GraphView 主 effect 补 height 依赖；5-9/5-10/5-13 全量清零；5-11 RuleSetContext 暴露 error 状态；5-12 by-doc 表格列抽 docColumns useMemo；5-14 DocumentCompare 切换 doc 清除 highlightTarget；5-15 默认超时 120s（上传类 600s）、GET 幂等重试一次、上传接口支持 AbortSignal 取消。

### 批次 6：工程卫生与收尾
- [x] 6-1 清理根目录 0 字节 `nul` 文件（Windows 重定向误产生）
- [x] 6-2 CORS 生产收紧（当前 `allow_origins=["*"]`）
- [x] 6-3 `seed_rules.py` 僵尸函数清理或接回（`init_seed_rules` 无调用方）
- [x] 6-4 决策已定：**确认清理**「测试规则集2」220 条三元组重复（用 cleanup_dupes.py，注意 BASE 端口 8801→8800 需调整）
- [x] 6-5 全量回归：tsc / py_compile / 验收脚本
- **Status:** complete

**批次 6 验证（运行级，2026-08-02 服务重启后完成）**：6-1 根目录 `nul` 已删除；6-2 CORS 白名单化；6-3 僵尸函数删除；6-4 cleanup_dupes.py 实测：测试规则集2（be2467a2）当前 92 条规则、重复组 0——目标达成（220 条重复此前已清理）；6-5 全量回归：backend py_compile 全量 + 前端 tsc 通过 + **acceptance_run.py 端到端两次全绿**（30 规则灌入/确认、25 文件上传分类、图谱构建 73 节点/60 关系、审查 21 条结果=13 通过/5 不通过/3 无法核验，467s 含 OCR；结果含批次 8/9 的 status/severity/evidence/intent_chain/missing_fields）。

**验收暴露并修复的运行时 bug**：① Neo4j 属性不支持嵌套 Map——tolerance_params/condition/exceptions 写入节点/关系属性报 `Property values can only be of primitive types`（自批次 1-4 潜伏，首次全链路跑通才暴露）；修复：neo4j_client 写图前 `_sanitize_props` 将嵌套结构 JSON 序列化，graph_review_service 读图时 `_props_dict/_props_list` 反序列化。② LLM 转换路径未注入 rule_text，审查结果/证据链缺规则文本；修复：`_convert_one_rule` 关系属性补 `rule_text`。另修验收脚本自身问题：规则集名唯一化（幂等可重跑）+ 不抢占默认规则集 + `upload()` 返回完整响应（main 需用 contract_id 与 classified）。

### 批次 7：审查意图结构升级（研究落地 · 规则建模）
> 对应调研模式 R1-R4。依赖批次 1（图谱链路修复）。
- [x] 7-1 规则结构升级：rule 表新增 structure JSONB（condition/assertion/exceptions）+ 迁移 + schema 全链路
- [x] 7-2 聚合语义建模：节点名 `|SUM|ANY|ALL` 后缀显式编码聚合意图，审查执行支持三种模式
- [x] 7-3 参与方/角色建模：source/target 的 role 写入 Field 节点 attributes（委托方/收款方/收货方等）
- [x] 7-4 Value 带单位与币别：currency/unit 写入节点与关系属性；审查时币别一致性校验前置（不一致 fail，缺失 unverifiable）
- [x] 7-5 规则导入引擎适配：LLM 解析输出契约加 structure；入库透传；graph_builder 对含 assertion 的规则程序化转图谱（确定性，不再调 LLM 转换）
- **Status:** complete

**批次 7 验证**：py_compile 6 文件全过；批次 7 测试 21 项全过（解析/ANY/ALL/币别/结构化转换/顶层与子级 aggregate）；批次 1 回归 5 项全过（新参数默认值向后兼容）。condition/exceptions 仅保存到图谱属性，执行（条件预检/例外豁免）留批次 8。需后端重启 + 数据库迁移生效（ALTER TABLE 自动执行）。

### 批次 8：图谱执行引擎升级（研究落地 · 执行层）
> 对应调研模式 E1-E4。依赖批次 7。
- [x] 8-1 意图链驱动比对：条件预检 → 断言比对 → 例外豁免 → 结果（升级 _dispatch_compare）
- [x] 8-2 聚合比对：多单求和/时间区间比对下沉（Cypher 聚合或服务层聚合，与批次 1-3 联动）
- [x] 8-3 防空满足：三态显式区分"数据缺失"与"不通过"，结果带字段级缺失清单
- [x] 8-4 容差统一：tolerance 挂到 Value 节点，去除全局 5% 与关系容差双轨（衔接 1-4）
- **Status:** complete

**批次 8 验证**：py_compile 4 个改动文件全过；批次 8 测试 37 项全过（条件命中/未命中/缺失/纯文本、结构化例外豁免/文本例外人工确认、节点聚合 ANY 优先、多单求和、缺失清单 no_docs_of_type/field_missing、unparseable、总额容差三来源、节点容差优先、构建器节点注入；回归批次 7 聚合/币别 + 批次 1 合同号语义）。测试期间暴露聚合优先级顺序 bug（节点应优先于关系，初版写反）已修复。端到端（Neo4j+DB）待后端重启后 acceptance_run.py 验证。

### 批次 9：结果闭环与解释性（研究落地 · 结果层）
> 对应调研模式 C1-C3。依赖批次 7/8。
- [x] 9-1 违规持久对象：ReviewResult 关联图谱实体/文档字段，支持问题状态流转（打开/确认/修复/关闭）
- [x] 9-2 严重度分级：pass/fail/unverifiable 之上加 severity（高/中/低）+ 偏离度
- [x] 9-3 证据链：结果 detail 携带 规则→条件→字段→文档 完整来源
- **Status:** complete

**批次 9 验证**：py_compile 全部后端 app 文件通过；批次 9 测试 35 项全过（状态机流转/幂等/非法拒绝、严重度分级 11 类场景含偏离度、证据链规则→字段→文档、图谱/旧逻辑结果对象与序列化、合并摘要状态继承）；批次 8 核心行为回归 9 项全过；前端 tsc -b 通过。新增 PATCH /api/reviews/results/{id}/status 状态流转接口。DB 迁移（6 条 ALTER + 存量 pass 回填 closed）重启自动执行；前端已显示严重度/状态标签，状态流转 UI 按钮留待后续批次。

### 批次 10：泛化规则体系重构（回归最初设计目标）
> 来源：2026-08-02 用户反馈——当前版本强制"文件类型 × 检查项"必检格人工补齐，泛化能力被锁死；目标应为"任意人类语言规则列表 → LLM 语义拆分 → 图谱保存 → LLM+图谱双引擎审查"。
> 设计文档：docs/泛化规则体系重构设计.md（已完成，未改代码）
- [x] 10-1 Phase A：解除规则-格子强绑定
  - rules 表 doc_type/check_category 改 nullable，新增 scope/intents/provenance（迁移幂等，已实测）
  - LLM 导入 prompt v2（枚举降级为建议 + ontology 输出），校验放宽（rule_text 与 assertion 至少其一）
  - 去重改语义级（规则集内按 structure+归一化文本），修复按格子分组导致的重复（实测二次导入 0 新增）
  - 派生标签兜底：doc_type 从结构断言/scope 推导，check_category 取 intents[0]；rule_text 缺失时由断言生成
  - 新类型注册修复：DocumentType 模型补齐 category/is_required（存量 schema 漂移导致注册一直静默失败），key_fields 由 ontology.fields 预填
  - RulesPage 移除"必检"Alert 与红色必检格，矩阵投影化；编辑表单标签可空；空标签展示"整批/全部""未分类"
- [x] 10-2 Phase B：本体涌现闭环
  - constants.py 降级为种子/兜底；OCR 字段提取改 DocumentType.key_fields 优先（resolve_field_template，三处调用点接入）
  - 文件分类器接入动态注册表（DocumentType name→is_required，active+pending_review），新类型无需改代码可识别
  - 种子数据补齐 category/is_required 对齐（required/optional/supporting/extra/other）
  - 新规则集零预设（前端创建时默认空，后端 schema 默认 list）
- [x] 10-3 Phase C：双引擎审查
  - 新增 llm_review_service：无 structure 定性规则批量审查 + 字符串相等失败语义复核（同义→pass/不同→保持fail/不确定→unverifiable）
  - review_results 新增 source/confidence 列；图引擎结果标记 source=graph，LLM 结果 source=llm
  - review pipeline 阶段 2.5 合并引擎 B（异常不影响确定性结果）；低置信护栏（fail≥0.8/pass≥0.6）
  - suggestion_service 接入 LLM 生成建议（证据链上下文，失败回退模板）
  - 前端结果页展示来源标签（图谱/LLM/旧逻辑）+ 置信度 tooltip
- [x] 10-4 Phase D：图谱本体化
  - 写图新增本体层：DocumentType（文件类型:X）/CheckIntent（检查意图:X）/Rule（规则:Rxxx）节点 + APPLIES_TO/CHECKS/INVOLVES/HAS_FIELD 边（与执行层同 graph_id，R 编号与执行层一致）
  - 节点/边标注 layer（ontology/rule/execution）；DocumentType 节点属性带描述/字段/业务含义/印章/必备标记
  - neo4j_client.get_ontology 本体查询接口；新增 GET /api/rules/graph/ontology
  - GraphPage：图层过滤（全部/本体/规则/执行）+ 本体概览面板（文档类型含字段、检查意图含规则数、规则清单）；GraphView 本体节点配色 + display_name 显示
- [x] 10-5 Phase E：回归验收
  - 后端重启加载新代码；回归 acceptance_run.py：30/30 规则灌入、25 文件上传、图谱 125 节点/215 关系、审查 38 条（25 通过/6 不通过/7 无法核验），双引擎来源 graph=22/llm=16，状态闭环正确（pass→closed、fail/unverifiable→open）
  - 新流程验收（acceptance_phase_e_new.py）：零预设规则集→任意规则文本导入 4/4→自动发现新类型（验收确认单 pending_review + key_fields 预填）→建图本体层（文件类型/检查意图/规则节点）→双引擎审查（graph+llm）→清理
  - 修复验收暴露的问题：① schemas/rule.py 缺 `import uuid` 且缺 model_rebuild → 导入接口 500（pydantic 前向引用）；② RuleImportResponse 缺 new_doc_types 字段（前端读取不到）；③ 全局规则 scope=ALL 被推导为 doc_type="ALL" 并误注册新类型
- **Status:** 批次 10 全部完成（10-1~10-5 全绿：回归验收 + 新流程验收通过，数据已清理）

## Key Questions
1. ~~历史图谱清理策略~~ → 已答：构建前全清
2. ~~多文档比对语义~~ → 已答：多单汇总为主（分批付款）
3. ~~「测试规则集2」220 条重复是否清理~~ → 已答：清理
4. 每批次是否需要用户确认后启动，还是授权连续执行？
5. ~~审查意图还原建模~~ → 已研究：条件/断言/例外 + 聚合/角色/Value 结构（批次 7-9）
6. 批次 7-9 是否需要与批次 1-2 穿插执行（研究结论影响 1-3/2-1 的设计），还是严格按编号顺序？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 任务清单采用 planning-with-files 三件套落盘 | 长任务文件化中间状态，防上下文丢失 |
| 批次 1 图谱审查链路优先 | 纯后端、范围清晰，为批次 2 的图谱路径验收铺路 |
| OCR 质量（批次 2）紧跟其后 | 验收报告 ROI 最高，直接解锁 AC5/AC6 |
| 图谱构建前全清旧图谱（1-1） | 用户决策：避免 Neo4j 节点无限累积；按规则集清理防止误删 |
| 多单汇总为主（1-3） | 用户决策：多张水单关联同一合同为分批付款，汇总比对符合业务 |
| 清理测试规则集2 重复（6-4） | 用户决策：历史脏数据需清理 |
| 审查算法与图谱案例先行研究（0.5） | 用户决策：现行审查未还原复杂审查意图，先调研再改 |
| 意图结构升级落地为批次 7（R1-R4） | 研究结论：复合规则 Clause 化 + 聚合/角色/Value 建模是"还原审查意图"的关键 |
| 执行引擎升级落地为批次 8（E1-E4） | 研究结论：意图链驱动 + 聚合下沉 + 防空满足 + 容差统一 |
| 结果闭环落地为批次 9（C1-C3） | 研究结论：违规持久化/严重度/证据链是合规系统标配 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Notes
- 批次状态流转：pending → in_progress → complete，每批完成后更新本文件 + progress.md
- 执行顺序按批次号推进；同一批次内子任务按编号顺序
- 所有代码改动遵守 AGENTS.md：只改必须改的、修 Bug 先说明根因、高风险操作先确认
- 外部依赖：后端需重启生效的改动，沙箱无法 kill 宿主机进程，需提示用户手动重启
