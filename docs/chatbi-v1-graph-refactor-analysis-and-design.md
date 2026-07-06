# ChatBI v1 图流程深度分析与重构架构设计

- 分析基准：分支 `codex/headless-dataset-chat` 当前工作区（含未提交改动），2026-07-04。
- 与 `chatbi-v1-optimization-analysis.md`（v1.0，2026-07-03）的关系：该报告的方向性结论（缺策略路由、缺结果校验、`selected_assets` 与 `slot_bindings` 冗余、跨模型结果结构不统一）仍然成立，本文不重复展开；本文在其基础上补充**代码级验证过的正确性缺陷**、**宽表资产设计下的 SQL 生成覆盖度分析**、**上下文的目标模型**和**可分步落地的重构架构**，并在第 6 节给出两份文档的结论对照。
- 本文结构对应四个问题：①当前流程方案是否合理、哪里可以优化（第 2 节）；②当前图流程能否覆盖"领域 + 单张宽表承载指标维度"资产设计下的 SQL 生成、能否覆盖用户各类问题（第 3 节）；③当前上下文设计是否合理（第 4 节）；④重构编码时的架构设计与迁移计划（第 5 节）。

---

## 1. 现状精确画像

先把"现在到底在跑什么"钉死，后面的分析都以此为基准。

### 1.1 引擎能力边界（apps/workflow_engine）

| 能力 | 现状 | 代码位置 |
| --- | --- | --- |
| 执行模型 | 单 Run、单活跃节点、同步 while 循环；**不支持图级并行节点** | `runtime/graph_runtime.py:33-172` |
| 路由 | 命名条件按边优先级求值，条件异常吞掉后落默认边 | `runtime/router.py:40-88` |
| 循环保护 | 每节点 visit 计数 ≤ `max_loop_iterations`（v1 = 3），超出即 Run 失败 | `graph_runtime.py:121-129` |
| 超时 | 墙钟时间：`now - run.created_at > run_timeout_ms`（v1 = 120s） | `graph_runtime.py:110-113` |
| 节点失败 | 节点 FAILED（含 adapter 抛异常）⇒ **整个 Run 失败**，路由器不参与失败路径 | `graph_runtime.py:133-137`、`nodes/v1.py:46-54` |
| 上下文写入 | 节点只能写 `conversation`/`variables`；每个 patch 全量 dump→改→revalidate | `runtime/context_patcher.py:25-67` |
| 节点 IO 映射 | 引擎支持 `input_mapping`/`output_mapping`（`runtime/mapping.py`），**v1 业务节点完全未用**——每个节点拿全量 `context_view` | `runtime/scheduler.py:50-61`、`nodes/v1.py:27-44` |
| 幂等 | 进程内存字典，按 `(run, node, visit, attempt)`；每请求新建 runtime 实例，**跨恢复/跨进程无效** | `runtime/scheduler.py:27-42`、`api/service.py:147-154` |
| 交互 | 节点返回 WAITING_INPUT 暂停；resume 时把回答原样写入 `allowed_update_paths` + 业务 ResponsePatcher 二次改写，再从交互节点按条件路由 | `graph_runtime.py:174-228` |
| 检查点 | 每节点边界存 Run 快照（DB）+ 内存 checkpoint 列表；`node.succeeded` 事件携带**节点全量输出**作为 summary | `runtime/checkpoint_manager.py:44-89`、`graph_runtime.py:285-297` |
| 事件 | 全部落库（outbox），SSE 轮询 0.5s 续传 | `infrastructure/events/publisher.py`、`api/service.py:201-235` |

结论：引擎是一个克制、可审计的确定性状态机，这个底座值得保留。业务层的多数问题**不是引擎缺能力，而是业务层没有用好或用错了引擎**（IO 映射闲置、失败语义误用、超时语义误配）。

### 1.2 v1 业务图的实际形态

`definitions/chatbi_v1.py`：21 个节点（15 能力 + 5 交互 + 1 终止），入口 `classify_question`。每个节点的真实成本：

| 节点 | 真实实现 | LLM 调用 | DB/检索 |
| --- | --- | --- | --- |
| classify_question | LLM 分类，**失败直接抛异常** | 1 | - |
| rewrite_question | LLM 重写，失败有规则兜底 | 1 | - |
| draw_image_profile | **仍是 placeholder**（real gateway 无此分支，落到占位网关） | 0 | - |
| recognize_intent | 3 个 LLM 子任务并行（shape/semantic/dimensions）+ 校验重试（最多 ×2）+ 规则兜底 | 3-6 | 全量加载 dataset schema |
| retrieve_knowledge | 词面匹配 + 文档最长公共子串打分 + 指标 embedding 检索 + 门控 + 槽位绑定 | 0 | 再次全量加载 schema + embedding 查询 |
| generate_sql | **确定性语义编译器**（`SemanticSQLCompiler`），非 LLM | 0 | 再次全量加载 schema |
| execute_sql | 权限改写 + 执行 + 采样 50 行 | 0 | 目标库查询 |
| handle_sql_error | 规则修复策略 | 0 | - |
| generate_question_answer | LLM 生成回答，**prompt 注入全量 variables** | 1 | - |
| recommend_questions | 字符串模板拼接（占位级） | 0 | - |
| compose_final_reply | 本地拼装，把占位 image_profile 当 chart 返回 | 0 | - |
| 5 个 ask_* 交互节点 | 生成澄清卡片（部分会再次加载 schema） | 0 | 0-1 次 schema |

两个重要的定性结论：

1. **当前主链路不是 Text2SQL，而是"LLM 做理解、编译器做 SQL"**。SQL 由 `slot_bindings` 确定性编译产出（`sql.py:55-64`），口径可控性其实优于两份文档描述的"LLM 生成 SQL"。这是现有实现最有价值的资产，重构必须保住。
2. 一次顺利的问数 ≈ **6-8 次 LLM 调用 + 3-5 次全量 schema 加载（每次 6-8 条 SQL 查询，无缓存，`headless/service.py:241` 起）**。延迟大头在 classify/rewrite/intent/answer 四段串行 LLM 上。

### 1.3 与两份现有文档的偏差

| 文档描述 | 代码现实 |
| --- | --- |
| `agentic-rag-*.md` 的策略路由 / 模板策略 / Validator / 回退循环 | 均不存在；单策略 + 一个 SQL 执行错误重试环 |
| 文档说 SQL 由 LLM 生成 | 实际是语义编译器，无 LLM |
| 文档未提及 | 代码已有：draw_image_profile、跨模型拆分确认、指标歧义选择、主题域裁剪、意图并行子任务 |
| `chatbi-v1-context-fields.md` | 与代码基本一致，但滞后于 `slot_bindings` 新增的 `business_dimensions`/`time_dimensions`/`group_dimensions`/`value_filters`/`dimension_filters`/`time_filters` 六个键（`knowledge.py:858-878`） |

---

## 2. 第一部分：当前流程方案合理性分析

### 2.1 值得保留的设计（重构的"不动产"）

