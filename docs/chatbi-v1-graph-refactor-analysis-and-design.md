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

1. **不 big-bang**：每一步独立可合入、可回滚，现有 workflow/workflow_engine/headless 测试始终绿。
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

- 节点 metadata 声明 `display.label`、`trace.output_path`、`trace.redaction`，事件公开摘要与 trace API 共用同一投影——事件与 trace 的脱敏口径合一，SQL/数据行是否公开变成一处配置。
- `execution.results[].sample_rows_ref` 走 artifact 仓储（`ArtifactRepository` 已存在）；trace/前端按需取。
- `RunScopedServices.schema(oid, dataset_id)`：Run 生命周期内缓存 DataSetSchema（修 C1），并作为所有 adapter 的注入来源。

### 5.4 迁移计划（每步独立可合入）

| 步骤 | 内容 | 验收 |
| --- | --- | --- |
| **Step 0** 缺陷修复 | A1 active_ms 超时；A2 作用域化交互条件（先在旧上下文上实现）；A3 降级链路 + 修复环出口软化；A4 轮次上限；**A6 六类时间结构的编译器渲染 + 未知 kind 显式失败**。**（已完成：见 `test_v1_clarification_regressions.py`、`test_v1_degradation.py`、`test_sql_compiler_time_filters.py`、`test_runtime_failures.py`）** | 新增回归测试：跨 2 分钟恢复、双澄清 skip、分类模型故障、修复环同 SQL、本月/上月/近3个月时间谓词；既有测试全绿 |
| **Step 1** 上下文读侧收敛 | 落 `context.py`（typed 域模型 + ChatBIRunContext），所有 adapter 改经 accessor 读取；写侧结构不变。**（已完成：`capabilities/context.py` + 六个 adapter 改造 + `test_run_context.py`）** | 纯重构，行为零变化 |
| **Step 2** 引入 QueryPlan | `bind_query_plan` 节点产出 `plan`（含 time.grain、VALUE→维度过滤翻译）；`sql.generate` 只读 plan；**编译器时间分桶（A7）+ sqlglot 方言渲染（C6）**；knowledge 输出瘦身（candidates 去 payload、删摘要字段）；保留一版 `slot_bindings` 双写供比对，验证后删除。**（已完成：planning.py + bind_query_plan 节点 + 编译器 time_bucket + candidate_groups 去 payload；golden 对比见 `test_query_plan_binding.py`。未尽事项：摘要字段与 selected_assets/ambiguities 的 payload 依赖 ResponsePatcher，随 Step 4 退役一并删除；slot_bindings 双写保留至 Step 4。）** | golden SQL 对比测试：旧可表达形态逐用例一致；趋势用例产出分桶 SQL |
| **Step 3** 执行域统一 | `execution.queries[]/results[]` 单/拆分同构；拆分子查询并行执行；rows 走 artifact；answer 改读投影视图。**（已完成：标准域与 `sql_execution` 兼容镜像、文件正文 + DB 元数据 artifact store、有界并行与部分失败保留、answer projection、Trace/前端新结构优先；见 `test_execution_domain.py`、`test_file_artifact_store.py`、`test_sql_adapter.py`、`graphWorkflowDisplay.test.mjs`。）** | 跨模型用例回答与图表数据不回归；答案 prompt 不再包含全量 variables、SQL 与候选 payload |
| **Step 4** 交互子系统定稿 | `interactions` 域 + 条件工厂 + ResponsePatcher 退役；澄清轮次进节点 metadata。**（已完成：标准 `variables.interactions.<ask_node>` 域、旧 response 字段兼容、slot/metric 回答改由消费节点处理、ChatBI runtime 不再注入 ResponsePatcher；见 `test_interactions_domain.py`、`test_interaction_conditions.py`、`test_v1_flow.py`。）** | 五类澄清 + 跳过 + 多轮组合的端到端测试 |
| **Step 5** 校验、能力矩阵与覆盖闭环 | `validate_result` 节点 + 空结果路径；capability_matrix + strategy 分派；**比较/占比的多查询计划**（复用拆分执行）；plan.having + detail 模式；ChatBIConfig 收拢 B8 阈值；retry 改检查点恢复。**（已完成：`validate_result` 图节点与 `execution.validation` 双写；空结果 `result.empty` 路由；`QueryPlan` detail/HAVING；`share_analysis` part/total 与 `comparison_analysis` current/baseline 子计划；`generate_split_queries` 优先读取 `plan.sub_plans`；answer projection 确定性计算 share/comparison；`ChatBIConfig` 接入 SQL 执行与候选门控默认值；v1 retry 保留上下文并优先从 checkpoint 恢复。见 `test_execution_domain.py`、`test_query_plan_binding.py`、`test_semantic_sql_compiler.py`、`test_question_rewrite_and_answer_adapters.py`、`test_sql_adapter.py`、`test_v1_flow.py`、`test_graph_api.py`。）** | 覆盖矩阵中 6 类意图端到端正确；"环比/占比"产出确定性计算结果；不可行问题得到如实说明 |
| **Step 6** 观测合一 | 节点 metadata（label/trace/脱敏）→ trace API 与前端派生；删除 V1_TRACE_OUTPUT_PATHS 与前端 GRAPH_NODE_LABELS 手抄表。**（已完成：v1/minimal-v1 节点 metadata 声明 display/trace/redaction；trace API 与 node.succeeded 事件共用 metadata 投影；前端优先读取后端 label，未知 label 退回节点名。见 `test_graph_api.py`、`graphWorkflowDisplay.test.mjs`。）** | 跨模型节点在 trace 可见；前端零硬编码节点名 |

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

