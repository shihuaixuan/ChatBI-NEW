# 41. ChatBI Evidence 驱动的受控 ReAct Agent 架构

> 状态：目标架构基线。本文按照一次 Research Run 的执行顺序描述目标架构。

# 0. 文档目标与架构结论

## 0.1 文档目标

本文定义 ChatBI 问数 Agent 的执行流程、组件职责、模型输入、工具协议、运行状态和结束方式。数据库表结构、接口版本和迁移代码在实施设计中确定。

## 0.2 顶层架构结论

ChatBI 使用一个顶层 Research Agent 完成整个问数任务。Research Agent 根据每次工具结果重新选择查询、计算、结果读取、语义检索、用户澄清或结束动作。

系统使用动态 Todo 保存跨轮目标，不使用独立 Planner 预先生成完整工具 DAG。单次语义查询内部仍由 Semantic Query Engine 生成确定性逻辑 DAG。

- Research Agent 判断下一步动作和结束状态；
- Agent Runtime 控制工具可见性、预算、重复、并发、取消、持久化和状态转换；
- 工具及其领域引擎校验参数并执行确定性操作；
- 查询和计算工具使用 Evidence 表达数据结果及业务口径；
- Responder 根据已经确认的 Finding 和 Evidence 生成最终回答。

## 0.3 完整执行流程

~~~text
用户问题
  -> 权限过滤后的语义检索
  -> Agent Input Builder 生成 ResearchAgentInput
  -> Runtime 初始化 ResearchState 和预算
  -> Runtime 投影模型输入和可见工具
  -> Research Agent 生成 ResearchTurnDecision
  -> Runtime 与工具校验并执行动作
  -> Runtime 持久化 Tool Call 和 ToolResult
  -> 查询或计算结果产生 Evidence
  -> Runtime 更新 ResearchState
  -> Research Agent 根据新状态继续判断
       -> 继续查询、读取、计算或补充语义资产
       -> 请求用户澄清并暂停
       -> 调用 finish_research
  -> finish_research 校验通过并结束 Research Run
  -> Responder 生成回答
~~~

## 0.4 为什么不采用强制顶层计划

复杂问数的后续动作依赖查询结果。执行前生成完整工具 DAG 会提前猜测结果依赖信息。Research Agent 每次读取最新 Evidence 后选择下一步，可以直接调整未执行方向。

# 1. 创建 Research Run

## 1.1 用户输入与语义检索

系统接收当前用户问题和解释该问题所需的对话消息。用户问题是 Research Run 的目标。

语义检索服务根据用户身份、权限范围、用户问题和对话上下文检索相关语义资产。检索结果经过权限过滤、相关性排序和输入预算裁剪后交给 Agent Input Builder。

## 1.2 Agent Input Builder

Agent Input Builder 将用户问题、必要对话上下文和语义检索结果组装为不可修改的 `ResearchAgentInput` 快照。

补充语义检索或用户澄清改变可用上下文时，Agent Input Builder 生成新快照。历史快照继续保留，用于恢复和审计。

## 1.3 semantic_context

`semantic_context` 使用面向模型的精简 YAML，按语义资产类型组织正式引用、业务定义和关系。

~~~yaml
semantic_context:
  metrics:
    - ref: METRIC:12:gmv
      name: GMV
      description: 支付成功商品的成交金额
      aggregation: SUM
      unit: 元
      dimensions: [DIMENSION:12:pay_date, DIMENSION:12:merchant]
  dimensions:
    - ref: DIMENSION:12:merchant
      name: 商家
      description: 产生交易的商家主体
      grains: []
  hierarchies:
    - ref: HIERARCHY:12:merchant_booth
      name: 商家档口层级
      levels: [DIMENSION:12:merchant, DIMENSION:12:booth]
  metric_formulas:
    - target_metric_ref: METRIC:12:avg_order_value
      expression: METRIC:12:gmv / METRIC:12:order_count
      source_metric_refs: [METRIC:12:gmv, METRIC:12:order_count]
  metric_analysis_relations:
    - metric_ref: METRIC:12:gmv
      related_metric_refs: [METRIC:12:order_count, METRIC:12:item_count, METRIC:12:avg_order_value]
      analysis_type: driver
  ambiguities:
    - term: 销售额
      candidate_refs: [METRIC:12:paid_amount, METRIC:12:ordered_amount]
~~~

`metrics` 和 `dimensions` 来自已发布语义资产。`hierarchies` 表达维度下钻顺序。`metric_formulas` 表达派生指标的确定性计算关系。`metric_analysis_relations` 表达已治理的指标分析关系。`ambiguities` 表达当前问题存在的语义候选。

语义检索命中派生指标时，同时返回组成指标和对应公式。

## 1.4 ResearchAgentInput

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `user_question` | `string` | 用户当前要求回答的问题 |
| `conversation_context` | `ConversationMessage[]` | 解释当前问题所需的历史消息 |
| `semantic_context` | `SemanticContext` | 权限过滤和相关性裁剪后的语义资产 |

`ConversationMessage` 包含 `role: enum[user, assistant]` 和 `content: string`。

`SemanticContext` 包含：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `metrics` | `SemanticMetric[]` | 可用指标 |
| `dimensions` | `SemanticDimension[]` | 可用维度 |
| `hierarchies` | `SemanticHierarchy[]` | 维度下钻关系 |
| `metric_formulas` | `MetricFormula[]` | 指标计算关系 |
| `metric_analysis_relations` | `MetricAnalysisRelation[]` | 指标分析关系 |
| `ambiguities` | `SemanticAmbiguity[]` | 当前语义歧义 |