1. **图定义 / 条件 / 能力三层分离**：`WorkflowDefinition` 声明结构，`conditions/core.py` 全部是无副作用的确定性条件，能力经 Gateway → Adapter 收口。结构是对的。
2. **澄清前置**：在 SQL 生成前完成意图与资产歧义消解，避免"先查错再澄清"。
3. **确定性 SQL 编译**：口径可控、可解释（`used_assets`、`decision.reason`），这是对 ChatBI 场景正确的技术选择。
4. **意图识别的工程化**：并行子任务 + 超时兜底 + 违例检测重试（`question.py:1139-1191`、`intent_validation.py`），是全链路完成度最高的一段。
5. **可审计性骨架**：每步路由带 `reason_code/reason_summary`，事件全量落库，trace API 可回放。

### 2.2 缺陷与优化点清单

按严重程度分三级：A = 正确性缺陷（当前行为就是错的，先于一切重构修复）；B = 结构性问题（重构的主要对象）；C = 性能与可观测。

#### A 类：正确性缺陷

**A1. 运行超时把用户思考时间计入，澄清后恢复大概率直接失败。**
`graph_runtime.py:110-113` 用 `now - run.created_at` 与 `run_timeout_ms`（v1 = 120s，`chatbi_v1.py:246`）比较；`resume()` 恢复后第一轮循环就做该检查，而 `created_at` 在仓储中原样保留（`run_repository.py:51-65` 不更新它）。**用户在澄清卡片上停留超过 2 分钟，回答提交后 Run 立即以 `RUN_TIMEOUT_EXCEEDED` 失败。** 测试全部即时应答，所以没暴露。
修复方向：超时语义改为"活跃执行时间"——在 control 里累计 `active_ms`，暂停期间不计。

**A2. `interaction.answered / interaction.skipped` 是跨交互全局条件，多轮澄清运行中"跳过"失效并导致 Run 失败。**
两个条件同时检查全部 5 个 `*_response` 变量（`conditions/core.py:182-222`），而这些变量**从不被清理**（业务代码零处 `remove_paths`）。推演：用户先回答了重写澄清（`rewrite_response` 留存）→ 后续意图澄清用户点"跳过"→ 从 `ask_intent_clarification` 路由时 `interaction.answered`（priority 0）因残留的 `rewrite_response` 命中 → 回到 `recognize_intent` 而不是兜底回答 → 意图依旧歧义 → 再次澄清 → 再跳过 → `recognize_intent` visit 4 > 3 → `LOOP_ITERATION_LIMIT_EXCEEDED`，**整个 Run 失败**。`test_interaction_conditions.py` 只测了单变量场景，未覆盖串扰。
修复方向：交互回答按节点作用域判定——每个交互节点的出边绑定"只看自己回答"的条件（见 5.3.1）。

**A3. 节点异常 = 整个 Run 失败，缺少面向用户的兜底回复路径。**
`ChatBIV1CapabilityNode` 把 adapter 异常转成 FAILED 且 `retryable=False`（`nodes/v1.py:46-54`），引擎随即终止 Run（`graph_runtime.py:133-137`）。以下都是现实可触发的"整场失败"：
- 分类模型调用失败/输出不合法：`question.py:1016-1025` 直接 raise（rewrite/intent 都有兜底，唯独入口节点没有）；
- SQL 校验失败：`sql.py:66-68` raise；
- 修复重试生成了相同 SQL：`sql.py:390-397` raise `SQL_REPAIR_REGENERATED_SAME_SQL`——而编译器是确定性的，槽位不变时重编译**必然**产出相同 SQL，该异常几乎是修复环的常规出口；
- knowledge / schema 加载的任何 DB 异常。
用户侧表现为一条 `run.failed` 事件，没有任何解释性回答。图里明明有 `generate_question_answer` 这个兜底节点，失败路径却到不了它。
修复方向：业务失败降级为**结构化失败输出 + 条件路由到解释性回答**，Run 失败只留给引擎级不可恢复错误（见 5.3.2）。

**A4. `max_loop_iterations=3` 把死循环保护与合法澄清轮次混为一谈。**
每次澄清应答都会使被回访节点 visit +1（`graph_runtime.py:121-123`），意味着任何一个澄清点最多 2-3 轮，第 4 次回访直接 Run 失败，且失败码是面向开发者的 `LOOP_ITERATION_LIMIT_EXCEEDED`。
修复方向：交互轮次单独计数、超限走"澄清失败 → 兜底回答"业务边；`max_loop_iterations` 只留作最后的安全网并调大。

**A5. `retry` 接口把上下文清零重跑，与"从最近检查点恢复"的语义不符。**
`api/service.py:374-391` 将 `variables` 整体清空、游标重置到起点。已花费的 LLM 调用与用户已补充的澄清全部丢弃。检查点机制存了快照却没有被 retry 使用。

**A6. 大部分中文时间范围编译为垃圾谓词或被静默丢弃——"本月销售额"今天会得到错误结果。**
`normalize_time_range`（`time_slots.py:42-118`）能归一出 `single_date` / `relative_range(day|week|month|year)` / `absolute_range` / `current_period` / `previous_period` 六类结构，但编译器 `_time_filter_condition`（`sql_compiler.py:453-479`）**只渲染其中三种**：`single_date`、`relative_range` 且单位为 day、`absolute_range`。其余（本周/本月/上月/本季度/今年/去年/近 3 个月/近 2 周…）落入 `_literal(dict)` 分支，把整个 Python dict 转成字符串字面量拼进 SQL：`t.date = '{"kind": "current_period", ...}'`——结果要么空集要么执行报错。时间过滤测试（`test_sql_compiler_time_filters.py`）恰好只覆盖了能用的三种。
修复方向：补齐六类时间结构的确定性渲染（全部是纯日期运算）；凡编译器不认识的 kind **必须显式失败/澄清，禁止静默拼接**。

**A7. 趋势查询没有时间分桶，退化为区间总量。**
时间维度只会进 WHERE（`time_filters`），永远不进 SELECT/GROUP BY（`slot_bindings.group_dimensions` 只收业务维度，`knowledge.py:896-919`）；意图层识别出的 `query_shape.time_grain` 在全代码库**没有任何 SQL 侧消费方**（唯一引用是 knowledge 判断"是否需要时间维度"，`knowledge.py:378`）。"最近 7 天销售额趋势"实际编译为 `select sum(amount) from t where date >= ... `——单行总量，前端按趋势意图画图只有一个点。
修复方向：QueryPlan 引入 `time.grain`，编译器支持按粒度分桶（date_trunc 类表达，需按数据源方言渲染），见第 3、5 节。

#### B 类：结构性问题（重构主对象）

**B1. 单策略、无结果校验、无跨策略回退。**（旧报告已述，仍成立）
当前唯一的"回退"是 SQL 执行错误重试环，且如 A3 所述该环的常规出口是异常。空结果、数值异常、口径可疑均无校验，直接进入回答生成。
优化方向：本期先落 `validate_result` 节点（空结果/异常值检测 + 结构化建议），策略路由以"扩展点"形式预埋而非立刻建满多策略（见 5.3.4）。