---

## 8. Step 3–5 实现审查（2026-07-07）

对已标记"已完成"的 Step 3（执行域统一）、Step 4（交互子系统）、Step 5（校验/能力矩阵/多查询）逐文件核查代码正确性、合理性、规范性与测试质量。审查基准为 `codex/headless-dataset-chat` 当前工作区（Step 5 尚未提交）。相关测试当前全绿（`test_execution_domain.py`/`test_query_plan_binding.py`/`test_interaction_conditions.py`/`test_v1_flow.py` 共 52 passed）——但绿测并不代表覆盖闭环，下面 R1 即是"测试通过却功能断裂"的例子。

### 8.1 结论摘要

骨架落地质量高：执行域单/拆分同构、有界并行 + 部分失败保留、artifact 写入闭环、降级链路（`node.degraded`）、作用域化交互条件（修 A2）、能力矩阵反静默降级、编译器 HAVING/detail/分桶/方言渲染，都实现得干净且有测试。但**多查询确定性计算（Step 5 的占比/环比核心卖点）在真实链路里是断的**，另有若干中低风险问题。按严重程度：

| 级别 | 编号 | 一句话 |
| --- | --- | --- |
| 高 | R1 | `ExecutionQuery` 无 `role` 字段 → 占比/环比在真实链路丢失 role → answer 侧确定性合并静默失效 |
| 中 | R2 | 环比 baseline 只认 `current_period`，`relative_range`/绝对区间的对比落空且无提示 |
| 中 | R3 | `validate_result` 的 `suspicious`（部分子查询空）无路由边，等同 passed |
| 中 | R4 | 能力矩阵 `_MULTI_QUERY_INTENTS` 缺 sub_plans 时判 `infeasible`，把"占比但没绑上时间/维度"的常见问句直接打成不可行 |
| 低 | R5 | `build_interaction_record` 为死代码（引擎 resume 内联了等价逻辑），`consumed` 字段设计文档写了但未落地 |
| 低 | R6 | `handle_error` 只读单查询 `sql_error`，拆分执行的部分失败无法进修复环 |
| 低 | R7 | share 分母 `_first_numeric` 靠"第一个数值列"猜指标，多指标/维度值为数字时会错配 |

### 8.2 高风险

