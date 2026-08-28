# 42. Evidence 驱动 ReAct Research Agent 执行计划

# 0. 文档目标

本文是 `41-evidence-driven-governed-react-agent-architecture.md` 的实施计划，负责将当前 `PlanAndSolveRuntime` 迁移为 Evidence 驱动的受控 ReAct Runtime。

计划拆分到契约、提示词、工具、Runtime、持久化、恢复、回答、评测和发布。所有任务直接修改当前实现，新 ReAct 流程在同一个改造分支中替换现有 Plan-and-Solve 流程。阶段表示开发顺序，阶段 10 验收完成前不发布中间状态。

# 1. 实施范围

## 1.1 目标调用链

~~~text
ResearchAgentInput
  -> Runtime 投影 ResearchState
  -> 模型生成 ResearchTurnDecision
  -> Runtime 应用 FindingChange 和 TodoChange
  -> Runtime 校验并执行 ResearchAction
  -> 工具返回 ToolResult<T>
  -> 查询或计算结果产生 Evidence
  -> Runtime 更新状态并进入下一轮
  -> finish_research 接受 Completion
  -> Responder 生成回答
~~~

## 1.2 当前改造入口

| 当前职责 | 主要位置 |
| --- | --- |
| Research DTO | `backend/apps/chatbi/models/dto/research_agent.py` |
| Plan-and-Solve Runtime | `backend/apps/chatbi/orchestration/pipeline/plan_and_solve_runtime.py` |
| Pipeline 适配 | `backend/apps/chatbi/orchestration/pipeline/plan_and_solve_pipeline.py` |
| Research 工具 | `backend/apps/chatbi/orchestration/agent/tools/research.py` |
| Reasoner 和 Profile | `backend/apps/chatbi/orchestration/agent/reasoning.py`、`reasoning_profile.py` |
| 模型上下文投影 | `backend/apps/chatbi/services/research/agent_context.py` |
| ToolContext 和计划状态 | `backend/apps/chatbi/services/research/tool_context.py` |
| 状态快照 | `backend/apps/chatbi/services/research/state_snapshot.py` |
| 生命周期和恢复 | `backend/apps/chatbi/services/research/run_lifecycle.py` |
| Semantic Query Engine | `backend/apps/chatbi/services/research/semantic_runtime.py` |
| Compute Engine | `backend/apps/chatbi/services/computation/engine.py` |
| 完整结果存储 | `backend/apps/chatbi/services/execution/result_store.py` |

## 1.3 改造原则

1. 直接修改当前 DTO、工具、Runtime、状态、快照和 Pipeline；
2. `ResearchTurnDecision` 直接替换 `submit_research_plan` 和 `ResearchPlanNode`；
3. ReAct Loop 直接替换 Planner/Solve 两阶段循环；
4. `ResearchState` 直接替换 `plan_execution_state`；
5. ToolResult 和 Evidence 直接替换当前 Observation 和 Evidence 登记流程；
6. Semantic Query Engine 和 Compute Engine 保持确定性执行；
7. 同一次改造完成全部调用方更新，不保留两套 Research Runtime；
8. 发布前处理仍在运行的旧 Research Run，发布后的恢复只识别新状态结构。

# 2. 阶段总览

| 阶段 | 名称 | 结果 |
| --- | --- | --- |
| 0 | 基线与变更边界 | 建立当前行为、评测和运行版本基线 |
| 1 | 新 DTO 和状态契约 | 新架构全部实体具备版本化 Schema |
| 2 | Agent 输入与提示词 | 模型能够读取新上下文并输出 ResearchTurnDecision |
| 3 | 统一工具执行协议 | 所有新工具统一返回 ToolResult<T> |
| 4 | 查询和计算工具 | query、compute、read 能独立工作并产生 Evidence |
| 5 | 语义补充、澄清和结束工具 | search、clarification、finish 完整可用 |
| 6 | ResearchState 状态变更 | Finding、Todo、预算和尝试历史可持久化更新 |
| 7 | ReAct Runtime | 当前 Runtime 被改造成完整多轮 ReAct 循环 |
| 8 | 快照、恢复和幂等 | 中断、澄清和进程重启后可以恢复 |
| 9 | Responder 与最终回答 | 回答只消费已接受 Completion 和 Evidence |
| 10 | 全量回归和评测 | 证明替换后的主路径满足正确性、成本和停止要求 |
| 11 | 发布 | 处理发布前运行任务，部署替换后的主路径并验证状态 |

依赖顺序：

~~~text
0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11
~~~