`SemanticMetric` 包含 `ref: string`、`name: string`、`description: string`、`aggregation: string`、`unit: Nullable<string>` 和 `dimensions: string[]`。

`SemanticDimension` 包含 `ref: string`、`name: string`、`description: string` 和 `grains: string[]`。

`SemanticHierarchy` 包含 `ref: string`、`name: string` 和按下钻顺序排列的 `levels: string[]`。

`MetricFormula` 包含 `target_metric_ref: string`、`expression: string` 和 `source_metric_refs: string[]`。

`MetricAnalysisRelation` 包含 `metric_ref: string`、`related_metric_refs: string[]` 和 `analysis_type: string`。

`SemanticAmbiguity` 包含 `term: string` 和 `candidate_refs: string[]`。

~~~json
{
  "user_question": "分析6月29日GMV相比6月28日下降的原因，并下钻到商家和档口",
  "conversation_context": [],
  "semantic_context": {
    "metrics": [{"ref": "METRIC:12:gmv", "name": "GMV"}],
    "dimensions": [{"ref": "DIMENSION:12:merchant", "name": "商家"}],
    "hierarchies": [],
    "metric_formulas": [],
    "metric_analysis_relations": [],
    "ambiguities": []
  }
}
~~~

## 1.5 初始化 ResearchState

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `agent_input_ref` | `string` | 当前 ResearchAgentInput 快照引用 |
| `evidence_refs` | `string[]` | 从 ToolResult 定位当前 Evidence 的引用索引 |
| `findings` | `Finding[]` | 当前有效和已被替代的结论 |
| `todo_items` | `TodoItem[]` | 跨轮研究目标 |
| `attempted_actions` | `AttemptSummary[]` | 动作历史摘要 |
| `budget_usage` | `BudgetUsage` | 已消耗资源 |
| `current_status` | `enum[running, waiting_for_user, completed, partial, unanswerable, failed, cancelled]` | 当前状态 |
| `completion` | `Nullable<Completion>` | finish_research 接受的结束结果 |

`BudgetUsage` 包含 `model_turns: integer`、`query_calls: integer`、`compute_calls: integer`、`semantic_search_calls: integer`、`wall_time_ms: integer` 和 `query_cost: number`。

~~~json
{
  "agent_input_ref": "agent_input_01",
  "evidence_refs": [],
  "findings": [],
  "todo_items": [],
  "attempted_actions": [],
  "budget_usage": {"model_turns": 0, "query_calls": 0, "compute_calls": 0, "semantic_search_calls": 0, "wall_time_ms": 0, "query_cost": 0},
  "current_status": "running",
  "completion": null
}
~~~

# 2. 构建每轮 Research Agent 输入

## 2.1 输入组成

Runtime 每轮投影：

- `ResearchAgentInput: object`：用户问题、对话上下文和语义资产；
- `evidence: Evidence[]`：查询和计算结果；
- `findings: Finding[]`：已经由 Evidence 支持的结论；
- `todo_items: TodoItem[]`：跨轮目标；
- `attempted_actions: AttemptSummary[]`：相关动作、状态和错误；
- `remaining_budget: RemainingBudget`：剩余模型轮次、工具次数、时间和查询成本。

`semantic_context` 使用 YAML，其余状态使用符合 DTO 和 JSON Schema 的 JSON。Evidence 表格数据使用列定义和行数组。

## 2.2 RemainingBudget

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `model_turns` | `integer` | 剩余模型决策轮次 |
| `query_calls` | `integer` | 剩余查询次数 |
| `compute_calls` | `integer` | 剩余计算次数 |
| `semantic_search_calls` | `integer` | 剩余语义检索次数 |
| `wall_time_ms` | `integer` | 剩余执行时间 |
| `query_cost` | `number` | 剩余查询成本 |

~~~json
{"model_turns": 5, "query_calls": 3, "compute_calls": 2, "semantic_search_calls": 1, "wall_time_ms": 45000, "query_cost": 80}
~~~

## 2.3 上下文投影

Runtime 根据输入预算投影当前判断需要的 Evidence、Finding、Todo 和尝试历史。完整运行事实保存在持久化状态中。

# 3. Research Agent 选择下一步动作

## 3.1 提示词组成

Research Agent 的模型调用由三部分组成：

1. 固定 System Prompt：定义研究目标、每轮判断顺序、Evidence 使用规则、工具选择规则和结束规则；
2. 运行上下文：Runtime 投影的 ResearchAgentInput、Evidence、Finding、Todo、尝试历史和剩余预算；
3. 工具定义：模型当前可见工具的名称、描述和参数 JSON Schema。

工具字段、类型和枚举由 Tool Schema 提供。System Prompt 描述模型如何根据当前状态选择工具。

## 3.2 System Prompt

~~~text
你是 ChatBI 的 Research Agent。

你的目标是根据用户问题、当前可用语义资产和已经取得的 Evidence，逐步取得回答问题所需的数据证据，并在结果属于 complete、partial 或 unanswerable 时结束研究。

每轮输入包含：

