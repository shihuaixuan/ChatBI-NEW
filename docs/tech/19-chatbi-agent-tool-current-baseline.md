# ChatBI Agent Tool 改造前实现基线

**日期：** 2026-07-28

**状态：** 阶段 0 历史基线，已冻结

**依据：** `17-chatbi-agent-tool-architecture-design.md`、`18-chatbi-agent-tool-architecture-implementation-plan.md`

## 1. 用途

本文记录 2026-07-28 阶段 0 时的实际实现，用于判断后续行为变化和确认历史依赖已经删除。本文不描述当前代码。当前架构以文档 17 为准，实施结果以文档 18 和文档 20 为准。

阶段 0 不修改生产行为。本文记录的问题应在后续阶段按计划解决，不能通过扩大测试基线长期保留。

## 2. 当前调用链

```text
ChatBI composition 显式注册 9 个 Tool
    ToolRegistry.tool_specs()
        DefaultAgentModelClient.bind_tools()
            模型返回 AIMessage.tool_calls
                AgentToolExecutor
                    ToolRegistry.execute()
                        Pydantic 参数校验
                        ErrorNormalize / Timeout / Latency middleware
                        Tool.execute(AgentToolContext, args)
```

当前 Tool 注册和执行使用项目自有代码，LangChain 用于模型、消息和 `bind_tools()`。当前 `apps/tool/messages.py` 仍直接导入 LangChain 消息类型，这属于待清理基线。

## 3. 9 个 Tool 基线

### 3.1 汇总

| Tool | 当前输入 | 当前主要输出 | 当前依赖 | 当前副作用 |
| --- | --- | --- | --- | --- |
| `search_semantic_assets` | 无模型参数 | 语义包、候选资产、表 | SemanticRetrievalService、Context state | 写语义包、资产 ID、`allowed_tables` |
| `get_dataset_schema` | `table_keyword` | 表、字段、类型、注释 | PhysicalSchemaService | 用返回表扩大 `allowed_tables` |
| `compile_semantic_sql` | 指标、维度、过滤、时间分桶、排序、LIMIT | SQL、表、指标、维度 | SemanticCompilationService、问题理解 state | 写 `compiled_sql`、扩大 `allowed_tables` |
| `validate_sql` | SQL | 补 LIMIT 后的 SQL、引用表 | QueryService；缺失时临时创建 GuardedQueryService | 不修改 state，但读取问题理解和 `allowed_tables` |
| `execute_sql` | SQL | 字段、样本行、行数、统计、Artifact 引用 | QueryService、ResultArtifactService | 保存 Artifact，写 `last_execution` 和 `full_data` |
| `search_terminology` | `term` | 术语条目和数量 | TermQueryService、dataset state | 不修改 state |
| `get_sql_examples` | `question` | SQL 示例和数量 | Session；执行时临时组装 Knowledge Service | 不修改 state |
| `clarify` | 问题、选项 | 澄清请求 | 无领域服务 | Tool 自身不挂起；当时由 ChatBI 执行层按名称处理 |
| `finish` | 回答、图表类型、字段 | 最终回答、图表、SQL | FinalReplyProjection、`last_execution` state | Tool 自身不结束 Run；当时由 ChatBI 执行层按名称处理 |

### 3.2 `search_semantic_assets`

- 输入：空参数模型，问题、意图和数据集从 Context/state 读取。
- 输出：检索服务返回的语义包。
- 当前校验：数据集存在、问题理解结构完整、检索服务已配置。
- 当前状态修改：
  - `semantic_package`；
  - `semantic_asset_ids`；
  - `allowed_tables`。
- 当前已知失败：`semantic_dataset_not_found`、`question_understanding_required`、`question_understanding_invalid`、`semantic_retrieval_service_required`。
- 目标变化：保留为 ChatBI 适配 Tool，返回结构化数据，由 ChatBI 结果处理更新状态。

### 3.3 `get_dataset_schema`

- 输入：可选 `table_keyword`。
- 输出：物理表、字段、类型和注释。
- 当前校验：数据源存在、Schema 服务已配置。
- 当前状态修改：把返回的所有表加入 `allowed_tables`。
- 当前已知失败：`datasource_required`、`physical_schema_service_required`。
- 当前缺口：服务签名不包含用户身份和明确授权表范围。
- 目标变化：移动到公共 Datasource Tool，Schema 服务返回前完成权限过滤，Tool 不修改状态。

### 3.4 `compile_semantic_sql`

- 输入：指标资产 ID、维度资产 ID、过滤、时间分桶、排序和 LIMIT。
- 输出：SQL、物理表、指标、维度、数据集和编译策略。
- 当前校验：
  - 问题理解已经通过；
  - 数据集存在；
  - 资产 ID 来自当前语义包；
  - 已确认时间范围必须正确进入过滤条件。
- 当前状态修改：写 `compiled_sql`，把编译结果中的表加入 `allowed_tables`。
- 当前已知失败：问题理解相关错误、`semantic_dataset_not_found`、`semantic_package_required`、`asset_not_in_package`、`time_range_unsupported`、`time_filter_mismatch`、`semantic_query_service_required` 和语义编译错误。
- 目标变化：保留为 ChatBI 适配 Tool，资产来源使用可信上下文或 validator，状态更新进入集中结果处理。