# 3. 阶段 0：基线与变更边界

## 3.1 任务

### P0-01 冻结改造范围

- 确认本次直接替换 `ResearchPlanNode`、`submit_research_plan`、Planner/Solve Loop 和 `plan_execution_state`；
- 冻结改造期间的其他 Research 架构变更；
- 确认发布窗口内旧 Research Run 的停止和清理方式。

### P0-02 建立代码引用清单

- 列出 `submit_research_plan` 的注册、提示词、Runtime 校验、持久化和测试引用；
- 列出 `ResearchPlanNode`、`SemanticAssessment`、Gap 和 Hypothesis 的引用；
- 列出 `plan_execution_state` 的读写、快照、恢复和 API 投影位置；
- 将引用按“继续复用、直接修改、本次删除”分类。

### P0-03 固定评测样本

评测集至少覆盖：

- 单指标查询；
- 两期指标对比；
- 指标驱动分析；
- 商家和档口连续下钻；
- 派生指标；
- 高基数维度 Top N；
- Evidence 后续计算；
- 语义资产不足；
- 关键歧义澄清；
- 查询失败后换参；
- 部分回答和无法回答；
- 预算耗尽；
- 取消和恢复。

### P0-04 记录改造前基线

每个问题记录：最终状态、查询次数、模型轮次、执行时间、查询成本、结论、Evidence 引用、失败代码和是否发生重复查询。

## 3.2 产物

- 旧协议引用清单；
- 固定评测集和期望结果；
- 当前主路径基线报告；
- 发布窗口和发布前运行任务处理方案。

## 3.3 验收

- 固定评测集可以重复执行；
- 相同版本在稳定数据上得到可比较结果；
- 每个旧计划协议引用都有迁移阶段归属。

# 4. 阶段 1：新 DTO 和状态契约

## 4.1 文件安排

直接修改 `backend/apps/chatbi/models/dto/research_agent.py`。删除顶层计划、Gap 和 Hypothesis 控制契约，加入 ResearchAgentInput、ToolResult、Evidence、ResearchState 和 ResearchTurnDecision。

## 4.2 任务

### P1-01 定义输入契约

实现：

- `ResearchAgentInput`；
- `ConversationMessage`；
- `SemanticContext` 及指标、维度、层级、公式、分析关系和歧义对象；
- `RemainingBudget`。

校验：正式语义引用格式、数组去重、快照标识和版本字段。

### P1-02 定义结果契约

实现泛型 `ToolResult[T]`、`ExecutionError`、`Evidence`、`EvidenceDefinition`、`EvidenceColumn`、`EvidenceData`、`EvidenceRows` 和限制对象。

约束：

- `status=succeeded` 时必须存在 `result`；
- `status=failed` 时必须存在 `error`；
- `status=waiting_for_user` 只允许澄清工具；
- `evidence_id` 根据 `tool_call_id` 确定性生成；
- 计算 Evidence 必须包含 `parent_evidence_ids`。

### P1-03 定义研究状态契约

实现 `Finding`、`FindingScope`、`TodoItem`、`AttemptSummary`、`BudgetUsage`、`Completion` 和 `ResearchState`。

### P1-04 定义模型输出契约

实现 `ResearchTurnDecision`、`FindingChange`、`TodoChange` 和 `ResearchAction` 判别联合类型。

校验：

- `FindingChange.add` 必须携带新 Finding；
- `FindingChange.supersede` 必须携带已有 finding_id；
- `TodoChange` 的字段组合与 change_type 一致；
- finish 动作不能和查询、计算或澄清动作并行；
- 并行动作只能是相互独立的只读动作；
- 每个动作的 arguments 使用对应工具 DTO。

### P1-05 契约版本

- 为 ResearchAgentInput、ResearchState 和快照增加 `schema_version`；
- 新版本初始值使用独立常量；
- 解析未知版本时返回明确错误；
- 不将旧 plan_execution_state 自动转换成新 ResearchState。

## 4.3 测试

新增 `backend/tests/chatbi/test_research_react_contracts.py`，覆盖每个合法示例、字段缺失、联合类型错误、重复引用、非法状态转换和物理 SQL 载荷拒绝。

## 4.4 验收

- 架构文档中的全部示例能够通过 DTO 校验；
- 非法字段组合返回稳定错误码；
- `research_agent.py` 中不再保留 ResearchPlanNode、顶层 Gap 和 Hypothesis 控制契约。

# 5. 阶段 2：Agent 输入与提示词

## 5.1 任务