- ResearchAgentInput：用户问题、必要对话上下文和 semantic_context；
- Evidence：查询和计算已经取得的数据、业务口径和结果摘要；
- Finding：已经由 Evidence 支持的业务结论；
- Todo：需要跨轮保留的研究目标；
- AttemptSummary：已经执行、失败或被拒绝的动作；
- RemainingBudget：剩余模型轮次、工具次数、执行时间和查询成本。

每轮按照以下顺序判断：

1. 明确用户问题中尚未回答的内容；
2. 检查现有 Evidence 是否已经包含所需数据；
3. 检查现有 Finding 是否仍然受到 Evidence 支持；
4. 从未完成 Todo 中选择当前最需要处理的方向；
5. 选择能够取得下一项关键证据的最小工具动作；
6. 根据工具定义生成参数；
7. 通过显式工具动作提交查询、计算或状态变更。

使用 Evidence 时：

- 数据结论引用当前 Run 中的 Evidence；
- 指标、维度和语义关系使用 semantic_context 中的正式引用；
- 检查 Evidence 的指标、维度、时间、筛选、结果列和限制；
- 结果被截断且需要更多行时读取已有 Evidence；
- Evidence 口径、粒度或时间范围不一致时，查询或计算能够确认差异的证据；
- 新 Evidence 与现有 Finding 冲突时，将原 Finding 标记为 superseded，并增加新的 Finding。

选择工具时：

- 获取新的业务数据：query_semantic_data；
- 对已有 Evidence 执行确定性计算：compute_evidence；
- 读取已有 Evidence 的更多结果：read_evidence_rows；
- 补充当前分析需要的语义资产：search_semantic_assets；
- 请求用户消除影响查询口径的歧义：request_clarification；
- 提交 complete、partial 或 unanswerable：finish_research。

生成查询动作时：

- 查询参数服务于当前 purpose；
- 使用用户问题要求的时间、筛选和对比范围；
- 高基数维度包含排序和结果数量限制；
- 优先复用已有 Evidence；
- 不重复已经成功或明确不可重试的等价动作；
- expected_result 说明预期取得的关键证据。

结束研究时：

- complete：现有 Finding 和 Evidence 能够回答用户问题；
- partial：能够回答部分问题，但存在明确的数据、语义、权限或预算限制；
- unanswerable：当前条件下无法取得支持用户问题的数据证据；
- partial 和 unanswerable 提交限制、影响和相关尝试记录。

工具失败时，根据结构化错误修正参数、补充语义资产、请求用户澄清或提交结束状态。

每轮提交当前最有价值的动作。相互独立的只读查询可以并行提交。
~~~

## 3.3 ResearchTurnDecision

`ResearchTurnDecision` 是模型每轮提交给 Runtime 的完整决策。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `actions` | `ResearchAction[]` | 本轮需要执行的工具动作 |

Finding、Todo、Scope 和状态事件不属于模型工具。模型在 `finish_research.findings` 中只提交结论文本和 Evidence 引用，Runtime 根据 Evidence 生成 Finding ID、Scope 和持久化事件。

`FindingChange`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `change_type` | `enum[add, supersede]` | 增加新 Finding 或替代已有 Finding |
| `finding` | `Nullable<Finding>` | `add` 时提交的新 Finding |
| `finding_id` | `Nullable<string>` | `supersede` 时引用的已有 Finding |
| `reason` | `string` | 增加或替代该 Finding 的依据 |

`TodoChange`：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `change_type` | `enum[add, set_status, set_order]` | Todo 变更类型 |
| `todo` | `Nullable<TodoItem>` | `add` 时提交的新 Todo |
| `todo_id` | `Nullable<string>` | 修改已有 Todo 时的引用 |
| `status` | `Nullable<enum[pending, in_progress, completed, skipped]>` | `set_status` 的目标状态 |
| `order` | `Nullable<integer>` | `set_order` 的目标顺序 |
| `result_reason` | `Nullable<string>` | 完成或跳过 Todo 的依据 |

## 3.4 ResearchAction

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `action_type` | `ResearchActionType` | 工具名称 |
| `purpose` | `string` | 当前动作的原因 |
| `expected_result` | `Nullable<string>` | 预期结果；结束动作为空 |
| `arguments` | `ActionArguments` | 对应工具参数 |

`ResearchActionType` 取 `query_semantic_data`、`compute_evidence`、`read_evidence_rows`、`search_semantic_assets`、`request_clarification` 或 `finish_research`。

~~~json
{
  "actions": [
    {
      "action_type": "query_semantic_data",
      "purpose": "定位对GMV下降贡献最大的商家",
      "expected_result": "得到各商家的两期GMV、差值和增长率",
      "arguments": {
        "request": {
          "operation": "breakdown",
          "metric_refs": ["METRIC:12:gmv"],
          "dimension_refs": ["DIMENSION:12:merchant"],
          "value_mode": "growth_rate",
          "filters": [],
          "order_by": [{"field_ref": "METRIC:12:gmv", "value_role": "difference", "direction": "asc"}],
          "limit": 20
        }
      }
    }
  ]
}
~~~

只读且相互独立的查询可以并行。存在结果依赖的动作等待上游 ToolResult 返回后再决定。

# 4. Agent Runtime 接收并调度动作

## 4.1 执行顺序

