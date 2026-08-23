# 38. Research Agent 重构实施计划

# 0. 文档定位

本文是 `37-agentic-chatbi-complete-technical-design.md` 第 9 章的实施计划，负责回答：

1. Research 重构分为哪些阶段；
2. 每个阶段解决什么问题；
3. 每个阶段的完成目标是什么；
4. 每个阶段具体修改哪些契约、服务、编排、持久化和测试；
5. 每个阶段产出什么；
6. 通过什么条件才能进入下一阶段；
7. 旧 `ResearchAction` 何时停止扩展、何时切流、何时删除；
8. 直接 SQL 在什么条件下才能开放。

本文不重新讨论语义层设计，也不修改 Fast 和 Plan 的产品边界。目标是将当前基于
`ResearchAction -> materialize_research_action() -> ExecutionRequirement -> PlanPipeline`
的实现，重构为：

~~~text
Research Agent Harness
  -> 少量稳定工具
  -> Semantic Query Runtime / Compute Engine / ResultStore
  -> ToolObservation
  -> Evidence / Hypothesis / Completion
  -> 下一轮或结束
~~~

实施顺序不以兼容旧 Action 为设计目标。兼容只用于安全迁移，不能进入新核心链路。

# 1. 重构总原则

## 1.1 先建立目标接口，再迁移调用方

新实现先定义稳定契约和执行入口，旧路径和新路径分别消费同一份冻结研究输入。禁止建立：

~~~text
旧 ResearchAction
  -> 新通用 Action
  -> 新 Tool
~~~

这种适配会保留旧动作分支，只是把它隐藏在新接口下面。

允许的迁移关系是：

~~~text
同一份冻结 Research 输入
  ├─ 旧 ResearchAction 路径
  └─ 新 Research Agent Tool 路径
~~~

## 1.2 先完成语义查询主路径，再实现 Agent 循环

Agent 循环不是第一阶段。必须先证明模型提交的稳定语义查询参数能够经过：

~~~text
Scope / Permission
  -> Semantic Planning
  -> Structural Proof
  -> SQL Compilation
  -> DAG Execution
  -> ResultStore
  -> Evidence Projection
~~~

如果查询执行入口尚不稳定，提前实现 Agent 只会把错误转换为更多 Prompt 和重试逻辑。

## 1.3 新路径不得依赖旧 Action 模块

新模块不得导入：

- `apps.chatbi.services.research.actions`；
- `apps.chatbi.services.research.action_batches`；
- 旧 `ResearchPolicyDecision`；
- `ResearchActionType`；
- 任何具体 Action DTO。

允许复用的内容必须满足“与动作类型无关”：

- 已发布 `DatasetSchema`；
- `QueryRequirement`、`CalculationRequirement` 和 Result Contract 中的通用执行含义；
- Semantic Query Planning、Validation 和 SQL Compilation；
- `AnalysisPlanner`、DAG 调度、ComputeEngine 和 ResultStore；
- 通用 `Tool`、`ToolRegistry`、Tool Call 持久化和 Trace；
- 权限、预算、取消和生命周期能力。

## 1.4 每个阶段必须可独立验收

阶段完成不以“代码已经合并”判断，而以以下条件判断：

1. 本阶段定义的契约和业务不变量都有自动化测试；
2. 新增失败路径返回明确错误，没有静默 fallback；
3. 阶段产物能够独立运行或被下一阶段消费；
4. 未完成能力没有通过临时默认值伪装成功；
5. 回滚不会破坏 Fast、Plan 和旧 Research 主路径。

## 1.5 直接 SQL 最后开放

核心重构阶段只允许语义查询和确定性计算。直接 SQL 必须等到：

- 新 Agent 主路径已经完成切流；
- Semantic Runtime 能稳定返回 `UNSUPPORTED_CAPABILITY`；
- 安全、权限、dry-plan、成本和结果分级已经完成；
- 已有独立 SQL 评测集。

# 2. 当前实现基线

## 2.1 当前调用链

当前 Research 入口为：

~~~text
ModeRouter
  -> build_research_requirement()
  -> ExecutionRequirement(route=research)
  -> ResearchPipeline.run()
  -> ResearchPolicy.decide_with_usage()
  -> ResearchAction
  -> materialize_research_action()
  -> ExecutionRequirement(route=plan, origin=research_action)
  -> PlanPipeline.execute_requirement()
  -> EvidenceSnapshot
  -> Hypothesis / Report
~~~

主要实现位置：

| 职责 | 当前文件 |
| --- | --- |
| Research DTO 和 Action | `backend/apps/chatbi/models/dto/research.py` |
| 根执行需求 | `backend/apps/chatbi/models/dto/execution_requirement.py` |
| Research 路由和资产快照 | `backend/apps/chatbi/orchestration/pipeline/mode_router.py` |
| Research 主循环 | `backend/apps/chatbi/orchestration/pipeline/research.py` |
| Action 物化 | `backend/apps/chatbi/services/research/actions.py` |
| Action 批次 | `backend/apps/chatbi/services/research/action_batches.py` |
| 旧模型 Policy | `backend/apps/chatbi/services/research/policy.py` |
| Policy Prompt | `backend/apps/chatbi/adapters/prompts/research_policy.py` |
| Requirement 和 Scope | `backend/apps/chatbi/services/research/requirements.py` |
| Evidence 投影 | `backend/apps/chatbi/services/research/evidence.py` |
| 假设状态 | `backend/apps/chatbi/services/research/hypotheses.py` |
| 报告 | `backend/apps/chatbi/services/research/report.py` |
| Plan 执行 | `backend/apps/chatbi/orchestration/pipeline/plan_mode.py` |

## 2.2 已经具备且应复用的通用能力

当前项目不是从零开始。以下能力符合目标架构，应优先复用或提取统一入口：

1. `Tool`、`ToolRegistry` 的 Pydantic 输入输出校验；
2. Tool 中间件、超时、幂等和并发属性；
3. `AgentReasoner` 的 Function Calling；
4. `AgentToolExecutor` 的 Tool Call 事实记录、Observation、Trace 和取消；
5. `ChatbiAgentRun`、`ChatbiAgentStep`、`ChatbiAgentToolCall`；
6. `derived_state` 的运行快照；
7. `ResultStore` 和 Result Artifact 的完整结果存储及所有权检查；
8. `AnalysisPlanner`、计划证明、SQL 编译和 DAG 批次执行；
9. `ComputeEngine` 的差值、增长率、合并、占比、贡献度等确定性计算；
10. Semantic Query Planning、关系、粒度、时间、fanout 和发布 Schema 证明；
11. Agent Trace 和指标记录入口；
12. 现有 Research Scope、层级、驱动关系和贡献度治理资产。

复用这些能力的理由是它们表达通用执行不变量，不是为了保留旧 ResearchAction。

## 2.3 当前需要替换的核心结构

以下结构不能作为长期目标继续扩展：

1. `ResearchActionType` 和所有 Action DTO；
2. `allowed_actions`；
3. `materialize_research_action()` 的类型分支；
4. `build_research_action_batches()`；
5. `route.origin == "research_action"` 的特殊豁免；
6. `ResearchPolicyDecision` 同时输出评估、假设更新和 Action；
7. 所有 Research 查询必须先转换为旧 `ExecutionRequirement` 的限制；
8. 所有 Research 无条件执行 `initial_compare_action()`；
9. `ResearchActionFailure` 只有动作指纹和错误码，无法支持有效 replan；
10. 主循环同时负责策略、物化、批次、执行、证据、预算、停止和报告。

## 2.4 当前测试基线

现有 Research 测试主要位于：

- `backend/tests/chatbi/test_research_contracts.py`；
- `backend/tests/chatbi/test_research_phase2.py`；
- `backend/tests/chatbi/test_research_phase3.py`；
- `backend/scripts/run_research_stage3_real_questions.py`；
- `backend/scripts/data/research_stage3_results.json`。

这些测试不能直接删除。重构期间应分为：

- **业务不变量测试**：迁移到新协议继续保留；
- **旧实现形态测试**：切流后删除；
- **真实问题评测**：扩展为新旧双跑和结论级评测。

# 3. 阶段总览

| 阶段 | 名称 | 主要目的 | 阶段完成后的系统状态 |
| --- | --- | --- | --- |
| 0 | 基线、冻结和评测准备 | 在改代码前建立可比较基线 | 能量化旧 Research 的正确性、失败和成本 |
| 1 | 新契约和迁移边界 | 定义新 Agent 输入、工具参数、Observation、Evidence 和状态 | 新旧路径契约隔离，新路径不依赖旧 Action |
| 2 | Semantic Query Runtime | 建立 Research 和 Plan 可复用的受治理执行入口 | 不经过 ResearchAction 也能执行一次完整语义查询 |
| 3 | Research 通用工具 | 将查询、证据读取、计算和结束暴露为稳定 Tool | 工具可以被确定性测试和单独调用 |
| 4 | 状态、证据依赖和恢复 | 建立 Tool Call、Observation、Evidence 和状态持久化 | 中断后可从已提交 Observation 恢复 |
| 5 | Research Agent Harness | 用 Function Calling 驱动动态循环 | 新路径能完成最小多轮 Research，不使用直接 SQL |
| 6 | 假设、完成度和报告 | 保证结论强度与 Evidence 一致 | 新路径能输出可审计的结构化报告 |
| 7 | Shadow 双跑、评测和切流 | 用真实问题证明新路径优于或不差于旧路径 | 新路径可逐步成为用户可见主路径 |
| 8 | 删除旧 ResearchAction | 清理旧契约、物化器、特殊分支和配置 | 代码中只剩新 Research Agent 架构 |
| 9 | 受限 SQL 和资产回流 | 扩展语义查询无法覆盖的长尾 | SQL 受控开放，高频模式回流语义资产 |

阶段依赖：

~~~text
阶段 0
  -> 阶段 1
      -> 阶段 2
          -> 阶段 3
              -> 阶段 4
                  -> 阶段 5
                      -> 阶段 6
                          -> 阶段 7
                              -> 阶段 8
                                  -> 阶段 9
~~~

阶段 2 的执行服务提取和阶段 4 的持久化设计可以由不同开发任务并行准备，但阶段验收仍按上述
依赖顺序进行。

# 4. 阶段 0：基线、冻结和评测准备

> **实施状态（2026-08-22）：阶段 0 已完成。** 旧架构自即日起冻结（冻结规则见下）；v1 旧基线
> 保留用于对照，v2 正式基线已按新判分器和观测口径生成：
>
> - 用例集 `backend/scripts/data/research_agent_eval_cases.json`（v2，11 例，13 类由用例显式覆盖，
>   另有 6 类由已注册的全局判分器覆盖，共 18/24 类）；统一入口
>   `backend/scripts/run_research_agent_eval.py`；
> - v1 基线结果保留在 `backend/data/research_agent_eval_baseline_20260822.json`；v2 正式基线和报告
>   分别位于 `backend/scripts/data/research_agent_eval_baseline_20260822.json` 和
>   `backend/scripts/data/research_agent_eval_baseline_20260822_report.md`。运行指标不可观测时写入
>   `null` 和 availability，不得用剩余预算反推实际消耗；
> - 四项根因留档供后续阶段对照：R1 单期问题被驱动关系 time_roles 契约硬阻断
>   （requirements.py 集合包含检查）、R2 贡献度误路由 plan 且 plan 计算崩溃、
>   R3 用户点名跨模型驱动被提升为共同目标触发单模型契约拒绝、R4 空结果后丢弃前提筛选
>   仍宣称部分成功（不可变筛选不变量自动命中）。
>
> 冻结规则（持续有效直至旧路径下线）：
>
> 1. 旧 `ResearchAction` 路径进入维护状态：不再新增 Action 类型、动作专用 Prompt 规则或
>    动作专用 Validator；只允许修复权限越界、数据错误、明确运行异常和影响基线可信度的
>    测试错误。
> 2. 所有旧路径变更必须在提交说明中标记 `research-baseline-fix`（基线修复）或
>    `research-new-capability`（新能力，冻结期禁止）。
> 3. 第三阶段剩余的真实资产端到端验收不再作为旧路径的独立完成门槛，其真实问题并入本阶段
>    评测集，用于建立旧路径基线（见 4.3.2、15.2）。

## 4.1 这个阶段是干什么的

在修改 Research 架构前，建立旧实现的真实表现和错误分布。没有基线就无法判断新 Agent 是
真正改善，还是只是将错误从显式失败变成静默错误。

本阶段还要冻结旧架构：只允许修复阻断性 Bug，不再新增 ResearchAction、动作专用 Prompt
规则或动作专用 Validator。

## 4.2 完成目标

本阶段完成后应满足：

