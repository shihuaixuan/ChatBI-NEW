# C1-Agent运行时详细设计

## 1. 范围与运行模型

本文定义 Run、AgentInstance、业务计划、决策、子任务、确认、完成校验与恢复协议。公共标量和错误由 C6 定义，工具由 C3 定义，持久化由 D 定义，模型任务由 C5 定义，前端协议由 C4 定义。

系统采用主 Agent、固定类型子智能体和业务计划。主 Agent 负责 MainPlan 和最终回答；子智能体负责 LocalPlan 和局部任务。Planner 是 Agent 的规划阶段，不是独立执行单元。业务计划只表达任务、验收条件和依赖，不包含工具、SQL、执行器或查询算子。执行调度以 AgentDecision 和 ToolCall 为单位。

```text
创建 Run → 准备版本与权限 → 创建 MainPlan → 计算 READY
→ 主 Agent 选择工具或委派子任务 → 保存 Observation
→ 完成步骤或修改计划 → 校验 finish_run → 交付并结束
```

Python 3.11、FastAPI、Pydantic 2、PostgreSQL 和 Redis 构成运行基础。目标链路不使用 LangGraph、Celery 或 Temporal。PostgreSQL 保存权威状态，Redis 仅提供唤醒、通知和限流。

## 2. 模块与所有权

| 模块 | 职责 | 对外接口 |
| --- | --- | --- |
| AgentRuntimeService | Run 创建、交互、取消、重跑 | create_run、submit_interaction、cancel_run、rerun |
| AgentController | 领取决策、调用模型、提交合法决策 | advance_agent |
| PlanManager | 计划版本、步骤状态、依赖与 StepResult | create_plan、apply_patch |
| SubAgentManager | 创建子任务、等待、局部结果回传 | delegate、finish_subtask |
| ToolHarness | 调用校验、派发、重试、正式结果提交 | dispatch、commit_result |
| ConfirmationManager | 确认排队、冻结参数、响应幂等 | request、resolve |
| FinishManager | 用户需求覆盖、证据和回答校验 | finish_run |
| RecoveryManager | 租约回收、唤醒补偿、取消超时 | recover |

工程目录：`apps.chatbi.models.dto.agent_runtime` 保存运行对象；`apps.chatbi.orchestration.agent_runtime` 保存控制器与管理器；`apps.chatbi.services.tool_runtime` 保存 Harness；`apps.chatbi.repository` 保存仓储。跨模块只调用公开 Service 和 DTO，不导入对方 ORM。

一个 Run 有一个 MAIN AgentInstance，最多同时运行三个子 AgentInstance。每个 AgentInstance 只有一个 Plan，Plan 有多个不可变结构版本。PlanStep 在同一 Plan 的不同版本中保留稳定 step_id；步骤状态单独维护。一个 StepResult 对应一个终态步骤。ToolCall 和子任务始终归属发起它的步骤。

## 3. 核心对象

除标注 `?` 的字段外均为必填。`Ref`、`JsonValue`、时间、Decimal 和版本规范见 C6。所有 DTO 禁止额外字段；服务端字段不能由模型或客户端填写。

| 对象 | 字段 |
| --- | --- |
| AgentRun | run_id、conversation_id、parent_run_id?、run_type、status、completion_level?、dataset_id、datasource_id、semantic_view_id、contract_version_id?、permission_snapshot_id?、runtime_snapshot_id?、data_snapshot_id?、reference_at、timezone、scheduling_reason?、wait_parent_run_id?、active_confirmation_id?、last_event_seq、lock_version |
| AgentInstance | agent_id、run_id、role、parent_agent_id?、subtask_id?、phase、plan_id?、context_revision、decision_generation、lease_owner?、lease_expires_at?、next_wake_at?、lock_version |
| Plan | plan_id、run_id、agent_id、active_version、goal |
| PlanVersion | plan_id、version、base_version?、steps: PlanStepDefinition[]、reason、created_at |
| PlanStepDefinition | step_id、description、acceptance_criteria: string[]、depends_on: StepDependency[] |
| StepDependency | step_id、allow_skipped: boolean |
| StepState | step_id、status、started_at?、finished_at?、lock_version |
| StepResult | step_result_id、run_id、agent_id、step_id、status、execution_refs: Ref[]、evidence_ids: string[]、covered_requirement_ids: string[]、completion_summary、error?、skip_reason? |
| AgentDecision | decision_id、run_id、agent_id、phase、context_revision、plan_version?、decision_type、decision_summary、commentary?、function_calls: FunctionCall[]、model_invocation_id |
| FunctionCall | call_id、ordinal、name、arguments |
| SubAgentTask | subtask_id、run_id、parent_agent_id、parent_step_id、agent_id、role、task_name、instruction、input_step_result_ids、status、result_id? |
| SubAgentTaskResult | subtask_result_id、subtask_id、status、completion_level、step_result_ids、result_refs、evidence_ids、limitations |