**R1（高）：多查询 `role` 在执行域被丢弃，占比/环比确定性计算在真实链路失效。~~【已修复 2026-07-07】~~**
`planning._sub_plan` 产出 `{role, slots}`，`sql.generate_split` 把 `role` 写进 `split_sql.queries[]`（`sql.py:265-275`）；但 `execute_split` 构造 `ExecutionQuery` 时**没有 role 字段**（`execution.py:42-51` 模型里就没有 role），`build_execution_output` 序列化 `execution.queries[]` 自然不含 role。而 answer 投影 `_results_by_role` 正是靠 `execution.queries[].role` 把结果映射到 part/total、current/baseline（`answer.py:185-201`）——真实链路里这个 map 恒为空，`_project_multi_query_analysis` 永远返回 `{}`，占比/环比退回"LLM 对采样行心算"，即 Step 5 声称要消灭的 G3/G4 静默降级。
**为什么测试没抓到**：`test_answer_projection_calculates_share_analysis_rows`/`_comparison_delta` 直接手工构造带 `role` 的 `execution.queries`（`test_question_rewrite_and_answer_adapters.py:1234-1235/1283-1284`），绕过了 generate_split→execute_split 这段真实路径；`test_sql_adapter` 的拆分用例只断言 SQL 与 model_id，不校验 role 透传。
**修法**：`ExecutionQuery` 增 `role: str | None`，`execute_split` 从 `raw_query.get("role")` 透传，`build_execution_output` 序列化保留；补一个 generate_split→execute_split→build_answer_projection 的端到端用例锁定 role 贯通。（`plan_ref` 已透传，次选方案是让 `_results_by_role` 回退用 `plan_ref` 对齐 `plan.sub_plans[i].role`，但显式 role 字段更直接。）
**修复实现（2026-07-07）**：`ExecutionQuery` 已增 `role: str | None`（`execution.py:47`）；`execute_split` 从 `raw_query.get("role")` 透传（`sql.py:309`）；`build_execution_output` 经 `model_dump` 自动保留 role。新增端到端回归 `test_execute_split_preserves_sub_plan_role_for_share_analysis_e2e`（`test_sql_adapter.py`），走 generate_split→execute_split→build_answer_projection 全链路，断言 `execution.queries[].role` 贯通且 `execution.analysis.kind=="share"`、`total==100`。全量 238 项相关测试通过。

### 8.3 中风险

**R2（中）：环比 baseline 推导只覆盖 `current_period`。~~【已修复 2026-07-07】~~**
`_comparison_sub_plans` 用 `_current_period_filter_index` 找过滤条件，只在 `kind=="current_period"` 时命中（`planning.py:287-294`），随后把 baseline 平移为 `previous_period`。"本月 vs 上月"可行，但"最近 7 天 vs 前 7 天"（`relative_range`）、"2026-06 vs 2025-06"（同比、`absolute_range`）都拿不到 index → 返回 `[]` → sub_plans 少于 2 → 能力矩阵判 `infeasible`。方向本身对（确定性平移窗口），但覆盖面比文档 3.3 承诺的"同比/环比/两对象"窄。**修法**：为 `relative_range`/`absolute_range` 补 baseline 平移规则，或在 8.4-R4 的前提下把"识别到 comparison 但无法构窗"如实降级为带说明的解释性回答，而非笼统 infeasible。
**修复实现（2026-07-07）**：新增 `_baseline_time_value` 统一平移逻辑：`current_period → previous_period`（原有）+ **月对齐 `absolute_range` → 上一自然月区间**（新增，纯字面日期运算，含跨年边界，`planning.py`）；非月对齐区间与 `relative_range` 不做易错的编译器日期平移，改走 R4 降级。回归用例 `test_binder_builds_comparison_sub_plans_for_absolute_month_range` 锁定"2026-06 → 2026-05"平移，边界（跨年、非整月拒绝）经手工验证。baseline 产出的是编译器已支持的 `absolute_range` 结构，无需改编译器。

**R3（中）：`validation.status=="suspicious"` 没有路由消费。~~【已修复 2026-07-07】~~**
`validate_execution_output` 会对"部分子查询空"产出 `suspicious`（`execution.py:178-183`），但图里只有 `result.empty`（匹配 `status=="empty"`）一条边（`chatbi_v1.py:443-448`、`core.py:319-330`）。`suspicious` 既不进空结果解释路径也无专门提示，落到默认边直接当正常结果回答——占比场景 part 有值 total 空（分母缺失）时尤其危险。**修法**：要么把 `suspicious` 纳入 `result.empty` 的解释路径，要么新增 `result.suspicious` 边；answer 投影已透传 `validation`，回答侧也应读到并提示。
**修复实现（2026-07-07）**：新增 `ResultSuspiciousCondition`（`core.py`，注册为 `result.suspicious`），与 `result.empty` 平级、互斥；`validate_result` 增一条 `result.suspicious`（priority 2）出边路由到 `generate_question_answer`，`route_reason=RESULT_SUSPICIOUS` 在 trace 可见。answer projection 本就透传 `validation.status/issues/suggestions`，回答 LLM 据此如实提示口径风险。回归用例 `test_validate_execution_output_marks_partial_empty_as_suspicious`（part 有值 + total 空 → suspicious），并更新 `test_v1_definition` 的条件全集断言。

