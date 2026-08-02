# 调研：审查算法设计与图谱审查案例

> 日期：2026-08-02
> 目的：为 DocGraph 图谱审查升级提供外部参照，回答"现行审查过于粗糙、未还原复杂审查意图"的问题
> 状态：进行中（第一批发现已落盘）

## 一、调研问题

1. 优秀审查系统如何建模"规则/审查意图"？（不只是一条规则一个比对）
2. 知识图谱用于审查/核验有哪些成熟案例与模式？
3. 哪些模式可以直接落到 DocGraph（规则结构、图谱 schema、查询模式、执行引擎）？

## 二、已定位的外部参照（首批）

| 参照 | 类型 | 相关性 | 要点 |
|---|---|---|---|
| Neo4j 官方 graphrag-contract-review | 开源示例 | ★★★★★ | 合同审查 Agent：LLM 提取 → Neo4j 图谱 → 检索函数，审查意图即图谱结构 |
| GRAPH-GRPO-LEX (arxiv 2511.06618) | 论文 | ★★★★★ | 合同转语义图：Clause 级元模型 + 本体层（NCKG），可发现条款间隐藏依赖 |
| Automating construction contract review using KG-enhanced LLMs (Sciencedirect 2025) | 论文 | ★★★★☆ | NCKG 双层架构：嵌套知识表示层 + 本体层（含风险等级），捕获复杂合同语义 |
| Graph-RAG for Regulatory/Technical Document Analysis (TUSUR) | 论文 | ★★★★☆ | 知识图谱用于监管/技术文档的完整性与一致性分析（n=300 查询显著性验证） |
| Mnemosyne (GWU) | 论文/系统 | ★★★☆☆ | LLM 把大文档切块转带归因的知识图谱，可回溯源文档，用于识别不一致 |
| ConsistentPeer | 论文 | ★★★☆☆ | GraphRAG 审查：词/句/方面多粒度一致性评估 |
| MMLogicInt (Devpost) | 项目 | ★★★★★ | 多模态物流单据图谱验证与审计：自动拆分识别文档、提取物流元数据、映射条目结构（场景最接近） |
| GAIJ (挪威研究委员会) | 研究项目 | ★★★☆☆ | 金融欺诈调查图谱：33k 文档、Neo4j 公司/人/审计师/地址网络 |
| Luminance | 商业产品 | ★★★★☆ | 条款偏离度评级：同类型文档/条款横向比对，按偏离 norm 程度评级 |
| Kira Systems / LawGeex | 商业产品 | ★★★☆☆ | 结构化条款提取 + 跨文档交叉引用比对 |
| 天津大学国际工程合同问答（工程·管理 2026-03） | 中文论文 | ★★★★☆ | 知识图谱+LLM 双架构，数千页国际工程合同问答 |
| 昆仑数智央国企合规管理 | 行业方案 | ★★★★☆ | 知识图谱与规则引擎融合：制度智能审查、合同全生命周期风险识别、风险预警闭环 |
| MedRule-KG (arxiv 2510.16309) | 论文 | ★★★☆☆ | 类型化知识图谱 + 符号验证器：在推理任务中强制可解释规则（100% 规则一致性） |
| GraphRAG Survey (arxiv 2501.13958) | 综述 | ★★★★☆ | 跨文档推理与聚合查询优势（聚合 3x、跨文档推理 4x 优于向量 RAG） |

## 三、已精读：GRAPH-GRPO-LEX 合同语义图本体（arxiv 2511.06618）

### 节点类型（9 类）
| 节点 | 含义 | 属性示例 |
|---|---|---|
| Clause | 条款/章节（图的最小结构单元） | id、title、text、clause_level（层级深度） |
| DefinedTerm | 合同中定义术语 | term、definition、definition_clause |
| Party | 当事方（含未签署方） | name、role（Buyer/Distributor）、address |
| Obligation | 义务（"shall/must"） | action、actor(Party)、deadline |
| Right/Permission | 权利（"may/is entitled to"） | action、holder、frequency |
| Prohibition | 禁止（"shall not"） | action、subject |
| Condition | 触发条件（IF…THEN） | trigger、operator |
| Reference | 外部标准/法律引用 | name、citation |
| Value | 数值量 | type(Currency/Percentage)、amount、unit |