### 3.5 `validate_sql`

- 输入：SQL。
- 输出：安全校验并补 LIMIT 后的 SQL 和引用表。
- 当前校验：问题理解门禁、单语句、只读、危险关键字、`allowed_tables`。
- 当前依赖：优先使用 Context 中的 QueryService；缺失时临时创建 `GuardedQueryService`。
- 当前状态修改：无。
- 当前缺口：`allowed_tables` 为空时表范围检查被跳过；读取的是 Agent 选择范围，不是权限服务确认的最大范围。
- 目标变化：移动到公共 Datasource Tool，使用明确可信范围；它提供提前反馈，但执行时仍需再次校验。

### 3.6 `execute_sql`

- 输入：SQL。
- 输出：最终 SQL、字段、样本行、行数、数值统计、Artifact 引用和 SQL 来源。
- 当前执行顺序：权限改写、SQL 校验、数据库执行、结果归一、Artifact 保存、状态更新。
- 当前依赖：QueryService、ResultArtifactService、执行归属信息和 Context state。
- 当前状态修改：`last_execution`、`full_data`。
- 当前外部写入：保存 SQL 结果 Artifact。
- 当前已知失败：问题理解错误、`datasource_required`、`query_service_required`、`result_artifact_service_required`、权限和 SQL 服务错误、`sql_result_artifact_write_failed`。
- 当前缺口：权限、SQL 语法、字段和临时连接错误没有稳定映射到不同重试建议。
- 目标变化：公共 Tool 只调用 Datasource 查询服务并返回结果；Artifact、SQL 来源和状态更新由 ChatBI 处理。

### 3.7 `search_terminology`

- 输入：业务术语 `term`。
- 输出：术语条目和数量。
- 当前依赖：TermQueryService、工作空间、数据集和可变 state。
- 当前状态修改：无。
- 当前已知失败：`semantic_dataset_not_found`、`semantic_term_query_unavailable`。
- 目标变化：移动到公共 Semantic Tool，只接收查询所需身份和数据集范围。

### 3.8 `get_sql_examples`

- 输入：问题文本 `question`。
- 输出：最多 5 条 SQL 示例、总数和“仅供参考”说明。
- 当前依赖：完整 Session、工作空间和数据源。
- 当前行为：在 `execute()` 中调用 `build_sql_example_query_service(ctx.session)`。
- 当前状态修改：无。
- 当前缺口：真实服务依赖只能进入实现后确认，示例数据权限边界不在 Tool 契约中表达。
- 目标变化：移动到公共 Knowledge Tool，服务由构造函数注入。

### 3.9 `clarify`

- 输入：问题和结构化选项。
- 输出：澄清请求。
- 当前状态修改：无。
- 当时的调用方行为：`AgentToolExecutor` 按工具名称记录澄清预算、创建澄清记录并挂起 Run。
- 当时的失败处理：参数校验失败；澄清次数超限由 ChatBI 执行层处理。
- 目标变化：继续作为 ChatBI 控制 Tool，返回控制请求，由 ChatBI 结果处理解释。

### 3.10 `finish`

- 输入：Markdown 回答、图表类型、X 字段和 Y 字段。
- 输出：回答、图表、SQL 和非标准口径提示。
- 当前依赖：从 state 读取 `last_execution`，调用 FinalReplyProjection。
- 当前状态修改：无。
- 当前已知失败：`execution_required_before_finish` 和图表字段校验错误。
- 当时的调用方行为：`AgentToolExecutor` 按工具名称结束 Run、发布图表事件并投影 ChatRecord。
- 目标变化：继续作为 ChatBI 控制 Tool，只返回请求；真正结束条件由 ChatBI 处理。

## 4. 当前通用运行时基线

### 4.1 Registry

- 未注册 Tool 返回 `denied/tool_not_allowed`。
- Pydantic 参数失败也返回 `denied/invalid_tool_args`。
- Registry 直接导出 OpenAI Function Calling 形状。
- Registry 调用 middleware 后执行 Tool。

目标设计要求参数失败改为可修正失败，并先生成厂商无关 `ToolDefinition`。

### 4.2 结果

- `ToolOutput` 同时保存 `success` 和 `status`。
- 模型内容使用 `summary`。
- 业务、前端和内部数据共同放在 `payload`。
- Latency middleware 把耗时写入 `payload._trace`。

目标设计要求使用单一终态，并拆分 `model_content`、`data` 和 `metadata`。

### 4.3 并发

- `is_concurrency_safe=True` 的连续调用可以并发。
- `clarify` 和 `finish` 通过名称强制串行。
- 并发结果按模型调用顺序返回。
- 并发线程内未知异常转换为 `tool_parallel_exception`。

### 4.4 超时和异常

- 单 Tool 超时使用 `ThreadPoolExecutor` 等待超时。
- 超时后不能保证底层操作终止。
- `ErrorNormalizeMiddleware` 把所有未知异常转换为 `tool_exception`。
- AgentLoop 最外层把未预期异常转换为 Run 失败事件。