1. 有一份版本化的 Research 评测问题集；
2. 每个问题有结构化预期，而不只是最终自然语言答案；
3. 能运行旧 Research 并输出统一评测记录；
4. 能统计正确完成、明确失败、静默错误、预算、延迟和成本；
5. 旧 Research 的新增能力入口已经冻结；
6. 后续阶段能够使用同一评测集比较新旧路径。

## 4.3 具体工作

### 4.3.1 冻结旧架构扩展

1. 在第 9 章和本实施计划中声明旧 ResearchAction 进入维护状态；
2. 新业务场景不得通过增加 Action 类型实现；
3. 新失败样例不得通过增加场景专用 Validator 修复；
4. 只允许修复：
   - 权限或 Scope 越界；
   - 数据错误；
   - 明确运行异常；
   - 会影响基线可信度的测试错误；
5. 所有旧路径变更必须在提交说明中标记是基线修复还是新能力，防止迁移期间继续扩张。

冻结守卫：执行 `backend/.venv/bin/python backend/scripts/check_research_action_freeze.py --check`。
守卫读取版本化清单 `backend/scripts/data/research_action_freeze_manifest.json`，同时检查
`ResearchActionType` 和动作 DTO 集合；新增动作类型或动作专用 DTO 会使检查失败。守卫测试位于
`backend/tests/chatbi/test_research_agent_eval.py`。当前守卫不解析 Prompt 或 Validator 的业务
实现，这两类变更仍由代码评审和阶段冻结规则约束，不能宣称已被自动阻止。

### 4.3.2 建立评测用例格式

建议新建：

~~~text
backend/scripts/data/research_agent_eval_cases.json
backend/scripts/run_research_agent_eval.py
~~~

单个用例至少包含：

~~~json
{
  "id": "research-cause-001",
  "question": "分析总 GMV 环比下降的主要原因",
  "dataset_id": 243,
  "expected": {
    "target_metric_refs": ["METRIC:271:246"],
    "immutable_filter_refs": [],
    "required_evidence_patterns": [
      "premise_confirmation",
      "dimension_or_driver_analysis"
    ],
    "allowed_finish_reasons": ["sufficient_evidence", "data_insufficient"],
    "forbidden_claims": ["strict_causality_without_evidence"]
  },
  "budgets": {
    "max_queries": 8,
    "max_model_calls": 8
  }
}
~~~

不能把整段答案作为唯一 Gold。需要分别记录：

- 目标指标是否正确；
- 时间和不可变筛选是否保持；
- 必须出现的关键 Evidence 类型；
- 允许使用的维度或驱动指标范围；
- 禁止出现的结论；
- 允许的结束原因；
- 是否允许澄清、部分成功或明确拒绝。

### 4.3.3 扩展问题覆盖

评测集 v2 的显式用例覆盖类别 1、2、3、4、6、7、8、9、10、12、16、20、22；已注册的全局
判分器另外确定性覆盖 13（Evidence 所有权）、15（动作去重）、18（发现引用）、19（报告数字
溯源）、20（相关性因果表述）和 23（Evidence 依赖顺序），合计 18/24 类。类别 5（当前数据层级
无法构造真实跳级下钻）、11、14、17、21、24 仍未覆盖，不能在报告中写成已覆盖。

具体类别如下：

1. 用户陈述变化事实，需要先确认前提；
2. 开放式趋势或异常探索，不应强制做环比确认；
3. 结果驱动筛选；
4. 治理层级相邻下钻；
5. 跳级下钻拒绝；
6. 贡献度和总量对账；
7. 同模型驱动指标验证；
8. 跨模型驱动指标验证；
9. 空结果；
10. 数据量不足；
11. 语义能力不支持；
12. Scope 外资产；
13. 其他 Run 的 Evidence 引用；
14. 查询执行失败和超时；
15. 模型重复同一查询；
16. 预算耗尽；
17. 部分查询成功、部分失败；
18. 报告引用不存在；
19. 报告数字不在 Evidence；
20. 将相关性写成因果关系；
21. 取消和恢复；
22. 多个独立查询可并行；
23. 存在 Evidence 依赖时必须分轮；
24. 权限和租户隔离。

### 4.3.4 定义评测指标

硬指标：

- 目标指标保持率；
- 时间范围保持率；
- 不可变筛选保持率；
- Scope 越界次数；
- 无引用结论次数；
- 引用其他 Run Evidence 次数；
- SQL 或物理字段绕过语义契约次数；
- 静默 fallback 次数。

质量指标：

- 关键 Evidence 命中率；
- 主要结论支持率；
- 正确拒绝率；
- 正确澄清率；
- 预算耗尽时部分报告质量；
- 重复方向比例；
- 无效查询比例；
- 假设覆盖和反例覆盖。

运行指标：

- 查询数；
- 模型调用数；
- Tool Call 数；
- 输入、输出 Token；
- 总延迟和各阶段延迟；
- 每种错误码数量；
- Result Artifact 大小；
- 恢复后重复执行次数。

### 4.3.5 增加旧路径观测

在不改变旧决策逻辑的前提下补充：

- Action 选择；
- Action 物化失败阶段；
- Plan 证明失败阶段；
- 查询和计算数量；
- Evidence 创建和依赖；
- 假设状态变化；
- Finish 原因；
- 报告引用校验结果；
- 预算预留和实际消耗。

这些信息写入现有 Trace、Step Result Summary 或评测输出，不新建第二套日志系统。

## 4.4 阶段产物

- 版本化评测用例；
- 旧路径评测脚本；
- 基线评测报告；
- 错误分类清单；
- 冻结旧 Action 的开发约束；
- 新旧双跑共用的结果格式。

## 4.5 验收标准

1. 全部评测用例能够重复运行；
2. 同一固定数据版本下，确定性字段输出稳定；
3. 评测结果能区分失败和静默错误；
4. Scope、时间、筛选和引用不变量能够自动判分；
5. 已记录当前旧路径基线；若使用 v2 评测器重跑，结果必须写入统一的
   `backend/scripts/data/research_agent_eval_baseline_20260822.json`；
6. 后续 PR 不允许无评测新增旧 Action，冻结守卫必须通过；
7. 评测器异常、判分器反例、错误目标指标、缺少时间角色、虚构数字、悬空/跨 Run 引用、同轮
   依赖、成功态允许错误码和预算上限均有自动化测试；
8. 运行指标必须区分 observed、lower_bound 和 unavailable，不得把 `0` 当作未观测。

## 4.6 本阶段不做

- 不定义新工具；
- 不改 Research 主循环；
- 不开放 SQL；
- 不切换用户流量；
- 不删除旧测试。

# 5. 阶段 1：新契约和迁移边界

## 5.1 这个阶段是干什么的

建立新 Research Agent 的稳定类型系统。此阶段不执行真实查询，重点是把目标、工具参数、
Observation、Evidence 和状态边界定义清楚，防止后续实现重新依赖旧 Action。

## 5.2 完成目标

1. 新 Agent 输入不再包含 `allowed_actions`；
2. 比较、分解、下钻和结果筛选能够由同一个语义查询协议表达；
3. 所有工具成功和失败都能返回统一 Observation；
4. Evidence 引用和依赖有稳定 DTO；
5. 新旧契约可以同时存在，但新契约不导入旧 Action；
6. 配置能够选择 `legacy`、`shadow` 或 `agent`，默认仍为 `legacy`。

## 5.3 具体工作

### 5.3.1 新建 Agent 契约文件

建议新建：

~~~text
backend/apps/chatbi/models/dto/research_agent.py
~~~

避免继续扩大当前已经混合 Requirement、Action、Evidence 和 State 的
`models/dto/research.py`。

建议定义：

- `ResearchAgentRequirement`；
- `ResearchPremise`；
- `ResearchEvidenceRequirement`；
- `ResearchSemanticQuery`；
- `ResearchEvidenceValueRef`；
- `ResearchComputeRequest`；
- `ResearchFinishRequest`；
- `ResearchToolObservation`；
- `ResearchEvidenceRecord`；
- `ResearchHypothesisRecord`；
- `ResearchRunSnapshot`；
- `ResearchIterationRecord`。

不要定义新的“通用 Action 联合类型”。Tool 参数由各 Tool 自己的 Pydantic Args Model 表达。

### 5.3.2 重构 Research 输入含义

新 Requirement 至少包含：

- `goal`；
- `reason`；
- `target_metric_refs`；
- 可选 `premise_to_verify`；
- `time_bindings`；
- `immutable_filters`；
- 冻结 `scope`；
- `evidence_requirements`；
- `budget`；
- `version_snapshot`。

必须校验：

1. 目标指标在冻结 Scope；
2. 时间已经归一化；
3. 不可变筛选引用已绑定资产；
4. Evidence Requirement 只能描述完成目标，不描述必须执行哪个工具；
5. Scope 资产引用无重复；
6. Schema、Contract 和指纹齐全；
7. `premise_to_verify` 只在问题中存在待验证事实时生成。

### 5.3.3 定义统一语义查询参数

`ResearchSemanticQuery` 至少表达：

- metrics；
- dimensions；
- time ranges；
- literal filters；
- Evidence value filters；
- comparison；
- order；
- limit；
- purpose；
- 可选 hypothesis IDs。

服务端校验：

- ref 在 Scope；
- Evidence 属于当前 Run；
- Evidence Value 对应逻辑列；
- 目标维度允许 FILTER；
- 下钻维度是已治理层级相邻节点；
- limit 和排序列有效；
- 模型不能提交物理 ID、表名、列名和 SQL。

### 5.3.4 定义 ToolObservation

统一字段至少包含：

- tool_call_id；
- tool_name；
- status；
- failure_stage；
- error_code；
- error_category；
- retryable；
- result IDs；
- evidence IDs；
- plan ID；
- statistics；
- sample rows；
- limitations；
- suggested corrections；
- budget consumed。

错误码必须是稳定枚举或集中常量，不能在工具中临时拼接自然语言作为控制信号。

### 5.3.5 定义 Evidence 记录

Evidence 至少记录：

- evidence ID；
- 当前 Run；
- 来源 Tool Call；
- 查询 purpose；
- 指标、维度、时间和筛选；
- 逻辑列；
- ResultSetRef；
- 统计和受控样本；
- 依赖 Evidence IDs；
- 假设 IDs；
- 数据限制；
- 证据级别：`GOVERNED` 或 `EXPLORATORY`；
- Schema 和 Contract 版本。

### 5.3.6 增加迁移配置

建议使用单一配置项表达执行引擎：

~~~text
legacy
shadow
agent
~~~

不要同时增加多个含义重叠的布尔开关。直接 SQL 使用独立开关，默认关闭。

### 5.3.7 增加依赖守卫测试

增加自动化测试，保证新模块不导入：

- `research.actions`；
- `research.action_batches`；
- `ResearchActionType`；
- 旧 Policy DTO。

可以通过 AST 或明确的导入边界测试完成，防止后续为了方便重新接回旧物化器。

## 5.4 阶段产物

- 新 Agent DTO；
- 新查询参数协议；
- Observation 和 Evidence 协议；
- 迁移配置；
- 契约测试和依赖守卫测试；
- 新旧输入投影规则，但不包含 Action 适配。

## 5.5 验收标准

1. 新契约测试全部通过；
2. Scope、时间、筛选和 Evidence 引用越界均明确失败；
3. 比较、分解、下钻和结果筛选均能由一个查询协议表达；
4. 新契约中不存在 Action 枚举；
5. 新模块对旧 Action 模块零依赖；
6. 默认配置不改变线上执行路径。

## 5.6 本阶段不做

- 不执行真实语义查询；
- 不调用模型；
- 不实现恢复；
- 不删除旧 DTO；
- 不开放直接 SQL。

## 5.7 实施状态（2026-08-22）

> **阶段 1 已完成契约和迁移边界实现，尚未进入阶段 2。** 本阶段只增加可被后续 Runtime
> 消费的类型和配置边界，没有把旧 `ResearchAction` 转换为新工具参数，也没有改变旧 Research
> 的默认流量。

实际文件：

- `backend/apps/chatbi/models/dto/research_agent.py`：独立的新契约文件，定义
  `ResearchAgentRequirement`、`ResearchPremise`、`ResearchEvidenceRequirement`、
  `ResearchSemanticQuery`、`ResearchEvidenceValueRef`、`ResearchToolCall`、
  `ResearchComputeRequest`、`ResearchInspectEvidenceRequest`、`ResearchFinishRequest`、
  `ResearchHypothesisAssessment`、`ToolObservation`、`ResearchEvidence`、
  `ResearchWorkingState`、`ResearchCompletion` 和 `ResearchAgentReport`，并统一使用
  `extra="forbid"`；
- `backend/apps/chatbi/models/dto/agent.py`、`backend/common/core/config.py` 和
  `backend/apps/chatbi/orchestration/agent/service.py`：增加单一配置项
  `CHATBI_RESEARCH_EXECUTION_MODE`，取值为 `legacy`、`shadow` 或 `agent`，默认值为
  `legacy`；