**B2. 交互回答的消费机制三头并存，且跨层调用私有方法。**
同一份用户回答有三条消费路径：① 原样写入 `variables.*_response`（引擎 `allowed_update_paths`）；② `ChatBIV1InteractionResponsePatcher` 深拷贝改写 `intent`/`knowledge`（`runtime.py:60-359`）；③ adapter 重跑时自行读取 `*_response` 当 `user_feedback`（`question.py:1035/1065`）。其中 ② 直接调用 `HeadlessKnowledgeAdapter._constrain_selected_assets_to_metric_models` 等**另一个类的私有方法**（`runtime.py:122-124`），把资产绑定逻辑复制到了装配层。任何 knowledge 结构调整都要同步改三处。
优化方向：回答消费收敛为一种机制——交互回答作为节点重跑的输入（见 5.3.1），ResponsePatcher 移除或降级为纯搬运。

**B3. 跨模型链路与单查询链路的结果结构多态。**（旧报告已述，仍成立并可补充证据）
`execute_split` 的 `sql_execution.rows` 里放的是"子查询结果对象"而非数据行，`fields` 是伪表头（`sql.py:225-236`）；答案生成与前端都要按形状分支。另外拆分执行是**串行**的（`sql.py:183-223`），子查询天然可并行。

**B4. followup 分类存在，多轮上下文却是空的。**
`conversation` 只有 `{"question": ...}`（`api/service.py:111`），rewrite 的"结合上文补全"没有真实上文可用；followup 与 data 走完全相同的链路。要么接入会话历史，要么当前阶段明确砍掉 followup 语义。

**B5. 回答生成把全量 `variables` 序列化进 prompt。**
`answer.py:54-58` 把 candidate_groups（含每个候选完整 payload）、50 行采样数据、全部中间结构一并 JSON 进 user prompt。token 成本失控且把内部结构暴露给模型；数据行多时回答质量反而被淹没。
优化方向：定义回答生成的**投影视图**（问题、口径说明、字段、少量行、执行统计、警告）。

**B6. 图表形态链路是断的。**
`draw_image_profile` 是占位实现（real gateway 无分支，`real.py:41-85`），位置又在意图识别之前——数据形状未知时决定图表；`compose_final_reply` 再把这个占位结果当 chart 返回（`answer.py:145-151`）。旧报告建议"后移或二次校验"，结合现实更彻底的做法：**该节点从主链路移除**，图表决策放到结果校验之后基于真实数据形状做（可以是纯规则），`rewrite.image_profile_hint` 保留为用户偏好输入。

**B7. 兜底规则里全是写死的业务词表。**
分类缺 dataset 判为 forbidden（`question.py:1009`，语义应为配置错误而非违规提问）；意图兜底的指标/维度关键词表（`question.py:2045-2070`）、检索打分的 BI 词根表（`knowledge.py:1435-1457`）、澄清卡片的默认选项"访问人数/销售额/订单数"（`interaction.py:396-409`）都是占位期遗产，换一个数据集就失真。
优化方向：兜底词表从语义层资产动态生成或删除；占位默认选项删除。

**B8. 阈值与开关散落硬编码。**（旧报告已述，仍成立）`CandidateGate(0.85/0.65/0.12)`、`IntentSubtaskConfig(20s/25s)`、`sample_row_limit=50`、`max_retry_count=2` 等分散在各构造函数默认值里，无法按租户/数据集调整，A/B 只能改代码。

**B9. 维值资产的过滤绑定是死路径。**
知识检索会把命中的维度值资产放进 `slot_bindings.value_filters`（`knowledge.py:935-948`），但该绑定**没有 operator/value 结构**，而编译器 `_select_filters` 只接受 `asset_type ∈ {"", "DIMENSION"}`（`sql_compiler.py:131`）——VALUE 过滤在编译时被静默丢弃。当前维值过滤实际只依赖意图层 `dimension_slots` 抽出"维度名+值"的路径。
优化方向：绑定阶段把 VALUE 资产翻译为"父维度 = 标准值"的维度过滤（见 3.3 矩阵）。

#### C 类：性能与可观测

**C1. schema 每节点重复全量加载。** `HeadlessSchemaBuilder.build_dataset_schema` 无缓存，intent / knowledge / sql.generate / sql.generate_split / 交互卡片各调一次，每次 6-8 条 DB 查询。同一 Run 内 schema 不会变（甚至有 `schema_version` 字段可做失效判断），应做 Run 级缓存。

**C2. 上下文体积无治理，放大系数很高。**
候选资产每个都带全量 `payload`（SchemaElement 完整 dump），同一资产在 candidate_groups / selected_assets / slot_bindings / ambiguities 中最多出现 4 份；50 行采样数据直接进 `variables`。而上下文每个节点边界要经历：全量 deepcopy + pydantic revalidate（`context_patcher.py:34-67`）→ Run 快照落库（`run_repository.py:59`）→ `node.succeeded` 事件再带一份全量输出落库（`checkpoint_manager.py:69-74` + `graph_runtime.py:285-297`）。一次运行同一份大 JSON 被序列化十几次。引擎有 `ArtifactRef` 协议（`domain/artifact.py`、`SqlExecuteOutput.artifact_ref` 字段都在），**从未使用**。

**C3. 检索质量：核心打分是暴力最长公共子串。**
`_longest_common_text`（`knowledge.py:1316-1330`）对问题×文档做 O(n²) 子串枚举；仅指标有 embedding 召回（`knowledge.py:1225-1269`），维度/维值/术语纯词面。中文同义表达（"营业额"→"销售额"）依赖别名维护。方向：维度/维值/术语纳入 embedding 索引，词面打分退居精确/别名命中。

**C4. 观测口径三处漂移、脱敏不一致。**
trace 的节点→输出路径表 `V1_TRACE_OUTPUT_PATHS`（`api/service.py:55-74`）缺 `ask_cross_model_split`/`generate_split_queries`/`execute_split_queries` 三个节点——跨模型链路在 trace 里不可见；前端 `GRAPH_NODE_LABELS`（`graphWorkflowDisplay.ts:78-98`）再手抄一份节点清单。trace 端 SQL 脱敏（`_sanitize_trace_output`）而事件端全文落库并推给前端——同一数据两个通道两种口径。
方向：节点显示名、trace 输出路径、脱敏策略作为**节点 metadata 进图定义**，API 与前端从定义派生（definition 已有 `metadata` 字段，`domain/definition.py:52`）。

**C5. 重复工具函数语义分叉。** `_int_or_none` 三处实现三种行为（`knowledge.py:1167` 只收 int；`sql.py:437` 收数字串；`question.py:716` 收数字串但拒 bool）；`_is_time_*` 判定逻辑在 question/knowledge/time_slots 三处近似重复。收敛到公共模块。