~~~text
1. 投影模型输入和可见工具
2. 解析 ResearchTurnDecision
3. 校验 FindingChange、TodoChange 和 ResearchAction
4. 应用 Finding 和 Todo 变更
5. Args DTO 校验工具参数结构
6. 校验状态、取消、并发和基础预算
7. 工具 prepare 校验领域参数并生成指纹和成本估算
8. 校验重复动作和精确预算
9. 执行工具
10. 构造并持久化 ToolResult
11. 更新尝试历史、预算和 Evidence 引用
12. 进入下一轮、暂停等待用户或结束
~~~

## 4.2 工具统一接口

~~~text
ResearchTool
  ├─ args_model
  ├─ result_model
  ├─ prepare(context, args)
  └─ execute(context, prepared_action) -> result payload
~~~

`args_model` 定义参数 JSON Schema。`result_model` 定义结果类型。`prepare` 校验参数关系和领域状态。`execute` 返回类型化结果负载。

## 4.3 校验边界

| 校验内容 | 执行位置 |
| --- | --- |
| ResearchTurnDecision、FindingChange、TodoChange 和 ResearchAction 结构 | Runtime |
| Finding 的 Evidence 引用和 Todo 状态转换 | Runtime |
| 工具可见性、Run 状态和基础预算 | Runtime |
| 参数字段、类型和枚举 | 工具 Args DTO |
| 参数组合和领域引用 | 工具 prepare |
| 语义资产和指标维度兼容性 | Semantic Query Engine |
| Evidence 所有权和结果读取 | 对应工具 |
| 数据权限 | 权限服务 |
| 查询或计算成本 | Query Engine 或 Compute Engine |
| 动作指纹、重复动作和精确预算 | Runtime |
| Completion 状态和引用一致性 | finish_research |
| 结果负载和 ToolResult 外层 | Runtime |

## 4.4 ToolResult

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `tool_call_id` | `string` | 工具调用标识 |
| `name` | `ResearchActionType` | 工具名称 |
| `status` | `enum[succeeded, failed, waiting_for_user]` | 执行状态 |
| `result` | `Nullable<ToolResultPayload>` | 类型化结果 |
| `error` | `Nullable<ExecutionError>` | 失败信息 |

`ToolResultPayload` 根据工具名称取 `Evidence`、`EvidenceRows`、`SemanticContextDelta`、`ClarificationRequest` 或 `CompletionResult`。

## 4.5 ExecutionError 与 AttemptSummary

`ExecutionError` 包含 `code: string`、`stage: enum[parsing, visibility, validation, permission, planning, compilation, execution, persistence, budget, completion]`、`message: string`、`retryable: boolean`、`parameter_retryable: boolean` 和 `same_parameter_retryable: boolean`。

`AttemptSummary` 包含：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `attempt_id` | `string` | 尝试标识 |
| `action_type` | `ResearchActionType` | 动作类型 |
| `purpose` | `string` | 动作目的 |
| `parameter_summary` | `string` | 参数摘要 |
| `action_fingerprint` | `string` | 等价动作指纹 |
| `status` | `enum[succeeded, failed, rejected, waiting_for_user]` | 执行结果 |
| `produced_evidence_ids` | `string[]` | 新 Evidence 引用 |
| `error` | `Nullable<ExecutionError>` | 错误 |

~~~json
{
  "attempt_id": "attempt_02",
  "action_type": "query_semantic_data",
  "purpose": "查询商家维度的GMV变化",
  "parameter_summary": "GMV；按商家；6月29日对比6月28日",
  "action_fingerprint": "query_f8c02a",
  "status": "rejected",
  "produced_evidence_ids": [],
  "error": {"code": "DIMENSION_CARDINALITY_LIMIT", "stage": "validation", "message": "需要排序方式和结果数量限制", "retryable": true, "parameter_retryable": true, "same_parameter_retryable": false}
}
~~~

# 5. 工具执行

## 5.1 工具集合与可见性

| 工具 | 用途 | 产生新 Evidence |
| --- | --- | --- |
| `query_semantic_data` | 获取语义数据 | 是 |
| `compute_evidence` | 基于 Evidence 执行确定性计算 | 是 |
| `read_evidence_rows` | 读取已有 Evidence 的更多结果 | 否 |
| `search_semantic_assets` | 补充权限内语义资产 | 否 |
| `request_clarification` | 请求用户消除关键歧义 | 否 |
| `finish_research` | 提交结束状态和回答依据 | 否 |

Runtime 默认提供查询、计算和结束工具。Evidence 展示数据不足时提供结果读取工具，语义资产不足时提供补充检索工具，关键歧义无法消除时提供澄清工具。

## 5.2 query_semantic_data

`query_semantic_data` 接收高层分析动作和业务资产，服务端编译为内部声明式语义查询，并返回 `ToolResult<Evidence>`。

### 5.2.1 输入参数

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `request` | `ResearchQueryOperation` | 带 `operation` 判别字段的标准分析动作 |

标准动作包括 `metric_snapshot`、`multi_period_snapshot`、`compare_metrics`、`breakdown`、`drilldown`、`contribution` 和 `validate_drivers`。动作只携带正式指标、维度、层级、Evidence 筛选、排序和 Limit；日期边界、时间维度、时间粒度、不可变筛选、Scope、权限与版本均由服务端从冻结 `ResearchRequirement` 补全。

`QueryFilter` 包含 `field_ref: string`、`operator: string`、`value: Nullable<ScalarValue>` 和 `evidence_selector: Nullable<EvidenceSelector>`。`EvidenceSelector` 包含 `evidence_id: string`、`column_ref: string` 和 `selection: enum[all, top, bottom]`。