### P2-01 构建 ResearchAgentInput

在路由冻结结果上新增新输入构造器：

- 读取用户问题和必要对话消息；
- 将权限过滤后的语义检索结果投影为 SemanticContext；
- 对派生指标补齐组成指标和公式；
- 对层级和分析关系只保留正式引用；
- 生成不可修改快照并持久化引用。

### P2-02 YAML 序列化

- 为 SemanticContext 实现稳定字段顺序的 YAML 序列化；
- 限制描述长度、资产数量和关系数量；
- 同一资产只出现一次；
- 快照 DTO 保存结构化对象，只有模型输入使用 YAML。

### P2-03 Working State 投影

调整 `agent_context.py`，新增 ReAct 投影函数，输出 ResearchAgentInput、Evidence、Finding、Todo、AttemptSummary 和 RemainingBudget。

投影规则包括：

- 优先当前有效 Finding 和未完成 Todo；
- Evidence 保留定义、列、限制和当前判断需要的结果行；
- 完整 Tool Call 不直接进入模型上下文；
- 重复失败按动作指纹合并摘要；
- 保留最近一次可修正错误；
- 预算使用剩余额度表达。

### P2-04 System Prompt

- 将第 41 号文档的 System Prompt 写入 Research Profile；
- Prompt 只描述判断规则，工具字段由 JSON Schema 提供；
- 为语义资产不足、Evidence 截断、错误重试和三种结束状态增加少量示例；
- Prompt 版本进入 Trace 和运行快照。

### P2-05 模型输出解析

- Reasoner 使用 `ResearchTurnDecision` 作为结构化输出；
- 当前可见工具决定 `ResearchAction.arguments` 的联合 Schema；
- 解析失败保存原始响应摘要和稳定错误码；
- 允许一次格式修正重试，重试计入模型预算。

## 5.2 测试

- SemanticContext YAML 快照测试；
- Token 裁剪测试；
- 提示词注入边界测试；
- ResearchTurnDecision 结构化输出测试；
- 不同可见工具集合的 Schema 测试。

## 5.3 验收

- 模型输入不包含 plan_execution_state；
- 模型能够对固定上下文生成合法 ResearchTurnDecision；
- 相同状态投影结果稳定；
- Prompt 和 Tool Schema 都有版本记录。

# 6. 阶段 3：统一工具执行协议

## 6.1 任务

### P3-01 新工具接口

为新工具提供统一接口：`args_model`、`result_model`、`prepare()` 和 `execute()`。

`prepare()` 返回标准化参数、动作指纹、成本估算和执行所需领域对象。`execute()` 只返回结果负载，Runtime 构造 ToolResult 外层。

### P3-02 Runtime 通用校验

实现工具存在性、可见性、Run 状态、取消、基础预算、并发和 ToolResult 外层校验。

### P3-03 动作指纹

分别定义查询、计算、读取、语义检索、澄清和结束动作的标准化指纹。指纹忽略 purpose 文本，包含影响执行结果的参数、语义版本和权限版本。

### P3-04 错误映射

统一映射 parsing、validation、permission、planning、compilation、execution、persistence、budget 和 completion 错误。

每个错误说明是否允许原参数重试、修改参数重试或停止重试。

### P3-05 Tool Call 持久化适配

复用现有 Agent Run、Step 和 Tool Call 记录，将工具输出持久化内容改为 ToolResult payload 和 schema_version。代码中的 Observation 领域对象和转换流程同步删除。

## 6.2 测试与验收

- 每个失败阶段生成合法失败 ToolResult；
- 相同标准化动作生成相同指纹；
- Runtime 和工具不会重复实现同一校验；
- ToolResult 可以从持久化记录完整恢复。

# 7. 阶段 4：查询、计算和结果读取工具

## 7.1 query_semantic_data

### P4-01 参数 DTO

实现带 `operation` 判别字段的高层查询动作契约：`metric_snapshot`、`multi_period_snapshot`、`compare_metrics`、`breakdown`、`drilldown`、`contribution` 和 `validate_drivers`。模型只提交业务资产、筛选、排序和 Limit；服务端从冻结 Requirement 补全时间、Scope、版本、不可变筛选和比较参数。

### P4-02 Semantic Query Engine 接口收敛

将 `semantic_runtime.py` 的入口整理为 prepare 和 execute 两阶段。prepare 完成语义证明、逻辑 DAG、成本估算和权限校验；execute 编译并执行只读 SQL。

### P4-03 Evidence 构建