**C6. SQL 渲染硬编码 MySQL 方言。** `DATE_SUB/DATE_ADD/CURRENT_DATE`（`sql_compiler.py:498-504`）不区分目标数据源；宽表落在非 MySQL 引擎（Postgres/达梦/ClickHouse 等）时时间条件可能不可执行。项目已依赖 sqlglot（权限适配器在用），应改为"构建表达式 → 按 datasource 方言 transpile"。

**C7. LLM 客户端与执行模型的约束。** `DefaultQuestionClassificationModelClient._get_llm` 在事件循环内直接 raise（`question.py:949-960`）——当前靠"SSE 走线程"绕开，是隐性契约；整个 Run 在请求线程同步执行（`api/service.py:106-115`），长查询占住 worker。短期可接受，需在架构上标记为已知约束。

### 2.3 优化优先级总表

| 级别 | 项 | 动作 |
| --- | --- | --- |
| P0（先修，不等重构） | A1 超时语义、A2 交互串扰、A3 失败兜底、A4 澄清轮次、A6 时间谓词正确性 | 小步修复 + 补回归测试 |
| P1（重构主体） | A7 时间分桶、B2 交互机制、B3 结果统一、B5 回答投影、B9 维值过滤、C1 schema 缓存、C2 体积治理、C6 方言渲染、上下文重构（第 4 节） | 按第 5.4 节迁移计划 |
| P1.5 | B1 结果校验 + 策略扩展点、覆盖度闭环（第 3.4 节：比较/占比/明细）、B6 图表链路、A5 retry 语义 | 迁移计划后段 |
| P2 | C3 检索升级、B4 多轮上下文、B7/B8 配置化、受约束宽表 LLM 回退、多策略实体化（模板策略需先有模板资产） | 独立立项 |

---

## 3. 第二部分：宽表资产设计下的 SQL 生成覆盖度分析

问题：资产按「领域（主题域）→ 模型 = 单张宽表 → 宽表上定义指标/维度」组织，当前图流程能否覆盖宽表的 SQL 生成、能否覆盖用户各种各样的问题？

### 3.1 先说结论

1. **图流程的骨架能覆盖宽表设计，且宽表是对这套架构最有利的形态。** 编译器里最复杂、最脆弱的部分是多模型 JOIN 编排（`sql_compiler.py:257-297`），单宽表下它整段不被触发；跨宽表（跨模型）问题已有拆分确认 + 分别查询机制。"理解 → 检索 → 绑定 → 编译 → 执行 → 回答"的骨架不需要为宽表做结构性改动。
2. **真正的覆盖缺口不在图结构，而在两处：查询计划 IR 与编译器的"单表查询形态表达力"，以及不可表达时的"静默降级"。** 当前编译器只会一种句型：`SELECT 维度, 聚合(指标) FROM 宽表 WHERE 过滤 [GROUP BY 维度] [ORDER BY 别名] [LIMIT n]`。意图层认识 8 种分析类型，编译器只能忠实表达其中 2-3 种，其余要么算错（A6/A7）、要么悄悄退化成普通聚合并装作回答了用户的问题（比较/占比/指标阈值）。**比"不支持"更危险的是"自信地给出另一个问题的答案"。**
3. 按 3.4 节的处理矩阵补齐后，8 类意图中 6 类（指标值/趋势/排名/占比/比较/明细）可以**确定性**覆盖（不依赖 LLM 写 SQL），归因类明确降级为"给出趋势+提示"，长尾问题可选接"受约束的单表 LLM 回退"——宽表恰好也是 LLM 生成 SQL 最安全的形态（单表、列清单封闭、无 JOIN 风险）。

### 3.2 编译器当前的表达力边界（精确清单）

可表达（`sql_compiler.py:32-105`）：

- SELECT：维度列（支持 expr）+ 聚合指标（SUM/COUNT/AVG/MIN/MAX/COUNT_DISTINCT，或资产自带含聚合的派生表达式，如 `sum(a)/count(b)`）；
- FROM：单表 / 子查询（`tableQuery`/`sqlQuery`），多模型按声明的 join 关系拼接（宽表场景不触发）；
- WHERE：模型级 `filterSql` + 维度过滤（`= != <> > < >= <= like`）+ 三种时间结构（今天/昨天类单日、近 N **天**、绝对月）；
- GROUP BY：有聚合指标时按维度分组；ORDER BY：按输出别名、方向受控（适配层只绑第一个指标，`sql.py:322-337`）；LIMIT：1-1000。

不可表达（且多数**静默**出错或降级）：

| # | 缺口 | 现状行为 |
| --- | --- | --- |
| G1 | 本周/本月/上月/季度/今年/去年/近 N 周/月/年 的时间过滤 | **A6：拼出垃圾谓词**（dict 转字符串进 SQL），空结果或报错 |
| G2 | 时间分桶（按天/周/月聚合） | **A7：退化为区间总量**，time_grain 无消费方 |
| G3 | 同比/环比/对比（comparison_target） | 静默降级为普通聚合；`_missing_required_slots` 不检查 comparison_target（`knowledge.py:755-784`） |
| G4 | 占比/构成（需要 part/total 或窗口） | 给出分组绝对值；百分比全靠回答 LLM 对 50 行采样"心算" |
| G5 | 明细查询（raw select，不聚合） | 指标列被强制套聚合；只能投影已注册为维度的列 |
| G6 | 指标阈值过滤（HAVING，"销售额大于 1 万的店铺"） | 条件被静默忽略 |
| G7 | 组内 TopN（"每个区域前 3 的商品"，窗口函数） | 退化为全局排序 |
| G8 | 多时间窗（"本月和上月各多少"） | 归一层标 conflict → 走澄清（尚可接受） |
| G9 | 维值资产过滤（VALUE → 父维度=值） | **B9：编译时被静默丢弃** |
| G10 | 按维度/次指标排序 | 适配层只绑 metrics[0] |
| G11 | 非 MySQL 方言的时间函数 | C6：DATE_SUB 等硬编码 |

### 3.3 用户问题类型覆盖矩阵

以意图层自己的 8 类分类为纲（补充常见形态），逐类给出"今天会发生什么"与"目标处理"：