- `backend/apps/chatbi/orchestration/agent/run_orchestrator.py`：阶段 1未实现
  `shadow`/`agent` 时返回明确的 `RESEARCH_SHADOW_MODE_NOT_READY` 或
  `RESEARCH_AGENT_MODE_NOT_READY`，不会静默回退旧路径；
- `backend/scripts/check_research_agent_dependencies.py`：使用 AST 检查新契约不导入
  `research.actions`、`research.action_batches`、`ResearchActionType`、
  `ResearchPolicyDecision` 或 `ResearchAction`；
- `backend/tests/chatbi/test_research_agent_contracts.py` 和
  `backend/tests/chatbi/test_research_agent_phase1_boundaries.py`：覆盖契约往返、Scope 和
  版本边界、物理字段/SQL 载荷、Evidence 所有权、依赖轮次、Observation 错误结构、冻结
  WHAT、报告引用、配置解析和依赖守卫。

关键决策：

1. 版本分为两层：Agent DTO 的协议字段固定为
   `agent_contract_version=1`；`ResearchVersionSnapshot.schema_version` 和
   `ResearchVersionSnapshot.contract_version` 表示已发布语义资产的版本，例如
   `schema_version=22、contract_version=3`，两者不能混用。运行快照同时保存 Schema、Scope
   和权限指纹；查询、Evidence 和恢复状态与快照不一致时直接失败。
2. `ResearchSemanticQuery` 只表达逻辑指标、维度、时间角色、受控筛选、Evidence 值引用、
   比较、排序和限制，不提供 SQL、表名、物理字段或旧 Action 联合类型。Scope 校验通过
   `ResearchAgentRequirement.validate_query()` 和
   `validate_research_semantic_query()` 作为统一入口完成。
3. `ToolObservation` 的成功和失败状态互斥：失败必须包含稳定 `error_code`、阶段、类别、
   可重试标记、消息和可供重规划使用的 `details`；成功状态不能携带错误信息。
4. Evidence 通过 `run_id`、来源 Tool Call、Result 引用、版本、逻辑列和前序轮次依赖形成
   当前 Run 的证据边界；`ResearchWorkingState` 只保存 Evidence 引用和逻辑列摘要，不保存
   完整结果，并通过 `evolve()` 阻止冻结 WHAT 变化。
5. 阶段 1没有定义通用 Action，也没有实现旧 Action 到新契约的转换器；旧 DTO 保留给旧
   Research 路径使用。
6. `ResearchScope` 保留阶段 0的层级、驱动关系、公式组件、方向校验、跨模型维度映射和
   relation path 等治理事实；新 Agent 只能引用这些已发布事实，不能在查询参数中自行声明
   关系或物理时间列。
7. `ResearchScope.hierarchies` 是层级的唯一事实来源，元组顺序就是下钻顺序；不再额外保存
   无序的 `hierarchy_ids`。Requirement 和 WorkingState 都保存
   `time_bindings_by_model`，每个模型的时间角色必须与主时间绑定一致，模型 ID、时间维度和
   发布 Scope 均由服务端校验；时间维度所属模型必须与映射键一致，不能把目标模型时间维度
   绑定给驱动模型。
8. Tool Args 已固定阶段 3所需的参数边界：`compute_evidence` 支持 ratio、ranking、
   top_n_other 及受控分组、排序、limit 和 tolerance；`inspect_evidence` 支持受控排序、
   offset、limit 和采样上限；`finish_research` 支持严格的假设评估和未回答问题，不接受
   自由 `options`。

迁移边界固定如下：旧 Requirement 中的 `goal`、目标指标、已归一化主时间绑定、旧
`time_bindings_by_model`、不可变筛选、Scope、预算和发布版本快照，分别直接投影到新
Requirement 的同名字段；`run_id`、Scope/权限指纹、Evidence Requirement 绑定和
`agent_contract_version` 由服务端生成。旧 `allowed_actions` 不进入新契约，
不得经过“旧 Action → 通用 Action → Tool 参数”的适配链；工具参数必须由各工具自己的严格
Args DTO 表达。跨模型驱动关系继续使用已发布 Scope 中的时间维度绑定和模型维度映射，不能
默认复用目标模型的时间维度。

验收结果：

- 阶段 1契约和边界测试：30 项通过；本次独立复核补充了下钻来源 Evidence 必须存在且属于当前
  Run 的回归校验；
- 本次复核的旧 Research 契约、Phase 2/3 回归测试：31 项通过；ExecutionRequirement、Fast、
  Plan 回归测试：25 项通过；
- Ruff、Research Agent 依赖守卫、旧 ResearchAction 冻结守卫和 `git diff --check` 均通过；
- 未运行真实模型评测，未执行真实语义查询，未实现工具、恢复和 Agent Harness；
- 阶段 1完成后仍使用 `legacy`，因此可以进入阶段 2，但阶段 2必须继续消费本阶段契约，
  且不得增加旧 Action 适配链。

# 6. 阶段 2：Semantic Query Runtime

## 6.1 这个阶段是干什么的

建立不依赖 ResearchAction 的统一分析执行入口。它接收稳定语义查询参数，生成查询与计算任务，
完成证明、SQL 编译、DAG 执行和结果保存。

这是整个重构最重要的服务端基础。如果该阶段没有完成，后续 Agent 只能依赖 Prompt 修复执行
层问题。

## 6.2 完成目标

1. 给定 `ResearchSemanticQuery`，无需 ResearchAction 即可执行；
2. Plan 和 Research 共享同一个分析执行服务；
3. 查询计划严格经历 `UNVALIDATED -> PROVEN`；
4. 单查询、多查询、计算和结果契约均可执行；
5. Plan 的回答生成与底层执行解耦；
6. 不再需要 `origin="research_action"` 才允许 Research 子计划执行。

## 6.3 具体工作

### 6.3.1 提取共享分析执行规格

当前 `ExecutionRequirement` 同时承担根模式路由和分析执行输入，导致 Research 子计划必须伪装
成 `route=plan`。

建议提取内部 `AnalysisExecutionSpec`，包含：

- query requirements；
- calculation requirements；
- result contract；
- runtime；
- asset snapshot。

它不包含：

- Fast、Plan、Research 根路由；
- Research Requirement；
- `origin="research_action"`；
- 模式选择理由。

根 `ExecutionRequirement` 继续负责模式路由。Plan 根路径和 Research Tool 都转换为同一个
`AnalysisExecutionSpec`。

### 6.3.2 从 PlanPipeline 提取执行服务

建议新增或提取：

~~~text
backend/apps/chatbi/services/execution/analysis_execution.py
~~~

职责包括：

1. `AnalysisPlanner` 生成 Draft Plan；
2. 计划结构校验；
3. strict semantic scope 准备；
4. 每个 QueryTask 语义计划检查；
5. SQL 编译；
6. SQL 安全校验；
7. 完整 Plan `PROVEN` 证明；
8. DAG 批次执行；
9. ComputeEngine 执行；
10. ResultStore 注册；
11. 返回统一 `AnalysisExecutionOutcome`。

`PlanPipeline.run()` 只负责：

- 从根状态读取 Plan 输入；
- 调用 AnalysisExecutionService；
- 生成 Plan 最终答案；
- 生命周期收口。

Research Runtime 调用同一执行服务，但不调用 Plan 的答案生成。

### 6.3.3 建立 Semantic Query Builder

建议新增：

~~~text
backend/apps/chatbi/services/research/semantic_query_builder.py
~~~

职责是把 `ResearchSemanticQuery` 转为 `AnalysisExecutionSpec`，包括：

- 时间角色展开；
- 指标和维度 QueryRequirement；
- Evidence Value 转换为已验证筛选；
- 比较差值或增长率 CalculationRequirement；
- 贡献度所需总量、分组、差值和对账任务；
- 跨模型驱动指标查询和显式 Merge；
- Result Contract；
- Purpose 和 Evidence 元数据。

Builder 只能读取冻结 Schema 和 Evidence，不读取自然语言补充缺失语义。

### 6.3.4 建立 Semantic Query Runtime

建议新增：

~~~text
backend/apps/chatbi/services/research/semantic_runtime.py
~~~

统一入口：

~~~text
execute(context, query) -> ResearchSemanticQueryOutcome
~~~

执行步骤：

1. 校验 Run、租户、Dataset 和冻结版本；
2. 校验 query refs 在 Scope；
3. 解析 Evidence Value；
4. 调用 Builder；
5. 调用 AnalysisExecutionService；
6. 将执行结果投影为 ResultSetRef 和 Evidence 输入；
7. 将异常分类为结构化失败；
8. 不在内部自动改变参数重试。

### 6.3.5 取消错误中间状态

必须同时处理以下已有问题：

- 未验证计划不能初始化为 `PROVEN`；
- 无法绑定不能使用虚假物理 ID；
- 缺少查询能力返回 `UNSUPPORTED_CAPABILITY`；
- 关系、粒度、时间或 fanout 失败返回具体证明阶段；
- Plan Proof 失败不能退回非严格执行；
- ResultStore 写入失败不能返回成功 Observation。

### 6.3.6 建立错误分类

至少定义：

- `INVALID_REQUEST`；
- `EVIDENCE_REFERENCE_INVALID`；
- `SCOPE_DENIED`；
- `PERMISSION_DENIED`；
- `UNSUPPORTED_CAPABILITY`；
- `SEMANTIC_PLAN_REJECTED`；
- `SQL_COMPILE_FAILED`；
- `SQL_VALIDATION_FAILED`；
- `EXECUTION_FAILED`；
- `EXECUTION_TIMEOUT`；
- `EMPTY_RESULT`；
- `RESULT_CONTRACT_FAILED`；
- `FANOUT_DETECTED`；
- `RECONCILIATION_FAILED`；
- `RESULT_STORE_FAILED`；
- `CANCELLED`；
- `BUDGET_EXHAUSTED`。

每个错误声明：

- failure stage；
- 是否允许模型修改输入重试；
- 是否允许相同参数重试；
- 是否代表语义能力不足；
- 是否允许升级直接 SQL。

只有 `UNSUPPORTED_CAPABILITY` 可以在未来申请 SQL 能力。

## 6.4 测试工作

### 6.4.1 单元测试

- 单指标单时间；
- 当前期和对比期；
- 差值和增长率；
- 指标按维度分组；
- Evidence Value 筛选；
- 层级相邻下钻；
- 贡献度 DAG 和对账；
- 跨模型 Merge；
- Scope 越界；
- 非相邻层级；
- 不支持能力；
- fanout；
- 空结果；
- ResultStore 写入失败。

### 6.4.2 回归测试

- Fast 不受影响；
- Plan 现有测试全部通过；
- Plan 的单查询、多查询和计算结果不变；
- Research 旧路径仍可运行；
- `AnalysisExecutionService` 被 Plan 和新 Runtime 共同测试。

## 6.5 阶段产物

- `AnalysisExecutionSpec`；
- `AnalysisExecutionService`；
- Semantic Query Builder；
- Semantic Query Runtime；
- 统一执行错误分类；
- PlanPipeline 的执行与回答解耦；
- 完整单元和回归测试。

## 6.6 验收标准

1. 不构造 ResearchAction 即可完成真实语义查询；
2. 真实 dataset 243 的比较、下钻、贡献度和驱动验证可以通过 Runtime 执行；
3. 全部 Query Plan 执行前达到 `PROVEN`；
4. 不存在 `origin="research_action"` 的新调用；
5. Plan 和 Fast 回归通过；
6. 所有失败有明确错误分类；
7. Runtime 不包含模型调用和自动修复。

## 6.7 本阶段不做

- 不实现 Agent 循环；
- 不实现 Tool；
- 不改用户可见 Research；
- 不删除旧 PlanPipeline 接口；
- 不开放 SQL。

## 6.8 实施状态（2026-08-22）

> **阶段 2已完成（2026-08-22）：真实 Dataset 243 端到端验收通过，§6.6 全部满足，允许进入
> 阶段 3。** 实现重点是消除 Research 对旧 `ResearchAction` 的执行依赖，并把跨模型、结果存储和
> 错误边界固定为可测试契约。

已落地的文件和职责：

- `backend/apps/chatbi/models/dto/execution_requirement.py`：新增
  `AnalysisExecutionSpec`，复用查询/计算 DAG、结果契约和唯一叶子校验；只允许来自受治理的
  `route.mode="plan"` 执行需求转换。
- `backend/apps/chatbi/services/planning/analysis_planner.py`：新增 `plan_spec()`，让无根路由的
  分析规格直接进入计划证明；旧 `plan()` 仍作为兼容入口。
