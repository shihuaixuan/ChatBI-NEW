# ChatBI 图节点架构设计

本文档基于第一版 ChatBI 流程图，说明当前图运行时如何调用节点、现有项目已经具备的能力，以及下一阶段如何把各类业务节点解耦成可替换、可测试、可观测的架构。

## 1. 第一版流程图理解

图片中的流程不是单一的 Text2SQL 链路，而是一个完整的问数图：

```text
问题分类
  ├─ 越权等异常问题 -> 拒绝回复 -> 结束
  ├─ 闲聊 -> 闲聊回复 -> 结束
  └─ 数据问题/追问 -> 问题重写

问题重写
  ├─ 信息不足 -> 询问用户
  │    ├─ 用户补充 -> 回到问题重写
  │    └─ 用户不补充 -> 回复 -> 结束
  ├─ 可选生成图像刻画
  └─ 进入意图识别

意图识别
  ├─ 结合上下文信息
  ├─ 意图不明确 -> 询问用户
  │    ├─ 用户补充 -> 回到意图识别
  │    └─ 用户不补充 -> 回复 -> 结束
  └─ 进入知识库检索

知识库检索
  ├─ 未命中知识 -> 回复 -> 结束
  ├─ 多维度指标歧义 -> 询问用户
  │    ├─ 用户选择 -> SQL 生成
  │    └─ 用户不选择 -> 回复 -> 结束
  └─ 命中 -> SQL 生成

SQL 生成 -> SQL 执行
  ├─ 执行成功 -> 问题回复 / 问题推荐 -> 回复 -> 结束
  └─ 执行异常 -> 异常处理 -> 回复 -> 结束
```

这张图里至少有 6 类节点：

| 节点类型 | 示例节点 | 核心职责 |
| --- | --- | --- |
| 路由决策节点 | 问题分类、意图识别、知识库检索结果判断 | 读取上下文，输出结构化决策，驱动条件边 |
| 能力执行节点 | 问题重写、SQL 生成、SQL 执行、问题推荐 | 调用底层能力，写入上下文变量 |
| 人机交互节点 | 询问用户、用户补充、用户选择 | 暂停 Run，等待用户输入后恢复 |
| 回复节点 | 拒绝回复、闲聊回复、未命中回复、异常回复、最终回复 | 生成面向用户的消息 |
| 异常处理节点 | SQL 执行异常处理 | 将错误转为可恢复策略或用户可读解释 |
| 终止节点 | 结束 | 标记流程完成 |

## 2. 当前图架构如何调用节点

当前 `main` 已有一条可运行的最小闭环，入口是 Graph API：

```text
GraphApiService.create_query()
  -> build_placeholder_chatbi_runtime()
  -> GraphRuntime.create_run()
  -> GraphRuntime.execute()
  -> NodeScheduler.execute()
  -> HandlerRegistry.get(node.handler)
  -> handler.execute(NodeExecutionRequest)
  -> ChatBICapabilityGateway.invoke(...)
  -> NodeExecutionResult
  -> ContextPatcher.apply(...)
  -> ConditionRouter.select(...)
  -> CheckpointManager 写事件和进度
```

关键代码位置：

| 模块 | 文件 | 作用 |
| --- | --- | --- |
| 图 API | `backend/apps/workflow_engine/api/service.py` | 创建 Graph Run，并同步执行当前最小图 |
| 图运行时 | `backend/apps/workflow_engine/runtime/graph_runtime.py` | 控制 Run 生命周期、循环推进节点、失败/暂停/成功 |
| 节点调度 | `backend/apps/workflow_engine/runtime/scheduler.py` | 根据 `node.handler` 找到 handler 并执行 |
| 条件路由 | `backend/apps/workflow_engine/runtime/router.py` | 按边优先级和条件判断下一个节点 |
| 上下文写入 | `backend/apps/workflow_engine/runtime/context_patcher.py` | 将节点输出 patch 到 `WorkflowContext` |
| 最小 ChatBI 图 | `backend/apps/chatbi_workflow/definitions/chatbi_minimal_v1.py` | 声明当前最小图节点和边 |
| 当前占位能力 | `backend/apps/chatbi_workflow/capabilities/placeholder.py` | 用确定性假数据跑通完整流程 |
| 运行演示脚本 | `backend/tests/workflow_engine/run_graph_flow_demo.py` | 直接打印从输入到每个节点状态与输出的全过程 |

