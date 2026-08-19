# ChatBI v1 图工作流 TODO

本文档把 `GRAPH_NODE_ARCHITECTURE.md` 中的架构设计拆成可执行 TODO。目标是先用占位能力复刻第一版 ChatBI 业务图，再逐步把旧 `agentic_chat` 的真实能力迁移到图节点。

## 里程碑 0：保持现有最小闭环稳定

- [x] 保留 `chatbi/minimal-v1` 作为 smoke test 基线。
  - 验收：`tests/workflow/test_minimal_flow.py` 继续通过。
- [x] 保留可直接运行的流程演示脚本。
  - 文件：`backend/tests/workflow_engine/run_graph_flow_demo.py`
  - 验收：脚本能打印输入问题、图执行响应、节点事件、节点状态和节点输出。
- [ ] 将演示脚本纳入提交清单。
  - 背景：当前 `.gitignore` 忽略 `backend/tests`，提交时需要显式 `git add -f backend/tests/workflow_engine/run_graph_flow_demo.py`。
  - 验收：脚本在目标分支中可被 `git ls-files` 查到。

## 里程碑 1：定义 ChatBI v1 图骨架

- [x] 新增 `chatbi/v1` 图定义文件。
  - 建议文件：`backend/apps/chatbi/orchestration/graph/definitions/chatbi_v1.py`
  - 节点范围：覆盖问题分类、拒绝回复、闲聊回复、问题重写、澄清、意图识别、知识检索、指标选择、SQL 生成、SQL 执行、异常处理、问题推荐、最终回复、结束。
  - 验收：图定义可以被 `WorkflowRegistry(DefinitionValidator(...)).publish()` 校验通过。

- [x] 注册 `chatbi/v1` 的 handler 名称。
  - 建议函数：`register_chatbi_v1_handlers(registry, gateway)`
  - 要求：先允许复用占位 handler，但 handler 名称必须按业务能力命名，如 `question.classify`、`intent.recognize`、`knowledge.retrieve`。
  - 验收：handler registry 不缺失任何图定义引用。

- [x] 新增 `chatbi/v1` runtime factory。
  - 建议文件：`backend/apps/chatbi/orchestration/graph/runtime.py`
  - 建议函数：`build_placeholder_chatbi_v1_runtime(session)`
  - 验收：可以创建并执行 `definition_name="chatbi"`、`definition_version="v1"` 的 run。

- [x] 更新 Graph API 的定义版本选择机制。
  - 当前状态：`GraphApiService.create_query()` 直接使用 `minimal-v1`。
  - 建议：短期用配置常量控制默认版本，默认切到 `v1` 前先确保测试覆盖。
  - 验收：API 可以通过配置或参数选择 `minimal-v1` / `v1`。

## 里程碑 2：定义节点输入输出契约

- [x] 为问题分类节点定义 schema。
  - 输入：`question`、`tenant_id`、`user_id`、`datasource_id`、`conversation_context`
  - 输出：`category`、`reason`、`risk_level`
  - 写入：`variables.classification`
  - 验收：越权、闲聊、数据问题三类占位输出可驱动不同路由。

- [x] 为问题重写节点定义 schema。
  - 输入：原始问题、上下文。
  - 输出：`original_question`、`rewrite_question`、`metric_phrases`、`dimension_phrases`
  - 写入：`variables.rewrite`
  - 验收：输出完整重写问题，并返回可供后续流程使用的指标、维度短语。

- [x] 为意图识别节点定义 schema。
  - 输入：重写问题、上下文信息、用户补充。
  - 输出：`intent_type`、`confidence`、`metric_mentions`、`dimension_mentions`、`time_mentions`、`filter_mentions`、`required_slot_types`、`query_shape`、`ambiguous_slots`、`conflict_slots`
  - 写入：`variables.intent`
  - 验收：意图不明确时走澄清分支，明确时进入知识检索。
  - 验收：意图识别只输出自然语言线索，不选择 Semantic `asset_id`、`biz_name` 或数据库字段。

- [x] 为知识库检索节点定义 schema。
  - 输入：重写问题、意图、数据源、租户。
  - 输出：`hit`、`tables`、`fields`、`metrics`、`terms`、`examples`、`ambiguities`
  - 写入：`variables.knowledge`
  - 验收：未命中、多指标歧义、命中三种结果可路由到不同节点。
  - 验收：检索节点继续使用既有输入契约；问题重写节点的短语输出单独保存在 `variables.rewrite`。
  - 验收：统一检索候选经过 `SemanticBindingPolicy` 门控，并输出通道分数、排名和 reason codes。