`QueryOrder` 包含 `field_ref: string`、`value_role: string` 和 `direction: enum[asc, desc]`。

### 5.2.2 Semantic Query Engine

Semantic Query Engine 执行：

1. 根据 operation 确定分析类型，并补全冻结时间、筛选、Scope 和版本；
2. 校验权限、资产存在性和指标维度兼容性；
3. 展开派生指标公式；
4. 选择已发布的语义模型关系；
5. 生成逻辑 DAG 和成本估算；
6. 编译只读 SQL 并执行；
7. 将完整结果写入引擎内部存储；
8. 返回逻辑列、统计信息和限制。

查询内部 DAG 由确定性规则生成。模型只提交标准分析动作和业务资产，不生成日期边界、比较 DAG、物理 SQL、Join 路径或执行顺序。

查询参数已经明确要求的派生指标、对比值、排序和 Top N 进入本次查询 DAG。取得 Evidence 后才决定的跨结果合并、对账、占比和重新排名由 `compute_evidence` 执行。

### 5.2.3 输出示例

~~~json
{
  "tool_call_id": "tool_call_query_01",
  "name": "query_semantic_data",
  "status": "succeeded",
  "result": {
    "evidence_id": "evidence:tool_call_query_01",
    "evidence_type": "query_result",
    "purpose": "对比两天总体GMV和订单数",
    "definition": {
      "metrics": ["METRIC:12:gmv", "METRIC:12:order_count"],
      "dimensions": ["DIMENSION:12:pay_date"],
      "time_ranges": [{"start": "2026-06-28", "end": "2026-06-29", "granularity": "day"}],
      "filters": [],
      "comparison": null,
      "computation": null
    },
    "columns": [
      {"name": "pay_date", "semantic_ref": "DIMENSION:12:pay_date", "role": "dimension", "data_type": "date", "unit": null},
      {"name": "gmv", "semantic_ref": "METRIC:12:gmv", "role": "metric", "data_type": "decimal", "unit": "元"}
    ],
    "data": {"row_count": 2, "truncated": false, "rows": [["2026-06-28", 100000], ["2026-06-29", 80000]], "statistics": []},
    "parent_evidence_ids": [],
    "limitations": []
  },
  "error": null
}
~~~

## 5.3 compute_evidence

`compute_evidence` 对当前 Run 的 Evidence 执行已注册、可复现的计算。

### 5.3.1 输入参数

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `operation` | `enum[difference, growth_rate, ratio, share, contribution, ranking, topn_other, merge, reconciliation]` | 计算类型 |
| `input_evidence_ids` | `string[]` | 输入 Evidence |
| `metric_refs` | `string[]` | 指标逻辑列 |
| `dimension_refs` | `string[]` | 维度逻辑列 |
| `group_by_refs` | `string[]` | 分组维度 |
| `order_by` | `QueryOrder[]` | 排序 |
| `limit` | `Nullable<integer>` | Top N 数量 |
| `tolerance` | `Nullable<number>` | 对账误差 |

Compute Engine 读取完整结果，校验逻辑列、单位、粒度和分组键，将计算结果写入引擎内部存储。新 Evidence 的 `parent_evidence_ids` 取输入 Evidence 引用。

~~~json
{
  "operation": "contribution",
  "input_evidence_ids": ["evidence:tool_call_query_02"],
  "metric_refs": ["METRIC:12:gmv"],
  "dimension_refs": ["DIMENSION:12:merchant"],
  "group_by_refs": ["DIMENSION:12:merchant"],
  "order_by": [{"field_ref": "METRIC:12:gmv", "value_role": "contribution", "direction": "desc"}],
  "limit": 20,
  "tolerance": null
}
~~~

### 5.3.2 输出示例

~~~json
{
  "tool_call_id": "tool_call_compute_01",
  "name": "compute_evidence",
  "status": "succeeded",
  "result": {
    "evidence_id": "evidence:tool_call_compute_01",
    "evidence_type": "computation_result",
    "purpose": "计算商家GMV下降贡献度",
    "definition": {
      "metrics": ["METRIC:12:gmv"],
      "dimensions": ["DIMENSION:12:merchant"],
      "time_ranges": [],
      "filters": [],
      "comparison": null,
      "computation": {"operation": "contribution", "input_evidence_ids": ["evidence:tool_call_query_02"], "parameters": []}
    },
    "columns": [{"name": "gmv_contribution", "semantic_ref": "METRIC:12:gmv", "role": "computed", "data_type": "decimal", "unit": "%"}],
    "data": {"row_count": 20, "truncated": true, "rows": [["商家A", 35.2], ["商家B", 21.8]], "statistics": []},
    "parent_evidence_ids": ["evidence:tool_call_query_02"],
    "limitations": []
  },
  "error": null
}
~~~

## 5.4 read_evidence_rows

`read_evidence_rows` 根据 `evidence_id` 从查询或计算引擎的内部结果存储读取指定逻辑列和结果行。

输入包含 `evidence_id: string`、`column_refs: string[]`、`order_by: QueryOrder[]`、`offset: integer` 和 `limit: integer`。

~~~json
{"evidence_id": "evidence:tool_call_query_02", "column_refs": ["DIMENSION:12:merchant", "METRIC:12:gmv"], "order_by": [{"field_ref": "METRIC:12:gmv", "value_role": "difference", "direction": "asc"}], "offset": 20, "limit": 20}
~~~