**R4（中）：能力矩阵对多查询意图"无 sub_plans 即 infeasible"过于激进。~~【已修复 2026-07-07】~~**
`decide_capability` 里 `comparison_analysis`/`share_analysis` 只要 `sub_plans<2` 就 `infeasible`（`capability_matrix.py:35-43`）。但 sub_plans 是否生成强依赖 R2 的时间窗识别与"metrics+group_bys 齐备"（`_share_sub_plans` 要求 group_bys 非空）。结果："各渠道销售额占比"若维度没绑上、"环比"若时间是 relative_range，都会被判成不可行并如实告知"不支持"——而这些正是文档矩阵里承诺确定性覆盖的高频问句。**这是"反静默降级"用力过猛变成"假阴性"**：把可降级为普通聚合 + 提示的情况，报成了能力缺失。**修法**：区分"意图是多查询但计划要素不全"（应澄清或降级为单查询 + 说明）与"真正不可表达"（infeasible）；前者不应占用 infeasible 语义。
**修复实现（2026-07-07）**：`decide_capability` 对"多查询意图但 sub_plans 不足"改为返回 `status="ready", strategy="semantic_compiler"` + `downgrade_note`，而非 `infeasible`（`CapabilityDecision` 新增 `downgrade_note` 字段）。binder 把降级说明写入 `plan.issues`（`type="multi_query_downgraded_to_single"`）并清空 sub_plans，走单查询聚合 + 如实提示；`unsupported_intent_type`（真正不可表达）仍判 infeasible 不变。回归用例：`test_binder_downgrades_comparison_without_shiftable_window_to_single_query`、`test_binder_downgrades_share_without_group_by_to_single_query`。**注**：降级说明经 `plan.issues` → answer projection 的 `_project_plan`（已含 issues 白名单）透传到回答侧，回答 LLM 可据此如实说明"已按单次聚合返回"。

### 8.4 低风险与规范性

**R5（低）：交互记录有死代码与未落地字段。** `interactions.build_interaction_record` 除测试外无生产调用点——引擎 `graph_runtime._interaction_update_value`（`graph_runtime.py:253-272`）内联了等价的记录构造。两处结构须手动保持一致（已经有细微差异：`build_interaction_record` 用 `answered_at.isoformat()` 且 `safe_round`，引擎侧字段相同但独立维护）。且 4.3 节设计的 `consumed` 字段全代码库无写入方（`grep consumed` 仅命中文档），"consumed 后不再生效"实际是靠消费节点重跑覆盖 + `skipped` 判定实现的。**修法**：让引擎 resume 复用 `build_interaction_record`（消除双份），或删除该死函数；文档 4.3 的 `consumed` 要么落地要么标注为"未采用，改由重跑覆盖实现"。

**R6（低）：修复环只服务单查询。** `handle_error` 读 `ctx.sql_execution` 的顶层 `error_code`（`sql.py:345-349`），拆分执行的失败虽然通过兼容摘要暴露了 first_failure 的 error_code，但 `repair_context` 里的 `failed_sql` 取自 `ctx.sql.get("sql")`（单查询字段），拆分场景为空 → 修复重生成走不通。当前拆分失败实际直接落 `handle_sql_error → generate_question_answer`，可接受，但与"拆分执行泛化复用"的目标不完全对齐。**修法**：明确拆分查询不进 SQL 重生成环（文档标注），或让 repair_context 支持按 query_id 定位失败子查询。

**R7（低）：share 指标靠"首个数值列"猜。** `_share_analysis` 先取 total 行的 `_first_numeric` 作为指标名，part 行按同名列取值、取不到再 `_first_numeric` 兜底（`answer.py:204-226`）。当宽表维度值本身是数字（如"年份""门店编号"）或存在多指标时，"第一个能转成 float 的列"可能是维度而非指标。**修法**：share/comparison 的指标列名应从 `plan.metrics[].display_name`/编译产出的别名确定，而不是从结果行猜。

