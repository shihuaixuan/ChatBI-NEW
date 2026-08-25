# 39A. 统一分析 Agent、计划执行与 Evidence 设计

> 状态：设计稿。
>
> 编号说明：仓库已经存在并被引用的 39-research-action-removal-inventory.md，
> 该文档是旧 ResearchAction 删除的历史执行记录，不能覆盖或改号。本文作为
> 39 号系列的后续设计文档，编号为 39A。

# 0. 文档定位

本文沉淀当前 Fast、Plan、Research 在模式路由、基础数据获取、动态规划、
Evidence 和报告校验方面的问题，并给出统一目标架构。

本文主要回答：

1. 当前 Research 的核心问题是动态循环本身，还是模式路由不准确；
2. Fast、Plan、Research 是否可以使用同一套分析执行流程；
3. Question Rewrite、Semantic Binding、Execution Requirement 和 Agent 的边界；
4. 统一流程更接近 ReAct 还是 Plan-and-Solve；
5. Research 如何批量获取基础 Evidence，再根据结果有限追加计划；
6. Evidence 和报告校验如何统一；
7. 如何分阶段迁移，避免一次性重写全部链路。

本文不修改语义事实来源。所有执行继续消费已发布 DatasetSchema 和冻结版本，
不允许模型在运行过程中重新绑定资产或扩大 Scope。

# 1. 核心结论

目标架构：

~~~text
用户问题
  -> Question Rewrite
  -> Semantic Binding
  -> 冻结 Execution Requirement
  -> 生成当前可确定的 AnalysisPlan
  -> 根据计划完整性识别执行模式
  -> 统一 Plan Executor
  -> 注册 Evidence
  -> Completion Evaluation
       -> 已满足：生成 Structured Findings
       -> 未满足且存在结果依赖：Research 追加 AnalysisPlan
       -> 无能力、失败或预算不足：明确结束
  -> 结构化证据校验
  -> 服务端生成最终报告
~~~

设计裁决：

1. Analysis Agent 从冻结的 Execution Requirement 之后开始。
2. 统一架构以 Plan-and-Solve 为主。
3. Fast 是单节点计划；Plan 是固定 DAG；Research 是允许根据 Evidence 追加
   有限计划的 Plan-and-Solve。
4. 当前 Research 属于 ReAct 式逐轮取证，不应继续承担执行前已经确定的基础查询。
5. 模式判断以计划结构为依据，不以“分析原因”“定位异常”等措辞为依据。
6. Fast、Plan、Research 统一产生 Evidence。
7. 最终报告改用结构化证据引用，不再依赖自然语言字符匹配证明正确性。

# 2. 当前问题

## 2.1 固定执行拓扑被错误路由到 Research

问题示例：

> 2026年6月29日总GMV较6月28日下降，请分析主要原因，分别验证总订单数、
> 总商品件数和订单平均客单价的变化，并定位异常档口。

该问题执行前已经明确：

- 目标指标：总GMV；
- 驱动指标：总订单数、总商品件数、订单平均客单价；
- 时间：2026年6月29日与6月28日；
- 维度：档口；
- 计算：差值、贡献和总量对账；
- 输出：驱动指标变化和异常档口。

完整执行拓扑可以提前确定，因此应进入 Plan。当前如果仅因“分析主要原因”
或“定位异常”进入 Research，会把固定多查询任务变成多轮模型决策。

## 2.2 Research 逐轮选择已确定的基础查询

当前 Research 提示强调：

- 每轮至少调用一个工具；
- 根据当前 Evidence 决定下一个工具；
- 先验证前提，再决定后续方向；
- 证据满足后调用 finish_research；
- 纯文本回答不构成完成。

因此固定问题容易形成：

~~~text
查询GMV
  -> 模型判断
  -> 查询订单数
  -> 模型判断
  -> 查询件数
  -> 模型判断
  -> 查询客单价
  -> 模型判断
  -> 查询档口贡献
  -> 模型提交结论
~~~

模型调用被用于重复决定已经确定的依赖。

## 2.3 独立查询没有真正并行执行