当前最小图是：

```text
understand_question
  -> retrieve_schema
  -> generate_sql
  -> validate_sql
  -> apply_permission
  -> execute_sql
  -> generate_answer
  -> finish
```

它证明了“图运行时 + 节点 handler + 能力网关 + 条件边 + 事件日志”可以完整运行，但还没有覆盖图片里的完整业务分支。

## 3. 当前项目已有实现

### 3.1 通用图引擎

`workflow_engine` 已具备：

- `WorkflowDefinition`、`NodeDefinition`、`EdgeDefinition`：声明式定义图。
- `GraphRuntime`：执行 run，处理超时、最大节点数、循环次数、失败、暂停、恢复。
- `NodeScheduler`：把节点定义转成一次 handler 调用。
- `ConditionRouter`：根据条件边选择下一节点。
- `CheckpointManager`、事件 outbox、event stream：提供可观测性基础。
- 数据库模型：`workflow_run`、`node_execution`、`workflow_checkpoint`、`workflow_event`、`interaction_request`、`workflow_artifact`。

### 3.2 当前 ChatBI 最小图

`chatbi_workflow` 已具备：

- 最小图定义：`chatbi/minimal-v1`。
- 节点 handler：问题理解、Schema 检索、SQL 生成、SQL 校验、权限应用、SQL 执行、答案生成、终止。
- `ChatBICapabilityGateway` 协议：节点通过网关调用能力，不直接依赖具体服务。
- `PlaceholderChatBICapabilityGateway`：占位实现，用于跑通端到端图流程。
- `build_placeholder_chatbi_runtime()`：当前同步装配入口。

### 3.3 旧 Agentic ChatBI 可复用能力

`agentic_chat` 已有很多真实能力雏形，后续可以迁移为 gateway adapter：

- `QueryUnderstandingTool` / `QueryUnderstandingService`：问题理解、槽位、低置信度、歧义、冲突识别。
- `schema.py`、`semantic_asset.py`、`terminology.py`、`sql_example.py`：证据召回。
- `sql_generator.py`、`semantic_sql_compiler.py`、`template_sql.py`：多种 SQL 生成策略。
- `sql_validator.py`、`permission.py`、`sql_executor.py`：SQL 安全和执行。
- `answer_generator.py`：答案生成。
- `planner.py`、`orchestrator.py`：旧线性/策略式编排，可作为迁移参考，但不建议继续扩展成更多 if/else。

## 4. 节点解耦设计

建议保持四层边界：

```text
Graph Definition
  只声明节点、边、条件、重试、超时、输入输出 schema

Node Handler
  只负责从 WorkflowContext 读输入、调用能力、返回 NodeExecutionResult

Capability Gateway / Adapter
  真实业务能力适配层，例如 LLM、语义资产、SQL 编译器、权限、DB 执行、推荐

Condition Evaluator
  只负责路由判断，不做副作用
```

### 4.1 Graph Definition 不依赖业务实现

图定义应该只写：

- 节点名：`classify_question`
- 节点类型：`DECISION` / `CAPABILITY` / `INTERACTION` / `TERMINAL`
- handler 名：`question.classify`
- 输入映射：从 `request`、`conversation`、`variables` 读取什么
- 输出路径：handler 将结果写入哪里
- 条件边：例如 `question.is_chat`、`rewrite.needs_user_input`

图定义不应该 import SQL 执行器、LLM 客户端、权限服务。

### 4.2 Node Handler 保持薄层

每个节点 handler 做三件事：

1. 用 Pydantic input schema 校验输入。
2. 调用 capability gateway。
3. 用 Pydantic output schema 校验输出，并返回 `ContextPatch`。

示例边界：

```text
ClassifyQuestionNode
  input: question, user_id, tenant_id, datasource_id, conversation_context
  output: variables.question_classification
  capability: question.classify

RewriteQuestionNode
  input: question, conversation_context, user_supplements
  output: variables.rewrite
  capability: question.rewrite

KnowledgeRetrieveNode
  input: rewritten_question, intent, datasource_id
  output: variables.knowledge
  capability: knowledge.retrieve
```

### 4.3 Capability Gateway 封装真实能力

节点只依赖：