**规范性正例（确认无误）：**
- 执行域 `build_execution_output` 用 `query_ids != result_ids` 强校验对齐（`execution.py:86-87`），并生成兼容镜像顶层字段，单/拆分同构落实到位；`_row_count` 正确处理 bool/str/list 多形态。
- 并行执行用独立 Session（`SessionSqlExecutionGateway`）+ `ThreadPoolExecutor(max_workers=min(len, max_parallel))`，`_completed_future_result` 兜住单子查询异常不影响其他——部分失败语义正确，线程安全前提（每查询独立 session）成立。
- artifact 写入失败转 `SQL_RESULT_ARTIFACT_WRITE_FAILED` 失败结果而非静默吞（`sql.py:208-213`）；answer 投影严格白名单投影，确实排除了 SQL 原文、候选 payload、全量 variables（`answer.py:128-167`）——B5 目标达成。
- 作用域化交互条件 `InteractionResponseAnsweredCondition/SkippedCondition` 按节点名 + legacy_key 只读本节点回答（`core.py:349-387`），配合 `ClarificationRoundGate` 的 allowed/exhausted 双条件 + 出边优先级，A2 串扰在结构上被消除；`ResponsePatcher` 已从 runtime 移除，跨类私有方法调用清除。
- 编译器 A6 六类时间结构齐全（`_period_condition` 用真实日历运算，`_shift_months` 正确处理月末），未知 kind 显式 `SEMANTIC_SQL_TIME_RANGE_UNSUPPORTED`（`sql_compiler.py:582-584`），分桶经 sqlglot 按 datasource 方言 transpile（C6）——A6/A7/C6 落实且有编译器测试。
- retry 优先从最近 checkpoint 恢复、无 checkpoint 才回退撤销失败节点 visit（`service.py:354-396`），A5 语义修正到位。

### 8.5 建议动作

1. ~~**R1 必须在 Step 5 合入前修**~~ **【已完成 2026-07-07】**——role 透传已修复并补端到端回归，真实链路占比/环比走确定性合并路径。
2. ~~R2/R4 一并处理~~ **【已完成 2026-07-07】**——comparison baseline 扩展到月对齐 absolute_range；多查询要素不全改为回退单查询 + `plan.issues` 说明，不再假阴性 infeasible。剩余：`relative_range` 的环比 baseline（"最近7天vs前7天"）仍走降级而非确定性平移，若要覆盖需在编译器补相对区间日期平移（易错，建议独立立项验证）。
3. ~~R3 给 `suspicious` 一条出边或并入空结果解释。~~ **【已完成 2026-07-07】**——新增 `result.suspicious` 平级路由 + 回归测试。
4. R5–R7 作为规范性清理，可随 Step 5 收尾或单独小提交。
5. 5.4 路线图 Step 5：R1（正确性阻断）、R2/R4（覆盖面）、R3（存疑结果路由）全部修复；占比/环比在要素齐备时端到端确定性正确、要素不全时如实降级、部分空结果如实提示。剩余仅 R5–R7 规范性清理。

---

## 9. Step 6 实现审查（2026-07-07）

对 Step 6（观测合一）核查：节点 metadata（label/trace/redaction）是否成为单一事实源、trace API 与事件摘要是否共用同一投影、`V1_TRACE_OUTPUT_PATHS` 与前端 `GRAPH_NODE_LABELS` 手抄表是否删除。测试当前全绿（后端 `test_graph_api.py` 21 passed，前端 `graphWorkflowDisplay.test.mjs` 7 passed）。

### 9.1 结论

**Step 6 是三步里完成度最高的一步，核心目标全部达成，没有发现高/中风险缺陷。** 三处手抄表已消灭，脱敏口径已合一。仅有若干低风险的一致性/规范性观察。

三处漂移逐一核实：
- **`V1_TRACE_OUTPUT_PATHS` 已删除**：`grep` 全仓无残留；trace 输出路径改为从 `node.metadata.trace.output_path` 读取（`public_projection.py:21-31`），`_trace_nodes` 直接筛"声明了 trace 输出路径的节点"（`service.py:452-458`）。
- **脱敏口径合一**：事件侧 `public_node_summary` 与 trace 侧 `_sanitize_trace_output` 都收敛到同一个 `sanitize_public_output(node, output)`（`public_projection.py:34-59`、`service.py:544-547`）——`sql_summary`/`split_sql_summary`/`execution_summary` 三种 redaction 策略只有一份实现，C4"两通道两口径"根治。测试 `test_graph_node_events_use_v1_metadata_projection_for_public_summary` 断言事件 payload 与 trace 投影逐字段一致。
- **前端零硬编码节点名**：`graphNodeLabel` 只做 `node.label || node.name`（`graphWorkflowDisplay.ts:280-283`），label 来自后端 `node_display_label`（metadata.display.label）；事件流分支也优先取 `public_payload.label`（`:118-133`）。无 `GRAPH_NODE_LABELS` 常量表。

