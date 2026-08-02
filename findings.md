# Findings & Decisions

## Requirements
<!-- Captured from user request -->
- 把需要做的优化开发任务整理成任务清单
- 后续分批次按清单一步一步执行
- 做好任务记录（文件化，可持续恢复）

## Research Findings
<!-- 所有待办线索来源与细节，2026-08-02 收集 -->

### 批次 8 落地：图谱执行引擎升级（2026-08-02）
- **E1 意图链驱动比对**：`_check_compare_relationships` 升级为 `_run_intent_chain` 执行器：条件预检 → 币别预检 → 断言比对 → 例外豁免 → 结果。
  - 条件：`structure.condition`（text/field/operator/value）。命中→继续断言；未命中→pass+skipped_reason；字段缺失/纯文本→unverifiable（防空满足）。
  - 例外：结构化例外（field/operator/value）可程序化豁免（fail→pass，detail 保留 original_result 与例外证据）；纯文本例外保留 fail 并标记 exception_manual_review 需人工确认。
  - 证据链：detail 新增 intent_chain（condition_precheck / currency_precheck / assertion_compare / exception_exemption）与 condition/exception_evidence。
- **E2 聚合下沉**：文档值存 Postgres（extracted_fields）、图内仅规则实体，真实 Cypher 聚合需文档值入图（远期），本轮采用"服务层聚合下沉"：`_collect_field_evidence` 统一采集 + `_aggregate_field` 求和；聚合语义（SUM/ANY/ALL）以 Value 节点属性优先、关系/节点名后缀兜底。
- **E3 防空满足**：所有比较器空值分支显式返回 unverifiable（杜绝空满足），reason 区分 field_data_missing / unparseable_numeric / unparseable_date / condition_field_missing / condition_text_only / currency_missing；detail.missing_fields 为字段级缺失清单（no_docs_of_type / field_missing + doc_files）；review_service 合并摘要时聚合 missing_fields。
- **E4 容差统一**：tolerance_params + tolerance 写入 Value 节点（构建器两路径均注入），执行时节点优先、关系兜底（旧图兼容）；"总额等于"去除全局 5% 双轨——规则声明优先，无声明严格 0%（tolerance_source: value_node_or_rule / legacy_param / strict_default）。旧 tolerance_pct 参数仅兼容批次 1 调用；review_service 旧逻辑（无图谱 fallback）仍用全局 5%，不在本轮范围。
- **规则导入**：prompt 的 exceptions 增加可选 field/operator/value，支持"金额小于5000元时除外"类结构化豁免。

### 批次 9 落地：结果闭环与解释性（2026-08-02）
- **C1 违规持久对象 → 问题状态机**：ReviewResult 新增 status（open/confirmed/fixed/closed）+ status_history（审计历史）+ graph_source/graph_target（图谱实体关联）。状态流转 RESULT_STATUS_FLOW 放 result_meta.py，非法流转拒绝；PATCH /api/reviews/results/{id}/status 提供接口。图谱三路关系（COMPARE_TO/REQUIRED/MUST_STAMP）均携带 source/target 节点名。注：未建 Neo4j violation 节点（远期）。
- **C2 严重度分级**：compute_severity——pass→无严重度；unverifiable→low（数据缺失需补档）；齐套性缺件/币别不一致→high；金额/数值偏差≥10%、时间偏差≥30 天→high，否则 medium；印章缺失→medium。deviation 结构 {kind: percent|days, value, src, tgt}。
- **C3 证据链**：意图链所有分支 detail.evidence = {rule(rule_id/rule_text/doc_type/check_category), source/target(node + docs[{doc_name, value}] + missing_fields)}，配合批次 8 的 intent_chain/condition/exception 形成 规则→条件→字段→文档 完整追溯。
- **序列化统一**：review_service._result_to_item 供 by-rule/by-doc/状态接口共用；合并 unverifiable 摘要继承状态、severity 固定 low。