- 使用 tool_call_id 生成 evidence_id；
- 将标准化查询写入 EvidenceDefinition；
- 将逻辑列转换为 EvidenceColumn；
- 根据上下文预算返回摘要和受控行；
- 将完整结果保存在引擎内部；
- 建立 evidence_id 到内部结果的映射。

### P4-04 内部 DAG 回归

覆盖总体查询、两期对比、派生指标、下钻、贡献度、Top N、EvidenceSelector 和多模型关系。

### P4-04A 显式状态动作

删除模型可见的状态更新工具和助手正文 sidecar。`finish_research` 只接收结论文本与 Evidence 引用，Runtime 根据 Evidence 生成 Finding ID、Scope、Todo 变化和状态事件。

## 7.2 compute_evidence

### P4-05 白名单计算

逐项接入 difference、growth_rate、ratio、share、contribution、ranking、topn_other、merge 和 reconciliation。

### P4-06 计算契约校验

校验 Evidence 所有权、列存在性、单位、粒度、合并键、容差、排序和 Limit。

### P4-07 计算 Evidence

计算结果写入内部存储，EvidenceDefinition.computation 保存标准化操作和输入引用，parent_evidence_ids 保存依赖关系。

## 7.3 read_evidence_rows

### P4-08 结果读取

根据 evidence_id 定位查询或计算内部结果，校验列引用、排序、offset、limit 和当前 Run 所有权。

### P4-09 分页稳定性

排序字段必须形成稳定顺序；相同 evidence_id、排序和分页参数返回相同结果。

## 7.4 测试与验收

- 三个工具分别具备单元测试、集成测试和权限测试；
- 查询与计算结果可以通过 read_evidence_rows 读取；
- Evidence 不包含内部结果存储引用；
- 新工具不依赖 ResearchPlanNode；
- 查询和计算错误不会被静默转换为空结果。

# 8. 阶段 5：语义补充、澄清和结束工具

## 8.1 search_semantic_assets

### P5-01 补充检索

使用当前问题、检索词、关联资产和权限快照执行补充检索，补齐派生指标依赖，并限制结果规模。

### P5-02 新输入快照

合并资产时按正式引用去重，生成新的 ResearchAgentInput 快照，更新 agent_input_ref，并保存 SemanticContextDelta。

## 8.2 request_clarification

### P5-03 澄清请求

保存 clarification_request_id、问题、选项和语义引用，将 Run 更新为 waiting_for_user。

### P5-04 用户回答恢复

校验回答属于当前请求，保存 ClarificationResponse，生成新输入快照并恢复 running 状态。

## 8.3 finish_research

### P5-05 结束校验

校验 Finding、Evidence、Scope、限制、Attempt 引用和未结束 Tool Call。

### P5-06 状态转换

- complete 映射为 completed；
- partial 映射为 partial；
- unanswerable 映射为 unanswerable；
- rejected 保持 running 并返回校验错误。

## 8.4 验收

- 补充语义资产不会扩大权限范围；
- 澄清可以暂停并恢复同一 Run；
- finish_research 只执行确定性校验；
- accepted 和 rejected 都返回合法 ToolResult。

# 9. 阶段 6：ResearchState 和状态变更

## 9.1 任务

### P6-01 ResearchState 存储

在 derived_state 中保存 schema_version、agent_input_ref、evidence_refs、findings、todo_items、attempted_actions、budget_usage、current_status 和 completion。

### P6-02 FindingChange

- add 只能引用已有 Evidence；
- supersede 只能指向 confirmed Finding；
- 新结论通过 supersede 旧 Finding 加 add 新 Finding 表达；
- Finding 变更使用事件顺序持久化。

### P6-03 TodoChange

实现 add、set_status 和 set_order。状态转换规则集中在一个校验入口。

### P6-04 决策事务顺序

一次 ResearchTurnDecision 的处理顺序：

~~~text
解析全部变更和动作
  -> 校验 FindingChange 和 TodoChange
  -> 校验所有动作结构和可见性
  -> 原子保存状态变更
  -> 执行工具动作
  -> 保存 ToolResult、Attempt 和预算变化
~~~

工具执行失败不回滚已经合法提交的 Finding 和 Todo 变更。Runtime 通过 AttemptSummary 表达后续动作失败。

### P6-05 并行动作

并行组只允许无数据依赖的只读动作。Runtime 为每个动作分别分配 tool_call_id、指纹和 ToolResult，并在全部结束后统一进入下一轮。

## 9.2 验收