Research 可以在同一轮提交多个独立查询，但当前 Harness 复用请求作用域 Session，
工具批次仍固定串行执行。

批量获取首先应解决“一次规划、统一调度”，不应在 Session、事务、Trace 和
ResultStore 尚未隔离时直接启用线程并发。

## 2.4 停滞和预算机制承担正常流程控制

固定问题误入 Research 后，完成依赖模型正确执行收口协议，容易出现：

- Evidence 已满足但继续查询；
- finish_research 校验失败后不能正确修正；
- 重复查询被去重但仍形成停滞；
- 最终以 no_new_direction 或 budget_exhausted 部分结束。

## 2.5 Evidence 与结论缺少精确引用

当前 Evidence 已记录指标、维度、逻辑列、样本行、统计、依赖、版本和局限，
但 Claim/Finding 主要通过 evidence_ids 整体引用 Evidence，没有精确声明：

- 使用哪一行；
- 使用哪个指标；
- 使用哪个逻辑列；
- 使用 current、previous、difference、share 还是 contribution；
- 维度值和数值是否来自同一行。

## 2.6 报告校验依赖字符匹配

当前校验器从自然语言提取数字，再在引用 Evidence 的样本数字集合中查找近似值。

例如：

~~~text
Evidence：0.390191...
报告：39.0%
校验：0.390191... × 100 与 39.0 比较
~~~

比例换算合理，但只能证明 Evidence 中存在一个可换算数字，不能证明：

- 39%属于报告提到的档口；
- 39%属于报告提到的指标；
- 它来自 contribution 列；
- 档口和数值位于同一行。

因果校验同样依赖“导致、造成、根因”等关键词，不能可靠表达结论关系。

# 3. 设计原则和业务不变量

## 3.1 统一流程，不合并职责

统一：

- Execution Requirement；
- AnalysisPlan；
- Plan Executor；
- ResultStore；
- Evidence；
- Completion Evaluation；
- Structured Findings；
- 报告生成。

保留边界：

- Question Rewrite 只规范用户表达；
- Semantic Binding 只绑定资产、时间和筛选；
- Planner 只能组织已绑定资产；
- Executor 只能执行已证明计划；
- Research 只能在冻结 Scope 内追加计划；
- Report Builder 只能从 Evidence 读取数据。

## 3.2 目标和 WHAT 不可漂移

进入 Analysis Agent 前冻结：

- 用户目标；
- 目标指标；
- 时间绑定；
- 不可变筛选；
- 数据集和租户；
- 允许使用的指标、维度和关系；
- 权限与语义版本。

Research 可以调整查询方式和 Scope 内分析方向，不能修改上述内容。

## 3.3 模式由执行信息何时确定来区分

| 模式 | 执行信息 | 控制方式 |
| --- | --- | --- |
| Fast | 单个查询语义已确定 | 单节点计划 |
| Plan | 全部节点和依赖可提前确定 | 固定 DAG |
| Research | 后续分析方向或新增节点依赖 Evidence | 有限追加计划 |

## 3.4 确定性工作不交给循环模型重复判断

以下内容已确定时直接进入初始计划：

- 前提指标比较；
- 用户明确要求的指标比较；
- 用户明确要求的维度分解；
- 差值、增长率、占比和贡献计算；
- 总量与分项对账；
- 完整拓扑中已知的下钻。

只有结果依赖内容允许触发 Research 追加计划。

## 3.5 结论引用必须结构化

正确性通过以下关系证明：

~~~text
Finding
  -> Evidence ID
  -> Row Selector
  -> Logical Column(asset_ref + value_role)
  -> Result Field
  -> Actual Value
~~~

最终自然语言只负责展示，不作为事实关系的规范来源。

# 4. Agent 边界

## 4.1 Analysis Agent 从哪里开始

~~~text
Question Rewrite             前置理解
  -> Semantic Binding        前置治理
  -> ExecutionRequirement    冻结可信输入
  -> Analysis Agent Runtime  开始
~~~

Question Rewrite 和 Semantic Binding 即使调用模型，也不是 Analysis Agent 动态循环，
因为它们没有根据查询 Observation 持续选择动作。

## 4.2 Analysis Agent State