| 问题类型 | 例句 | 今日实际行为 | 根因 | 目标处理（落点见 5.4） |
| --- | --- | --- | --- | --- |
| 指标值（单日/近N天/绝对月） | "昨天销售额""近 7 天订单数" | ✅ 正确 | - | 保持（编译器） |
| 指标值（本月/上月/今年/近3个月…） | "本月销售额" | ❌ 错误结果或执行报错 | G1/A6 | **Step 0 修编译器时间渲染** |
| 趋势 | "近 30 天销售额趋势""按月看" | ❌ 单行总量冒充趋势 | G2/A7 | Step 2：plan.time.grain + 分桶渲染（方言化） |
| 排名 TopN | "销售额最高的 5 个店铺" | ✅ 基本正确（分组+排序+limit） | G10 小瑕疵 | 保持；order 扩展到维度/次指标 |
| 对比（同比/环比/两对象） | "销售额环比上月呢" | ⚠️ 静默降级：算的是普通聚合，回答装作比较 | G3 | Step 5：**双时间窗子查询**（复用既有拆分执行机制）+ 回答侧确定性合并 |
| 占比/构成 | "各渠道销售额占比" | ⚠️ 半靠运气：绝对值+LLM 心算 | G4 | Step 5：part+total 双查询，回答侧确定性算百分比 |
| 明细 | "昨天的订单明细" | ⚠️ 只能投影已注册维度列，指标被聚合 | G5 | Step 5：plan.select_mode=detail + 编译器 raw 投影 |
| 归因/异常 | "为什么销售额下降" | ⚠️ 当普通查询处理 | 多步分析未实现 | 显式降级：给出趋势 + 说明"归因分析暂不支持"；多步分析独立立项（蓝图 P3） |
| 指标阈值筛选 | "销售额超过 1 万的店铺" | ❌ 条件静默丢失 | G6 | Step 5：plan.having + HAVING 渲染 |
| 组内 TopN | "每个区域销售额前 3 的商品" | ❌ 退化为全局排序 | G7 | P2：窗口函数扩展或受约束 LLM 回退 |
| 维值筛选 | "北京的销售额"（"北京"作为维值资产命中） | ⚠️ 依赖意图层抽出"城市=北京"；VALUE 资产路径是死的 | G9/B9 | Step 2：VALUE → 父维度=标准值 的绑定翻译 |
| 比率类指标 | "客单价" | ✅ 前提是资产用**含聚合的表达式**定义（`sum(amt)/count(id)`）；裸表达式会被套 SUM 算错 | `_metric_measure_expr` 规则 | 资产建模守则 + 建模期校验（3.5） |
| 语义层未注册的列/口径 | 宽表里有列但没建资产 | ✅ 按设计 missed → 澄清/解释 | 语义层即覆盖边界 | 治理上正确；可选：受约束 LLM 回退兜长尾（3.4） |

**读法**：✅ 3 类、⚠️/❌ 9 类里，除"组内 TopN"和"归因"外，其余全部可以在**不引入 LLM 写 SQL**的前提下，通过「计划 IR 扩展 + 编译器句型扩展 + 多查询计划」确定性补齐。这就是"图流程能不能覆盖"的量化答案：骨架能，表达力差 6 个句型特性 + 1 个多查询泛化。

### 3.4 覆盖策略：四条处理通道 + 能力矩阵路由

架构上把"一个问题如何被覆盖"收敛为四条明确通道，由绑定阶段依据**策略能力矩阵**（intent_type × query_shape → 通道）做显式决策，写入 `plan.status/strategy`，杜绝静默降级：

1. **编译器直出**（首选）：指标值/趋势/排名/明细/阈值筛选——扩展后的单语句句型；
2. **多查询计划**：比较（两个时间窗）、占比（part+total）、跨宽表——**泛化既有 `multi_query_plans` + `generate_split_queries/execute_split_queries` 机制**（图结构已备，只差计划语义与回答侧合并）；
3. **受约束 LLM 回退**（P2、可配置开关）：组内 TopN、复杂组合等长尾——单张宽表 + 封闭列清单 + 只读校验 + 表白名单，是 LLM 生成 SQL 风险最低的形态；产出必须带"非标准口径"标识；
4. **显式澄清/如实降级**：归因类给"趋势 + 说明"；能力矩阵判定四条通道都不可行时，回答如实说明不支持什么，而不是给出另一个问题的答案。

### 3.5 宽表资产建模守则（配套约束，防"覆盖得了但算不对"）

1. 宽表中**所有可问的列**都注册为维度或指标——语义层是覆盖边界，未注册列对用户不可见（这是治理特性，不是缺陷）；
2. 比率/派生指标必须用**含聚合的表达式**定义（`sum(a)/nullif(sum(b),0)`），禁止裸表达式（会被默认套 SUM）；
3. 时间列必须标记 `is_default_time`/粒度信息（分桶与默认时间过滤都依赖它）；
4. 维度值资产（枚举）与维度别名持续运营——当前检索对同义词的容忍度完全来自别名（C3 升级前尤其如此）；
5. 建模期增加校验器：上述守则做成资产保存时的 lint（新增到 headless 资产管理，属 P2）。

---

## 4. 第三部分：上下文设计分析

### 4.1 评估框架

对 `variables` 的 17 个顶层键，从四个维度评估：**所有权**（谁写谁读）、**生命周期**（何时产生何时失效）、**契约强度**（结构是否被类型锁定）、**体积**。

### 4.2 问题详解

**4.2.1 同一事实的多份镜像。**
一个被选中的指标资产会同时出现在：`knowledge.candidate_groups.metrics[i]`（含全量 payload）、`knowledge.selected_assets.metrics[0]`（含全量 payload）、`knowledge.slot_bindings.metrics[0]`、`knowledge.metrics[0]`（biz_name 摘要）、事后还有 `sql.used_assets`。维度更甚：`slot_bindings` 内部就有 `dimensions ⊇ business_dimensions + time_dimensions`、`group_dimensions`，`filters = value_filters + dimension_filters + time_filters` 的拼接（`knowledge.py:858-878`）——**同一字典内自带三层冗余**。
最有力的证据是消费端：`sql.py:276-307` 为了读维度和过滤条件，实现了**三代结构的兼容分支**（先看 `group_dimensions`，再看 `business_dimensions`，最后退回 `dimensions` 减 filter 资产）。上下文没有版本概念，结构演进的成本全部堆积在读取方。

**4.2.2 生命周期不受管理。**
- 5 个 `*_response` 是一次性输入，却永驻 variables，直接造成 A2 缺陷；
- `sql_error` 在修复成功后残留，`_repair_context` 靠再次判断 `retryable` 规避（`sql.py:371-388`）；
- `rewrite.need_user_input` 靠 rewrite 节点重跑覆盖来"清除"，如果模型再次返回 true 就靠 visit 上限兜底。
上下文里"事实"（问题被改写为 X）与"瞬时信号"（需要用户补充）混在同一层级，没有任何机制区分。

**4.2.3 类型契约有名无实。**
`schemas/v1.py` 的关键字段几乎全是 `dict[str, Any]`/`list[dict[str, Any]]`（intent 的 8 个结构字段、knowledge 的 candidate_groups/selected_assets/slot_bindings、sql 的 used_assets……）。真实形状只存在于生产者代码、消费者代码和 markdown 文档三处的"口头约定"里。后果具体化：ResponsePatcher、条件层、SQL 适配层到处是 `isinstance(x, dict)` 防御式取值；`chatbi-v1-context-fields.md` 已经落后于实现。

**4.2.4 结构多态。** `sql_execution` 单查询与拆分查询两种形状（B3）；`knowledge.hit`（bool）与 `knowledge.status`（枚举）语义重叠且可能组合出矛盾态（`hit=True, status=cross_model` 时既"命中"又不能直接生成 SQL）。