`role = MAIN | QUERY_AGENT | ANALYSIS_AGENT | REPORT_AGENT`。`run_type = INTERACTIVE | REPORT | MONITOR`。`completion_level = COMPLETE | PARTIAL`，只在 FINISHED 时设置。创建阶段允许快照引用为空；进入 PLANNING 前必须全部完成准备。

## 4. Run 准备与边界

创建事务保存用户消息、CREATED Run、MAIN AgentInstance、初始化触发器和 RUN_CREATED 事件，不创建业务计划，不等待模型或数据源连接。

调度器取得 Run 并发额度后设置 RUNNING，MAIN.phase=PREPARING，依次执行：

1. 固定会话绑定的数据集、唯一数据源与语义视图。
2. 读取当前 ACTIVE 发布合约，记录不可变 contract_version_id。
3. 创建 PermissionSnapshot、RuntimeSnapshot 和状态为 CREATED 的 DataSnapshotContext。
4. 固定 reference_at、timezone；解析追问和当前用户需求，保存 C2 QuestionContext。
5. 生成权限内 Agent Semantic Context；保留歧义候选及能力限制。
6. 将阶段转为 PLANNING，持久化唤醒。

数据库物理快照在首次查询时打开，准备阶段只确定快照策略。所有查询使用同一 DataSnapshotContext。物理快照失效后不能在原 Run 更换数据视图，处理规则见 C2。

Run 固定数据集、数据源、语义版本、权限快照、模型路由、提示词和工具版本。具体指标、维度、时间与筛选在查询工具内绑定。用户明确的范围和不可变筛选记录在 QuestionContext；工具不能通过修改计划删除这些约束。用户明确修改范围时形成带消息来源的 QuestionContext 新修订，既有产物仍绑定原修订；不能将不同口径的结果作为可比较数据。

## 5. 业务计划与版本

### 5.1 创建

```json
{
  "goal": "分析本月各渠道销售额变化",
  "steps": [
    {"step_id":"s1","description":"查询本月与上月各渠道销售额","acceptance_criteria":["两期使用相同渠道口径"],"depends_on":[]},
    {"step_id":"s2","description":"识别主要下降渠道及其变化贡献","acceptance_criteria":["贡献合计与总变化对账"],"depends_on":[{"step_id":"s1","allow_skipped":false}]}
  ]
}
```

`create_plan` 在 PLANNING 阶段调用一次。计划至少一个步骤；step_id 在所属 Plan 内唯一，引用必须存在且无环。计划描述不能包含工具名、参数、SQL、物理字段、执行器或语义资产 ID。标准简单场景由确定性模板提交同一 CreatePlanInput；模板只创建业务步骤，执行仍由 Agent 决策。

### 5.2 PlanPatch

`UpdatePlanInput = {base_version: int, reason: string, operations: PlanOperation[]}`。每次调用只修改当前 Agent 的计划。操作采用 `op` 判别联合：

| op | 参数 | 状态条件 |
| --- | --- | --- |
| ADD_STEP | definition: PlanStepDefinition | step_id 尚不存在 |
| UPDATE_DESCRIPTION | step_id、description、acceptance_criteria | PENDING / READY |
| UPDATE_DEPENDENCIES | step_id、depends_on | PENDING / READY；RUNNING 仅允许追加已终态依赖，且当前无未结束调用或子任务 |
| SKIP_STEP | step_id、reason | 无活动调用、子任务和确认；步骤尚未终态 |
| COMPLETE_STEP | step_id、summary、evidence_ids、covered_requirement_ids | 所有调用、子任务和确认已结束，验收通过 |

