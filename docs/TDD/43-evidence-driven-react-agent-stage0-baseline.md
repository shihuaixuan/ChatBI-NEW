# 43. Evidence 驱动 ReAct Research Agent 阶段 0基线与变更边界

> 状态：阶段 0执行中
>
> 日期：2026-08-27
>
> 依据：[41-evidence-driven-governed-react-agent-architecture.md](41-evidence-driven-governed-react-agent-architecture.md)、[42-evidence-driven-react-agent-implementation-plan.md](42-evidence-driven-react-agent-implementation-plan.md)

## 1. 阶段 0结论

本阶段确认 41/42 描述的是 Research Agent 主流程替换，不是旧 Plan-and-Solve 流程上的局部修补。

当前仓库仍以 `PlanAndSolveRuntime` 为 Research 主入口，旧计划协议、旧 Observation、旧
`plan_execution_state` 以及旧完成度判断仍在运行路径中。阶段 0已经完成以下基础工作：

- 冻结本次迁移的范围和不迁移的共享能力；
- 建立旧协议的代码引用清单及后续处理分类；
- 建立面向 41/42 的固定场景清单，文件为
  `backend/scripts/data/research_agent_react_stage0_eval_cases.json`；
- 保留 2026-08-22 的 11 例历史基线，不把新场景的未知结果写成通过或失败。

阶段 0仍有一项后续工作：新增场景依赖阶段 3～8 的工具故障注入、澄清恢复和新状态记录，当前不能用旧运行结果代替。新增场景在固定清单中标记为 `baseline_available=false`。

## 2. 当前代码基线

| 项目 | 当前值 |
| --- | --- |
| Git 分支 | `codex/agentic-chatbi-redesign` |
| 当前提交 | `aed9b5c7ae689571aa5c528e9aa4546a6bb43abe` |
| 当前 Research Runtime | `backend/apps/chatbi/orchestration/pipeline/plan_and_solve_runtime.py` |
| 当前 Pipeline 适配 | `backend/apps/chatbi/orchestration/pipeline/plan_and_solve_pipeline.py` |
| 当前 Research 工具入口 | `backend/apps/chatbi/orchestration/agent/tools/research.py` |
| 历史固定用例集 | `backend/scripts/data/research_agent_eval_cases.json`，11 例 |
| 历史基线原始数据 | `backend/scripts/data/research_agent_eval_baseline_20260822.json` |
| 历史基线报告 | `backend/scripts/data/research_agent_eval_baseline_20260822_report.md` |
| 当前工作区额外文件 | 用户新增的 `docs/TDD/42-evidence-driven-react-agent-implementation-plan.md` |

历史固定基线的 JSON 数据为：

| 结果 | 数量 |
| --- | ---: |
| `pass` | 5 |
| `correct_reject` | 2 |
| `explicit_failure` | 3 |
| `silent_error` | 1 |
| `harness_error` | 0 |

可接受结果为 `pass + correct_reject = 7/11`。JSON 中记录的平均耗时为 7.9 秒，最长耗时为 19.5 秒；逐查询次数和逐模型轮次在旧运行事实中不可用，不能从剩余预算反推。

基线报告正文写有平均 8.5 秒，与原始 JSON 的 7.9 秒不一致。本阶段以原始 JSON 的结构化字段为数值来源，并将报告正文差异留档，不修改历史文件。

## 3. P0-01：冻结改造范围

### 3.1 本次必须改造

1. `ResearchAgentInput`、`ResearchState`、`ToolResult`、`Evidence`、`Finding`、`Todo`、`Completion` 和 `ResearchTurnDecision` 契约；
2. 每轮上下文投影、语义上下文 YAML 和 Research Profile；
3. `query_semantic_data`、`compute_evidence`、`read_evidence_rows`、`search_semantic_assets`、`request_clarification`、`finish_research` 六个工具；
4. Research 工具统一的 `prepare()`、`execute()`、动作指纹、错误分类和结果封装；
5. Research 专用状态、Evidence 依赖、Finding/Todo 变更、预算和尝试记录；
6. `PlanAndSolveRuntime` 的主循环、Research Pipeline 适配和相关导入；
7. Research 快照、恢复、幂等和澄清恢复；
8. Responder 输入，使最终回答只消费已接受 Completion、confirmed Finding、Evidence 和限制；
9. Research 相关测试、评测、Trace 字段和发布前运行任务处置。

### 3.2 本次不改造

- Fast 和 Plan 的产品行为；
- Semantic Query Engine 的确定性语义规划原则；
- Compute Engine 的确定性执行原则；
- 完整结果存储的内部实现，除非为了补充 `evidence_id` 映射和幂等所需的适配；
- 通用 Agent Run、Step、Tool Call、Trace、取消信号和权限服务的非 Research 能力；
- 不属于 Research 工具的通用 `Observation`，例如时间理解相关的观察对象；
- 直接 SQL 工具和物理 Schema 暴露能力。