统一状态至少包含：

~~~text
AnalysisAgentState
  - requirement
  - frozen_scope
  - evidence_requirements
  - active_plan
  - completed_plan_nodes
  - evidences
  - recent_failures
  - completion_gaps
  - budget
  - plan_revision
  - status
~~~

requirement 和 frozen_scope 不可修改，其余状态由服务端根据执行事实更新。

## 4.3 外层对话与内部分析

Conversation Agent 负责：

- 对话上下文；
- 问题重写；
- 语义澄清；
- 启动分析任务；
- 返回结果。

Analysis Agent 负责：

- 规划当前证据批次；
- 执行查询和计算；
- 观察 Evidence；
- 根据 Completion Gap 追加计划；
- 生成 Structured Findings。

# 5. Plan-and-Solve 与 ReAct

## 5.1 当前 Research

当前流程是：

~~~text
Reason
  -> Act
  -> Observe
  -> Reason
  -> Act
  -> Observe
  -> finish_research
~~~

它属于 ReAct 式循环，适合无法提前确定下一步的探索，不适合大量可提前证明的固定查询。

## 5.2 目标架构

~~~text
Plan
  -> Execute
  -> Evidence
  -> Completion Evaluation
  -> Structured Findings
~~~

| 模式 | 目标范式 |
| --- | --- |
| Fast | 单节点 Plan-and-Solve |
| Plan | 一次规划的 Plan-and-Solve |
| Research | 基于 Evidence 有限 Replan 的 Plan-and-Solve |

Research 的核心不再是“每轮选择一个工具”，而是“Completion Gap 是否要求新增计划”。

## 5.3 Replan 触发条件

允许追加计划：

- 下一步筛选值来自上一批 Evidence；
- 下一步分析维度必须根据异常程度选择；
- 对账失败，需要增加验证节点；
- 查询为空或截断，需要在冻结 Scope 内调整方式；
- Evidence 冲突，需要补充反证或细分。

不允许追加计划：

- 初始固定计划尚未执行完成；
- 只是重复确认已有结果；
- 失败没有可修正方向；
- 需要 Scope 外资产；
- Evidence Requirement 已满足；
- 只是为了丰富报告文字。

# 6. 统一执行流程

## 6.1 Question Rewrite

只规范问题表达，不绑定资产、不选择模式、不生成计划。

## 6.2 Semantic Binding

输出：

- 目标和驱动指标；
- 维度和筛选；
- 时间角色；
- 比较和分析要求；
- 已发布层级、驱动和贡献关系；
- 绑定歧义。

本阶段不根据原因分析类措辞直接决定 Research。

## 6.3 Execution Requirement

统一冻结输入建议包含：

~~~text
goal
target_metric_refs
driver_metric_refs
dimension_refs
time_bindings
immutable_filters
requested_analyses
frozen_scope
semantic_relationships
evidence_requirements
output_requirements
version_snapshot
budget
~~~

Evidence Requirement 描述完成目标需要什么证据，不指定必须调用哪个工具。

## 6.4 Initial Plan Decomposition

Planner 读取冻结 Requirement，生成当前可确定节点和依赖。

Planner 只能引用：

- 已绑定资产；
- 冻结时间角色；
- 不可变筛选；
- 已发布关系；
- 其他节点的结构化输出。

不能输出 SQL、物理字段、未绑定资产或扩大 Scope。

## 6.5 Mode Classification

模式识别移动到初始计划生成之后：

~~~text
一个查询节点，且无后续计算
  -> Fast

所有查询、计算、选择和依赖均已确定
  -> Plan

存在无法提前确定的分析方向或新增节点
  -> Research
~~~

筛选值来自上游结果并不必然属于 Research。如果 Planner 已能声明“选择贡献最低档口并
按商品分解”，完整 DAG 仍可由 Plan 执行。

只有“根据结果决定分析商品、渠道还是客户”这种节点类型未确定的情况进入 Research。

## 6.6 Unified Plan Executor

三种模式共用：

- Semantic Query Planning；
- 结构证明；
- SQL 编译；
- 查询执行；
- 确定性计算；
- ResultStore；
- Trace；
- Evidence 注册。