结果类型 `EvidenceRows` 包含 `evidence_id: string`、`columns: EvidenceColumn[]`、`rows: Scalar[][]`、`total_row_count: integer`、`offset: integer` 和 `truncated: boolean`。

~~~json
{
  "tool_call_id": "tool_call_read_01",
  "name": "read_evidence_rows",
  "status": "succeeded",
  "result": {
    "evidence_id": "evidence:tool_call_query_02",
    "columns": [{"name": "merchant", "semantic_ref": "DIMENSION:12:merchant", "role": "dimension", "data_type": "string", "unit": null}],
    "rows": [["商家C", -12000], ["商家D", -9000]],
    "total_row_count": 100,
    "offset": 20,
    "truncated": true
  },
  "error": null
}
~~~

该工具读取已有结果，不产生新 Evidence。

## 5.5 search_semantic_assets

`search_semantic_assets` 在当前 semantic_context 缺少分析所需指标、维度或关系时执行权限过滤后的补充检索。

输入包含 `query: string`、`asset_types: SemanticAssetType[]`、`related_asset_refs: string[]` 和 `limit: integer`。`SemanticAssetType` 取 `metric`、`dimension`、`hierarchy`、`metric_formula` 或 `metric_analysis_relation`。

~~~json
{"query": "退款金额、退款订单数和退款原因", "asset_types": ["metric", "dimension", "metric_analysis_relation"], "related_asset_refs": ["METRIC:12:gmv"], "limit": 20}
~~~

成功后，Runtime 合并原语义资产和新增资产，生成新的 ResearchAgentInput 快照，并更新 `ResearchState.agent_input_ref`。

`SemanticContextDelta` 包含 `new_agent_input_ref: string` 和 `added_semantic_context: SemanticContext`。

~~~json
{
  "tool_call_id": "tool_call_search_01",
  "name": "search_semantic_assets",
  "status": "succeeded",
  "result": {"new_agent_input_ref": "agent_input_02", "added_semantic_context": {"metrics": [], "dimensions": [], "hierarchies": [], "metric_formulas": [], "metric_analysis_relations": [], "ambiguities": []}},
  "error": null
}
~~~

## 5.6 request_clarification

`request_clarification` 在多个候选解释会产生不同查询口径且当前上下文无法可靠选择时暂停 Research Run 并请求用户确认。

输入包含 `question: string`、`options: ClarificationOption[]` 和 `allow_free_text: boolean`。`ClarificationOption` 包含 `option_id: string`、`label: string`、`description: string` 和 `semantic_refs: string[]`。

~~~json
{
  "question": "这里的销售额是指支付金额还是下单金额？",
  "options": [
    {"option_id": "paid_amount", "label": "支付金额", "description": "支付成功订单金额", "semantic_refs": ["METRIC:12:paid_amount"]},
    {"option_id": "ordered_amount", "label": "下单金额", "description": "成功创建订单时的金额", "semantic_refs": ["METRIC:12:ordered_amount"]}
  ],
  "allow_free_text": false
}
~~~

`ClarificationRequest` 增加 `clarification_request_id: string`。用户回答形成 `ClarificationResponse`，包含 `clarification_request_id: string`、`selected_option_ids: string[]` 和 `free_text: Nullable<string>`。

~~~json
{
  "tool_call_id": "tool_call_clarification_01",
  "name": "request_clarification",
  "status": "waiting_for_user",
  "result": {
    "clarification_request_id": "clarification_01",
    "question": "这里的销售额是指支付金额还是下单金额？",
    "options": [{"option_id": "paid_amount", "label": "支付金额", "description": "支付成功订单金额", "semantic_refs": ["METRIC:12:paid_amount"]}],
    "allow_free_text": false
  },
  "error": null
}
~~~

用户回答后，Runtime 生成新的 ResearchAgentInput 快照并恢复 Agent Loop。

## 5.7 finish_research

Research Agent 根据用户问题、Evidence、Finding、Todo 和尝试历史判断结果属于 `complete`、`partial` 或 `unanswerable`，然后调用 `finish_research`。

### 5.7.1 Completion

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | `enum[complete, partial, unanswerable]` | 结束状态 |
| `summary` | `string` | 当前 Evidence 如何回答用户问题 |
| `finding_ids` | `string[]` | 进入回答的 Finding |
| `evidence_ids` | `string[]` | 支持结束结果的 Evidence |
| `limitations` | `CompletionLimitation[]` | 影响回答范围的限制 |

`CompletionLimitation` 包含 `code: string`、`description: string`、`impact: string` 和 `attempt_ids: string[]`。

~~~json
{
  "status": "partial",
  "summary": "现有证据可以解释总体变化并定位主要下降商家",
  "finding_ids": ["finding_01", "finding_02"],
  "evidence_ids": ["evidence:tool_call_query_01", "evidence:tool_call_compute_01"],
  "limitations": [{"code": "SEMANTIC_ASSET_MISSING", "description": "缺少档口维度", "impact": "无法继续下钻到档口", "attempt_ids": ["attempt_05"]}]
}
~~~

### 5.7.2 确定性校验

`finish_research` 校验：

- Finding 存在于当前 Run 且状态为 `confirmed`；
- Evidence 存在于当前 Run 的查询或计算 ToolResult；
- Finding 引用的 Evidence 包含在 Completion 中；
- `Finding.scope` 能在对应 Evidence 的 definition 和 columns 中定位；
- `partial` 和 `unanswerable` 至少包含一项限制；
- 限制引用的尝试记录存在；
- 当前没有尚未结束的工具调用。