- [x] 为人机交互节点定义统一 schema。
  - 输入：澄清类型、问题、候选项、允许写回路径。
  - 输出：`interaction_id`、`status`、`response`
  - 写入：`interaction_request` 表和 `variables.clarification`
  - 验收：Run 能进入 `waiting_input`，用户回答后能恢复到指定节点。

- [x] 为 SQL 相关节点定义 schema。
  - SQL 生成输出：`sql`、`strategy`、`explanation`、`used_assets`
  - SQL 执行输出：`rows`、`row_count`、`fields`、`execution_ms`
  - SQL 异常输出：`error_code`、`message`、`retryable`、`repair_hint`
  - 验收：执行成功和执行异常可以分别进入答案或异常处理分支。

- [x] 为回复和推荐节点定义 schema。
  - 回复输出：`answer`、`warnings`、`render_type`、`citations`
  - 推荐输出：`questions`
  - 最终回复输出：`final_answer`、`recommendations`、`chart`、`metadata`
  - 验收：最终输出能统一落到 `variables.final_reply`。

## 里程碑 3：实现路由条件 evaluator

- [x] 新增问题分类条件。
  - 条件：`question.forbidden`、`question.chitchat`、`question.data_or_followup`
  - 文件建议：`backend/apps/chatbi/orchestration/graph/conditions/question.py`
  - 验收：三类输入分别路由到拒绝、闲聊、问题重写。

- [x] 新增意图识别条件。
  - 条件：`intent.ambiguous`
  - 验收：低置信度、歧义、冲突时进入 `ask_intent_clarification`。

- [x] 新增知识检索条件。
  - 条件：`knowledge.missed`、`knowledge.metric_ambiguous`、`knowledge.hit`
  - 验收：三种检索结果分别进入回复、指标选择、SQL 生成。

- [x] 新增交互恢复条件。
  - 条件：`interaction.answered`、`interaction.skipped`
  - 验收：用户回答后恢复原业务节点，用户放弃后进入回复结束。

- [x] 新增 SQL 执行条件。
  - 条件：`sql.execution_succeeded`、`sql.execution_failed`
  - 验收：执行失败进入异常处理，执行成功进入答案生成。

## 里程碑 4：占位能力跑通完整 v1 图

- [x] 扩展 `PlaceholderChatBICapabilityGateway`。
  - 新增能力：`question.classify`、`question.rewrite`、`intent.recognize`、`knowledge.retrieve`、`interaction.ask_*`、`sql.handle_error`、`question.recommend`、`answer.compose`。
  - 验收：每个能力返回稳定、可预测的占位结果。

- [x] 新增 v1 主路径测试。
  - 建议文件：`backend/tests/workflow/test_v1_flow.py`
  - 场景：数据问题 -> 重写 -> 意图识别 -> 知识命中 -> SQL -> 推荐 -> 最终回复。
  - 验收：Run 状态 `succeeded`，节点顺序符合图定义。

- [x] 新增 v1 分支测试：越权拒绝。
  - 输入：模拟越权问题。
  - 验收：进入 `reject_answer` 后结束，不执行 SQL。

- [x] 新增 v1 分支测试：闲聊。
  - 输入：非数据闲聊。
  - 验收：进入 `chitchat_answer` 后结束，不执行知识检索和 SQL。

- [x] 新增 v1 分支测试：问题重写澄清。
  - 输入：信息不足的数据问题。
  - 验收：Run 进入 `waiting_input`；回答后回到 `rewrite_question`。

- [x] 新增 v1 分支测试：意图澄清。
  - 输入：指标或时间范围歧义。
  - 验收：Run 进入 `waiting_input`；回答后回到 `recognize_intent`。

- [x] 新增 v1 分支测试：知识未命中。
  - 输入：无法匹配知识的问题。
  - 验收：进入 `generate_question_answer`，不执行 SQL。

- [x] 新增 v1 分支测试：指标选择。
  - 输入：多维度指标歧义。
  - 验收：Run 进入 `waiting_input`；选择后进入 `generate_sql`。
  - 验收：交互 prompt/options 来自 `InteractionAdapter`，不是通用占位文案。