`services/planning/execution_state.py` 以及 Fast、Plan 使用的 `plan_execution_state` 不能在阶段 0或后续 Research 改造中直接删除。后续只移除 Research 对该状态的依赖；共享状态的清理必须先证明不影响 Fast 和 Plan。

### 3.3 迁移规则

- 不保留旧 Research Runtime 与新 Research Runtime 的双主路径；
- 不建立旧 `ResearchPlanNode` 到新 `ResearchAction` 的运行时适配器；
- 不把旧 `plan_execution_state` 自动转换为新 `ResearchState`；
- 发布前处理仍运行的旧 Research Run，发布后只允许查看历史 JSON，不能通过新 Runtime 恢复旧状态；
- 语义检索结果、权限版本、Schema 版本和结果存储等确定性能力可以复用，但必须通过新契约进入新 Runtime；
- 旧路径只允许阻断性修复，不再新增业务能力。

## 4. P0-02：旧协议引用清单

### 4.1 主调用链和协议

| 旧对象或入口 | 当前引用位置 | 当前职责 | 41/42 处理 |
| --- | --- | --- | --- |
| `PlanAndSolveRuntime` | `orchestration/pipeline/plan_and_solve_runtime.py` | Planner/Solve 两阶段、READY 节点调度、批次执行和旧状态持久化 | 改名并改造成 `ResearchAgentRuntime`，移除计划执行主循环 |
| `PlanAndSolvePipeline` | `orchestration/pipeline/plan_and_solve_pipeline.py` | 将路由请求接入旧 Runtime，并恢复旧研究状态 | 改为新 Runtime 适配，恢复只接受新 `ResearchState` |
| `submit_research_plan` | `agent/tools/research.py`、`reasoning_profile.py`、Runtime、测试 | 提交完整计划和计划周期 | 删除工具、提示词、可见性和测试引用 |
| `ResearchPlanNode` | `models/dto/research_agent.py`、`tools/research.py`、`services/research/premise_gap.py`、`tool_context.py` | 描述计划节点、依赖和 READY 执行步骤 | 删除顶层控制契约，改用 `ResearchAction` |
| `ResearchAgentRequirement` | `models/dto/research_agent.py`、`routing_freeze.py`、Pipeline、`tool_context.py` | 路由期冻结旧 Research 输入和规划约束 | 审计后由 `ResearchAgentInput` 接替；权限、语义和版本冻结能力保留 |
| `ToolObservation` | DTO、Research 工具、Runtime、生命周期、快照和测试 | 统一承载旧工具成功/失败结果 | 改为 Runtime 构造 `ToolResult<T>`，删除 Observation 转换流程 |
| `ResearchEvidence` | DTO、工具、快照、报告和评测投影 | 当前 Evidence 快照和报告引用 | 按 41 的 `Evidence` 结构调整；保留完整结果存储与父 Evidence 关系 |

### 4.2 Gap、Hypothesis 和完成度协议

| 旧对象或模块 | 当前引用位置 | 41/42 处理 |
| --- | --- | --- |
| `SemanticAssessment`、`SemanticAssessmentStatus` | DTO、`tools/research.py`、`agent_context.py`、`premise_gap.py`、完成工具测试 | 删除作为顶层模型控制协议；结束状态改由 `Completion` 和 `finish_research` 确定性校验 |
| `ResearchGap`、`ResearchGapResolution`、`StructuralCoverageGap` | DTO、`completion.py`、`premise_gap.py`、报告和测试 | 删除旧 Gap 控制流程；未完成内容改由 `TodoItem` 和 `CompletionLimitation` 表达 |
| `ResearchHypothesisAssessment`、`hypothesis_ids`、`hypothesis_audit` | DTO、`hypothesis_evaluator.py`、`tool_context.py`、快照、报告、语义运行时和评测 | 迁移为 Finding、Evidence 和 Todo；需要保留的证据强度校验要改写成新 Finding 校验 |
| `premise_gap.py` | `tools/research.py`、`tool_context.py`、测试 | 删除旧计划 Gap 校验；关键歧义改由 `request_clarification` 处理 |
| `hypothesis_evaluator.py` | `tool_context.py`、报告和测试 | 逐项确认是否能由 Finding/Evidence 校验替代；不能把旧 Hypothesis 状态机原样带入新 Runtime |
| `report_draft.py`、`report_validator.py` | 旧 Runtime、完成工具和评测 | 改为只消费 accepted Completion、confirmed Finding 和 Evidence |