校验通过后，Runtime 保存 Completion，并将 `complete` 映射为 `completed`，将 `partial` 和 `unanswerable` 映射为同名运行终态。校验失败后，Research Agent 根据错误修正请求或继续研究。

### 5.7.3 输出结果

`CompletionResult` 包含 `decision: enum[accepted, rejected]`、`message: string`、`completion_status: Nullable<enum[complete, partial, unanswerable]>` 和 `validation_errors: CompletionValidationError[]`。

`CompletionValidationError` 包含 `code: string`、`message: string`、`finding_id: Nullable<string>` 和 `evidence_id: Nullable<string>`。

~~~json
{
  "tool_call_id": "tool_call_finish_01",
  "name": "finish_research",
  "status": "succeeded",
  "result": {"decision": "accepted", "message": "结束请求通过校验", "completion_status": "complete", "validation_errors": []},
  "error": null
}
~~~

校验拒绝仍表示工具执行成功，业务结果通过 `decision: rejected` 和 `validation_errors` 返回。

~~~json
{
  "tool_call_id": "tool_call_finish_02",
  "name": "finish_research",
  "status": "succeeded",
  "result": {
    "decision": "rejected",
    "message": "Finding引用的Evidence不存在",
    "completion_status": null,
    "validation_errors": [{"code": "EVIDENCE_REFERENCE_INVALID", "message": "finding_02引用的Evidence不属于当前Run", "finding_id": "finding_02", "evidence_id": "evidence:tool_call_missing"}]
  },
  "error": null
}
~~~

# 6. 工具结果进入下一轮

## 6.1 Evidence