**4.2.5 体积。** 见 C2。上下文应存**引用与结论**，不应存原料（候选全量 payload）与产物（数据行）。

**4.2.6 时间语义分散。** 时间在 `intent.time_range`（含 normalized）、`intent.time_mentions`、`knowledge.slot_bindings.time_dimensions/time_filters`、编译器内部各处理一遍，`_is_time_*` 判定三处重复（`question.py:907`、`knowledge.py:741/792`、`time_slots.py:29`）。时间应在理解层归一一次，之后只携带结构化结果。

### 4.3 目标上下文模型

核心思想：**以"语义查询计划（QueryPlan）"为中心**。检索、门控、澄清、用户选择的全部产物，最终收敛为一个可编译、可校验、可展示的查询计划；SQL 编译只读 QueryPlan，这是唯一事实源（取代 slot_bindings 的地位并终结其内部冗余）。结合第 3 节，QueryPlan 的表达力必须**覆盖策略能力矩阵的全部通道**——它是"用户问题空间"与"SQL 生成能力"之间的显式契约。

```jsonc
{
  "variables": {
    "question": {            // 所有权：classify/rewrite；生命周期：全程
      "raw": "…",
      "classification": {"category": "data", "confidence": 0.93, "risk_level": "low"},
      "rewritten": "…",
      "image_profile_hint": "line"        // 用户偏好提示，不再是独立节点输出
    },
    "understanding": {        // 所有权：recognize_intent；纯自然语言线索层，不含资产 ID
      "intent_type": "trend_analysis",
      "confidence": 0.9,
      "metric_mentions": [], "dimension_slots": [], "time": {"raw": "…", "normalized": {…}},
      "query_shape": {…}, "subject_domain": {…},
      "open_issues": [ {"type": "ambiguous_dimension", "name": "店铺"} ]   // 取代 ambiguous/conflict_slots + validation.slot_issues 三套表达
    },
    "evidence": {              // 所有权：retrieve_knowledge；只为 trace/澄清选项服务，瘦身版
      "candidates": {"metrics": [{"asset_id": 1, "name": "销售额", "score": 0.97, "model_id": 3, "matched": "…"}], …},
      "decision": {"status": "accepted", "reason": "…"},
      "schema_version": 5, "index_version": 2
      // 不再携带资产全量 payload；编译时按 asset_id 从 schema 现取
    },
    "plan": {                  // 所有权：bind/route/用户选择；SQL 生成唯一事实源
      "status": "ready | ambiguous | infeasible | needs_split | needs_confirmation",
      "strategy": "semantic_compiler | multi_query | constrained_llm",   // 能力矩阵路由结果
      "select_mode": "aggregate | detail",
      "metrics":   [{"asset_id": 1, "name": "销售额"}],
      "group_bys": [{"asset_id": 7, "name": "店铺"}],
      "time":      {"dimension_id": 9, "range": {"kind": "relative_range", …}, "grain": "day"},  // A7：grain 进计划
      "filters":   [{"dimension_id": 7, "op": "=", "value": "1号"}],
      "having":    [{"metric_id": 1, "op": ">", "value": 10000}],        // G6
      "comparison": {"kind": "period_over_period", "baseline": {"kind": "previous_period", "unit": "month"}},  // G3
      "derived":   {"kind": "share", "denominator": "total"},            // G4
      "order": [{"ref": "metric:1", "direction": "desc"}], "limit": 10,
      "sub_plans": [ {…} ],                  // 跨宽表 + 比较窗口 + part/total：统一的多查询表达
      "issues": [ {"type": "metric_ambiguous", "candidates": [...asset_id/name/score] } ],
      "infeasible_reason": null              // 能力矩阵判定不可行时必填，回答如实转述
    },
    "execution": {             // 所有权：sql 生成/执行/校验；单/拆分统一结构
      "queries": [ {"sql": "…", "datasource_id": 13, "plan_ref": 0} ],
      "results": [ {"status": "succeeded", "row_count": 120, "fields": [...],
                    "sample_rows_ref": {"artifact": "res-0"},   // 数据行走 artifact，context 只留引用+极少量行
                    "execution_ms": 45} ],
      "validation": {"status": "passed | empty | suspicious", "issues": [], "suggestions": []},
      "error": {"code": null, "retry_count": 0, "history": ["…"]}   // 修复消费后清空 code，history 留审计
    },
    "reply": {                 // 所有权：answer/recommend/compose
      "answer": {…}, "recommendations": [], "final": {…}, "chart": {…}
    },
    "interactions": {          // 所有权：引擎 resume + 消费节点；生命周期显式
      "ask_slot_clarification": {"round": 1, "response": {…}, "consumed": true}
    }
  }
}
```

要点：

1. **写权限矩阵**：每个域只有一个所有者节点（组），条件层只读。用 pydantic 模型锁定（每域一个 model），`ChatBIV1CapabilityNode` 的 `output_model` 校验机制保留并加强。
2. **`plan` 取代 `slot_bindings` + `selected_assets` + `multi_query_plans` + `metric_selection` 的合流角色**：门控歧义 → `plan.issues`；用户选指标 → 直接改 `plan.metrics`（ResponsePatcher 的深拷贝改写逻辑随之消亡）；跨模型/比较/占比 → `plan.sub_plans`，单/拆分执行统一为 `execution.queries[]/results[]`。
3. **`plan` 的表达力 = 第 3 节矩阵的全集**：time.grain（G2）、comparison（G3）、derived.share（G4）、select_mode（G5）、having（G6）、sub_plans 泛化（G3/G4/跨表）、infeasible_reason（反静默降级）。编译器能力与 plan 表达力的差集，就是策略路由要处理的部分。
4. **`interactions` 域给回答以作用域和生命周期**，A2 类串扰在结构上不可能再发生。
5. **体积治理内建**：evidence 候选不带 payload（上限截断，如每组 top 10）；数据行进 artifact 存储（引擎协议已备）；`answer` 生成读投影而非全量。
6. **`context_version` 字段**：上下文结构演进时消费端可显式分支，替代现在"三代兼容读"的猜测式解析。
7. 摘要类字段（`knowledge.metrics` 等 biz_name 列表）删除，展示层从 evidence/plan 派生。

---

## 5. 第四部分：重构编码架构设计

### 5.1 设计原则

1. **不 big-bang**：每一步独立可合入、可回滚，现有 chatbi_workflow/workflow_engine/headless 测试始终绿。
2. **引擎最小侵入**：引擎只改超时语义、交互作用域两点，其余全部在业务层完成。
3. **先修正确性，再动结构**：A 类缺陷的修复不依赖新上下文模型，先行落地。
4. **确定性优先**：能用计划+编译器表达的绝不用 LLM 写 SQL；LLM 回退是带开关、带标识的末位通道。
5. **为多策略留门，不提前造房**：模板策略需要模板资产（表、管理界面、运营流程），本期只落策略分派接口与回退边，不实现模板本体。

### 5.2 目标分层