- `backend/apps/chatbi/orchestration/pipeline/plan_mode.py`：提取独立的
  `AnalysisExecutionService`，负责计划、严格语义证明、编译、DAG 执行、ComputeEngine 和
  ResultStore；`PlanPipeline` 组合该服务，仅负责 Plan 根路径的回答生成和生命周期收口，并保留
  旧的私有执行入口代理以支持迁移期测试和扩展点。
- `backend/apps/chatbi/services/execution/analysis_execution.py`：从公开执行子域导出真正的
  `AnalysisExecutionService`，不再使用 `AnalysisExecutionService = PlanPipeline` 类型别名。
- `backend/apps/chatbi/services/research/semantic_query_builder.py`：把统一
  `ResearchSemanticQuery` 编译为 `AnalysisExecutionSpec`；跨模型维度按
  `dimension_refs_by_model` 建立同一逻辑连接列，缺失模型时间绑定、筛选映射或能力时明确返回
  `UNSUPPORTED_CAPABILITY`；贡献度总量 Merge 不使用明细维度连接键。
- `backend/apps/chatbi/services/research/semantic_runtime.py`：校验租户、Dataset、Schema、契约和
  权限版本边界；仅捕获已声明的业务异常，未知程序异常继续抛出；Evidence Value 通过当前 Run
  的 `ResultStore.read()` 读取完整结果，`sample_rows` 只作为模型上下文摘要。
- `backend/apps/chatbi/errors.py`：增加带 `failure_stage`、重试资格和能力缺口元数据的
  `SemanticQueryRuntimeError`；依赖守卫覆盖上述新增核心模块。

关键边界：

1. 跨模型查询的每个时间角色都必须为每个实际模型提供合法时间维度绑定；不能用 `time=None`、
   目标模型时间维度或固定物理 ID 补齐。
2. Dataset 引用按 `ASSET:dataset:<id>` 精确解析，Dataset 24 不会匹配 Dataset 243；运行时还会
   校验 Schema fingerprint，并在上下文提供权限版本/指纹时进行一致性检查。
3. Runtime 错误分类使用稳定异常类型和显式错误码映射，不再通过异常文本包含关系推断阶段；
   当前 `UNSUPPORTED_CAPABILITY` 只能记录未来 SQL 申请资格，阶段 2仍不会升级为裸 SQL。

本次阶段 2相关测试已覆盖单查询、current/previous、差值、跨模型逻辑连接键、缺失时间绑定、
贡献度总量 Merge、Dataset 24/243 反例、ResultStore 完整结果读取、空结果和未知异常传播；
Plan/Research/Fast 回归测试保持通过。2026-08-22 复核：契约与 Runtime 核心单元测试 71 项通过，
Fast/Plan/旧 Research 回归 120 项通过、1 项跳过；相关模块的 Ruff、10 个源码模块的 mypy、
Research Agent 依赖守卫、旧 ResearchAction 冻结守卫和 `git diff --check` 均通过。

真实 Dataset 243 验收已由 `backend/scripts/run_semantic_runtime_dataset243.py` 完成（无 LLM、
不构造 ResearchAction，直接走冻结 Requirement -> Runtime -> 严格规划 -> PROVEN -> SQL 编译 ->
DAG 执行 -> ResultStore -> Evidence 投影）：比较、按商家分组比较、基于 Evidence 值筛选的层级
相邻下钻、贡献度对账、驱动指标同期变化共 5 个用例全部 `succeeded` 且计划均达 `PROVEN`
（agent run 1263，schema_version=22、contract_version=3）。比较用例 GMV 99194.88 vs
141225.27、差值 -42030.39，与贡献度用例总量差值一致，对账残差约 -2.18e-11 在 1e-6 容差内。
§6.6 七条全部满足：阶段 2 新模块零 `origin="research_action"` 引用（残留仅存在于冻结的旧
`actions.py`，按计划在阶段 8 删除）；Runtime 无模型调用和自动修复。Agent Tool、恢复和用户
可见 Research 属于阶段 3 及之后的工作，不在本阶段范围。

# 7. 阶段 3：Research 通用工具

## 7.1 这个阶段是干什么的

把已经稳定的 Runtime、ResultStore 和 ComputeEngine 包装为模型可调用的少量 Tool。Tool 是
Agent 与服务端能力的边界，不再暴露业务 Action 类型。

## 7.2 完成目标

1. 四个核心 Tool 可被 ToolRegistry 注册和执行；
2. Tool 参数和结果都经过 Pydantic 校验；
3. Tool 失败统一转换为 `ResearchToolObservation`；
4. Tool 能单独测试，不依赖 Agent Prompt；
5. 模型不能通过 Tool 参数传入物理字段、SQL 或 Scope 外 ref；
6. 本阶段仍不开放 `query_readonly_sql`。

## 7.3 具体工作

### 7.3.1 建立 Research Tool Context

建议新增：

~~~text
backend/apps/chatbi/services/research/tool_context.py
~~~

Context 至少提供：

- run ID、chat ID、record ID；
- tenant、workspace、dataset；
- 冻结 Requirement 和 Schema；
- 当前 ResearchRunSnapshot；
- Evidence Registry；
- Semantic Query Runtime；
- ComputeEngine；
- ResultStore；
- 预算和取消句柄；
- Session；
- Trace Recorder。

Context 不向模型序列化，只在服务端 Tool 执行时使用。

### 7.3.2 实现 `query_semantic_data`

建议位置：

~~~text
backend/apps/chatbi/orchestration/agent/tools/research.py
~~~

职责：

1. 校验语义查询参数；
2. 预留查询预算；
3. 调用 Semantic Query Runtime；
4. 注册 ResultSet；
5. 创建 Evidence；
6. 返回受控样本、统计、限制和 Evidence ID；
7. 失败时返回结构化 retry advice；
8. 不在 Tool 内部静默修改参数或自动重试。

### 7.3.3 实现 `inspect_evidence`

职责：

- 验证 Evidence 所有权；
- 从 ResultStore 读取完整结果；
- 按指定列、排序、范围和采样预算投影；
- 返回逻辑列和统计，不返回其他 Run 数据；
- 读取操作幂等；
- 防止完整大结果进入 Prompt 和 Trace。

### 7.3.4 实现 `compute_evidence`

第一版只支持已经存在的确定性操作：

- difference；
- growth rate；
- share；
- ratio；
- ranking；
- top N + other；
- merge；
- contribution；
- reconciliation。

Tool 必须：

1. 只接受 Evidence 或 ResultSetRef；
2. 验证输入属于当前 Run；
3. 调用 ComputeEngine；
4. 将输出保存到 ResultStore；
5. 创建依赖输入 Evidence 的新 Evidence；
6. 对零分母、缺少键、重复键、对账失败返回明确错误；
7. 禁止任意表达式和 Python。

### 7.3.5 实现 `finish_research`

Tool 参数包含：

- finish reason；
- findings；
- Evidence 引用；
- hypothesis assessments；
- limitations；
- unanswered questions。

本阶段先完成结构校验和引用存在性，完整结论强度校验在阶段 6 完成。

### 7.3.6 Tool 注册与可见性

建议新增：

~~~text
build_research_tool_registry(...)
~~~

只注册 Research 所需 Tool，或者在共享 Registry 上通过统一可见性规则只暴露这四个 Tool。

不能向 Research Agent 暴露普通：

- `execute_sql`；
- `validate_sql`；
- `get_dataset_schema`；
- `compile_semantic_sql`；
- 其他可能绕过冻结 Requirement 的工具。

### 7.3.7 并发和幂等规则

Tool 属性：

| Tool | 并发 | 幂等 | 说明 |
| --- | --- | --- | --- |
| query_semantic_data | 条件并发 | 是 | 同轮调用互不引用 Evidence 时可并行 |
| inspect_evidence | 可并行 | 是 | 只读 |
| compute_evidence | 条件并发 | 是 | 输入依赖明确时由批次器判断 |
| finish_research | 串行 | 是 | 必须是当前轮最后的收口动作 |

同一 Tool Call ID 不能重复写 ResultStore。恢复时先读取已有终态，避免重复查询和重复计算。

## 7.4 阶段产物

- Research Tool Context；
- 四个核心 Tool；
- Research Tool Registry 或可见性配置；
- Tool Result 到 Observation 的统一转换；
- Tool 单元测试；
- Tool Trace 和预算记录。

## 7.5 验收标准

1. 四个 Tool 可以在没有模型的测试中独立执行；
2. 真实语义查询能返回 Evidence；
3. Evidence 所有权、Scope、逻辑列和预算校验有效；
4. Tool 不接收 SQL 和物理字段；
5. 失败不会被 Tool 内部吞掉；
6. 相同 Tool Call ID 重放不会重复写结果；
7. Research Tool 列表不包含直接 SQL。

## 7.6 本阶段不做

- 不实现完整 Agent Harness；
- 不生成最终自然语言报告；
- 不做 Shadow；
- 不删除旧 Action。

## 7.7 实施状态（2026-08-22）

阶段 3 已完成。四个工具、Tool Context 和注册表均已落地，全部验收标准满足，
无模型参与即可独立执行。

### 落地文件

| 文件 | 内容 |
| --- | --- |
| `backend/apps/chatbi/services/research/tool_context.py` | `ResearchToolContext` 门面：证据台账、observation 重放表、请求指纹表、预算用量、迭代号、completion，统一存放在 `state["research_state"]`（JSON dump，阶段 4 可整体持久化为 derived_state）；`result_store`/`execution_identity()` 提供 ResultStore 读写归属 |
| `backend/apps/chatbi/orchestration/agent/tools/research.py` | 四个工具 + 模型可见 Args DTO + `build_research_tool_registry` + `RESEARCH_TOOL_NAMES`；统一 `_finalize` 入口（重放 → 执行 → 失败转换 → 记录观察） |
| `backend/apps/chatbi/services/research/semantic_runtime.py` | 新增公开 `semantic_query_plan_id(query)`，与 Runtime 默认 plan ID 完全一致，供工具层指纹去重 |
| `backend/tests/chatbi/test_research_tools.py` | 20 项验收测试（见下） |
| `backend/scripts/check_research_agent_dependencies.py` | `NEW_MODULES` 登记两个新模块 |

### 关键设计决策

1. **参数边界**：模型可见 Args DTO 不含 run_id / scope_fingerprint / version_snapshot；
   服务端在 execute 时从冻结 Requirement 组装完整契约对象
   （`ResearchSemanticQuery` / `ResearchComputeRequest` / `ResearchFinishRequest`），
   由契约自身校验器触发全部边界检查；`extra="forbid"` + 嵌套 DTO 的物理载荷守卫生效。
   v1 参数面不含 `evidence_value_filters`（其嵌套 run_id 与注入原则冲突），
   请求 `filter_from_result` 分析时按不支持处理。
2. **结果统一转换**：四个工具 result_model 均为 `ToolObservation`；预期失败在工具内
   转成结构化失败观察（稳定错误码 + failure_stage + 重试标志 + 建议），Registry 视角
   工具总是成功产出合法观察；未知程序异常不经过 `_finalize`，直接向宿主传播。
3. **状态边界**：同一 tool_call_id 重放直接返回已存 observation，不再写 ResultStore；
   同内容语义查询按 plan_id 指纹去重（复用既有证据、不消耗预算），同内容计算按
   `research-compute-{sha256}` 指纹去重；失败的查询不记指纹，允许换参重试并照常计费。
4. **操作映射**：ComputeEngine 无原生 ranking/reconciliation——ranking 在工具内做
   确定性排序 + 截断（NULL 最后、类型混排退化为字符串比较）；reconciliation 复用
   CONTRIBUTION 操作的 `reconciliation_difference` 输出和 tolerance 校验，失败映射为
   `RECONCILIATION_FAILED`（PROOF 阶段）；ratio 用 EXPR 操作 + 服务端确定性构造的
   受限表达式（`TRY_CAST(a AS DOUBLE) / NULLIF(TRY_CAST(b AS DOUBLE), 0)`）。
5. **派生证据**：新 Evidence 迭代号 = max(当前迭代, 输入最大迭代) + 1，保证依赖先于
   派生；逻辑列继承输入映射，派生列（`*_current/_previous/_difference/_growth_rate/
   _share/*_contribution/dimension_value/metric_value/ratio 列/reconciliation_difference`）
   按后缀规则合成 asset_ref + value_role 映射。
6. **预算口径**：query_semantic_data 每次真实执行消耗 1 个 query 名额（失败也计费）；
   compute 本阶段不计入 max_queries（由迭代数间接约束），留给阶段 5 的预算句柄细化。
7. **并发策略**：infra 只有 PARALLEL_SAFE/SERIAL 两档；§7.3.7 的"条件并发"落地为
   query/inspect/compute = PARALLEL_SAFE、finish = SERIAL，输入依赖判断留给阶段 5 批次器。

### 验收记录（对应 §7.5）