### 9.2 确认无误的要点

- **节点 metadata 是单一事实源**：`display.label` / `trace.output_path` / `trace.redaction` 全部在图定义 `_trace_metadata` 声明（`chatbi_v1.py:19-35`），runtime（node.started/succeeded 事件）、trace API、前端三条通道都从它派生。新增节点（`bind_query_plan`/`validate_result`）与跨模型三节点（`ask_cross_model_split`/`generate_split_queries`/`execute_split_queries`）都在 trace 可见——正是 C4 点名缺失的节点，测试 `test_graph_trace_is_derived_from_v1_node_metadata` 显式锁定。
- **未执行节点不误归因**：`_trace_node_output` 对 `status=="not_run"` 的节点直接返回 None，不去读共享 variables 路径（`service.py:483-485`），避免把后写入的结果错挂到未跑节点——这是 metadata 驱动读取共享路径时的正确防御。
- **交互节点特判合理**：trace 读到 `ask_*` 节点时优先返回 pending_interaction 快照（`service.py:486-493`），label 也经 `node_display_label` 派生，与展示层一致。
- **数据行仍走 artifact**：`_execution_result_summaries` 只带 `PUBLIC_SAMPLE_ROW_LIMIT=5` 行 + `artifact_ref`（`public_projection.py:149-171`），公开通道不外泄全量结果，与 Step 3 的 artifact 化一致。
- **minimal-v1 定义同构**：`chatbi_minimal_v1.py` 用同一套 `_trace_metadata` 结构，投影逻辑复用，无第二份实现。

### 9.3 低风险观察

**O1（低，非缺陷，需确认意图）：SQL 全文在公开通道保留。** `_sql_summary` 返回 `statement_type` + 完整 `sql`（`public_projection.py:84-91`），`_query_summaries` 同样带全量 `sql`；测试也断言 `output["sql"].startswith("select ")`。也就是说"redaction"当前实为"结构化摘要"，SQL 原文对前端可见（口径已统一为"都可见"，解决了 C4 的不一致，但并非"隐藏 SQL"）。设计文档 5.3.6 只要求"SQL/数据行是否公开变成一处配置"——现在确实是一处配置（改 redaction 策略即可），目标达成。若产品上要求对终端用户隐藏 SQL 原文，只需在 `_sql_summary`/`_query_summaries` 去掉 `sql` 字段，一处生效。**建议**：在文档或代码注释里明确"当前策略：SQL 全文公开"是有意为之，避免被误读为脱敏遗漏。

**O2（低）：redaction 策略是字符串魔法值，无枚举约束。** `redaction` 为自由字符串（`"none"/"sql_summary"/"split_sql_summary"/"execution_summary"`），`sanitize_public_output` 用 if-else 分派，拼错的策略名会静默落到"原样返回 output"（`public_projection.py:51-59`）——即无脱敏。风险低（图定义是内部代码、有测试覆盖已用策略），但一个 typo 会让某节点意外全量公开。**建议**：把 redaction 收敛为枚举/常量，未知值 fail-fast 或至少默认走最严格投影而非透传。

**O3（低）：`execute_sql` 空结果的 query_count 补丁是特例硬编码。** `_execution_summary` 里 `if query_count == 0 and node_name == "execute_sql": query_count = 1`（`public_projection.py:118-119`），用节点名做特判修正计数。可读性可接受但属"按名字打补丁"，若将来单查询节点改名会失效。**建议**：改由 execution 结构本身表达（如 results 恒非空、或用 plan_ref 数量），而非依赖节点名。

**O4（低，规范性正例转提示）：前端 `intentTypeLabel` 仍是前端本地词表。** `graphWorkflowDisplay.ts:504-516` 保留了一份 intent_type→中文 的映射。这不是 Step 6 要消灭的"节点名手抄表"（节点 label 已从后端来），但它是同类"前后端各存一份业务枚举翻译"的隐患——intent_type 若后端增减，前端需同步。属 Step 6 范围之外，记录备查即可。

### 9.4 建议动作

1. Step 6 可标记为**验收通过**（三处漂移消除 + 脱敏合一 + 测试锁定，均达标）。
2. O1 明确"SQL 公开"的产品决策并注释；O2 把 redaction 收敛为枚举 + 未知值兜底最严格投影——两项都是"防未来回归"的小加固，不阻断验收。
3. O3/O4 作为可选清理，随手做或记入技术债。