```text
Layer 4  definitions/     图结构 + 节点元数据（display_label、trace_path、redaction、interaction_rounds）
                          ——API trace 表与前端 label 从这里派生，消灭三处手抄
Layer 3  capabilities/
   ├─ context.py          typed 域模型 + ChatBIRunContext 读取器 + PatchBuilder（唯一写入口）
   ├─ services/           RunScopedServices：schema 缓存、LLM 客户端、embedding、配置(ChatBIConfig)
   ├─ understanding/      classify.py / rewrite.py / intent/（由 2600 行 question.py 拆分：prompts、subtasks、merge、fallback）
   ├─ retrieval/          candidate_search.py / gate.py（由 knowledge.py 拆分）
   ├─ planning/           binder.py（资产→QueryPlan，含 VALUE→维度过滤翻译）/ splitter.py（跨表+比较+占比的多查询计划）
   │                      / capability_matrix.py（intent×shape→通道路由）/ strategy.py（分派接口）
   ├─ execution/          sql_generate.py / sql_execute.py / result_validate.py / repair.py
   ├─ reply/              answer.py（投影视图）/ recommend.py / compose.py
   └─ interaction/        cards.py / responses.py（回答消费的唯一实现，替代 ResponsePatcher 的业务逻辑）
Layer 2  nodes/ + conditions/   通用节点包装（失败→结构化降级输出）+ 条件（作用域化交互条件）
Layer 1  workflow_engine/       引擎：active_ms 超时 + 交互轮次支持（微改）
Layer 0  headless/sql_compiler  句型扩展：六类时间结构渲染(A6)、时间分桶(A7)、HAVING(G6)、detail 投影(G5)、
                                方言渲染改造(sqlglot, C6)——编译器是覆盖度的核心杠杆
```

公共工具（`_int_or_none`/`_normalize_text`/时间判定）收敛到 `capabilities/shared.py`，语义统一为一份。

### 5.3 关键机制设计

**5.3.1 交互子系统（修 A2/A4，替换 B2 的三头机制）**

- 回答写入 `variables.interactions.<node_name>`：`{round, response, consumed}`；`allowed_update_paths` 指向该路径（引擎无需改写入机制）。
- 新条件工厂 `interaction.answered_at(node)` / `interaction.skipped_at(node)`：只看当前交互节点的最新回答。图定义中五个交互节点的出边分别绑定各自条件实例。
- 消费统一为"目标节点重跑时读取"：rewrite 读 `interactions.ask_rewrite_clarification.response`，intent 同理；指标选择/槽位澄清由 planning/understanding 模块在重跑中合并（`consumed=true` 后不再生效）。`ChatBIV1InteractionResponsePatcher` 整体退役。
- 轮次控制：节点 metadata 声明 `max_interaction_rounds`（默认 2）；超限走 `interaction.exhausted` 条件 → 兜底回答边。`max_loop_iterations` 上调为纯安全网（如 8）。

**5.3.2 失败降级链路（修 A3）**

- `ChatBIV1CapabilityNode` 捕获 adapter 异常后不再返回 FAILED，而是写入结构化失败输出：`{status:"failed", error_code, message, degraded:true}` 到 `variables.node_failure`，并成功返回。
- 新条件 `node.degraded` 优先路由到 `generate_question_answer`，回答生成对 degraded 上下文产出解释性文案（"未能完成查询：口径校验未通过…"）。降级边只加在回答节点之前的能力节点上；回复段（answer/recommend/compose）失败仍走 Run 失败。
- 仅保留两类真正的 Run 失败：引擎错误（路由/patch/仓储）与安全类拒绝执行。
- 修复环出口软化：`SQL_REPAIR_REGENERATED_SAME_SQL` 不再抛异常，转为 `sql_error.retryable=false` + 说明，走解释性回答。

**5.3.3 超时与重试语义（修 A1/A5）**

- 引擎 `ControlContext` 增加 `active_ms` 累计；`execute()` 每轮把本轮耗时累加，超时检查改用 `active_ms`。WAITING_INPUT 期间不累计。改动集中在 `graph_runtime.py` 单文件，行为对既有测试透明（即时应答场景数值不变）。
- `retry`：失败 Run 从最近成功检查点恢复（checkpoint 已有快照），而非清零重跑；无检查点才整场重跑。

**5.3.4 能力矩阵路由与策略扩展点（落 B1 与第 3.4 节）**

- `planning/capability_matrix.py`：`(intent_type, query_shape, plan 特征) → 通道`（compiler / multi_query / constrained_llm / clarify / infeasible）的确定性映射，是"覆盖用户各种问题"的显式机制；判定结果写 `plan.strategy/status/infeasible_reason`，**消灭静默降级**。
- `planning/strategy.py` 分派接口：本期实现 `semantic_compiler` 与 `multi_query`（比较窗口/占比/跨表复用既有拆分执行节点）；`constrained_llm`（P2）与 `template`（待资产）作为后续注册项。
- 新节点 `validate_result`（execute 之后）：空结果 / 负值指标 / 行数异常 → `execution.validation`；`result.empty` 条件走"解释 + 建议放宽条件"的回答路径（本期不自动改写重查，避免不可控的静默口径变更）。

**5.3.5 图结构调整（最小集）**

```text
删除：draw_image_profile（主链路）            // B6：hint 并入 rewrite 输出，图表决策移到校验后
新增：bind_query_plan（retrieve_knowledge 与 generate_sql 之间：evidence→plan 绑定、门控出口、能力矩阵路由）
新增：validate_result（execute_sql / execute_split_queries 之后）
边变更：五个交互节点的出边条件替换为作用域条件；回答前的能力节点补 node.degraded 边
复用：generate_split_queries / execute_split_queries 从"跨模型专用"泛化为"多查询计划执行"（比较/占比同机制）
```

其余节点与主拓扑保持不变，前端 label 通过节点 metadata 下发，展示层无需硬编码更新。

**5.3.6 观测与体积（落 C2/C4）**

- 节点 metadata 声明 `public_summary_fields`（白名单投影），`_public_node_summary` 与 trace 共用同一投影——事件与 trace 的脱敏口径合一，SQL/数据行是否公开变成一处配置。
- `execution.results[].sample_rows_ref` 走 artifact 仓储（`ArtifactRepository` 已存在）；trace/前端按需取。
- `RunScopedServices.schema(oid, dataset_id)`：Run 生命周期内缓存 DataSetSchema（修 C1），并作为所有 adapter 的注入来源。

### 5.4 迁移计划（每步独立可合入）