Executor 只执行已证明的 AnalysisPlan，不重新解释用户问题。

## 6.7 Completion Evaluation

每批执行后由服务端判断：

~~~text
全部Evidence Requirement满足
  -> Structured Findings

存在未执行固定节点
  -> 继续现有计划

存在Completion Gap且下一节点依赖Evidence
  -> Research追加计划

需要Scope外资产
  -> 能力不足或重新绑定

失败且不可修正
  -> 明确失败或部分完成
~~~

完成判断不能只按 Evidence 数量和资产交集，还应检查 Evidence 类型、value role、
依赖、对账和目标覆盖。

# 7. 基础 Evidence 批量获取

## 7.1 定义

基础 Evidence 是读取查询结果前就能由冻结 Requirement 确定的查询和计算结果。
它不是 Research 新提出的假设，而是用户明确需求的固定输入。

## 7.2 GMV 问题的基础计划

~~~text
Q1：总GMV current/previous 比较
Q2：总订单数 current/previous 比较
Q3：总商品件数 current/previous 比较
Q4：订单平均客单价 current/previous 比较
Q5：按档口比较以上指标
C1：计算档口GMV变化贡献
C2：档口贡献与总GMV变化对账
~~~

批次：

~~~text
批次1：Q1、Q2、Q3、Q4、Q5
批次2：C1、C2
批次3：仅在确有结果依赖时追加
~~~

## 7.3 批量不等于立即并发

实施顺序：

1. Planner 一次生成所有基础节点；
2. Executor 按 DAG 批次执行，消除节点间模型决策；
3. 完成 Session、事务、Trace 和 ResultStore 隔离；
4. 再对同批无依赖节点启用并行。

即使初期仍串行执行，只要节点之间不重复调用模型，也能降低成本和停滞风险。

# 8. 统一 Evidence

## 8.1 所有模式产生 Evidence

~~~text
Fast QueryResult     -> Evidence
Plan NodeResult      -> Evidence
Research NodeResult  -> Evidence
Compute Result       -> Evidence
~~~

Evidence 是执行事实，不是 Research 专属概念。

## 8.2 Evidence 职责

Evidence 记录：

- 当前 Run 和来源节点；
- 工具调用和 ResultStore 引用；
- 指标、维度、时间和筛选；
- 逻辑列到结果字段映射；
- 统计和受控样本；
- 上游 Evidence；
- Evidence 等级；
- 语义与权限版本；
- 截断、空结果和探索属性。

Evidence 不保存最终自然语言结论。

## 8.3 Evidence Requirement 增强

需要表达：

- 指标比较；
- 驱动指标比较；
- 维度分解；
- contribution；
- reconciliation；
- 反证和冲突验证；
- 指定 value role 和最低覆盖。

不能只用 required_asset_refs 和 minimum_count 近似所有完成条件。

# 9. Structured Findings 与报告

## 9.1 当前顺序的问题

~~~text
模型写自由文本
  -> 正则提取数字和关键词
  -> 在Evidence数字集合中查找
~~~

服务端需要从文字反向猜测结构关系。

## 9.2 Finding 契约示例

~~~json
{
  "subject": {
    "dimension_ref": "DIMENSION:278:246",
    "value": "100013"
  },
  "metric_ref": "METRIC:271:246",
  "value_role": "contribution",
  "relationship": "contribution",
  "evidence_value_ref": {
    "evidence_id": "evidence:research-query-xxx",
    "row_selector": {
      "DIMENSION:278:246": "100013"
    },
    "asset_ref": "METRIC:271:246",
    "value_role": "contribution"
  },
  "confidence": "high"
}
~~~

## 9.3 服务端生成数据正文

服务端：

1. 校验 Evidence 属于当前 Run；
2. 根据 Row Selector 唯一定位行；
3. 根据 asset_ref 和 value_role 定位逻辑列；
4. 读取真实值；
5. 格式化金额、数量、比例和差值；
6. 使用受控模板生成正文。

例如：

~~~text
Evidence原值：0.3901919539647385
格式化：39.0%
正文：档口100013贡献了总GMV下降的39.0%。
~~~