### 批次 3 落地：规则管理体验优化（2026-08-02）
- 现状核对结论：07-30 记忆文件描述的 5 项硬伤中，①矩阵 confirmed 过滤/三态色、②空格新增、③必备格红字+齐套率、④tooltip 部分字段，已在 v0.7.0（07-31 提交，含 07-30 未提交内容）落地；本轮补齐剩余缝隙。
- **必备格=0 报警**：新增全局 Alert（essentialMissingCells），列出"必备文档 × 检查项"无已确认启用规则的全部空格，配合单元格红底「必检」。
- **tooltip 字段**：关键字段/业务含义/印章要求/样例文档 4 个闲置字段 + 行头 name。
- **CHECK_CATEGORIES 单源化**：/api/constants/doc-types 新增 completeness_category（后端 CHECK_COMPLETENESS），前端不再硬编码 '齐套性'；check_categories 本就由该接口下发，FALLBACK 仅兜底离线。
- **skill 停用 UX**：前端下拉只列 enabled skill 并提示停用数量；后端 import_rules_with_skills 对不存在/停用 skill 显式报错（文本导入 400、文件导入 task.error），终结"选停用 skill 静默用默认配置"。

### 批次 4 落地：LLM 解析健壮性（2026-08-02）
- 现状：规则导入 max_tokens 已 8192、2000 字符分段（07-30 的 4096 截断问题主体已解），本轮补缝隙。
- **截断 JSON 修复（核心）**：llm_client._repair_truncated_json 三级策略——回退到最后完整 `}`/`]`（丢弃截断尾部不完整元素）→ 闭合未闭合字符串 + 补齐括号 → 仅补括号。修复成功记 warning 日志；数字中途截断（如 `0.`）无法安全修复，保持原错误。chat_json 全调用方受益。
- **表格行切分**：_split_text 超长表格行按 `|` 单元格边界切，避免切断单元格；无分隔符超长行仍字符硬切。
- **紧凑输出提示**：rules 每行一条、无空行/注释、structure/tolerance 有内容才输出、defects 无缺陷省略数组。

### 批次 5 落地：前端工程质量 P2（2026-08-02）
- **类型收紧**：`catch (e: any)` 41 处清零（client.ts 提供 getErrorMessage/getErrorDetail/isFormValidationError 三件套）；`as any` 33 处全清（GraphView 的 d3 泛型用 SimNode/SimLink + linkEndName/linkEndPoint 收敛，Badge status 用 BadgeProps['status'] 类型化）。
- **关键交互修复**：RulesPage 保存防重复提交（saving + confirmLoading）；ResultsPage OCR 抽屉改用 afterOpenChange 动画后清空（消除闪烁）；DocumentCompare 切换文档清除高亮；GraphView 主 effect 补 height 依赖。
- **样式收敛**：ResultsPage 内联 `<style>` 移入 index.css。
- **Skill YAML 预校验**：引入 js-yaml，保存前语法检查（语义校验仍由后端把关）。
- **网络层**：默认超时 10min→120s（上传类 600s）、GET 幂等自动重试一次、上传接口支持 AbortSignal；client.ts 内联类型导入统一到顶部。
- **去重**：缺陷 Drawer error/warning 共用 renderDefectTable；by-doc 表格列抽 docColumns useMemo；5-5（RulesPage/GraphPage 导入重复）现状核对 GraphPage 已无导入逻辑，无需抽取。