### 边类型（核心关系）
| 边 | 语义 | 示例 |
|---|---|---|
| IS_PART_OF / CONTAINS | 层级结构（文档树） | Clause_3.1 → Section_3 |
| REFERENCES | 显式交叉引用 | Clause_10.2 → Clause_3.1（"违反 3.1 视为重大违约"） |
| DEFINES / USES | 术语定义与使用 | Clause_1.1 → DefinedTerm |
| ASSIGNS_OBLIGATION_TO | 义务归属 | Obligation(Pay_Fee) → Party(Licensee) |
| GRANTS_RIGHT_TO | 权利归属 | Right(Terminate) → Party(Licensor) |
| DEPENDS_ON | 隐性依赖（论文创新点） | 条款间条件/时序依赖，可由模型自动发现 |

### 对 DocGraph 的启示
1. **"审查意图" = 图谱结构本身**：Obligation/Right/Prohibition/Condition 这类语义节点 + 归属/依赖边，比 DocGraph 现有的"字段 COMPARE_TO 字段"更接近原始规则文件的复杂意图（如"先收后付、总额一致、同合同分批"）。
2. **Clause 级建模**：DocGraph 规则来自"文件类型 × 检查项"矩阵，原始规则文本往往是复合句（多条件、多单据、例外条款）。可把每条规则文本解析为 Clause 级结构（条件 + 断言 + 例外），而不是压成一条 COMPARE_TO 边。
3. **DEPENDS_ON 类隐式依赖**：对应 DocGraph 的时间逻辑与总额关联（多水单分批付款 → 汇总节点），需显式建模"聚合"关系。
4. **Value 节点带 unit/type**：对应 DocGraph 的 tolerance 体系，但应挂到 Value 节点而非散落在边属性上。

## 四、对 DocGraph 现状的差距分析

| 维度 | DocGraph 现状 | 外部参照模式 | 差距 |
|---|---|---|---|
| 规则意图建模 | 一条规则 → 1~N 条 COMPARE_TO 边（operator/tolerance） | Clause 语义图（条件/义务/禁止/例外 + 归属边） | 复合条件、例外条款、多单据关联被压平 |
| 多单汇总 | 仅"总额等于"支持求和，其余取第一份文档 | MMLogicInt 条目结构映射；GAIJ 实体网络聚合 | 汇总语义应成为一等公民（用户已确认） |
| 图谱执行 | 3 条固定 Cypher 全量拉取后 Python 侧比对 | 图谱内推理（路径查询、依赖遍历）+ 符号验证器 | 比对逻辑可在 Cypher/引擎侧表达 |
| 解释性 | 结果带 rule_text 快照 | Luminance 偏离度评级、ConsistentPeer 多粒度证据 | 结果应携带"意图链"证据（条件/例外/引用来源） |
| 风险分级 | pass/fail/unverifiable 三态 | 条款风险等级（NCKG）、偏离 norm 评级 | 可按严重度/置信度细化 |

## 五、待深入
- [x] Neo4j graphrag-contract-review（README/API 均超时，改用其博客技术说明补充：四阶段 = LLM 定向提取 → Neo4j 图谱 → Cypher/Text2Cypher/向量检索函数 → Q&A Agent）
- [ ] MMLogicInt 细节（Devpost 需 JS；核心信息已从 snippet 获取：多页 PDF 自动拆分识别、物流元数据提取、条目结构映射、图谱验证审计）
- [x] 昆仑数智合规方案（融合知识图谱+规则引擎：制度智能审查/合同全生命周期风险识别/风险预警闭环）
- [x] 天津大学国际工程合同问答（KG+LLM 双架构，DOI 10.1007/s42524-026-4237-0，626 份 88 国合同数据集）
- [ ] 审查执行引擎设计模式（规则引擎 vs 图谱路径查询 vs 符号验证器）→ 下一轮
- [ ] 沉淀"可落地设计模式"清单 → 转化为 task_plan 实施批次

## 六、第二批发现（中文工程化案例 + Neo4j 生态）