### 4.3 状态、持久化和上下文引用

| 当前位置 | 当前旧职责 | 41/42 处理 |
| --- | --- | --- |
| `services/research/agent_context.py` | 投影 `plan_execution_state`、Hypothesis、旧失败 Observation 和旧计划规则 | 改为投影 `ResearchAgentInput`、Evidence、Finding、Todo、AttemptSummary 和 RemainingBudget |
| `services/research/tool_context.py` | 保存 Requirement、Evidence、Observation、Hypothesis 和 `plan_execution_state` | 改为新 `ResearchState` 的唯一服务端入口 |
| `services/research/state_snapshot.py` | 保存旧 Evidence、Hypothesis、Observation 和计划状态 | 改为保存新状态版本、输入快照引用、事件序号和 Evidence 依赖 |
| `services/research/run_lifecycle.py` | 恢复旧状态、重放 Observation 和计划节点 | 只恢复新 `ResearchState`；旧版本返回明确错误 |
| `orchestration/agent/reasoning_profile.py` | 暴露旧 `submit_research_plan` 和 `finish_research` | 暴露动态工具集合和新 System Prompt |
| `orchestration/agent/run_orchestrator.py` | 装配并分发 `PlanAndSolvePipeline` | 改为新 Research Pipeline 入口 |
| `orchestration/agent/composition.py` | 装配旧 Research Pipeline、Policy 和依赖 | 改为装配新 Runtime、工具注册表和 Responder |
| `orchestration/pipeline/mode_router.py` | 冻结旧 Requirement | 改为构建 `ResearchAgentInput` 所需的权限过滤语义快照 |

### 4.4 `plan_execution_state` 的范围判断

当前 `plan_execution_state` 不是 Research 独占对象。引用分为两类：

- **Research 专用引用**：`plan_and_solve_runtime.py`、`plan_and_solve_pipeline.py`、`research/agent_context.py`、`research/tool_context.py`、`research/state_snapshot.py`、`research/run_lifecycle.py` 以及 Research 工具和测试；这些引用属于阶段 6～8 的删除或改造范围。
- **Fast/Plan 共享引用**：`services/planning/execution_state.py`、`orchestration/pipeline/fast.py`、`services/execution/analysis_execution.py` 以及对应通用测试；这些引用不属于本次直接删除范围。

阶段 0的结论是：删除 Research 顶层 `plan_execution_state`，不是删除仓库内同名通用执行状态模块。

## 5. 复用、修改和删除矩阵

| 分类 | 内容 |
| --- | --- |
| 继续复用 | Semantic Query Engine、Compute Engine、完整结果存储、Agent Run/Step/Tool Call、Trace、取消信号、权限服务、语义检索和权限版本快照 |
| 直接修改 | `research_agent.py`、`agent_context.py`、`tool_context.py`、`state_snapshot.py`、`run_lifecycle.py`、Research 工具、Reasoner/Profile、Pipeline、Runtime、Responder、Research 测试和评测入口 |
| 迁移后删除 | `submit_research_plan`、`ResearchPlanNode`、旧 SemanticAssessment/Gap/Hypothesis 控制契约、旧 Observation 登记流程、Planner/Solve/READY 调度和 Research 专用 `plan_execution_state` 读写 |
| 暂不删除 | Fast/Plan 共用的 `services/planning/execution_state.py`、非 Research 的 Observation、通用结果存储和通用 Agent 生命周期设施 |

## 6. P0-03：固定评测集

目标架构的机器可读场景清单为：

`backend/scripts/data/research_agent_react_stage0_eval_cases.json`

清单包含 18 个场景，覆盖 42 §3.1 要求的 14 类场景，并补充结果读取、并行查询、停滞检测和权限拒绝。用例使用固定问题、固定目标语义引用、固定预算和固定预期状态；模型输出、Evidence 数值和运行事实由执行时记录。