### 批次 6 落地：工程卫生与收尾（2026-08-02）
- `nul` 垃圾文件（Docker 重定向 3602B）删除：Windows 保留设备名须用 `\\?\` 前缀 + .NET File.Delete。
- CORS 白名单化：config 新增 cors_origins_raw（逗号分隔，可 .env 覆盖），main.py 不再 `["*"]`；同源生产可留空。
- seed_rules.py 僵尸函数 init_seed_rules 移除（无调用方），ALL_SEED_RULES 数据保留供验收脚本。
- cleanup_dupes.py BASE 端口 8801→8800 修正，脚本待服务在线后执行（测试规则集2 220 条重复清理）。
- 全量静态回归通过（backend py_compile 全量 + 前端 tsc）；端到端验收（acceptance_run.py）依赖 Postgres/Neo4j/后端启动，留待用户环境动作。

### 批次 6 运行级验收与运行时 bug（2026-08-02 服务重启后）
- **Neo4j 属性不支持嵌套 Map**：tolerance_params/condition/exceptions（dict/list）直接 `SET n += $attrs` 报 `Property values can only be of primitive types or arrays thereof`。自批次 1-4 注入容差参数起即存在，此前验收从未真正跑到建图（脚本先后被 up 未定义、重名 400 挡住），本次首跑暴露。修复：neo4j_client._sanitize_props 写入侧 JSON 序列化嵌套结构，graph_review_service._props_dict/_props_list 读取侧反序列化（含 update_node/update_edge 编辑路径）。
- **LLM 转换路径缺 rule_text**：_convert_one_rule 只注入 rule_id/doc_type/check_category，审查结果与证据链 rule_text 为空（结构化路径正常）。修复：关系属性补 rule_text。
- **验收脚本三处修复**：规则集名称时间戳唯一化 + is_default=False（原固定名重跑撞 400、且抢占默认规则集）；upload() 返回完整响应（原返回字符串导致 main 二次索引 TypeError）。
- **端到端结果**：图谱 73 节点/60 关系；审查 21 条 = 13 pass / 5 fail / 3 unverifiable；status/severity/evidence/intent_chain/missing_fields 全部生效。5 条 fail 为多主体协议方 vs 单主体水单的字符串相等语义，符合当前规则设计。

### 1. 图谱审查链路深挖（2026-08-02，来源：graph_review_service.py / neo4j_client.py / graph_builder_service.py / cypher_guard.py）
- 审查入口：review_service._run_review_pipeline 优先 `run_graph_review_with_contract`，无快照/异常 fallback 旧逻辑。
- 取数：按 contract.rule_set_id 查最新 RuleSnapshot.graph_id → 3 条参数化只读 Cypher（REQUIRED / MUST_STAMP / COMPARE_TO），全部硬编码 + $graph_id 参数化。
- 节点统一标签 RuleEntity，graph_id 属性隔离版本/规则集；节点类型 Field/RequiredDoc/StampRequirement/CheckRoot。
- **1-1 历史图谱不清理**：build_graph 每次生成新 graph_id（`graph_{时间戳}_{uuid6}`），注释写"先清除旧图谱"但实际不调用 clear_graph，旧图自然累积。clear_all_rule_graphs 仅 DB 重置时用。
- **1-2 snapshot_id 空转**：start_review(snapshot_id) 与 reviews/start 路由均接收，但 run_graph_review 固定取最新快照，忽略传入值（grep 证实无使用）。
- **1-3 多文档只取第一份**：_first_field 只取同类型第一份文档；仅"总额等于"用 _aggregate_field 跨文档求和。
- **1-4 两套容差**：COMPARE_TO 关系 tolerance（绝对值）只作用于不大于/不小于；"总额等于"固定用 settings.amount_tolerance_percent（5%）。
- **1-5 _cmp_contains 依赖 ContextVar**：_current_contract 由 run_graph_review_with_contract 注入，直接调 run_graph_review 遇"包含于"关系抛 RuntimeError（commit 0e2c5a2 修复并发串数据时引入）。
- **1-6 cypher_guard 误伤**：validate_cypher_params 拒绝字符串值含 MATCH/CREATE/RETURN/UNION/CALL/apoc./LOAD CSV 的参数；write_rule_graph 的 attrs 含 rule_text，含"CREATE"等词会被挡。另 _WRITE_KEYWORDS 有 "DETONATE" 疑似 "DETACH" 笔误（无实际影响）。
- operator 派发：等于/不大于/不小于/时间早于/时间不晚于/总额等于/包含于，未知降级字符串相等。

### 2. 验收报告（acceptance_output/验收报告.md，主样本 24HCSP012260253）
- 结果规模：25 条 = 14 通过 / 9 不通过 / 2 无法核验；三态、齐套性、印章降级、合同号归一化正确。
- **AC5/AC6 未触发**：付款/收款水单金额、日期字段 OCR 未提取 → 总额比对退化为 unverifiable，¥5,239,994.43 差额没被发现；收付同日未进入比对。
- **7 个假阳性**：OCR 把 24HCSP012260253 误识为缺末位/次末位错（24HCSP01226025 / 24HCSP01226024），付款申请×4、收汇认领×3 全因源数据错。
- 验收路径未走图谱（fallback 旧逻辑），build-graph 图谱路径未正式验收。
- 建议按 ROI：①OCR 字段提取增强（金额/日期/长数字串）②合同号文件名交叉校验 ③正式跑图谱路径 ④精确计时 ⑤手动验证 AC11/AC12。

### 3. 记忆文件待办（.workbuddy/memory/）
- **规则二维矩阵 5 项硬伤（07-30 识别）**：①入桶未过滤 enabled/confirmed（RulesPage.tsx:174-182）②空格快捷新增 ③必备格=0 报警+齐套率 ④悬停 tooltip 用上 5 闲置字段 ⑤CHECK_CATEGORIES 前后端双硬编码需后端单源化。
- **skill 开关 vs 导入选择耦合（07-29 识别）**：导入下拉不过滤停用项（service:35-55 不过滤 enabled），执行时 rule_import_service:583-592 用 enabled 过滤 → 选中停用 skill 被静默丢弃。候选 A 前端过滤（推荐）/ B 后端 400。
- **LLM 解析 JSON 截断（07-30 排障）**：PDF 第 7 段 LLM 输出被 max_tokens=4096 截断 → JSONDecodeError。修复方向：①提 max_tokens 或按表格行切分限流 ②_extract_json 截断容错 ③提示词紧凑输出。
- **导入未关联 Skill**：测试规则集没关联任何 Skill，YAML 优化配置未生效（独立待办，批次 4 顺带）。
- **P0(5)+P1(全部) 已清完**（07-29/07-30）：切换重挂载、递归轮询、审查历史、OCR 闭包、dead state、二维矩阵 Popover、铃铛、OCR 完成度校验、静默吞错、Spin tip、图表库卸载、bodyStyle、stageLabel。剩 P2 类工程优化。
- **git 历史**：v0.1→v0.7（4cb8ab3），master 分支干净。

### 4. 前端体检报告（前端代码体检报告.html，28 项）
- 统计：P0×5 / P1×11 / P2×12；P0、P1 已全部修复。
- **剩余 P2 类 15 项**（任务清单批次 5，执行时以报告逐项核对）：表单防重复提交、Drawer 动画清空闪烁、ResultsPage 内联 style、Skill YAML 前端预校验、导入逻辑重复、缺陷 Drawer Tab 重复、ReloadOutlined 重复导入、GraphView useEffect 依赖、catch any、client.ts 内联 import、RuleSetContext 无 error、表格列未 memo、GraphView as any、DocumentCompare highlight 不清除、axios timeout/取消/重试。

### 5. 其他
- duplicate_report.md（07-27）：规则导入同规则集内不去重 → 测试规则集2 有 220 条三元组重复（59%）。**去重合并逻辑已于 07-29 上线**（_find_similar_rule 阈值 0.75 + _merge_into_existing），但旧数据未清理（6-4 可选）。
- 根目录 `nul` 0 字节文件（Windows 重定向误产生）。
- main.py CORS allow_origins=["*"]（MVP 放开，生产应限制）。
- seed_rules.py 的 init_seed_rules 为僵尸函数（07-30 记忆确认无调用方）。
- .bat 硬性约定：纯 ASCII 英文，禁止中文（cmd 按 ANSI/GBK 解析）。
- 端口：后端 8800 / 前端 5173 / PG 15432 / Neo4j 17687；start.bat/stop.bat 已移除，手动启动流程见 MEMORY.md。

### 6. 审查算法与图谱审查案例调研（2026-08-02，批次 0.5，详见 docs/research_审查算法与图谱审查案例调研.md）
- 定位 20+ 外部参照：Neo4j 合同审查示例、GRAPH-GRPO-LEX（arxiv 2511.06618）、NCKG 建筑施工合同审查、Mnemosyne、ConsistentPeer、MMLogicInt（物流单据图谱验证，场景最接近）、GAIJ（金融欺诈图谱）、Luminance/Kira（商业合同审查）、天津大学国际工程合同问答（KG+LLM 双架构，DOI 10.1007/s42524-026-4237-0）、昆仑数智合规方案、MedRule-KG（符号验证器）、Violation Situation Pattern（arxiv 2606.03326）、RWTH NL→Cypher 验证、SHACL/Stardog ICV、GraphRAG Survey 等。
- **核心结论**：①审查意图应建模为 条件/断言/例外 + 聚合/角色/Value 结构，而非单条比对边；②多单汇总是一等语义（用户已确认）；③执行层应"声明式规则 + 可解释验证器"，防空满足；④结果需闭环（违规持久化、严重度、证据链）。
- 产出 11 个可落地模式（R1-R4 / E1-E4 / C1-C3），转化为任务清单批次 7-9。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 规划文件放项目根目录（legacy mode） | 无 .planning 目录，单一主线任务 |
| 批次顺序：图谱审查 → OCR 质量 → 规则管理 → LLM 健壮性 → 前端工程 → 收尾 | ROI × 依赖关系排序 |
| 图谱构建前全清（1-1） | 用户决策：避免图谱无限累积；按规则集清理防误删 |
| 多单汇总为主（1-3） | 用户决策：分批付款场景下多单汇总比对 |
| 清理测试规则集2 重复（6-4） | 用户决策 |
| 研究结论落地批次 7-9 | 意图结构升级 / 执行引擎升级 / 结果闭环 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 体检报告 HTML 正则多次提取失败 | 改用 Select-String 提取 issue-title 行，成功拿到全部 28 项标题 |
| GitHub raw / API 获取 Neo4j 示例仓库超时 | 改用其官方博客技术说明补充四阶段架构 |
| Devpost（MMLogicInt）需 JS 无法打开 | 用搜索 snippet 提取核心信息（自动拆分/元数据提取/条目映射/图谱验证） |

## Resources
- 图谱审查：backend/app/services/graph_review_service.py（569 行）、neo4j_client.py、graph_builder_service.py、utils/cypher_guard.py
- 验收：acceptance_run.py、acceptance_output/验收报告.md、analyze.py
- 记忆：.workbuddy/memory/MEMORY.md + 2026-07-28/29/30/31.md
- 体检：前端代码体检报告.html
- 重复规则：duplicate_report.md、check_dupes.py、cleanup_dupes.py
- 架构：docs/规则解析控制架构设计.md、docs/构建图谱入口收敛变更说明.md
- PRD：基于知识图谱的自动文档审查智能体-PRD.md、需求规格精简版.md
- 调研：docs/research_审查算法与图谱审查案例调研.md（批次 0.5 产出）
- 外部参照：arxiv 2511.06618（GRAPH-GRPO-LEX）、arxiv 2606.03326（Violation Situation Pattern）、arxiv 2510.16309（MedRule-KG）、github.com/neo4j-product-examples/graphrag-contract-review、journal.hep.com.cn/fem/EN/10.1007/s42524-026-4237-0（天大合同问答）