| 标准 | 结果 |
| --- | --- |
| 1. 无模型独立执行 | 20 项测试全部通过（StubRuntime + 真实 ComputeEngine + 真实 ResultStore/MemoryArtifactGateway），`tests/chatbi/test_research_tools.py` |
| 2. 真实语义查询返回 Evidence | 阶段 2 已验证 Runtime 主路径；本阶段测试验证 Runtime outcome → Evidence 台账登记链路 |
| 3. 所有权 / Scope / 逻辑列 / 预算校验有效 | 跨 Run 证据拒绝、未知/越界逻辑列拒绝、BUDGET_EXHAUSTED 观察、inspect 按 logical_columns 冻结映射投影均有专项断言 |
| 4. Tool 不接收 SQL 和物理字段 | filters.value 内嵌 `sql` 键被 Registry 层 `invalid_tool_args` 拒绝；顶层多余键（含 run_id）被 `extra="forbid"` 拒绝 |
| 5. 失败不被内部吞掉 | SCOPE_DENIED/BUDGET_EXHAUSTED/EVIDENCE_REFERENCE_INVALID/RECONCILIATION_FAILED 全部转结构化观察；Runtime 未声明异常（RuntimeError）原样传播 |
| 6. 相同 Tool Call ID 重放不重复写 | query/compute 重放测试断言 runtime 调用次数与 artifact 写入次数不变 |
| 7. Research Tool 列表不含直接 SQL | 注册表恰好 4 个工具，`execute_sql` 等不在白名单 |

回归：`tests/chatbi` + `tests/tool` 共 683 通过（16 个 `test_graph_api.py` 失败为
环境性预存问题，与本阶段无关，已用 stash 对照确认）；Ruff、mypy（8 个相关文件）、
依赖守卫 `--check` 全部通过。

### 遗留与移交阶段 4/5

- `ResearchBudgetUsage.duration_seconds/evidence_rows/evidence_chars` 尚未在工具层累计；
- compute 消耗未计入查询预算（见决策 6）；
- `filter_from_result` 分析与 `evidence_value_filters` 参数面暂未开放（见决策 1）；
- Trace Recorder 回调点已留（`record_observation`），实际 recorder 由阶段 5 注入。

# 8. 阶段 4：状态、证据依赖和恢复

## 8.1 这个阶段是干什么的

让新 Research 的每次模型决策、Tool Call、Observation、Evidence 和假设变化都可以持久化、
审计和恢复。状态必须以已经提交的事实为准，不能仅依赖内存循环。

## 8.2 完成目标

1. 每个 Tool Call 有开始和终态事实记录；
2. 每个 Observation 和 Evidence 可以从持久化状态恢复；
3. Evidence 依赖无环且只引用当前 Run；
4. 运行中断后不重复执行已经成功的调用；
5. 完整结果在 ResultStore，Prompt 和 derived_state 只保存受控摘要；
6. 第一版不为同一事实再建重复数据库表。

## 8.3 持久化策略

优先复用现有：

- `ChatbiAgentRun`：根运行和 derived_state；
- `ChatbiAgentStep`：模型轮次；
- `ChatbiAgentToolCall`：Tool Call 事实；
- Agent Trace：详细输入输出和阶段；
- ResultStore / Artifact：完整结果；
- Event Publisher：产品事件。

第一版不新建 Research Event 表。原因是当前 Research 预算较小，现有 Tool Call 表、Step、
derived_state 和 ResultStore 已经能够表达恢复需要。只有在压测证明 JSONB 状态大小、查询效率
或审计需求无法满足时，才设计独立事件表。

## 8.4 具体工作

### 8.4.1 定义 ResearchRunSnapshot

Snapshot 至少包含：

- research ID；
- goal；
- status；
- finish reason；
- premise result；
- remaining budget；
- Evidence Registry；
- Evidence dependency edges；
- hypotheses；
- completed tool call IDs；
- running tool call IDs；
- failed observations；
- iteration index；
- report draft 或最终报告；
- version snapshot。

### 8.4.2 统一提交边界

每个 Tool Call 使用：

1. 创建 Tool Call RUNNING；
2. 提交；
3. 执行外部查询或计算；
4. 保存 Result Artifact；
5. 创建 Evidence 和 Observation；
6. Tool Call 标记终态；
7. 更新 ResearchRunSnapshot；
8. Tool Call 终态、Observation 事件和 Snapshot 在同一事务提交。

如果外部查询已成功但 Artifact 写入失败，Tool Call 必须失败，不能生成 Evidence。

### 8.4.3 Evidence ID 和指纹

Evidence ID 必须稳定且属于当前 Run。指纹至少包含：

- 冻结 Schema 指纹；
- 语义查询规范化参数；
- 输入 Evidence IDs；
- Tool 类型；
- 计算操作。

用途：

- 防止重复方向；
- 支持幂等；
- 支持恢复；
- 识别相同查询不同 Tool Call ID；
- Shadow 对比。

运行态 ID 和自然语言 purpose 不应单独决定查询是否重复。

### 8.4.4 Evidence DAG 校验

新增统一校验：

- 依赖 Evidence 存在；
- 依赖属于当前 Run；
- 不允许自引用；
- 不允许环；
- Evidence Value 的来源列存在；
- 结论引用只能指向已经成功持久化的 Evidence；
- `EXPLORATORY` Evidence 的限制不能丢失。

### 8.4.5 恢复规则

恢复时：

1. 加载冻结 Requirement；
2. 校验当前发布 Schema 不是运行时事实源，继续使用冻结快照；
3. 加载最后 ResearchRunSnapshot；
4. 关闭或标记中断的 Step；
5. 检查 RUNNING Tool Call；
6. 如果已有终态 Result Artifact 和 Observation，完成状态修复；
7. 如果没有完成事实，根据 Tool 幂等性决定重试或失败；
8. 恢复消息上下文时使用 Observation 摘要，不注入完整结果；
9. 继续下一轮 Agent 决策。

### 8.4.6 取消规则

取消需要覆盖：

- 模型调用中；
- Query Runtime 中；
- DAG 执行中；
- ComputeEngine 中；
- ResultStore 写入前后；
- 报告生成中。

取消后未完成 Tool Call 标记 `INTERRUPTED` 或 `CANCELLED`，已经持久化的 Evidence 保留，并可
生成受限部分报告。

## 8.5 测试工作

- Tool Call 开始后进程中断；
- 查询成功、Artifact 写入失败；
- Artifact 成功、Snapshot 提交失败；
- 恢复后相同 Tool Call 不重复执行；
- Evidence DAG 自引用和环；
- 其他 Run Evidence；
- 取消时部分 Evidence 保留；
- 冻结 Schema 在恢复时不被新版本替换；
- derived_state 只保存摘要，不保存大结果。

## 8.6 阶段产物

- ResearchRunSnapshot；
- Evidence Registry 和 DAG 校验；
- 统一持久化提交边界；
- 恢复和取消服务；
- 幂等和指纹规则；
- 状态与恢复测试。

## 8.7 验收标准

1. 在每个主要阶段人工注入中断后能够恢复；
2. 已成功 Tool Call 不重复执行；
3. Evidence DAG 无环、无跨 Run 引用；
4. Artifact 和 Snapshot 不会出现一边成功一边被当作完整成功；
5. 冻结 Scope 和版本在恢复中保持；
6. Trace、Tool Call 和 Snapshot 可以互相定位。

## 8.8 实施状态（2026-08-23）

阶段 4 已完成。统一提交边界、快照投影、Evidence DAG 校验、恢复与取消服务均已
落地；未新建任何数据库表，全部事实落在既有 ChatbiAgentRun.derived_state /
ChatbiAgentStep / ChatbiAgentToolCall 上。

### 落地文件

| 文件 | 内容 |
| --- | --- |
| `backend/apps/chatbi/models/dto/research_agent.py` | 新增 `ResearchEvidenceEdge`；`ResearchRunSnapshot` 从别名升级为真实契约模型（§8.4.1 字段全集 + 校验器：scope 指纹一致、调用 ID 集合唯一且不相交、依赖边端点存在、预算 usage ≤ budget 且 remaining 精确推导、终态 ⟺ finish_reason） |
| `backend/apps/chatbi/services/research/state_snapshot.py` | `validate_evidence_dag`（重复/跨 Run/缺失/自引用/迭代先序/Kahn 环）；`build_research_run_snapshot`（Context → 快照投影）；`ensure_snapshot_size`（400k 字符硬守卫 → `RESEARCH_SNAPSHOT_TOO_LARGE`） |
| `backend/apps/chatbi/services/research/run_lifecycle.py` | `ResearchToolCallCommit` 统一提交边界；`recover_research_run` / `rebuild_research_context` / `load_frozen_requirement` / `ResearchRecoveryReport`；`cancel_research_run`；受控摘要 `_bounded_summary`（2000 字符截断） |
| `backend/apps/chatbi/services/research/tool_context.py` | `bind_to_context` 把冻结 Requirement / execution_id / dataset_id 落入 research_state（恢复自包含）；`register_evidence` 登记前全图 DAG 校验；`observations()` / `record_hypothesis_assessments()` |
| `backend/apps/chatbi/orchestration/agent/tools/research.py` | compute 指纹扩展为 `{tool, schema_fingerprint, run_id, request, inputs}`（§8.4.3）；`finish_research` 先落假设评估再完成 |
| `backend/scripts/check_research_agent_dependencies.py` | 登记两个新模块，依赖守卫通过 |
| `backend/tests/chatbi/test_research_state_recovery.py` | 16 项状态/恢复验收测试（FakeSession 以撤销日志模拟 commit/rollback 并保持行对象身份） |

### 关键设计决策

1. **规范事实源 vs 审计投影**：`state["research_state"]` 仍是规范事实源（保留全部
   成功观察以支撑按 tool_call_id 重放）；快照是它的投影，额外携带运行中调用、失败
   观察、依赖边和剩余预算。两者连同 `result_sets` 一起写入 `derived_state` 三个键。
2. **提交边界事务语义**：enter 创建 RUNNING 行并立即提交（崩溃可见）；finish 写
   终态行 + 观察 + 快照但不提交；exit 单事务一次提交。执行体抛未知异常 → 行标
   FAILED(`tool_undeclared_exception`) 后提交并原样传播；exit 提交失败 → 回滚，
   保持“RUNNING 行 ⇒ 无已提交终态事实”不变式（§8.7.4），Artifact 可能已写但
   恢复流程会将其收口为 INTERRUPTED，不会当作完整成功。
3. **合成观察**：Registry 拒绝（unknown args → EXECUTION_FAILED、白名单外工具 →
   INVALID_REQUEST）等不产出研究观察的终态结果由边界补一条结构化失败观察，
   保证“每个终态行都有事实”的重放/恢复不变式。
4. **恢复只认冻结 Requirement**：从 derived_state 加载冻结 Requirement 和研究
   事实，当前发布 Schema 不参与恢复；旧快照版本指纹与冻结不一致 →
   `RESEARCH_RECOVERY_VERSION_CONFLICT`。RUNNING Step 收口为 CANCELLED；
   RUNNING Tool Call 有已提交终态观察则按观察修复状态，否则 INTERRUPTED
   （重试安全性由指纹去重保证，不重复计费）。
5. **Evidence 同 ID 再登记 = 后写覆盖**（服务端补齐逻辑列的合法路径），但覆盖后的
   全图必须通过 DAG 校验，且校验发生在账本变更之前，失败不留脏数据。
6. **取消只收研究事实**：未完成调用 INTERRUPTED、已持久化证据保留、写入 cancelled
   completion 并刷新快照；Run / ChatRecord 终态仍由通用生命周期
   （AgentLifecycle.finalize_cancellation）负责，重复取消幂等。
7. **摘要尺寸**：Tool Call 行的 args/result 摘要按 2000 字符有界截断；快照超 400k
   字符直接拒绝持久化，完整正文只在 ResultStore。

### 验收记录（对应 §8.5 / §8.7）

| 标准 | 结果 |
| --- | --- |
| Tool Call 开始后进程中断 | crash 场景（仅 RUNNING 行无观察）：恢复收口 Step(CANCELLED) + 调用标 INTERRUPTED，已成功调用的事实（预算/证据/指纹）完整恢复 |
| 查询成功、Artifact 写入失败 | Runtime 把写入失败转成 `RESULT_STORE_FAILED/PERSISTENCE` 结构化失败结果，边界落 FAILED 行，无证据登记 |
| Artifact 成功、Snapshot 提交失败 | exit 提交失败回滚：行保持 RUNNING、快照键不存在、无已提交观察；随后恢复标 INTERRUPTED 且台账为空 |
| 恢复后相同 Tool Call 不重复执行 | 恢复后同内容查询命中指纹（runtime 零调用，返回 `duplicate_query_reuse`）；陈旧 RUNNING 行若有已提交观察则被修复而非重跑 |
| Evidence DAG 自引用和环 | cycle/self/cross-run/missing 四类单测（model_construct 构造绕过契约校验器的坏状态）+ 登记前校验 + 快照构建与恢复时复检 |
| 冻结 Schema 在恢复时不被新版本替换 | 恢复断言 requirement 原样相等、schema_fingerprint 不变；篡改快照版本指纹被 VERSION_CONFLICT 拒绝；缺冻结 Requirement 报 REQUIREMENT_MISSING |
| 取消时部分 Evidence 保留 | 取消后证据仍在、调用 INTERRUPTED、cancelled completion 入账、二次取消幂等 |
| derived_state 只保存摘要 | 大 sample_rows 触发 `RESEARCH_SNAPSHOT_TOO_LARGE`；compute 指纹覆盖 schema_fingerprint 变更 |