- [x] 新增 v1 分支测试：SQL 执行异常。
  - 输入：占位网关模拟 SQL 执行失败。
  - 验收：进入 `handle_sql_error`，再进入回复结束。

## 里程碑 5：迁移旧 Agentic ChatBI 能力为 adapter

- [x] 实现 `InteractionAdapter`。
  - 能力：`interaction.ask_intent_clarification`、`interaction.ask_metric_selection`
  - 验收：根据 `ambiguous_slots/conflict_slots`、`knowledge.ambiguities` 生成 prompt/options/response_schema。
  - 验收：metric selection 在 ambiguity candidates 为空时可回退到 `knowledge.candidate_groups.metrics`。
  - 验收：没有 knowledge 上下文时，真实 runtime 注入 `DatasetSchemaProvider`，可按 `dataset_id` 加载 schema 候选。
  - 后续：按问题文本对 schema 候选排序，而不是直接取前 5 个。

- [x] 实现 `QuestionAdapter`。
  - 复用：`agentic_chat.tools.query_understanding` 和 `agentic_chat.services.query_understanding`
  - 能力：`question.classify`、`question.rewrite`、`intent.recognize`
  - 验收：真实问题理解结果能写入 `variables.classification/rewrite/intent`。
  - 验收：`intent.recognize` 输出自然语言 mention 和 `query_shape`，不直接确认 Semantic 资产。
  - 验收：模型失败或输出非法时保留规则兜底。

- [x] 实现 `KnowledgeAdapter`。
  - 复用：`agentic_chat.tools.schema`、`semantic_asset`、`terminology`、`sql_example`
  - 能力：`knowledge.retrieve`
  - 验收：输出 tables、fields、metrics、terms、examples、ambiguities。
  - 验收：基于 Semantic dataset schema 做 schema mapper + asset document 候选召回。
  - 验收：按 intent mention 分槽位召回并做可解释 rerank，完整短语优先于弱词重叠。
  - 验收：`访问人数` 可优先命中完整短语资产；只有 `人数` 等弱词时仍保留 `metric_ambiguous`。
  - 后续：接入 BM25 / embedding / hybrid score，并完善同义词/别名治理。

- [x] 实现 `SqlAdapter.generate`。
  - 复用：`SemanticSQLCompiler`、`DatasetSchemaProvider`、`SqlValidateTool`
  - 能力：`sql.generate`
  - 验收：真实 SQL 生成和安全校验可由图节点调用，输出 `sql`、`strategy`、`datasource_id`、`explanation`、`used_assets`。

- [x] 实现 `SqlAdapter.execute`。
  - 复用：`SqlExecuteTool`、`apps.db.db.exec_sql`
  - 能力：`sql.execute`
  - 验收：真实 SQL 执行可由图节点调用，输出 `status`、`rows`、`row_count`、`fields`、`execution_ms`，失败时进入 `handle_sql_error`。

- [x] 实现 SQL 异常处理。
  - 复用：`SqlAdapter.handle_error`
  - 能力：`sql.handle_error`
  - 验收：真实 SQL 异常处理可由图节点调用，输出 `error_code`、`message`、`retryable`、`repair_hint`、`repair_plan`。

- [x] 实现 SQL 自动修复/重试策略第一版。
  - 复用：`sql_repair`
  - 能力：SQL repair plan、retryable policy
  - 验收：可对可修复 SQL 错误生成修复建议或重试计划。
  - 验收：`regenerate_sql` 计划已通过 `sql.error_retryable` 图条件回到 `generate_sql`，由 `max_loop_iterations` 控制重试上限。
  - 验收：`SqlAdapter.generate` 已把上一轮错误写入 `repair_context`，并拒绝再次提交同一条失败 SQL。
  - 验收：`SemanticSQLCompiler` 已消费 `candidate_tables`，可把失败表上的同名资产切换到候选表资产。
  - 后续：继续消费 `candidate_fields`，并扩展多表 join 修复策略。

- [x] 实现 `PermissionAdapter`。
  - 复用：`agentic_chat.tools.permission`
  - 能力：SQL 执行前置权限检查、行过滤改写、禁用列拒绝
  - 验收：权限拒绝不会执行 SQL；策略 provider 返回行过滤条件时会改写 SQL；命中禁用列时拒绝执行。