结构变更创建新 PlanVersion；完成和跳过也通过同一版本校验事务提交，版本随之递增。旧版本只保留结构历史，步骤状态以 StepState 为准。所有操作先整体校验，再原子提交。终态步骤不可修改、重开或删除。调整已完成工作必须新增步骤。

### 5.3 就绪和完成

- 无依赖或全部依赖 COMPLETED 的 PENDING 步骤转 READY。
- `allow_skipped=true` 的 SKIPPED 依赖允许继续，其限制必须传递到下游结果。
- FAILED / CANCELLED 依赖阻止执行，由 Agent 删除尚未执行步骤的依赖、跳过步骤或新增替代步骤；不能修改失败结果。
- 同一 Agent 的模型决策串行；不同 READY 步骤的工具可并行执行。
- COMPLETE_STEP 校验输出引用、验收条件及用户需求覆盖。技术调用成功不等于业务步骤完成。
- 步骤进入终态时创建唯一不可变 StepResult；包含完整执行引用、错误和限制。完整 ResultSet 保存在 ArtifactStore，不以内联行数组复制到 StepResult。

## 6. 决策与 Function Calling

### 6.1 决策分类

`decision_type = CREATE_PLAN | UPDATE_PLAN | EXECUTE | WAIT | REQUEST_CONFIRMATION | FINISH`，由归一化 Function Calling 确定，模型正文不参与状态解释。

- CREATE_PLAN、UPDATE_PLAN、WAIT、REQUEST_CONFIRMATION、FINISH 各只包含一个对应控制函数。
- EXECUTE 可包含多个业务工具调用及 delegate_task，每个调用必须携带所属 step_id。
- 同批调用只能引用该决策开始前已存在的对象，不接受同批前序调用输出占位符。
- 计划控制与业务调用混合时拒绝整个决策，不执行其中合法部分。
- `decision_id` 是批次标识，不创建第二种批次对象。
- 决策摘要与 commentary 由模型适配层统一提取；commentary 是简短进度描述，不能包含未经验证的数字结论。

### 6.2 控制函数

| 函数 | 输入 | 约束 |
| --- | --- | --- |
| create_plan | CreatePlanInput | 当前 Agent 无计划 |
| update_plan | UpdatePlanInput | 操作当前计划 |
| delegate_task | step_id、role、task_name、instruction、input_step_result_ids | 仅 MAIN；角色为三种子类型；步骤 READY/RUNNING |
| wait_for_events | step_id?、tool_call_ids、subtask_ids、policy、required_ids、reason | 引用当前 Agent 未结束工作；禁止空等待集合 |
| request_confirmation | step_id、confirmation_type、question、options、resume_strategy、frozen_tool_call_id? | 服务端验证真实触发条件 |
| finish_subtask | completion_level、summary、step_result_ids、result_refs、evidence_ids、limitations | 仅子 Agent，LocalPlan 已收口 |
| finish_run | answer、result_ids、evidence_ids、chart_ids、report_id?、completion_level、unmet_requirement_ids、limitations | 仅 MAIN |

字符串长度、数组长度和角色白名单由运行快照中的 Schema 固定。CreatePlanInput、UpdatePlanInput 按本章对象定义生成 JSON Schema；C5 不维护副本。

### 6.3 阶段可见函数

| phase | 可见函数 |
| --- | --- |
| PREPARING / IDLE | 不调用模型 |
| PLANNING | create_plan |
| EXECUTING | 角色允许的业务工具、delegate_task（仅 MAIN）、update_plan、wait_for_events、request_confirmation、finish_run / finish_subtask |
| REPLANNING | update_plan、request_confirmation |
| FINISHING | 系统执行校验与持久化，不发起新的模型调用 |

工具可见集合为注册版本、角色、阶段、权限、发布能力、结果可见范围与资源预算的交集。模型拒绝或未知函数产生明确错误 Observation；不从正文恢复工具调用。

## 7. 子智能体与等待