```python
gateway.invoke(capability: str, request: dict, idempotency_key: str) -> dict
```

真实实现可以拆成多个 adapter：

| Adapter | 能力 |
| --- | --- |
| `QuestionAdapter` | 分类、重写、意图识别 |
| `ClarificationAdapter` | 澄清问题生成、选项生成 |
| `KnowledgeAdapter` | 语义资产、术语、训练样例、schema 检索 |
| `SqlAdapter` | SQL 生成、校验、修复、执行 |
| `PermissionAdapter` | 行列权限、数据源权限 |
| `AnswerAdapter` | 拒绝、闲聊、未命中、异常、最终答案 |
| `RecommendationAdapter` | 问题推荐 |

这样节点只稳定依赖 capability contract，具体能力可以从占位实现切到旧 `agentic_chat`，再切到更成熟服务。

### 4.4 Condition Evaluator 只做判断

条件 evaluator 应该是纯函数式设计：

```text
context + last_node_result -> ConditionDecision
```

示例条件：

- `question.is_forbidden`
- `question.is_chitchat`
- `question.is_data_question`
- `rewrite.need_user_input`
- `intent.ambiguous`
- `knowledge.missed`
- `knowledge.metric_ambiguous`
- `sql.execute_failed`
- `recommendation.available`

条件只决定走哪条边，不调用外部服务，不修改上下文。

## 5. 第一版 ChatBI 图建议

建议新增 `chatbi/v1`，不要直接替换 `minimal-v1`。`minimal-v1` 保留为 smoke test 和回归基线。

### 5.1 节点清单

| 节点名 | 类型 | handler | 输出 |
| --- | --- | --- | --- |
| `classify_question` | DECISION | `question.classify` | `variables.classification` |
| `reject_answer` | CAPABILITY | `answer.reject` | `variables.answer` |
| `chitchat_answer` | CAPABILITY | `answer.chitchat` | `variables.answer` |
| `rewrite_question` | CAPABILITY | `question.rewrite` | `variables.rewrite` |
| `ask_rewrite_clarification` | INTERACTION | `interaction.ask_rewrite` | `pending_interaction_id` |
| `draw_image_profile` | CAPABILITY | `question.image_profile` | `variables.image_profile` |
| `recognize_intent` | DECISION | `intent.recognize` | `variables.intent` |
| `ask_intent_clarification` | INTERACTION | `interaction.ask_intent` | `pending_interaction_id` |
| `retrieve_knowledge` | DECISION | `knowledge.retrieve` | `variables.knowledge` |
| `ask_metric_selection` | INTERACTION | `interaction.ask_metric_selection` | `pending_interaction_id` |
| `generate_sql` | CAPABILITY | `sql.generate` | `variables.sql` |
| `execute_sql` | CAPABILITY | `sql.execute` | `variables.sql_result` 或 `variables.sql_error` |
| `handle_sql_error` | CAPABILITY | `sql.handle_error` | `variables.error_response` |
| `generate_question_answer` | CAPABILITY | `answer.generate` | `variables.answer` |
| `recommend_questions` | CAPABILITY | `question.recommend` | `variables.recommendations` |
| `compose_final_reply` | CAPABILITY | `answer.compose` | `variables.final_reply` |
| `finish` | TERMINAL | `answer.finish` | `variables.completed` |

### 5.2 路由条件

| source | condition | target |
| --- | --- | --- |
| `classify_question` | `question.forbidden` | `reject_answer` |
| `classify_question` | `question.chitchat` | `chitchat_answer` |
| `classify_question` | `question.data_or_followup` | `rewrite_question` |
| `rewrite_question` | `rewrite.need_user_input` | `ask_rewrite_clarification` |
| `rewrite_question` | default | `recognize_intent` |
| `ask_rewrite_clarification` | `interaction.answered` | `rewrite_question` |
| `ask_rewrite_clarification` | `interaction.skipped` | `generate_question_answer` |
| `recognize_intent` | `intent.ambiguous` | `ask_intent_clarification` |
| `recognize_intent` | default | `retrieve_knowledge` |
| `retrieve_knowledge` | `knowledge.missed` | `generate_question_answer` |
| `retrieve_knowledge` | `knowledge.metric_ambiguous` | `ask_metric_selection` |
| `retrieve_knowledge` | `knowledge.hit` | `generate_sql` |
| `ask_metric_selection` | `interaction.answered` | `generate_sql` |
| `ask_metric_selection` | `interaction.skipped` | `generate_question_answer` |
| `generate_sql` | default | `execute_sql` |
| `execute_sql` | `sql.execution_failed` | `handle_sql_error` |
| `execute_sql` | `sql.execution_succeeded` | `generate_question_answer` |
| `generate_question_answer` | default | `recommend_questions` |
| `recommend_questions` | default | `compose_final_reply` |
| `compose_final_reply` | default | `finish` |