- 任意状态都能通过事件重建；
- Finding 和 Todo 的非法转换被拒绝；
- 并行动作部分失败时结果和错误分别保存；
- ResearchState 不复制完整 Evidence 对象。

# 10. 阶段 7：ReAct Runtime

## 10.1 文件安排

直接重写 `backend/apps/chatbi/orchestration/pipeline/plan_and_solve_runtime.py` 的主循环，并将类改名为 `ResearchAgentRuntime`。同一任务更新全部导入位置；完成后将文件改名为 `research_agent_runtime.py`。

## 10.2 任务

### P7-01 初始化

支持 Research Run 初始化和 ResearchState 恢复。初始化过程直接创建新状态，不再创建 plan_execution_state。

### P7-02 主循环

实现取消检查、预算检查、上下文投影、模型调用、决策解析、状态变更、工具准备、执行、ToolResult 持久化和下一轮控制。

### P7-03 工具可见性

根据 Evidence 截断、语义资产缺口、澄清状态、剩余预算和当前终态动态提供工具。

### P7-04 停滞检测

检测连续无新 Evidence、重复动作、连续相同错误、无状态变化和模型空动作。

### P7-05 预算收口

预留一次结束判断模型调用。动作预算不足时仅提供 finish_research，使模型提交 partial 或 unanswerable。

### P7-06 终态

处理 completed、partial、unanswerable、failed 和 cancelled。failed 只用于系统错误，业务能力不足使用 partial 或 unanswerable。

## 10.3 测试

重写 `test_research_agent_pipeline.py`、`test_research_agent_harness.py` 和相关 Runtime 测试，覆盖：单轮完成、多轮查询、查询后计算、动态下钻、并行查询、失败换参、语义补充、澄清恢复、finish 拒绝、预算收口、取消和停滞。

## 10.4 验收

- 主循环中不存在 Planner revision 和 READY 节点调度；
- 每个重要 ToolResult 后重新调用模型；
- 简单查询和复杂分析使用同一循环；
- 达到停止条件后不会继续调用数据工具。

# 11. 阶段 8：快照、恢复和幂等

## 11.1 任务

### P8-01 快照格式

更新 state_snapshot.py，保存 Runtime 类型、schema_version、ResearchState、最近事件序号和输入快照引用。

### P8-02 恢复入口替换

- `run_lifecycle.py` 只恢复 ResearchState；
- 删除 plan_execution_state 的恢复分支；
- 旧快照返回明确的状态版本不支持错误；
- 未知版本明确失败。

### P8-03 幂等

使用 run_id、tool_call_id 和动作指纹保证成功动作不重复执行。澄清回答使用 clarification_request_id 去重。

### P8-04 故障注入

在模型返回后、状态保存后、工具执行后、ToolResult 保存后和终态提交前分别模拟进程中断。

## 11.2 验收

- 每个故障点恢复后不重复产生 Evidence；
- waiting_for_user 可以跨进程恢复；
- 新状态快照能够完成恢复；
- 快照缺失关键字段时返回明确错误。

# 12. 阶段 9：Responder 与最终回答

## 12.1 任务

### P9-01 Responder 输入

构建只包含 accepted Completion、confirmed Finding、对应 Evidence 和限制的回答输入。

### P9-02 回答提示词

要求结论引用 Finding，数值来自 Evidence，partial 和 unanswerable 明确说明限制。图表只能使用 Evidence 中存在的列和结果。

### P9-03 回答审计

保存回答使用的 finding_ids、evidence_ids、图表数据来源和最终状态。

## 12.2 验收

- Responder 不调用研究工具；
- 回答中的数值可追溯到 Evidence；
- superseded Finding 不进入回答；
- partial 和 unanswerable 的限制完整显示。

# 13. 阶段 10：全量回归和评测

## 13.1 代码清理检查

在进入评测前完成以下检查：

- 删除 ResearchPlanNode、submit_research_plan、旧 SemanticAssessment、顶层 Gap 和 Hypothesis 控制契约；
- 删除 Planner/Solve、revision、READY 节点和计划批次调度；
- 删除 plan_execution_state 的构建、投影、快照和恢复；
- 删除 submit_research_plan 提示词、allowlist 和单独轮次限制；
- 删除 Observation 到 Evidence 的登记流程；
- 删除只验证旧实现形态的测试。

## 13.2 离线评测

使用阶段 0 固定的相同问题和稳定数据执行改造后主路径，比较改造前基线：问题完成率、数据正确率、Evidence 引用正确率、查询次数、模型轮次、查询成本、执行时间、重复动作率、停滞率、部分回答率、失败率和恢复成功率。