## 9.4 保留的硬校验

- Evidence 存在且属于当前 Run；
- Evidence 与 Requirement 版本一致；
- 指标、维度和筛选位于冻结 Scope；
- Row Selector 唯一定位数据行；
- 逻辑资产、value role 和结果字段匹配；
- contribution 结论引用 contribution 或 reconciliation Evidence；
- 截断数据不能支持绝对结论；
- EXPLORATORY Evidence 必须披露局限；
- 高置信结论不能引用冲突或无法判断的 Evidence。

## 9.5 降级或移除

- 从最终自然语言正则提取全部数字；
- 在 Evidence 全部数字中做集合匹配；
- 用关键词推断因果关系；
- 要求模型自行抄写和格式化全部数值。

迁移期间可以保留现有文本校验作为兼容门禁，但新路径不能依赖它证明核心正确性。

# 10. 典型模式判断

| 用户问题 | 模式 | 原因 |
| --- | --- | --- |
| 查询6月29日总GMV | Fast | 单查询 |
| 比较GMV、订单数、件数和客单价，并按档口定位异常 | Plan | 全部依赖已确定 |
| 按档口、渠道和商品分别分析GMV变化 | Plan | 分析方向明确 |
| 找出下降最大的档口 | Plan | 排序和Top 1固定 |
| 找出下降最大档口，再按商品分析 | Plan | 完整DAG已确定 |
| 找出下降最大档口，再决定分析商品、渠道还是客户 | Research | 节点类型依赖Evidence |
| 先判断哪个维度最异常，再沿该维度下钻 | Research | 后续维度依赖Evidence |

# 11. 统一运行伪代码

~~~python
state = AnalysisAgentState.from_requirement(requirement)
state.active_plan = planner.create_initial_plan(requirement)
state.mode = classify_plan(state.active_plan)

while True:
    execution = executor.execute_ready_batches(state.active_plan)
    state.register_evidences(execution.evidences)
    state.register_failures(execution.failures)

    completion = completion_evaluator.evaluate(state)
    if completion.satisfied:
        findings = finding_builder.build(state)
        return report_builder.render(findings, state.evidences)

    if state.active_plan.has_unexecuted_nodes():
        continue

    if state.mode != "research":
        return finalize_incomplete(completion, state)

    delta_plan = planner.create_delta_plan(
        requirement=state.requirement,
        evidences=state.evidences,
        completion_gaps=completion.gaps,
        failures=state.recent_failures,
        budget=state.remaining_budget,
    )
    state.append_plan(delta_plan)
~~~

业务不变量必须由 DTO 和服务端校验器表达，不能只写在 Prompt 中。

# 12. 分阶段实施

## 阶段1：修正模式路由

目标：固定拓扑问题不再误入 Research。

工作项：

1. Semantic Binding 不依据原因分析类措辞直接决定 Research；
2. 初始计划生成后判断计划完整性；
3. 区分结果值依赖和节点类型依赖；
4. 当前 GMV 问题稳定进入 Plan；
5. 真正动态问题保持 Research。

验收：

- 固定指标、固定维度归因进入 Plan；
- 固定 Top N 后按固定维度下钻仍可进入 Plan；
- 根据结果选择下一分析维度进入 Research；
- 不以关键词作为唯一模式依据。

## 阶段2：基础 Evidence 计划

目标：Research 不逐轮决定执行前已知的查询。

工作项：

1. Planner 根据 Evidence Requirement 生成基础计划；
2. Research 先执行全部当前可确定节点；
3. 只有 Completion Gap 需要结果决策时才调用 Replan；
4. 去除每完成一条基础查询就调用模型的依赖。

验收：

- 模型调用次数下降；
- 基础节点覆盖用户明确要求；
- Evidence 满足后不再无目的探索；
- no_new_direction 不再是固定问题的主要终态。

## 阶段3：统一 Executor 与 Evidence

目标：Fast、Plan、Research 使用同一结果和 Evidence 协议。

工作项：