### 昆仑数智「央国企智能合规管理」方案
- 知识图谱与规则引擎融合，多业务场景智能化合规应用。
- 落地能力：制度智能审查、合同全生命周期风险识别、重大事项合规辅助、风险动态预警与闭环整改。
- 另一个视角（2024-12 "三合一"模式）：大数据 + NLP + 知识图谱 + ML 支撑内控/风险/合规一体化，强调"智能预警、管控闭环"。
- **启示**：DocGraph 的三态结果（pass/fail/unverifiable）+ 修正建议已具备"预警"，缺的是"闭环整改"（问题处理状态流转）——可作为后续增强方向。

### 天津大学国际工程合同问答系统（Frontiers of Engineering Management, 2026-03）
- 双架构：知识图谱（合同条款/实体/关系的结构化表示）+ 大语言模型（语义理解与生成）。
- 数据集：88 国 626 份国际工程合同；任务导向分析（风险条款识别、跨条款问答）。
- **启示**：审查结果应支持"任务导向"查询（如"哪些条款存在风险""合同间一致性"），图谱 + LLM 双通道而非纯规则比对。

### Neo4j 合同审查示例（graphrag-contract-review + 官方博客）
- 四阶段：① LLM+prompt 定向信息提取 ② 存入 Neo4j 图谱 ③ 图谱数据检索函数（Cypher / Text2Cypher / 向量搜索）④ Q&A Agent。
- Microsoft Agent Framework 集成案例：Contracts/Clauses/Organizations/Jurisdictions 节点 + 结构化 Cypher 遍历（"哪些合同引用 GDPR 且供应商在德国"）。
- **启示**：审查意图可部分表达为"图谱遍历查询"（Cypher 路径），比"全量拉边 + Python 比对"更贴近意图；DocGraph 的 3 条固定查询可升级为按规则类型生成的参数化查询。

### GAIJ（挪威：图谱驱动的金融欺诈调查）
- 33k 文档 → Neo4j 图谱（公司/人/审计师/地址），浏览器工具供调查记者使用。
- **启示**：实体网络 + 多跳遍历是发现"隐式关联"的手段——对应 DocGraph 的"多单据关联同一合同"跨文档推理。

## 七、第三批发现（执行引擎与结果建模）

### The Violation Situation Pattern（arxiv 2606.03326, EKAW 2026）
- 痛点：合规管道把违规检测当**临时查询结果**，查询结束即丢弃——违规没有审查状态、受影响实体、审计历史。
- 主张：把 **violation 作为持久图对象**（含 review state / affected entities / audit history）。
- **与 DocGraph 的对照**：ReviewResult 存在 Postgres 但图谱里没有"违规节点"，问题无法与文档/字段实体关联、无状态流转。这是"结果闭环"缺口（昆仑数智方案强调闭环整改）。

### NL 规则 → Cypher 自动生成（RWTH Aachen 论文）
- 用 LLM 把自然语言业务规则翻译为可执行 Cypher，对流程事件日志做合规验证。
- 指出 Cypher 验证的经典缺陷：**vacuous satisfaction（空满足）**——查询结果不区分"规则真正满足"与"空虚满足"（如"车辆不得关联超过 3 辆卡车"，没有车辆时返回空 = 假满足）。
- **与 DocGraph 的对照**：三态 unverifiable 正是防空满足的雏形，但需显式区分"数据缺失 → 无法验证"与"数据齐全 → 通过"。

### GDPR 合规：Prolog + Neo4j/Cypher 双架构（SCITEPRESS 2026）
- 合规规则表达为可复用逻辑模式，Prolog 做演绎推理/约束验证，Neo4j/Cypher 做交互式分析。

### 声明式约束校验（SHACL / Stardog ICV / pg_ripple）
- SHACL / Stardog Integrity Constraint Validation：约束声明式定义，引擎按完整性约束批量校验，可解释、零延迟、可扩展。
- pg_ripple：把 SHACL 校验嵌入数据库引擎（同步/异步队列）。
- **启示**：DocGraph 的规则 → 图谱 → 检查器链路，本质是"约束声明 + 执行引擎"，可借鉴 SHACL 的"校验结果可追踪到具体约束/实体"思路（诊断报告带约束来源）。