## 5. 当前持久化、Event 和 Trace 基线

- Step 表示模型推理轮次，但同时保存单一 `tool_name`、参数、结果和工具耗时。
- 一轮多个 Tool Call 会反复覆盖同一个 Step 的工具字段。
- `tool_call_id` 只用于模型 Observation 和澄清恢复，没有独立 Tool Call 表。
- 产品事件使用 `tool.called` 和 `tool.completed`，普通工具失败仍使用 `tool.completed`。
- Tool Event 不包含 `tool_call_id`。
- Tool block ID 当前由 Step 和工具名组成。
- 每个 Tool 调用有 Trace span，但属性没有 `tool_call_id`。
- EventPublisher 当前发布一次事件就提交一次 Session。

## 6. 阶段 0 架构债务基线

阶段 0 显式登记以下现有依赖：

1. `apps/tool/messages.py -> apps.tool.output` 是函数内导入，被全局依赖基线识别。
2. `apps/tool/messages.py -> langchain_core.messages` 是 Tool 核心中的 LangChain 依赖。

新增架构守卫后：

- `apps/tool` 不能新增 ChatBI、Event 或 Trace 依赖；
- Datasource、Semantic 和 Knowledge 服务不能反向依赖具体 Tool；
- `apps/tool/tools` 中的公共 Tool 不能依赖 ChatBI；
- ChatBI Tool 不能依赖生命周期、执行器、Repository、Event 和 Trace；
- Tool 核心中的 LangChain 依赖只能减少，不能增加。

## 7. 阶段 0 回归基线

阶段 0 保护以下现有行为：

- Registry 拒绝未注册 Tool 和非法参数；
- Tool schema 可以传给模型；
- 并发批次保持 Observation 原始顺序；
- 同一模型响应包含多个 Tool Call 时，调用事件和 Observation 保持模型顺序；
- SQL 执行再次校验权限改写后的 SQL；
- 权限拒绝时不执行数据库调用；
- SQL 完整结果保存到 Artifact，状态保留 Artifact 引用；
- 澄清挂起和恢复保持 Tool Call ID；
- Event sequence 连续且 Run 只有一个终态；
- Trace 关闭或导出失败不改变产品事件和最终结果。

阶段 0 不把以下问题固化为正确行为：

- 空 `allowed_tables` 跳过表范围检查；
- 权限提供者缺失时默认允许；
- 未知异常转换成普通 Tool 失败；
- 多个 Tool Call 覆盖同一个 Step；
- 失败 Tool 使用 `tool.completed`；
- 线程池超时被解释为底层调用已取消。

这些问题只记录现状，不新增断言保护，后续阶段必须按目标设计修改。

## 8. 初始测试结果

阶段 0 第一次运行以下范围：

```text
tests/tool
tests/agent
tests/chatbi/test_query_service.py
tests/event
tests/architecture
```

结果：207 个测试通过，1 个架构基线测试失败。失败原因是现有 `apps/tool/messages.py -> apps.tool.output` 函数内导入没有登记在 `known_dependency_violations.json`。阶段 0 将其加入显式基线，后续删除该函数内导入时测试会要求同步移除基线。

完成阶段 0 修改后重新验证：

- 定向范围：214 个测试通过，111 个既有告警；
- 新增架构守卫和多 Tool Call 回归：41 个测试通过；
- 后端全量：1231 个测试通过，136 个既有告警；
- Ruff：阶段 0 修改的 Python 文件检查通过。

告警主要来自 Pydantic、SQLModel、Passlib 和 SQLAlchemy 的既有弃用提示，本阶段不修改相关生产代码。

## 9. 当前状态指引

阶段 0 之后已经完成 Tool Runtime、Datasource 安全查询入口、Tool Call 持久化、结果投影、超时取消和 Semantic Tool 公共化。当前状态与本基线的主要差异如下：

| 基线问题 | 当前状态 |
| --- | --- |
| Tool 直接读取和修改 ChatBI state | 公共 Tool 读取稳定可信上下文，ChatBI 结果处理器统一投影状态 |
| `search_semantic_assets`、`compile_semantic_sql` 属于 ChatBI | 两者已迁入公共 Semantic Tool，ChatBI 直接注册公共实现 |
| ChatBI 拥有语义检索和编译转发 Service、重复 DTO | 转发层和重复 DTO 已删除 |
| 资产来源来自候选集合或可变 state | 编译白名单统一来自 Retrieval 决策的 `allowed_asset_ids` |
| Graph 通过 ChatBI 语义转发服务调用 | Graph Adapter 直接调用公共 Retrieval 和 Semantic SQL 编译 Service |
| Tool Call、Event 和 Trace 标识不统一 | 使用独立 Tool Call 记录和同一 `tool_call_id` 关联 |

当前公共 Tool、执行交互和项目映射见 `20-tool-runtime-technical-design-and-implementation-guide.md`；详细追加实施记录见 `18-chatbi-agent-tool-architecture-implementation-plan.md` 第 19 章。