回归：新增 16 项测试全部通过；research + tool 六套件 109 通过；`tests/chatbi`
全量 682 通过（16 个 `test_graph_api.py` 失败为环境性预存问题，与本阶段无关，
Phase 3 已用 stash 对照确认）；Ruff、mypy（5 个相关文件）、依赖守卫 `--check`
全部通过。

### 遗留与移交阶段 5

- `ResearchToolCallCommit` 尚无宿主调用方——Harness 循环负责每次模型工具调用的
  enter / finish / exit 接线，以及恢复入口和取消边界的触发；
- `premise_result` / `report_draft` / `final_report` 快照字段由阶段 6 回填；
- `duration_seconds` / `evidence_rows` / `evidence_chars` 预算轴仍未累计
  （沿用阶段 3 移交项）；
- Trace Recorder 回调点已保留（`record_observation`），实际 recorder 由阶段 5 注入。

# 9. 阶段 5：Research Agent Harness

## 9.1 这个阶段是干什么的

使用 Function Calling 实现真正的动态 Research 循环。模型根据 Requirement、当前 Snapshot 和
Observation 选择 Tool，不再输出 ResearchPolicyDecision 和 ResearchAction。

## 9.2 完成目标

1. 新 Harness 能完成多轮语义查询；
2. 每轮真实 Tool 结果进入下一轮；
3. replan 不维护完整全局 DAG；
4. 用户 WHAT 在每次 Tool 参数校验中保持不变；
5. 预算、重复、停止、取消和恢复由服务端控制；
6. 本阶段只能使用四个核心 Tool，不使用直接 SQL；
7. 新路径只在测试或 Shadow 模式运行。

## 9.3 具体工作

### 9.3.1 复用通用 Agent 基础能力

应复用：

- `AgentReasoner`；
- `AgentToolExecutor`；
- `ToolRegistry`；
- Step 和 Tool Call 持久化；
- Trace；
- Budget 和 Cancellation。

需要改造：

1. 将当前 `normal`、`soft` 字符串收敛为集中定义的 Reasoning Profile；
2. 增加 Research Profile；
3. Research Profile 只暴露 Research Tool；
4. Research 使用独立系统上下文和 Working State 投影；
5. 直接自然语言回答不视为完成，必须调用 `finish_research`；
6. Tool Observation 使用 Research 协议投影。

### 9.3.2 新建 ResearchAgentHarness

建议新增：

~~~text
backend/apps/chatbi/orchestration/pipeline/research_agent.py
~~~

主流程应在一个方法内清晰可见：

~~~text
load requirement
  -> initialize or restore snapshot
  -> optional premise preflight
  -> while budget available
       -> prepare bounded context
       -> reasoner.decide(profile=research)
       -> validate selected tools
       -> execute independent tool batch
       -> persist observations and evidence
       -> if finish accepted: finalize
  -> budget or failure partial report
~~~

不要为了拆分而拆成大量只调用一次的微型方法。只有 Tool 执行、状态持久化、预算和报告等真实
复用或复杂职责下沉到服务。

### 9.3.3 条件前提确认

如果存在 `premise_to_verify`：

1. 服务端构造确定性语义查询；
2. 调用 `query_semantic_data` 等价底层能力；
3. 创建 premise Evidence；
4. 确定问题前提是否成立；
5. 如果不成立，允许直接进入 `PREMISE_NOT_SUPPORTED` 报告；
6. 如果无法判断，形成 Observation 交给 Agent。

没有 premise 时禁止无条件比较。

### 9.3.4 构造受控推理上下文

每轮模型看到：

- 用户问题；
- 冻结目标和不可变条件；
- Scope 中与研究有关的逻辑资产目录；
- 当前预算；
- 当前 hypotheses；
- Evidence 摘要和依赖；
- 最近失败 Observation；
- Evidence Requirements 完成情况；
- 可用 Tool 定义。

模型看不到：

- 物理表列；
- 完整 DatasetSchema JSON；
- 完整大结果；
- 其他 Run Evidence；
- 旧 Action 类型；
- 可绕过 Scope 的工具。

### 9.3.5 工具选择规则

服务端控制：

1. 一轮至少一个 Tool Call；
2. `finish_research` 不能和查询 Tool 同批执行；
3. 同轮 Tool 之间存在 Evidence 引用时拒绝并要求分轮；
4. 只有独立查询允许并行；
5. 重复指纹返回 Observation，不重复执行；
6. 达到查询、模型、时间或结果预算后禁止继续查询；
7. 模型连续没有新方向时结束或输出部分报告；
8. Scope 越界直接拒绝，不自动扩大资产。

### 9.3.6 动态 replan

不定义 `Replan` DTO。以下 Observation 进入下一轮即可触发重新决策：

- 查询报错；
- 空结果；
- 时间粒度不支持；
- 层级节点不允许；
- fanout；
- 对账失败；
- Evidence 不足；
- 候选假设被削弱；
- 预算接近上限。

模型可以修改查询维度、排序、限制、拆分方式和 Scope 内驱动指标，不能修改目标指标、时间、
不可变筛选、租户、权限和冻结版本。

### 9.3.7 停止条件

服务端停止条件：

- Agent 提交有效 finish；
- premise 不成立；
- Evidence Requirements 已满足；
- 无新方向次数达到上限；
- 查询或模型预算耗尽；
- 时间预算耗尽；
- 权限或不可恢复执行失败；
- 用户取消。

模型不能自行增加预算或声明忽略失败继续。

## 9.4 测试工作

- 两轮结果驱动筛选；
- 三层相邻下钻；
- 查询失败后换维度；
- 空结果后调整粒度；
- premise 不成立立即结束；
- 无 premise 不执行固定比较；
- 同轮独立查询并行；
- 有依赖 Tool 拒绝同轮执行；
- 重复查询被去重；
- Scope 越界；
- 预算耗尽；
- 取消和恢复；
- 模型直接回答但未调用 finish；
- 模型调用普通 execute_sql 被拒绝。

## 9.5 阶段产物

- Research Agent Profile；
- ResearchAgentHarness；
- Research Prompt / Working State；
- Tool 可见性和批次规则；
- 动态循环、停止和预算控制；
- Harness 集成测试。

## 9.6 验收标准

1. 新路径可以完成真实多轮问题；
2. 下一步查询确实依赖前一轮 Observation；
3. 无完整全局 Research DAG；
4. 目标、时间、筛选和 Scope 不漂移；
5. 全部 Tool Call 可追踪、可恢复；
6. 预算和停止由服务端强制；
7. 新路径未调用旧 Action 和物化器；
8. 新路径未开放直接 SQL。

## 9.7 实施状态（2026-08-23）

阶段 5 已完成。Function Calling 动态循环取代 ResearchPolicyDecision /
ResearchAction：`ResearchAgentHarness.run` 在一个方法内可见地实现 §9.3.2 主流程，
每轮真实 Tool 结果经阶段 4 提交边界持久化后进入下一轮。新路径只在测试中运行
（§9.2.7），RunOrchestrator 分发与 Shadow 接线保持不动。

### 落地文件

| 文件 | 内容 |
| --- | --- |
| `backend/apps/chatbi/orchestration/pipeline/research_agent.py` | `ResearchAgentHarness` + `ResearchAgentRunOutcome`：主循环（取消/预算检查 → 前提 preflight → requirements 提醒 → decide(research) → 批次校验 → 提交边界执行 → 观察回放 → 完成/停止检查）、`_run_premise_preflight`、`_validate_batch` / `_execute_batch` / `_reject_batch`、终态收口与 `resume()` |
| `backend/apps/chatbi/orchestration/agent/reasoning_profile.py` | `ReasoningProfile` 冻结数据类（`direct_answer_finishes` / `soft_reminder` / `fixed_tool_allowlist` / `working_state_builder` / `working_state_note`）；NORMAL / SOFT / RESEARCH 三个单例，RESEARCH 固定四工具白名单且纯文本不构成完成 |
| `backend/apps/chatbi/orchestration/agent/reasoning.py` | `decide(..., profile=)` 注入点：工具可见性、Working State 投影、soft 提醒和 Working State 说明全部由 Profile 提供；固定白名单 Profile 跳过旧名修正——越界工具名原样交给宿主拒绝 |
| `backend/apps/chatbi/services/research/agent_context.py` | `build_research_system_context`（frozen-boundary + 协议规则 + 可用层级）；`project_research_working_state`（有界投影：证据 ≤12 条 × 5 行样本、最近失败 ≤3、假设 ≤20）；`build_premise_query_args`；`evaluate_premise_verdict` / `_observed_direction`（按逻辑列 value_role current/previous 映射结果字段推方向） |
| `backend/apps/chatbi/services/research/tool_context.py` | `premise_result` 读写访问器；`consume_model_call`（模型预算轴登记） |
| `backend/apps/chatbi/models/dto/agent.py` | 新增 `research_max_stall_turns`（默认 3，§9.3.5.7 服务端停止阈值） |
| `backend/apps/chatbi/services/research/run_lifecycle.py` | 三处快照构建全部透传 `premise_result` |
| `backend/scripts/check_research_agent_dependencies.py` | 登记三个新模块，依赖守卫通过 |
| `backend/tests/chatbi/test_research_agent_harness.py` | 17 项 Harness 验收测试（脚本化模型客户端 + 真实注册表 + 真实提交边界 + FakeSession） |

### 关键设计决策

1. **Profile 收敛**：`normal` / `soft` 字符串白名单集中为 `ReasoningProfile`；
   ChatBI 两条既有路径行为不变（NORMAL/SOFT 单例等价迁移），RESEARCH Profile
   以 `fixed_tool_allowlist` 复用同一 reasoner——固定白名单下投影节点不做任何
   改写，Scope 越界由宿主整批拒绝而不是静默纠正。
2. **前提确认走真实工具边界**：preflight 以确定性参数构造 `query_semantic_data`
   调用并经 `ResearchToolCallCommit` 执行，预算扣减、指纹去重、观察落账免费获得；
   verdict 从证据的逻辑列映射读取 current/previous 取值推导，`not_supported` 直接
   写 succeeded / `PREMISE_NOT_SUPPORTED` 终态（零模型调用）；supported /
   undetermined 只记录 `premise_result` 并把判断交给后续循环。
3. **受控上下文**：系统上下文承载冻结边界与协议（finish 单独成轮、禁止 SQL、
   纯文本不算完成）；Working State 每轮由 `profile.working_state_builder` 重建，
   只含目标/资产目录/预算/证据摘要/最近失败/需求进度，物理列、完整 Schema、
   完整结果、跨 Run 证据一律不进入消息。
4. **批次规则整批拒绝**：未知工具、`finish_research` 与其他调用同批、同批引用尚
   不存在的 Evidence —— 三类违规整批转 INVALID_REQUEST 失败事实（合成观察落
   Tool Call 行），不做部分执行；独立批次在 `tool_parallel_workers > 1` 时用
   ThreadPoolExecutor 并行，每调用仍独立提交边界。
5. **停止条件全部服务端持有**：有效 finish；premise 不成立；迭代 / 模型 / 时间
   预算耗尽（partial / BUDGET_EXHAUSTED）；连续无新方向达到
   `research_max_stall_turns`（stalled）；证据需求满足后提醒并给 N 轮宽限仍未
   收口（requirements_unanswered）；不可恢复失败（failed / EXECUTION_FAILED）；
   用户取消（cancelled）。直接自然语言回答计入 stall 轴并下发纠偏提醒。
6. **预算双轨**：查询轴继续由工具层强制（BUDGET_EXHAUSTED 观察化）；模型轴由
   Harness 每轮 `consume_model_call()` 登记进 research_state，快照剩余量精确推导。
7. **恢复即续跑**：`resume()` = `recover_research_run` → 已有 completion 则返回
   already_finished / already_cancelled；否则以冻结 Requirement 和恢复后的 ctx
   继续 `run()`，recovery 报告随 outcome 返回。重试安全性由指纹去重保证：
   恢复后重发相同查询得到 `duplicate_query_reuse` 观察，Runtime 零调用。