`Evidence` 是 `query_semantic_data` 和 `compute_evidence` 成功后的 `ToolResult.result` 类型。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evidence_id` | `string` | 当前 Run 内唯一标识，由 tool_call_id 确定性生成 |
| `evidence_type` | `enum[query_result, computation_result]` | 来源类型 |
| `purpose` | `string` | 产生该结果的研究目的 |
| `definition` | `EvidenceDefinition` | 查询或计算口径 |
| `columns` | `EvidenceColumn[]` | 结果列定义 |
| `data` | `EvidenceData` | 摘要和受控结果行 |
| `parent_evidence_ids` | `string[]` | 上游 Evidence |
| `limitations` | `EvidenceLimitation[]` | 截断、质量和范围限制 |

`EvidenceDefinition` 包含 `metrics: string[]`、`dimensions: string[]`、`time_ranges: TimeRange[]`、`filters: EvidenceFilter[]`、`comparison: Nullable<EvidenceComparison>` 和 `computation: Nullable<EvidenceComputation>`。

`TimeRange` 包含 `start: string`、`end: string` 和 `granularity: string`。

`EvidenceFilter` 包含 `field_ref: string`、`operator: string` 和 `value: ScalarValue`。

`EvidenceComparison` 包含 `base_period: string`、`against_period: string` 和 `outputs: string[]`。

`EvidenceComputation` 包含 `operation: string`、`input_evidence_ids: string[]` 和 `parameters: KeyValue[]`。

`EvidenceColumn` 包含 `name: string`、`semantic_ref: Nullable<string>`、`role: enum[dimension, metric, computed]`、`data_type: string` 和 `unit: Nullable<string>`。

`EvidenceData` 包含 `row_count: integer`、`truncated: boolean`、`rows: Scalar[][]` 和 `statistics: EvidenceStatistic[]`。

`EvidenceStatistic` 包含 `name: string`、`value: Scalar` 和 `unit: Nullable<string>`。`EvidenceLimitation` 包含 `code: string`、`description: string` 和 `impact: string`。

查询和计算引擎分别保存完整结果，并维护 `evidence_id` 到内部结果的映射。该映射不进入 ResearchState 或 ToolResult。

## 6.2 Finding

`Finding` 保存已经由 Evidence 支持、可以进入最终回答的业务结论。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `finding_id` | `string` | Finding 标识 |
| `statement` | `string` | 业务结论 |
| `evidence_ids` | `string[]` | 支持该结论的 Evidence |
| `scope` | `FindingScope` | 结论的指标、维度、时间和筛选范围 |
| `status` | `enum[confirmed, superseded]` | 当前状态 |

`FindingScope` 包含 `metric_refs: string[]`、`dimension_refs: string[]`、`time_ranges: TimeRange[]` 和 `filters: EvidenceFilter[]`。

~~~json
{
  "finding_id": "finding_01",
  "statement": "6月29日GMV下降20%，订单数下降21%，下降主要表现为订单量减少",
  "evidence_ids": ["evidence:tool_call_query_01"],
  "scope": {"metric_refs": ["METRIC:12:gmv", "METRIC:12:order_count"], "dimension_refs": ["DIMENSION:12:pay_date"], "time_ranges": [{"start": "2026-06-28", "end": "2026-06-29", "granularity": "day"}], "filters": []},
  "status": "confirmed"
}
~~~

## 6.3 TodoItem

`TodoItem` 包含 `todo_id: string`、`goal: string`、`status: enum[pending, in_progress, completed, skipped]`、`order: integer`、`related_evidence_ids: string[]` 和 `result_reason: Nullable<string>`。

~~~json
{"todo_id": "todo_01", "goal": "定位对GMV下降贡献最大的商家", "status": "pending", "order": 1, "related_evidence_ids": ["evidence:tool_call_query_01"], "result_reason": null}
~~~

动态 Todo 用于保存跨轮目标和展示进度。模型根据 Evidence 增加、完成、跳过或重排 Todo，Runtime 不根据 Todo 自动生成工具调用。

## 6.4 状态更新

Runtime 持久化 ToolResult，将查询和计算结果的 `evidence_id` 加入 `ResearchState.evidence_refs`，并更新尝试历史和预算。Finding 和 Todo 的结构化变更进入 ResearchState 后，Runtime 构建下一轮输入。

# 7. 查询结果改变分析方向

查询结果改变方向是正常的下一轮判断，不触发独立 Replan 阶段。

Research Agent 可以：

- 使用 `query_semantic_data` 查询新数据；
- 使用 `compute_evidence` 计算已有 Evidence；
- 使用 `read_evidence_rows` 读取更多结果；
- 使用 `search_semantic_assets` 补充新方向需要的资产；
- 使用 `request_clarification` 消除影响口径的歧义；
- 更新未完成 Todo；
- 使用 `finish_research` 提交结束状态。

# 8. 结束运行并生成回答

`finish_research` 接受 Completion 后，Runtime 保存终态并停止 Agent Loop。

Responder 读取 Completion、状态为 `confirmed` 的 Finding、相关 Evidence 和限制，生成文字、表格和图表。最终回答中的数据结论必须能够追溯到对应 Finding 和 Evidence。

# 9. 事件、持久化和恢复

## 9.1 持久化事实

系统持久化：

- ResearchAgentInput 快照及语义版本；
- 权限、语义和预算配置；
- 每轮 `ResearchTurnDecision`；
- Tool Call、参数和 ToolResult；
- Evidence 及其 parent_evidence_ids；
- Finding 和 Todo 变化；
- 用户澄清请求、回答和等待状态；
- Completion 和 finish_research 校验结果；
- 预算、取消和失败终态。

查询和计算引擎分别持久化完整结果，并维护 evidence_id 到内部结果的映射。

## 9.2 恢复规则

恢复运行从已提交事实继续。已经成功且幂等键一致的动作不重复执行。模型输入由持久化事实重新投影，不依靠模型重新描述历史。

# 10. 运行约束

Runtime 强制：

- 最大模型调用次数；
- 最大查询、计算和补充语义检索次数；
- 最大执行时间、查询成本和结果规模；
- 维度基数、排序和 Limit；
- 等价动作去重；
- 连续无新 Evidence 检测；
- 工具错误分类和重试范围；
- 用户取消；
- 语义资产和权限校验；
- finish_research 的状态和引用校验。

Runtime 应为 Research Agent 保留结束判断所需的模型轮次。当剩余动作预算不足以继续研究时，只向模型提供当前可执行的结束动作，使其提交 partial 或 unanswerable 及明确限制。

# 11. 架构不变量

1. 用户问题只能由用户输入改变，semantic_context 只能由权限过滤后的语义检索结果构建；
2. 模型不能生成或执行物理 SQL；
3. 查询和计算只能通过正式工具契约执行；
4. 数据结论必须引用有效 Evidence；
5. Evidence 只能由确定性查询或计算结果构建；
6. 已持久化的 Tool Call、ToolResult 和 Evidence 不可由模型修改；
7. Runtime 强制权限、预算、重复、取消、恢复和状态转换；
8. Research Agent 判断下一步动作、结束时机和结束状态；
9. finish_research 校验结束状态和引用一致性；
10. Responder 消费 finish_research 已接受的 Completion、Finding、Evidence 和限制；
11. 工具失败、能力不足和数据缺失必须显式进入状态。

# 12. 当前实现的迁移

## 12.1 保留能力

- 语义检索结果快照和权限版本；
- Semantic Query Engine；
- Compute Engine；
- 查询和计算引擎内部的完整结果存储；
- Tool Call、Trace、预算、取消、快照和恢复；
- Finding 和最终回答的 Evidence 引用。

## 12.2 调整内容

- ResearchPlanNode 不再作为顶层工具执行协议；
- submit_research_plan 不再是推进研究的前置动作；
- _solve_current_plan() 不再完整执行模型预生成的工具 DAG；
- plan_execution_state 不再承担顶层研究状态；
- Planner revision 改为 ResearchState 和动态 Todo 的增量记录；
- 每次重要 ToolResult 返回后重新进入 Research Agent 判断。

## 12.3 迁移顺序

1. 引入统一 ToolResult 和新的 Evidence 契约；
2. 将查询和计算结果改为 Evidence；
3. 引入 ResearchState 和每轮上下文投影；
4. 将底层查询 DAG 收回 Semantic Query Engine；
5. 用 ResearchTurnDecision 驱动状态更新和单步工具执行；
6. 接入补充语义检索、澄清和结束状态；
7. 切换 Responder 输入；
8. 移除旧顶层计划执行入口。

# 13. 后续待确定内容

1. DTO、JSON Schema 和接口版本；
2. 每轮上下文裁剪和 Token 预算；
3. 动作指纹和无进展判断算法；
4. finish_research 的错误编码和状态转换；
5. 强制停止和异常超时处理；
6. 并行查询的事务、Session、Trace 和内部结果隔离；
7. 简单查询快捷路径；
8. 正确性、查询效率、停止行为和恢复能力评测集。