| 场景 | 清单 ID | 当前基线 |
| --- | --- | --- |
| 单指标查询 | `react-basic-single-metric` | 不可用：旧评测集没有独立单指标样本 |
| 两期指标对比 | `react-period-comparison` | 可参考历史 case 001，但需按新 Evidence 契约重跑 |
| 指标驱动分析 | `react-driver-analysis` | 可参考历史 case 005 |
| 商家和档口连续下钻 | `react-hierarchy-drilldown` | 可参考历史 case 003 |
| 派生指标 | `react-derived-metric` | 不可用：需新语义公式快照 |
| 高基数维度 Top N | `react-high-cardinality-topn` | 不可用：需新查询 Limit/排序观测 |
| Evidence 后续计算 | `react-compute-evidence` | 可参考历史 case 004，但历史路由失败 |
| 语义资产不足 | `react-semantic-asset-missing` | 可参考历史 case 009 |
| 关键歧义澄清 | `react-clarification-resume` | 不可用：旧路径无目标澄清工具 |
| 查询失败后换参 | `react-query-retry-with-new-args` | 不可用：需确定性故障注入 |
| 部分查询成功部分失败 | `react-partial-success` | 不可用：需并行动作部分失败记录 |
| 无法回答 | `react-unanswerable` | 可参考历史 case 007，但需按新 `unanswerable` 状态重跑 |
| 预算耗尽 | `react-budget-exhaustion` | 可参考历史 case 011 |
| 取消和恢复 | `react-cancel-resume-idempotent` | 不可用：需运行中取消和恢复注入 |
| 并行独立查询 | `react-independent-parallel-queries` | 可参考历史 case 010 |
| 截断结果读取 | `react-read-evidence-rows` | 不可用：旧工具名为 `inspect_evidence` |
| 重复动作和停滞 | `react-stall-detection` | 不可用：需新动作指纹和停滞字段 |
| 权限和租户隔离 | `react-permission-tenant-isolation` | 可参考历史 case 008/009，但当前数据集不能构造跨租户对照 |

固定清单不是要求阶段 0伪造新增场景的运行结果。新增场景必须在对应工具、Runtime 和恢复能力完成后执行，并与本文件的预期终态和禁止行为比较。

## 7. P0-04：基线记录字段

每个场景必须记录以下字段；字段没有事实来源时使用 `null`，同时记录 `unavailable_reason`：

| 分组 | 字段 |
| --- | --- |
| 输入 | `case_id`、问题、数据集、租户、用户、权限版本、Schema 版本、模型和 Prompt 版本 |
| 终态 | `run_status`、`completion_status`、结束原因、是否 accepted、限制和错误码 |
| 事实 | Finding、Evidence、Evidence 父依赖、引用一致性、是否有 superseded Finding |
| 消耗 | 模型轮次、查询次数、计算次数、语义检索次数、执行时间、查询成本、结果行数 |
| 行为 | 动作序列、工具可见性、动作指纹、重复动作、并行组、停滞次数、取消点、恢复次数 |
| 质量 | 数据正确性、Evidence 引用正确率、权限越界、物理 SQL 注入、部分回答率、无法回答率 |
| 恢复 | 中断点、恢复后的重复 Evidence 数、重复 Tool Call 数、恢复结果、幂等键 |

历史旧路径只能观测到部分字段。旧路径缺失的模型轮次、查询次数、查询成本和完整动作序列保持不可用，不用预算剩余值估算。

## 8. 发布前运行任务处理方案

在阶段 11 发布前执行以下步骤，阶段 0先冻结为发布要求：

1. 暂停新的 Research Run 进入执行，Fast 和 Plan 不受影响；
2. 扫描 `chatbi_agent_run` 中 Research 且处于运行或可恢复中间状态的记录；
3. 短时运行任务等待完成，长时间运行或等待用户澄清的旧任务取消；
4. 将 `run_id`、处置方式、时间和原因写入发布处置清单；
5. 新 Runtime 不实现旧 `plan_execution_state` 到 `ResearchState` 的运行时转换；
6. 历史旧 Run 保留 JSON 展示和 Trace/Artifact 查看能力，发布后旧运行状态只读；
7. 旧快照被误提交恢复时返回明确的状态版本不支持错误；
8. 发布后冒烟验证单指标、两期对比、连续下钻、Evidence 计算、澄清、部分回答、取消和恢复。

建议的扫描条件：`execution_mode = 'research'` 且 `status` 不属于已完成、部分完成、失败或取消终态。实际状态枚举以部署版本的 ORM 和数据库约束为准，不能把通用 `react_legacy` 行混入 Research 处置清单。

## 9. 阶段 0验收状态

| 验收项 | 状态 | 说明 |
| --- | --- | --- |
| 改造范围已冻结 | 已完成 | 本文第 3 节；Fast/Plan 共享状态边界已单独标出 |
| 旧协议引用有迁移归属 | 已完成 | 本文第 4～5 节 |
| 固定评测集覆盖目标场景 | 已完成 | 18 个场景，机器可读清单已提交 |
| 历史基线可复核 | 已完成 | 11 例原始 JSON 和历史报告均保留；差异已记录 |
| 新增场景均已有可运行基线 | 未完成 | 依赖阶段 3～8 的新工具、故障注入和恢复事实 |
| 阶段 0全部通过 | 未完成 | 完成新增场景的确定性执行入口和基线采集后再核销 |

阶段 1的前置条件已经具备：旧协议引用和新目标场景均已明确，后续可以开始新 DTO 和状态契约设计。