## 6. 状态与数据契约

建议把 `WorkflowContext.variables` 按领域分区，避免节点互相猜字段：

```json
{
  "classification": {},
  "rewrite": {},
  "intent": {},
  "knowledge": {},
  "clarification": {},
  "sql": {},
  "sql_validation": {},
  "permission": {},
  "sql_result": {},
  "sql_error": {},
  "recommendations": [],
  "answer": {},
  "final_reply": {}
}
```

每个节点应声明：

- input schema
- output schema
- patch path
- public output summary
- internal diagnostics path

不要把完整 SQL 执行大结果直接塞进公开事件；大结果应该进 artifact 或内部 payload，只把 row_count、fields、sample_rows 等摘要给前端。

## 7. 与开源优秀项目的对照

### LangGraph

可参考点：

- StateGraph：图围绕可序列化状态推进。
- conditional edges：条件边由节点结果和状态决定。
- checkpoint：每步保存状态，支持恢复。

落地建议：

- `WorkflowContext` 就是 ChatBI 的 state。
- `ConditionRouter` 对齐 conditional edge。
- `CheckpointManager` 后续要接入数据库 UoW，提升恢复能力。

### Temporal

可参考点：

- Workflow 只做编排。
- Activity 做真实副作用。
- Activity 有重试、超时、幂等要求。

落地建议：

- `GraphRuntime` 类似 Workflow。
- `ChatBICapabilityGateway` adapter 类似 Activity。
- SQL 执行、LLM 调用、权限调用必须有 idempotency key 和 timeout。

### Prefect

可参考点：

- Flow/Task 可观测性强。
- 每个 task 有状态、日志、retry、cache。

落地建议：

- `node_execution` 表需要成为节点观测主表。
- demo 脚本展示的节点状态，后续应能从 API 直接查询。

### Haystack / LlamaIndex Pipeline

可参考点：

- Component 有明确输入输出。
- Pipeline 通过组件连接表达数据流。

落地建议：

- 每个 ChatBI node 都应该有 Pydantic input/output。
- 节点之间只通过 `WorkflowContext` 和声明式 mapping 传值。

### Dagster

可参考点：

- typed inputs/outputs。
- asset/op 边界清晰。

落地建议：

- 对语义资产、SQL、执行结果、推荐问题建立稳定 artifact/summary 类型。

## 8. 推荐演进顺序

1. 保留 `chatbi/minimal-v1`，继续作为最小闭环 smoke test。
2. 新增 `chatbi/v1` 图定义，先只接占位 gateway，复刻图片分支。
3. 为 `chatbi/v1` 定义所有节点 input/output schema。
4. 实现条件 evaluator，不接外部能力，先验证所有分支可走通。
5. 将旧 `agentic_chat` 能力逐个封装成 gateway adapter。
6. 接入真实能力后，为每个关键分支补集成测试：
   - 越权拒绝
   - 闲聊回复
   - 信息不足澄清
   - 意图歧义澄清
   - 知识未命中
   - 指标歧义选择
   - SQL 执行成功
   - SQL 执行异常
7. 将同步执行逐步演进为可选异步执行和事件推送。

## 9. 设计结论

当前项目已经有可运行的图底座，下一步不应该继续扩展旧 `AgenticOrchestrator` 的线性逻辑，而应该把图片中的业务流程落到新的 `chatbi/v1` 图定义中。

核心原则：

- 图定义负责流程。
- handler 负责节点适配。
- gateway 负责真实能力。
- condition 负责路由。
- context/artifact 负责数据传递。
- event/node_execution 负责可观测性。

按这个拆法，具体节点功能可以独立开发、独立测试、独立替换，ChatBI 业务图也可以在不改运行时的前提下持续演进。