### MedRule-KG（arxiv 2510.16309）
- 类型化知识图谱 + 轻量**符号验证器**：规则为闭集确定性约束（0/1 可计算），LLM 推理被验证器强制约束，90 例基准上 100% 规则一致性。
- **启示**：图谱审查可加"符号验证层"：规则确定性部分（数值/日期/聚合）由代码验证器执行，LLM 只负责解析与生成，防止 LLM 幻觉污染审查结论。

### KG-ACE（医学推理一致性增强）
- 三级验证（概念/关系/逻辑）+ 连续对齐分数 + 结构化诊断报告。

## 八、可落地设计模式（面向 DocGraph）

### 意图结构层（解决"审查意图被压平"）
| 模式 | 说明 | 外部参照 | DocGraph 落点 |
|---|---|---|---|
| R1 规则 Clause 化 | 复合规则拆为 条件(Condition) + 断言(Assertion/Obligation) + 例外(Exception) 结构化 | GRAPH-GRPO-LEX / NCKG | rule_parse_engine 输出结构升级；rule 模型加结构字段 |
| R2 聚合语义节点 | SUM/ALL/ANY/时间区间 成为一等节点，指向一组同合同单据 | 用户确认分批付款；MMLogicInt | 图谱增加 Aggregate 节点，COMPARE_TO 支持聚合源 |
| R3 参与方/角色边 | 委托方/收款方/收货方等角色显式建模 | GRAPH-GRPO-LEX Party 节点 | "收付对象一致"类规则不再只比字符串 |
| R4 Value 带单位 | 金额带币别、数量带单位，容差挂到 Value | GRAPH-GRPO-LEX Value 节点 | 币别不一致应 fail，而非数值比对 |

### 执行层（解决"比对过于粗糙"）
| 模式 | 说明 | 外部参照 | DocGraph 落点 |
|---|---|---|---|
| E1 意图链驱动比对 | 先检条件 → 再断言比对 → 例外豁免 → 出结果（带证据） | MedRule-KG 验证器 / KG-ACE | _dispatch_compare 升级为意图链执行器 |
| E2 聚合下沉图谱 | 多单求和/时间区间在查询侧完成 | RWTH NL→Cypher | get_compare_relationships 支持聚合查询 |
| E3 防空满足 | 三态显式区分"数据缺失"与"不通过" | RWTH 论文 | 已有 unverifiable 雏形，补齐字段级缺失清单 |
| E4 声明式约束（远期） | 规则编译为类 SHACL 约束批量校验 | SHACL / Stardog ICV | 远期可选 |

### 结果层（闭环与解释性）
| 模式 | 说明 | 外部参照 | DocGraph 落点 |
|---|---|---|---|
| C1 违规持久对象 | 问题作为持久实体，带状态流转与受影响实体 | Violation Situation Pattern / 昆仑数智闭环 | ReviewResult 关联图谱实体 + 问题状态机 |
| C2 严重度分级 | 在 pass/fail/unverifiable 上加严重度/偏离度 | NCKG 风险等级 / Luminance | review 结果加 severity 字段 |
| C3 证据链 | 结果携带 规则→条件→字段→文档 的完整来源 | ConsistentPeer / KG-ACE 诊断报告 | detail 结构扩展 |

## 九、调研结论

1. **"审查意图"应建模为结构化语义（条件/断言/例外/聚合/角色），而非单条比对边**——这是 DocGraph"过于粗糙"的根因，外部主流做法一致（NCKG、GRAPH-GRPO-LEX、昆仑数智规则引擎）。
2. **多单汇总是一等语义**（用户已确认），外部案例（MMLogicInt、GAIJ）均以实体/条目聚合为核。
3. **执行层"声明式规则 + 可解释验证器"优于"全量拉边 + 散装 Python 判断"**；DocGraph 可渐进升级，不必推翻。
4. **结果需要闭环**：违规持久化、严重度、证据链是合规系统的标配，DocGraph 目前只有"一次性结果展示"。
5. 建议转化 3 个实施批次（7-9），见 task_plan.md。