- [x] 实现 SQL 执行结果 sample 保护。
  - 目标：避免完整 SQL 结果集进入 run context/trace。
  - 能力：`SqlAdapter.execute` 只保留 sample rows，并输出 `row_count`、`sampled_row_count`、`result_truncated`、`artifact_ref`。
  - 验收：大结果只把 sample rows 写入 `variables.sql_execution.rows`。

- [x] 实现真实行列权限改写第一版。
  - 复用：`PermissionTool` 后续增强、旧 Chat row permission 逻辑
  - 能力：tenant/user/datasource 维度权限策略 provider、行权限 SQL AST 改写、列权限拒绝
  - 验收：真实权限策略能改写或拒绝 SQL。
  - 后续：接入真实权限表和 current_user，扩展复杂 SQL/多层子查询改写覆盖面。

- [x] 实现 `AnswerAdapter`。
  - 复用：`answer_generator`
  - 能力：`answer.reject`、`answer.chitchat`、`answer.generate`、`answer.compose`
  - 验收：最终回复符合前端消费格式。

- [x] 实现 `RecommendationAdapter`。
  - 能力：`question.recommend`
  - 验收：可根据已选指标、维度、SQL 执行状态生成问题推荐。

## 里程碑 6：运行时与持久化增强

- [ ] 将 `CheckpointManager` 的节点边界提交接入数据库 UoW。
  - 目标：Run、NodeExecution、Checkpoint、Event 在同一事务提交。
  - 验收：每个节点都有 `node_execution` 记录。
  - [x] P0：API Runtime 已写入 `node_execution` 记录，包含节点状态、输入摘要、输出摘要和路由摘要。

- [ ] 增强事件公开摘要。
  - 目标：事件包含节点名、状态、关键摘要，不泄露 SQL 敏感信息或大结果。
  - 验收：`/graph/runs/{run_id}/events` 可以支撑前端进度展示。

- [ ] 支持节点输出 artifact。
  - 目标：大 SQL 结果、图像刻画、诊断信息写入 artifact，不直接塞入事件。
  - 验收：节点输出摘要和 artifact ref 都可查询。
  - [x] P0：Trace API 已对 SQL/结果集做脱敏，并返回 `artifact_ref` 协议字段；真实 artifact 写入待接入。
  - [x] P0：`SqlAdapter.execute` 已限制 context 中的 SQL 结果为 sample rows。

- [x] 完善 retry/cancel/resume API 与图执行联动。
  - 目标：`retry` 能重新推进图，`resume` 能从交互节点继续，`cancel` 能阻止后续执行。
  - 验收：控制 API 有集成测试覆盖。

- [ ] 设计同步/异步执行切换。
  - 当前：API 同步执行。
  - 目标：支持后台 worker 推进长流程，同时保留同步 smoke mode。
  - 验收：长流程不阻塞 HTTP 请求，事件可轮询或推送。

## 里程碑 7：前端与可观测性

- [x] 定义前端 trace 数据结构。
  - 来源：`workflow_event`、`node_execution`、`workflow_artifact`
  - 验收：前端能展示节点状态、耗时、输出摘要、错误原因。

- [x] 增加 Graph Run trace API。
  - 建议接口：`GET /graph/runs/{run_id}/trace`
  - 验收：返回节点列表、边路由、当前节点、最终输出。

- [x] 更新演示脚本支持 v1 图。
  - 参数：`--definition-version v1`
  - 验收：可以直观看到完整 v1 图节点推进日志。

## 推荐执行顺序

1. 先做里程碑 1、2、3：把 `chatbi/v1` 的图、schema、条件全部声明清楚。
2. 再做里程碑 4：用占位 gateway 跑通第一版图片里的所有分支。
3. 然后做里程碑 5：逐个迁移真实能力 adapter。
4. 最后做里程碑 6、7：增强持久化、异步执行和前端 trace。

## 每轮开发的完成定义

每个 TODO 完成时至少满足：

- 有对应单元测试或集成测试。
- `uv run ruff check apps/chatbi/orchestration/graph platform/workflow_engine tests/workflow_engine tests/workflow` 通过。
- `pytest tests/workflow_engine tests/workflow -q` 通过。
- 新增节点必须能在 demo 或测试中看到节点状态和输出。
- 真实能力接入不得破坏 `chatbi/minimal-v1` smoke test。