| 角色 | 职责 | 禁止事项 |
| --- | --- | --- |
| QUERY_AGENT | 语义检索、消歧、查询组织、结果完整性判断 | 直接生成 SQL、修改主计划 |
| ANALYSIS_AGENT | 查询分析输入、对比、异常、归因与自动下钻 | 任意算法、任意阈值、再次委派 |
| REPORT_AGENT | 整理当前 Run 已完成结果、图表和报告 | 查询数据源、再次委派、引用其他 Run 为证据 |

每个子任务创建独立 AgentInstance 和 LocalPlan。委派输入只能引用父步骤直接依赖的 StepResult；对子 Agent 的这些输入形成只读 initial_input_refs，无须伪造局部步骤。子任务新增信息通过正式 SubAgentTaskResult 回到父步骤 Observation，主 Agent 不自动读取其未交付内部结果。

等待策略属于 WaitSubscription，不属于单个子任务：

- ALL：目标全部终态时唤醒。
- ANY：任一目标首次终态时唤醒，其余任务继续运行。
- REQUIRED：required_ids 非空且为目标子集；必需目标全部终态时唤醒。

终态包含成功、失败和取消，避免失败造成永久等待。唤醒不等于完成父步骤；父步骤收口前全部子任务和调用必须终态。需要提前结束剩余任务时由程序传播取消并等待取消完成。确认、权限撤销和硬错误不受等待策略阻挡，立即进入对应控制流程。

## 8. 状态机

### 8.1 RunStatus

唯一枚举：`CREATED | RUNNING | WAITING_CONFIRMATION | FINISHING | FINISHED | FAILED | CANCELLING | CANCELLED`。

| 当前状态 | 事件 | 下一状态 |
| --- | --- | --- |
| CREATED | 取得并发额度 | RUNNING |
| CREATED | 准备条件无效 | FAILED |
| RUNNING | 有待用户确认 | WAITING_CONFIRMATION |
| WAITING_CONFIRMATION | 所有阻塞确认已处理 | RUNNING |
| RUNNING | 接受 finish_run 候选 | FINISHING |
| FINISHING | 回答与证据校验通过 | FINISHED |
| FINISHING | 可修正校验失败且预算允许 | RUNNING |
| RUNNING / WAITING_CONFIRMATION / FINISHING | 不可恢复错误、权限撤销或快照失效 | FAILED |
| CREATED / RUNNING / WAITING_CONFIRMATION / FINISHING | 取消命令 | CANCELLING |
| CANCELLING | 工作全部终态或超过 300 秒 | CANCELLED |

FINISHED、FAILED、CANCELLED 为不可逆终态。非支持问题使用 FAILED + 明确错误码，不新增 RunStatus。部分完成使用 FINISHED + PARTIAL。CREATED 中等待额度或父 Run 的原因保存在 scheduling_reason，不新增业务状态。

### 8.2 AgentPhase、步骤和子任务

`AgentPhase = PREPARING | PLANNING | EXECUTING | REPLANNING | FINISHING | IDLE`，保存在每个 AgentInstance 上。

`PlanStepStatus = PENDING | READY | RUNNING | WAITING_SUBTASK | WAITING_CONFIRMATION | COMPLETED | FAILED | CANCELLED | SKIPPED`。READY 首次接受业务调用后转 RUNNING；等待子任务和确认时进入对应状态；解除阻塞后转 RUNNING；终态不再转换。

`SubAgentTaskStatus = CREATED | RUNNING | WAITING_TOOL | WAITING_CONFIRMATION | COMPLETED | FAILED | CANCELLED`。程序根据子 Agent 活动设置等待状态；正常 finish_subtask 进入 COMPLETED，completion_level 区分完整和部分。不可恢复错误由程序设为 FAILED，取消由程序设为 CANCELLED。

`ToolCallStatus = PENDING | RUNNING | WAITING_CONFIRMATION | SUCCEEDED | FAILED | CANCELLED`，重试时间与 Attempt 状态单独保存。

## 9. 上下文与结果可见性

每次模型调用从权威存储重建：系统规则、角色、阶段、QuestionContext、当前语义上下文、全部计划结构和步骤状态、各目标步骤直接依赖的 StepResult、当前步骤全部 Observation、子 Agent 初始授权输入、确认响应及剩余预算。