| 步骤 | 内容 | 验收 |
| --- | --- | --- |
| **Step 0** 缺陷修复 | A1 active_ms 超时；A2 作用域化交互条件（先在旧上下文上实现）；A3 降级链路 + 修复环出口软化；A4 轮次上限；**A6 六类时间结构的编译器渲染 + 未知 kind 显式失败**。**（已完成：见 `test_v1_clarification_regressions.py`、`test_v1_degradation.py`、`test_sql_compiler_time_filters.py`、`test_runtime_failures.py`）** | 新增回归测试：跨 2 分钟恢复、双澄清 skip、分类模型故障、修复环同 SQL、本月/上月/近3个月时间谓词；既有测试全绿 |
| **Step 1** 上下文读侧收敛 | 落 `context.py`（typed 域模型 + ChatBIRunContext），所有 adapter 改经 accessor 读取；写侧结构不变。**（已完成：`capabilities/context.py` + 六个 adapter 改造 + `test_run_context.py`）** | 纯重构，行为零变化 |
| **Step 2** 引入 QueryPlan | `bind_query_plan` 节点产出 `plan`（含 time.grain、VALUE→维度过滤翻译）；`sql.generate` 只读 plan；**编译器时间分桶（A7）+ sqlglot 方言渲染（C6）**；knowledge 输出瘦身（candidates 去 payload、删摘要字段）；保留一版 `slot_bindings` 双写供比对，验证后删除。**（已完成：planning.py + bind_query_plan 节点 + 编译器 time_bucket + candidate_groups 去 payload；golden 对比见 `test_query_plan_binding.py`。未尽事项：摘要字段与 selected_assets/ambiguities 的 payload 依赖 ResponsePatcher，随 Step 4 退役一并删除；slot_bindings 双写保留至 Step 4。）** | golden SQL 对比测试：旧可表达形态逐用例一致；趋势用例产出分桶 SQL |
| **Step 3** 执行域统一 | `execution.queries[]/results[]` 单/拆分同构；拆分子查询并行执行；rows 走 artifact；answer 改读投影视图。**（已完成：标准域与 `sql_execution` 兼容镜像、文件正文 + DB 元数据 artifact store、有界并行与部分失败保留、answer projection、Trace/前端新结构优先；见 `test_execution_domain.py`、`test_file_artifact_store.py`、`test_sql_adapter.py`、`graphWorkflowDisplay.test.mjs`。）** | 跨模型用例回答与图表数据不回归；答案 prompt 不再包含全量 variables、SQL 与候选 payload |
| **Step 4** 交互子系统定稿 | `interactions` 域 + 条件工厂 + ResponsePatcher 退役；澄清轮次进节点 metadata | 五类澄清 + 跳过 + 多轮组合的端到端测试 |
| **Step 5** 校验、能力矩阵与覆盖闭环 | `validate_result` 节点 + 空结果路径；capability_matrix + strategy 分派；**比较/占比的多查询计划**（复用拆分执行）；plan.having + detail 模式；ChatBIConfig 收拢 B8 阈值；retry 改检查点恢复 | 覆盖矩阵中 6 类意图端到端正确；"环比/占比"产出确定性计算结果；不可行问题得到如实说明 |
| **Step 6** 观测合一 | 节点 metadata（label/trace/脱敏）→ trace API 与前端派生；删除 V1_TRACE_OUTPUT_PATHS 与前端 GRAPH_NODE_LABELS 手抄表 | 跨模型节点在 trace 可见；前端零硬编码节点名 |

依赖关系：Step 0 完全独立；1→2→3 顺序执行；4、5 依赖 1；6 随时可做。P2 项（受约束宽表 LLM 回退、检索 embedding 化、多轮 conversation、模板策略实体化、资产建模 lint）在此骨架上另行立项。

### 5.5 测试策略

- **行为锁定**：Step 0 前先为"当前正确行为"补齐端到端图测试（顺利问数 / 各澄清路径 / 跨模型 / SQL 失败重试），作为重构护栏；
- **golden SQL 对比**：Step 2 的双写期用同一批意图/知识 fixture 对比新旧编译输出；
- **覆盖矩阵回归集**：3.3 节矩阵的每一行至少一个端到端用例（含"必须显式失败"的负例）；
- **缺陷回归**：每个 A 类缺陷一个最小复现测试，先红后绿；
- **上下文契约测试**：每个域模型的 schema 快照测试，结构变更必须显式改快照（配合 `context_version`）。

---

## 6. 与既有优化报告（chatbi-v1-optimization-analysis.md）的结论对照

| 旧报告结论 | 本文判定 |
| --- | --- |
| 缺策略路由 / 模板策略 / 结果校验 / 回退 | 成立；本文调整落地策略——能力矩阵 + 多查询计划先行，模板策略等资产就绪再实体化（5.3.4） |
| `selected_assets` vs `slot_bindings` 冗余，建议保留 slot_bindings | 部分修订：slot_bindings 自身已长出三层内部冗余，本文主张两者都被 QueryPlan 取代（4.3） |
| 意图识别"串行执行多个 LLM 调用"是瓶颈 | **已过时**：并行子任务已实现（question.py:1139-1191） |
| 跨模型查询串行执行、结果结构不统一 | 成立，纳入 Step 3；且拆分执行机制被泛化复用（5.3.5） |
| 统一上下文访问（ChatBIContext）、统一异常、分层配置 | 成立，分别体现为 context.py（Step 1）、降级链路（Step 0）、ChatBIConfig（Step 5） |
| 图表类型决策过早，建议后移 | 采纳并加强：该节点实为占位实现，直接移出主链路（B6） |
| 旧报告未发现 | 本文新增：A1（超时吞澄清）、A2（交互条件串扰）、A3（异常=全场失败）、A4（轮次上限）、A5（retry 清零）、**A6（本月/上月等时间范围编译为垃圾谓词）**、**A7（趋势无分桶退化为总量）**、B9（维值过滤死路径）、C1（schema 重复加载）、C2（上下文放大系数）、C4（观测三处漂移）、C6（方言硬编码）、B5（answer 全量注入）、**第 3 节 SQL 覆盖度矩阵与四通道处理策略** |

---

## 7. 结论

1. **流程骨架是对的**（确定性引擎 + 澄清前置 + 语义编译），且宽表资产设计对它是利好——join 复杂度消失，覆盖问题全部收敛为"单表查询形态的表达力"。
2. **当前最紧迫的不是架构，而是七个正确性缺陷（A1-A7）**：其中 A6/A7 意味着"本月销售额""销售额趋势"这类最高频问题今天就会给出错误或误导性的结果。
3. **"能否覆盖用户各种各样的问题"的答案是分层的**：8 类意图中 3 类今天真正可用；补齐 6 个句型特性（时间渲染、分桶、HAVING、detail、维值翻译、方言）+ 1 个多查询泛化（比较/占比复用既有拆分机制）后，6 类可确定性覆盖；归因类如实降级；长尾可选接受约束的单表 LLM 回退。覆盖的边界由语义层资产决定，这是治理特性，配套 3.5 节建模守则。
4. **重构主线**：以 QueryPlan 为中心重塑上下文（表达力 = 覆盖矩阵全集），以作用域化交互与降级链路修正控制流语义，以能力矩阵消灭静默降级，六步迁移每步可合入可回滚。

建议的执行顺序：Step 0（A1-A4 + A6，含回归测试）先行单独提交评审，其余按 5.4 推进。