1. Plan 节点统一注册 Evidence；
2. Fast 单节点结果形成 Evidence；
3. Completion Evaluation 只读取 Requirement 和 Evidence；
4. 移除 Research 专属重复结果投影。

## 阶段4：Structured Finding

目标：建立行、指标、value role 和 Evidence 的精确引用。

工作项：

1. 定义统一 EvidenceValueRef 和 RowSelector；
2. Claim/Finding 引用精确结果值；
3. 服务端校验行列对应；
4. 服务端格式化数据。

验收：

- 档口和数值错配被拒绝；
- 0.3900 到 39.0% 由服务端格式化；
- 模型不能引用同一 Evidence 的其他行数字绕过校验。

## 阶段5：报告生成切换

目标：报告以 Structured Findings 和真实 Evidence 为输入。

工作项：

1. 服务端生成数据句；
2. 模型只负责受限分析组织或文字润色；
3. 文本数字校验降级为兼容检查；
4. 因果和贡献关系通过结构字段判断。

## 阶段6：安全并行

前置条件：

- 每个任务拥有隔离 Session 或明确事务边界；
- ResultStore 无共享可变写入；
- Trace 和 Tool Call 持久化支持并发；
- 取消和超时独立传播；
- 计划节点具备幂等键。

# 13. 测试与评测

## 13.1 模式边界

- 单查询；
- 固定多指标比较；
- 固定贡献和对账；
- 结果值驱动的固定下钻；
- 结果驱动的分析方向选择；
- Scope 外资产请求。

## 13.2 计划完整性

- 用户明确要求的指标不得漏计划；
- 无依赖节点处于同一批；
- Evidence 依赖节点位于后续批次；
- 计划不得使用未绑定资产；
- Delta Plan 不得修改冻结 WHAT。

## 13.3 Evidence 与完成度

- Evidence 类型和 value role 匹配；
- contribution 完成对账；
- 数量满足但类型错误时不得完成；
- 空结果、截断和冲突形成明确 Gap；
- 同一 Evidence 不得错误重复计数。

## 13.4 Structured Findings

- 正确行列引用通过；
- 同一 Evidence 内错配档口和数值被拒绝；
- 百分比、金额和差值由服务端正确格式化；
- 跨 Run、跨版本和跨 Scope 引用被拒绝；
- 普通比较 Evidence 不得提交 contribution 或 causal 关系。

## 13.5 真实问题回归

至少覆盖：

1. GMV、订单数、件数、客单价和档口贡献固定分析；
2. 客户GMV、客户订单数、购买件数和新增客户固定分析；
3. 找出异常档口后按明确商品维度下钻；
4. 找出异常档口后动态选择商品、渠道或客户方向；
5. 库存风险按商品和档口分析，验证跨模型同名维度兼容性。

# 14. 风险与约束

## 14.1 不实现成一个大提示词

禁止让一次模型调用同时负责问题改写、资产绑定、模式选择、SQL、分析和自由文本报告。
统一的是协议和执行主线，不是取消治理边界。

## 14.2 不删除 Research 动态能力

目标是减少不必要的 ReAct 轮次，不是把所有开放问题强制转换成固定 Plan。

## 14.3 不立即启用共享 Session 并发

先完成一次规划和批次执行，再解决执行隔离，避免事务、Trace 和结果写入竞争。

## 14.4 不同时替换全部契约

按“路由、基础计划、统一 Evidence、Structured Finding、报告切换、并行执行”
顺序迁移，每个阶段保持主路径可回归。

# 15. 最终目标

Fast、Plan、Research 是同一分析执行框架的三种运行特征：

~~~text
Fast
  = 单节点计划，不重新规划

Plan
  = 完整固定计划，不重新规划

Research
  = 初始计划执行后，允许根据Evidence有限追加计划
~~~

统一后的收益：

- 路由依据从自然语言措辞变为计划结构；
- 固定问题不再消耗多轮 Research 模型调用；
- Research 保留真正需要的结果驱动能力；
- 三种模式共用执行、Evidence 和报告链路；
- Completion Evaluation 成为统一停止依据；
- 结论从字符匹配升级为结构化证据引用；
- 查询、计算、证据、结论和报告形成完整审计关系。