EXECUTE 涉及多个步骤时，上下文按 step_id 分区；参数引用仍逐调用校验所属步骤的可见集合，不能借批次共享上下文越过依赖。PLANNING / REPLANNING 可读取计划结果目录；FINISH 可读取当前 Agent 全部已完成步骤正式交付的结果。

StepResult 和 Observation 中保存有类型的结果引用及完整控制信息，不内联大表。所有已保存引用、错误和限制完整进入上下文；`read_result` 读取授权结果的有界完整投影，不能分页或任意抽样。输出字节数、列宽和 Token 预算在调用前校验。结果过大时返回 RESULT_CONTEXT_LIMIT_EXCEEDED，Agent 使用确定性聚合工具形成新的小结果，不截断既有证据。

模型输入与完整存储分离：完整数据始终保留，模型获得的正式引用和计算结果不被静默替换。具体预算见 C5。

## 10. 确认与消息路由

`ConfirmationRequest = {confirmation_id, run_id, agent_id, step_id, type, question, options, resume_strategy, frozen_tool_call_id?, argument_fingerprint?, status, created_at}`。

类型：SEMANTIC_AMBIGUITY、RESOURCE_SOFT_LIMIT、DRILLDOWN_DEPTH。权限扩大不通过确认授权；权限不足直接拒绝，权限变更后创建新 Run。首期不提供报告外部分发接口。

- RESUME_TOOL_CALL：冻结调用参数，接受响应后校验参数指纹、权限、版本、快照和预算再执行；不重新让模型生成参数。
- RESUME_AGENT：保存用户选择形成 QuestionContext 修订，再由所属 Agent 继续决策。
- 多个确认按 `(created_at, confirmation_id)` 排队，只展示一个 ACTIVE 确认。其余为 PENDING；解决后依次激活。
- Run 有待确认时停止新决策与新调用派发，已派发只读调用可完成并保存。等待期间释放 Worker 和并发 Run 额度。
- 等待五分钟不改变 RunStatus；若数据库物理快照达到寿命上限，按 C2 标记快照失效并结束 Run。
- 用户拒绝仅拒绝对应操作，形成 Observation，由 Agent 跳过、调整计划或部分完成。
- 原 Run 的确认和取消作用于原 Run；明确修改当前任务通过交互命令形成计划修订；独立新问题创建新 Run。

## 11. 完成校验与 Evidence

Evidence 类型：QUERY_FACT、CALCULATION、ANOMALY、CONTRIBUTION、SYNTHESIS、SEMANTIC_FACT。工具生成前五类中的数据事实及语义事实；`evidence_create` 仅从已验证来源生成 SYNTHESIS，不生成新数值。

`FinishRunInput.answer = {sections: AnswerSection[]}`。AnswerSection 包含 title、content_template、numeric_bindings、evidence_ids；数字通过证据路径绑定，由 C4 格式化替换，模型不提交任意数字作为事实。

FinishManager 按顺序校验：

1. 当前计划所有步骤终态，所有 ToolCall、子任务和确认已结束。
2. 每个用户 requirement_id 被有效 StepResult 覆盖，或列入 unmet_requirement_ids。
3. 引用属于当前 Run、当前授权范围且正式可用；历史 Run 只能作为理解输入。
4. 查询语义版本一致；需要比较的结果口径和数据快照一致。
5. Evidence 来源、数字路径、图表和报告引用校验通过。
6. COMPLETE 没有未满足需求；PARTIAL 至少有一个有效 Evidence，明确缺失需求和影响。

SEMANTIC_FACT 支持口径解释类回答；NON_ANALYTICAL 在准备阶段以 FAILED + NON_ANALYTICAL_REQUEST 结束并返回固定能力说明，不创建业务计划或调用数据源。无有效证据的分析 Run 以 FAILED 结束。可修正错误形成 Observation；超过修正预算后 FAILED，不绕过校验输出。

## 12. 调度、租约与幂等

### 12.1 决策执行

决策采用两个短事务，中间模型调用不持有数据库锁：