8. **确定性计划 ID 支撑脚本化验收**：`semantic_query_plan_id` 使测试能在执行前
   推导 evidence id（`evidence:{plan_id}`），脚本化 finish 引用因此可以预先写出。

### 验收记录（对应 §9.4 / §9.6）

| 场景 | 结果 |
| --- | --- |
| 两轮结果驱动筛选 | 第二轮查询携带第一轮观察追加的字面过滤（`filters[0].value == "华东"`）后 finish 成功 |
| 三层相邻下钻 | 维度序列 20 → 21 → 22 共三个查询步骤 + finish，共 4 个 Step |
| 查询失败后换维度 | EXECUTION_FAILED 行落账后换维度成功，finish 引用新证据 |
| 空结果后调整粒度 | EMPTY_RESULT/PROJECTION 后换粗粒度维度成功 |
| premise 不成立立即结束 | 零模型调用；确定性比较查询一次；succeeded / PREMISE_NOT_SUPPORTED 引用前提证据；snapshot.premise_result.observed_direction=decrease |
| 无 premise 不执行固定比较 | Runtime 仅收到模型发起的一次查询，无 preflight Step |
| 同轮独立查询并行 | workers=4 时两查询同 Step 双 SUCCEEDED，各自独立事实行 |
| 有依赖 Tool 拒绝同轮执行 | 违规批次两调用均 INVALID_REQUEST 且查询未执行；分轮后 inspect 进入执行层（存储缺失 → RESULT_STORE_FAILED 结构化失败，非协议拒绝），随后 finish 成功 |
| 重复查询被去重 | 相同指纹只执行一次，重复调用得 `duplicate_query_reuse` 观察，budget_usage.queries == 1 |
| Scope 越界 | METRIC:9:999 被 SCOPE_DENIED 拒绝；finish data_insufficient；Scope 指纹不变（无自动扩界） |
| 预算耗尽 | max_model_calls=1 时第二轮推理被服务端拦截，partial / BUDGET_EXHAUSTED |
| 取消和恢复 | 取消后已完成的证据保留、cancelled completion 入账；再次 resume 返回 already_cancelled 不续跑 |
| 中断恢复不重复执行 | 手工构造 RUNNING Step + RUNNING 调用残留：恢复收口 Step、调用标 INTERRUPTED；resume 后重发相同查询命中指纹（runtime.calls 保持 1）正常收口 |
| 模型直接回答未调用 finish | 服务端纠偏提醒后模型补 finish 成功；连续直接回答达阈值 → stalled / failed / DATA_INSUFFICIENT |
| execute_sql 被拒绝 | FAILED / INVALID_REQUEST，SQL 从未进入执行层 |

§9.6 对照：多轮真实问题（场景 1–2）；下一步依赖上一轮 Observation（场景 1）；
无全局 DAG（无 Replan/Action DTO，模型逐轮决策）；目标/时间/筛选/Scope 不漂移
（frozen-boundary + 场景 Scope 越界）；Tool Call 可追踪可恢复（全部经提交边界 +
场景中断恢复）；预算与停止服务端强制（场景预算耗尽 / 停止条件族）；未触碰旧
Action（依赖守卫 `--check` 含全部新模块）；未开放 SQL（场景 execute_sql）。

回归：新增 17 项 Harness 测试全部通过；research 四套件 78 通过；`tests/chatbi`
全量 699 通过（16 个 `test_graph_api.py` 失败为环境性预存问题，与前几阶段相同）；
`tests/agent` 117 通过（31 个失败为本阶段开工前 stash 对照确认的预存问题）；
Ruff、mypy（8 个相关文件）、依赖守卫 `--check` 全部通过。

### 遗留与移交阶段 6

- premise 的 supported / undetermined 路径目前只记录 verdict 并继续循环；
  `report_draft` / `final_report` 快照字段与报告校验由阶段 6 回填；
- 假设评估已随 finish 落账（`hypothesis_assessments`），但假设状态机和
  Evidence Requirement 完成度评估属于阶段 6；
- events / RunOrchestrator / Shadow 接线未动：新路径仍只能在测试运行（§9.2.7）；
- duration / evidence_rows / evidence_chars 预算轴仍未累计（沿用阶段 3/4 移交项）；
- messages 不跨恢复持久化：resume 后模型对话从 Working State 与快照重建，历史
  文本不保留（研究事实完整，可接受）；
- 并行批次（tool_parallel_workers > 1）下 Session 的线程安全需在生产接入前评审
  （沿用 legacy executor 先例，默认配置为串行）。

# 10. 阶段 6：假设、完成度和报告

## 10.1 这个阶段是干什么的

将“工具能够运行”提升为“研究结论可信”。重点是控制假设状态、Evidence Requirement 完成度、
结论引用和表达强度，防止 Agent 查询正确但最终报告过度推断。

## 10.2 完成目标

1. Hypothesis 状态变化都有 Evidence；
2. 完成度由服务端根据 Evidence Requirements 判断；
3. Report 中每个数据 Claim 都能定位 Evidence；
4. `EXPLORATORY` Evidence 不会自动生成高置信度结论；
5. 对账失败、数据截断和样本不足会降低结论强度；
6. 预算耗尽和部分失败能够生成受限报告。

## 10.3 具体工作

### 10.3.1 重构 Hypothesis 管理

模型可以：

- 新建 hypothesis；
- 提交支持、削弱、无法判断或否定建议；
- 引用 Evidence。

服务端负责：

- hypothesis ID 唯一；
- Evidence 所有权；
- Evidence 与 hypothesis 的指标、维度、时间相关性；
- 状态转换合法性；
- 确定性对账结果优先；
- `SUPPORTED` 和 `INVALID` 必须有 Evidence；
- 数据不足时限制为 `INCONCLUSIVE`。

现有 `hypotheses.py` 中与 Action 物化无关的确定性公式、方向和对账逻辑可以迁移为 Evidence
Evaluator；依赖 `MaterializedResearchAction` 的部分应重写。

### 10.3.2 Evidence Requirement Evaluator

建议新增：

~~~text
backend/apps/chatbi/services/research/completion.py
~~~

职责：

- 判断 premise 是否已处理；
- 判断必须覆盖的目标指标是否有 Evidence；
- 判断要求的层级、驱动或贡献度证据是否完成；
- 判断核心结论是否至少有一个有效 Evidence；
- 判断是否仍有阻断性信息缺口；
- 输出结构化完成度和缺口，不决定下一 Tool。

不得根据 Action 是否执行判断完成度。

### 10.3.3 报告草案契约

每个 Finding 至少包含：

- statement；
- Evidence IDs；
- confidence；
- evidence level；
- limitations；
- 是否为相关性或因果性表述。

禁止模型在 Finding 中直接生成没有 Evidence 来源的数字。

### 10.3.4 服务端报告校验

必须校验：

1. Evidence 存在且属于当前 Run；
2. Finding 中的指标、维度和值出现在 Evidence；
3. `PENDING` 或 `INCONCLUSIVE` hypothesis 不能写成确定结论；
4. 贡献度结论必须通过对账；
5. 数据截断时不能使用“全部”“唯一”等强表达；
6. 相关性不能写成严格因果；
7. `EXPLORATORY` Evidence 必须披露；
8. 引用校验失败后不能删除引用继续输出原结论。

### 10.3.5 部分报告

以下情况生成部分报告：

- 预算耗尽但已有有效 Evidence；
- 部分 Tool 失败；
- 数据不足；
- 用户取消但已有 Evidence；
- 无法继续但已经确认部分事实。

部分报告必须列出：

- 已确认内容；
- 未确认内容；
- 失败查询和限制；
- 未执行原因；
- 是否建议用户缩小问题或补充资产。

## 10.4 测试工作

- 无 Evidence 的 SUPPORTED；
- 引用其他 Run；
- 报告新增数字；
- 对账失败仍输出主要贡献；
- 截断数据输出绝对结论；
- 相关性写成因果；
- EXPLORATORY 未披露；
- 预算耗尽部分报告；
- premise 不成立报告；
- 多个相互矛盾 Evidence；
- Evidence Requirements 已满足但模型继续查询；
- 模型 finish 过早。

## 10.5 阶段产物

- Hypothesis Evaluator；
- Completion Evaluator；
- Report Draft DTO；
- Report Validator；
- 部分报告生成；
- 结论级测试和评测判分器。

## 10.6 验收标准

1. 所有数据 Finding 都有有效 Evidence；
2. 引用和数字校验为 100% 硬门禁；
3. Hypothesis 状态变化可审计；
4. 对账、截断和 Evidence 级别影响结论强度；
5. 预算耗尽和部分失败有明确报告；
6. 完成度不依赖旧 Action 类型。

# 11. 阶段 7：Shadow 双跑、评测和切流

## 11.1 这个阶段是干什么的

在不影响用户答案的情况下，用同一份冻结目标和 Scope 同时运行旧路径和新路径，比较查询、
Evidence、结论、错误、成本和延迟。只有真实数据证明达标后才切流。

## 11.2 完成目标

1. `shadow` 模式不会覆盖用户可见结果；
2. 新旧路径使用相同冻结 Requirement 和版本快照；
3. 双跑结果能够自动比较；
4. 建立硬门禁、质量门槛和运行门槛；
5. 支持按 Dataset、租户或采样比例切流；
6. 支持快速回退到 legacy，但新路径内部不调用 legacy fallback。

## 11.3 具体工作

### 11.3.1 Shadow 执行隔离

Shadow 路径必须：

- 不发布用户可见回答事件；
- 不调用最终生命周期 finish；
- 不修改主路径 `last_execution`；
- 使用独立 plan ID、Tool Call ID 和 Result Artifact 标识；
- 标记 `shadow=true`；
- 查询仍经过真实权限和资源限制；
- 可以配置采样率和允许的数据集。

### 11.3.2 双跑比较维度

输入比较：

- target metrics；
- immutable filters；
- time bindings；
- Scope 和版本。

过程比较：

- 查询方向；
- 查询次数；
- Evidence 类型；
- 关键 Evidence；
- 错误和重试；
- 假设状态；
- 停止原因。

输出比较：

- 核心 Findings；
- Evidence 引用；
- 禁止结论；
- 部分报告；
- 用户可理解性。

运行比较：

- 模型调用；
- Token；
- SQL 次数；
- 延迟；
- 超时；
- Artifact 大小；
- 资源成本。

### 11.3.3 硬切流门禁

以下指标必须为零或 100%：

- Scope 越界为 0；
- 目标指标漂移为 0；
- 不可变筛选漂移为 0；
- 时间漂移为 0；
- 跨 Run Evidence 引用为 0；
- 无来源数字为 0；
- 静默 fallback 为 0；
- 未经 `PROVEN` 执行查询为 0；
- 报告引用校验通过率为 100%。

### 11.3.4 质量切流门槛

质量门槛采用“不得低于旧路径 + 达到绝对最低要求”的组合：

- 关键 Evidence 命中率不低于旧路径；
- 静默错误率显著低于旧路径；
- 正确拒绝率不低于旧路径；
- 结论支持率不低于旧路径；
- 重复无效查询率低于预设上限；
- 真实问题人工抽检通过。

具体数值在阶段 0 基线完成后写入评测配置，不能在没有数据时随意设定。

### 11.3.5 运行门槛

- p95 延迟在产品允许范围；
- 平均查询数和模型调用数在预算内；
- 超时率不高于旧路径；
- 取消和恢复无重复执行；
- Shadow 不影响主路径数据库事务和生命周期；
- Result Artifact 清理正常。

### 11.3.6 切流顺序

建议按风险从低到高：

1. 内部测试数据集；
2. 单指标、单数据集的结果驱动分析；
3. 治理层级下钻；
4. 贡献度和驱动验证；
5. 跨模型研究；
6. 全量 Research；
7. 默认引擎切换为 `agent`。

每一步都保留快速配置回退。回退发生在路由入口，不能在新 Harness 执行失败后静默调用旧
ResearchPipeline。

## 11.4 阶段产物

- Shadow Runner；
- 新旧结果比较器；
- 指标看板和评测报告；
- 切流配置；
- 数据集和问题类型白名单；
- 回退操作说明。

## 11.5 验收标准

1. Shadow 不影响用户答案和主路径状态；
2. 硬门禁全部通过；
3. 质量指标达到切流标准；
4. 运行成本和延迟在预算内；
5. 至少完成一次真实流量小比例切流和回退演练；
6. 新路径连续稳定后才允许进入旧代码删除阶段。

# 12. 阶段 8：删除旧 ResearchAction

## 12.1 这个阶段是干什么的

在新路径完成切流和稳定运行后，删除旧 Action 架构及其特殊兼容分支，防止项目长期维护两套
Research 控制系统。

## 12.2 删除前置条件

必须同时满足：