## 13.3 Trace 检查

每次运行必须记录 Prompt 版本、ResearchTurnDecision、可见工具、动作指纹、ToolResult、Evidence 依赖、预算变化和结束状态。

## 13.4 验收门槛

- 关键正确性用例全部通过；
- 不存在权限越界和物理 SQL 注入；
- 重复查询率和失败率不高于改造前基线；
- 平均查询成本和模型轮次处于设定范围；
- 取消、澄清、预算收口和恢复用例全部通过；
- 全量 Research 测试不再引用旧计划协议。

# 14. 阶段 11：发布

## 14.1 发布前处理

### P11-01 停止创建 Research Run

在发布窗口暂时停止新的 Research Run 进入执行，Fast 和其他模式继续按既有策略处理。

### P11-02 处理运行中任务

- 等待短时运行的 Research Run 完成；
- 取消仍在执行或等待用户澄清的旧 Research Run；
- 将取消原因记录为架构升级；
- 确认不存在仍需使用 plan_execution_state 恢复的 Run。

### P11-03 部署直接替换版本

部署修改后的 DTO、工具、ResearchAgentRuntime、Research Pipeline、快照恢复和 Responder。部署产物中只包含替换后的 Research Runtime 入口。

### P11-04 清理旧状态

根据数据保留要求删除或归档旧 plan_execution_state 快照。旧状态被错误提交恢复时返回明确版本错误。

### P11-05 发布验证

依次执行单指标查询、对比分析、连续下钻、计算、澄清、部分回答、取消和恢复冒烟测试。

## 14.2 验收

- 发布后创建的 Research Run 使用 ResearchAgentRuntime；
- Pipeline 不再导入 PlanAndSolveRuntime；
- 数据库和快照中不再写入 plan_execution_state；
- Fast 和其他模式行为不变；
- 发布后 Trace 和指标完整。

# 15. 测试矩阵

| 层级 | 重点 |
| --- | --- |
| DTO 单元测试 | 字段、联合类型、状态转换、非法载荷 |
| Prompt 测试 | 上下文组成、工具选择、结束状态、错误修正 |
| 工具单元测试 | prepare、指纹、成本、执行和错误映射 |
| Engine 集成测试 | 语义 DAG、SQL、计算和完整结果读取 |
| Runtime 测试 | 多轮循环、并行、预算、停滞、取消和终态 |
| 恢复测试 | 每个持久化边界的故障注入和幂等 |
| Responder 测试 | Finding/Evidence 引用和限制展示 |
| 端到端测试 | 路由、执行、回答、Trace 和生命周期 |
| 离线评测 | 与改造前基线比较正确性、成本、时延和稳定性 |

# 16. 每个开发任务的完成标准

每个任务完成时必须同时提交：

1. 代码和中文注释；
2. DTO 或接口变更说明；
3. 正常路径和失败路径测试；
4. 稳定错误码；
5. Trace 或指标字段；
6. 受影响范围和发布注意事项；
7. 对应阶段验收结果。

# 17. 建议实施批次

## 批次 A：基础契约

P0 全部、P1 全部、P2-01 至 P2-03。

## 批次 B：模型和工具框架

P2-04、P2-05、P3 全部。

## 批次 C：数据工具

P4 全部，可按 query、compute、read 三个开发任务并行实施，统一使用阶段 3 的工具协议。

## 批次 D：控制工具和状态

P5 和 P6。先完成状态变更校验，再接 finish_research 的本轮 Finding 引用。

## 批次 E：Runtime 和恢复

P7 和 P8。先通过内存状态测试，再接数据库快照和故障恢复。

## 批次 F：回答、评测和发布

P9、P10 和 P11。

# 18. 第一批具体开发顺序

1. 修改 `research_agent.py`，用目标 DTO 替换顶层计划契约并重写契约测试；
2. 实现 ToolResult、Evidence、ResearchState 和 ResearchTurnDecision；
3. 实现 SemanticContext 投影和 YAML 快照测试；
4. 新增 ReAct Research Profile 和 System Prompt；
5. 让 AgentReasoner 能解析 ResearchTurnDecision；
6. 建立新工具统一接口和错误映射；
7. 先接通 query_semantic_data 的 prepare、execute 和 Evidence；
8. 使用固定模型决策构建最小 Runtime 测试链路；
9. 接入 compute_evidence 和 read_evidence_rows；
10. 再进入语义补充、澄清、结束和完整多轮 Runtime。