```python
async def advance_agent(agent_id: str, trigger_id: str) -> None:
    # 第一事务领取租约并保存本轮上下文版本。
    ticket = decision_store.claim(agent_id, trigger_id)
    context = context_builder.build(ticket)
    output = await model_gateway.invoke(context)
    # 第二事务校验代次、上下文和状态；过期响应只能审计。
    decision_store.validate_and_commit(ticket, output)
```

领取时递增 decision_generation，保存 context_revision、plan_version 和输入指纹。提交时必须匹配 generation、租约所有者、未过期租约、context_revision 和可执行 Run 状态。上下文变化使旧模型响应失效并重新调度，不将旧调用部分应用。

### 12.2 工具执行

ToolCall 的业务幂等键包含 run_id、agent_id、step_id、tool_name/version、规范化参数、语义/权限/快照引用和 QuestionContext 修订。ToolAttempt 每次领取递增 generation；心跳只延长匹配代次的租约。

正式提交事务校验 Run 未取消或失败、调用未终态、Attempt 代次和租约有效，再同时写结果元数据、Evidence、Observation、调用状态、事件、审计及唤醒。超时旧 Worker 的结果即使新 Attempt 尚未提交，也不能成为正式结果。

技术重试只重试明确可重试的连接错误、429 和短暂服务故障。Schema、权限、语义、算法定义域和资源错误不进行技术重试。只读查询可能实际执行多次，但正式结果最多提交一次；不声称外部执行恰好一次。

### 12.3 唤醒可靠性

触发器写入 PostgreSQL，与产生触发器的状态变更同事务。Redis 发布失败不撤销已提交事务；调度器定期扫描未消费触发器。去重键为 `(agent_id, cause_type, cause_id)`。普通工具进度和 commentary 不触发模型。

## 13. 取消、恢复与追问

取消事务先将 Run 转 CANCELLING、增加全部 Agent 的 context_revision、失效待执行决策并写审计，再传播到数据源和子任务。300 秒内等待外部调用结束；到期强制设 CANCELLED，迟到结果只进入诊断记录。已提交结果保留，按当前权限读取。

恢复扫描读取 PostgreSQL：回收过期 Agent 和 ToolAttempt 租约、重派可重试调用、补建缺失唤醒、重算 READY、补齐子任务结果 Observation、恢复确认队列并关闭超时取消。进程重启不能绕过快照存活校验。

每个独立追问创建新 Run，固定最新语义版本、新权限与新数据快照。父结果仅用于识别实体、筛选和用户意图，新结论必须重新查询。依赖未完成父结果时，新 Run 保持 CREATED 并记录 wait_parent_run_id；父 Run 终态后解析其有效结果，无可用结果则 FAILED + PARENT_RESULT_UNAVAILABLE。

## 14. 配置与验收

| 配置 | 默认值 | 约束 |
| --- | --- | --- |
| max_concurrent_runs | 100 | 包含运行和收尾，不包含 CREATED 与待确认 |
| max_user_runs | 5 | 超额 Run 留在 CREATED |
| decision_lease_seconds / heartbeat_seconds | 120 / 30 | 心跳小于租约三分之一 |
| tool_lease_seconds / heartbeat_seconds | 300 / 30 | 数据源查询时间由独立技术配置控制 |
| max_parallel_subagents | 3 | 子 Agent 不再委派 |
| max_parallel_calls_per_decision | 4 | 实际并发受数据源配额限制 |
| max_plan_steps_soft / hard | 12 / 20 | 每个 Plan 单独计数 |
| max_agent_decisions_soft / hard | 20 / 40 | Run 内主、子决策合计 |
| finish_validation_reserve | 2 | 硬预算内预留两次完成修正决策，不派发新查询 |
| max_plan_patch_count | 10 | Run 内合计 |
| cancellation_grace_seconds | 300 | 到期强制终止 |

软限制只允许确认已定义的增量，不能突破硬上限。预算由事务预留，多个子任务不能分别消耗同一余额。超限后仅允许收尾，无法生成有效结果则 FAILED。

验收覆盖：计划无环和版本冲突；直接依赖可见性；同批依赖拒绝；三类子任务及禁止嵌套委派；ALL/ANY/REQUIRED 的失败唤醒；重复确认；终态不可变；模型调用期间取消；租约过期旧响应隔离；Redis 中断恢复；完整和部分完成校验。