1. 默认 Research 引擎已切为 `agent`；
2. Shadow 和小流量切流门槛已通过；
3. 回滚观察期结束；
4. 没有正在运行或等待恢复的 legacy Research Run；
5. 历史 Run 查看不依赖加载旧 Action DTO；
6. 新评测集覆盖旧阶段 2、3 的业务不变量；
7. 直接 SQL 尚未与旧 Action 清理耦合。

## 12.3 具体删除工作

### 12.3.1 删除 DTO

删除：

- `ResearchActionType`；
- `ResearchCompareAction`；
- `ResearchBreakdownAction`；
- `ResearchDrilldownAction`；
- `ResearchFilterFromResultAction`；
- `ResearchContributionAction`；
- `ResearchValidateHypothesisAction`；
- `ResearchFinishAction`；
- `ResearchPolicyDecision` 中的 Action 决策结构；
- `allowed_actions`；
- 只服务旧 Action 的字段。

保留或迁移：

- 时间绑定；
- 不可变筛选；
- Scope；
- 版本快照；
- Evidence 和 Hypothesis 中仍适用的通用含义。

### 12.3.2 删除服务

删除或清空引用后删除：

- `research/actions.py`；
- `research/action_batches.py`；
- 旧 `research/policy.py`；
- 旧 `research/policy_rules.py`；
- 旧 Research Policy Prompt；
- `materialize_research_action()`；
- `merge_research_action_requirements()`；
- Action 指纹和专用完成度函数。

### 12.3.3 删除编排特殊分支

删除：

- 旧 `ResearchPipeline`；
- `origin="research_action"`；
- Plan 单查询对 ResearchAction 的豁免；
- Composition 中旧 Policy 装配；
- `max_actions_per_iteration` 配置；
- 旧 Action 可见性和 Prompt 说明。

### 12.3.4 重写测试归属

旧测试分为：

- 业务不变量已迁移：删除旧测试；
- 新路径仍未覆盖：先补新测试再删除；
- 只验证旧 DTO 形态：直接删除；
- 真实问题：迁移到新评测脚本。

必须保证不是通过降低覆盖率完成删除。

### 12.3.5 处理历史运行记录

历史 derived_state、Trace 和 Result Artifact 按 JSON 展示，不要求重新反序列化为已删除 Action
DTO。需要恢复的旧 Run 在删除前完成、取消或明确终止。

不为历史 Run 建立旧 Action 到新 Tool 的运行时适配器。

### 12.3.6 清理文档和配置

- 第 37 号文档保留旧方案折叠历史；
- 本文标记删除完成日期；
- 删除旧环境变量和默认配置；
- 更新运行手册、错误码和 Trace 说明；
- 更新 API 示例和测试数据。

## 12.4 阶段产物

- 只包含新 Agent 的 Research 代码；
- 删除清单和引用扫描报告；
- 新回归测试；
- 历史 Run 处置记录；
- 更新后的配置和运维说明。

## 12.5 验收标准

1. 全仓库不存在旧 Action 类型引用；
2. 全仓库不存在 `origin="research_action"`；
3. 旧 Policy 和物化器文件删除；
4. Fast、Plan、Research 全量回归通过；
5. 历史 Run 仍可查看；
6. 没有隐藏 fallback 到旧 Pipeline；
7. 新 Agent 是唯一 Research 执行路径。

# 13. 阶段 9：受限 SQL 和资产回流

## 13.1 这个阶段是干什么的

在核心重构完成后，针对 Semantic Query Runtime 明确无法表达但确有业务价值的长尾问题，开放
受限只读 SQL。同时建立从正确 SQL 到正式语义资产的回流流程。

这不是核心重构完成条件。阶段 8 完成后即可以认为 Research Agent 主架构重构完成。

## 13.2 完成目标

1. 只有 `UNSUPPORTED_CAPABILITY` 可以申请 SQL；
2. SQL 经过权限、安全、dry-plan、资源和结果检查；
3. SQL Evidence 标记为 `EXPLORATORY`；
4. 高风险 SQL 支持多候选和选择；
5. SQL 使用和错误有独立评测；
6. 高频正确模式能够回流语义资产。

## 13.3 具体工作

### 13.3.1 定义 SQL 能力升级协议

Agent 不能直接看到 SQL Tool。只有 Semantic Runtime 返回：

~~~text
error_code = UNSUPPORTED_CAPABILITY
sql_escalation_allowed = true
~~~

Harness 才在下一轮暴露 `query_readonly_sql`。

普通错误不能升级：

- 参数错误；
- Scope 越界；
- 权限拒绝；
- 时间未绑定；
- 关系证明失败；
- fanout；
- 用户语义不明确。

### 13.3.2 SQL 安全和资源门禁

必须包括：

- 独立只读账号；
- AST 单语句；
- 只允许 SELECT / WITH；
- 禁止 DDL、DML、COPY、PRAGMA 和危险函数；
- 表列白名单；
- 租户行权限；
- 超时；
- 行数；
- 成本；
- 并发；
- dry-plan；
- 查询审计。

### 13.3.3 SQL 语义边界

- 不允许重新定义已有指标；
- 已有指标必须引用治理表达或经过对账；
- 不允许绕过不可变筛选和时间；
- 不允许访问 Scope 外表列；
- SQL 结果默认不能直接成为高置信度核心结论；
- 需要披露 SQL Evidence。

### 13.3.4 多候选和选择

只在以下节点使用 2 至 3 个候选：

- 最终核心结论依赖的 SQL；
- 多个 Join 或聚合策略冲突；
- 第一个 SQL 结果和语义基线对账冲突；
- 历史评测显示为高风险类型。

选择依据：

- AST 和执行计划；
- 执行错误；
- 结果结构；
- 语义总量对账；
- 候选一致性；
- 必要的模型选择器。

不在每个 SQL 调用默认生成多个候选。

### 13.3.5 Verified Query 和资产回流

建立流程：

~~~text
SQL 查询成功
  -> 进入候选库
  -> 评测或人工确认
  -> 判断能否提炼为通用定义
  -> 更新指标、筛选、术语、关系、层级、能力或示例
  -> 发布新 Semantic Contract
  -> 同类问题重新走 query_semantic_data
~~~

禁止未经确认的 SQL 自动进入正式语义资产。

### 13.3.6 SQL 独立评测

指标至少包括：

- SQL 升级率；
- 升级原因分布；
- 执行成功率；
- 对账通过率；
- 静默错误率；
- 多候选使用率；
- 平均 SQL 修复次数；
- SQL Evidence 进入核心结论比例；
- 回流资产比例；
- 回流后同类问题语义查询命中率。

## 13.4 阶段产物

- SQL 能力升级协议；
- `query_readonly_sql`；
- SQL 安全和资源门禁；
- SQL Evidence 分级；
- 多候选策略；
- Verified Query 审核和资产回流流程；
- SQL 独立评测。

## 13.5 验收标准

1. 非 `UNSUPPORTED_CAPABILITY` 无法获得 SQL Tool；
2. SQL 无法越过 Scope、权限、时间和筛选；
3. 所有 SQL Evidence 标记并披露；
4. 高风险 SQL 有额外验证；
5. SQL 静默错误率达到上线门槛；
6. 已验证至少一个 SQL 模式成功回流正式语义能力。

# 14. 跨阶段工作流

## 14.1 测试迁移策略

测试按以下顺序迁移：

1. 先复制业务不变量到新协议测试；
2. 新协议测试通过后保留旧测试，形成双覆盖；
3. Shadow 达标并切流；
4. 删除只验证旧实现形态的测试；
5. 保留真实问题和所有拒绝路径；
6. 每次删除旧模块前运行全仓库引用扫描。

建议测试层次：

| 层次 | 主要内容 |
| --- | --- |
| DTO | 输入、Scope、Evidence 引用和状态转换 |
| Builder | 语义查询到 AnalysisExecutionSpec |
| Runtime | 计划、证明、编译、执行和错误分类 |
| Tool | 参数、预算、幂等和 Observation |
| Harness | 多轮决策、停止、取消和恢复 |
| Report | 引用、数字、强度和限制 |
| E2E | 真实模型、真实数据源和真实语义资产 |
| Eval | 关键 Evidence、静默错误、成本和延迟 |

## 14.2 错误码治理

重构期间禁止新旧模块各自维护同义错误码。错误码应按阶段统一到：

- input；
- authorization；
- semantic planning；
- proof；
- compilation；
- execution；
- result validation；
- evidence；
- completion；
- budget；
- cancellation。

每个错误码记录：

- 用户可见分类；
- 模型可见摘要；
- 是否 retryable；
- retry same call 是否允许；
- 是否允许修改 HOW；
- 是否允许 SQL escalation；
- Trace 字段。

## 14.3 Trace 和指标

新 Research Trace 至少包含：

~~~text
research_start
premise_check
research_reasoning
research_tool_call
semantic_plan
plan_proof
query_execution
evidence_projection
hypothesis_assessment
completion_evaluation
report_validation
research_finish
~~~

Trace 不保存：

- 完整 SQL 大正文；
- 完整结果集；
- 未脱敏权限信息；
- 模型私有推理；
- 其他 Run 数据。

## 14.4 配置收敛

重构结束后保留的核心配置应尽量少：

- Research 引擎选择；
- 查询、模型、时间、结果和 Evidence 预算；
- Shadow 采样与数据集范围；
- 直接 SQL 开关和独立预算。

应删除：

- `max_actions_per_iteration`；
- Action 白名单；
- 每种 Action 的独立开关；
- 旧 Policy 修复次数配置；
- 旧 Action Prompt 版本。

## 14.5 文档更新规则

每个阶段完成后更新本文：

- 实际完成日期；
- 合并的实现路径；
- 与原计划的差异；
- 验收结果；
- 未完成项；
- 是否允许进入下一阶段。

第 37 号文档只维护目标架构和最终边界，不继续堆积实施任务明细。

# 15. 最终完成定义

Research 核心重构完成需要满足：

1. Research 后续步骤由 Tool Observation 动态驱动；
2. 不生成完整全局 Research DAG；
3. 单次复杂查询继续使用服务端确定性执行 DAG；
4. Evidence 依赖在执行后形成 DAG；
5. Agent 只看到少量稳定 Tool；
6. 语义查询是默认路径；
7. 每个 Query Plan 执行前均为 `PROVEN`；
8. 目标、时间、筛选、权限和冻结 Scope 不可漂移；
9. Tool Call、Observation、Evidence、Hypothesis 和 Report 可审计、可恢复；
10. 所有数据结论都有 Evidence；
11. 新路径没有导入旧 ResearchAction；
12. 旧 Action、物化器、Policy 和特殊 Plan 豁免已经删除；
13. Fast 和 Plan 回归通过；
14. Shadow 和真实问题评测达到切流门槛；
15. 线上不存在静默 fallback 到旧 Research；
16. 直接 SQL 即使尚未开放，也不影响核心重构完成。

## 15.1 阶段状态表

| 阶段 | 状态 | 完成日期 | 验收记录 |
| --- | --- | --- | --- |
| 0 基线、冻结和评测准备 | 已完成（旧架构持续冻结直至下线） | 2026-08-22 | 见第 4 章实施状态；基线 `research_agent_eval_baseline_20260822` |
| 1 新契约和迁移边界 | 已完成 | 2026-08-22 | 见 5.7；30 项契约/边界测试通过，默认仍 `legacy` |
| 2 Semantic Query Runtime | 已完成 | 2026-08-22 | 见 6.8；Dataset 243 五用例端到端验收通过，全部 `PROVEN` |
| 3 Research 通用工具 | 已完成 | 2026-08-22 | 见 7.7；20 项工具验收测试通过，Registry 恰含四个无 SQL 工具 |
| 4 状态、证据依赖和恢复 | 已完成 | 2026-08-23 | 见 8.8；16 项状态/恢复测试通过，提交边界 + 恢复 + 取消落地，无新表 |
| 5 Research Agent Harness | 已完成 | 2026-08-23 | 见 9.7；17 项 Harness 测试通过，动态循环 + 前提确认 + 提交边界/恢复/取消接线落地，新路径仅在测试运行 |
| 6 假设、完成度和报告 | 待开始 | - | - |
| 7 Shadow 双跑、评测和切流 | 待开始 | - | - |
| 8 删除旧 ResearchAction | 待开始 | - | - |
| 9 受限 SQL 和资产回流 | 待开始 | - | - |

## 15.2 建议的第一个实施任务

第一个开发任务不是实现 Agent，而是阶段 0：

1. 整理并扩展 `research_stage3` 真实问题；
2. 定义结构化 Gold 和禁止结论；
3. 让旧 Research 输出统一评测记录；
4. 跑出当前基线；
5. 冻结新增 ResearchAction。

只有完成这些工作，后续每次重构才有可验证的方向。
