# 40. Research 执行循环与统一 Agent 范式问题讨论

> 状态：统一 Plan-and-Solve Runtime 设计修正版。
>
> 本文沉淀一次连续设计讨论中形成的三个问题。三个问题相互关联，但不是同一个问题，
> 需要分别说明现状、初步解决方案和设计理由。

# 0. 文档范围

本次讨论包含三个问题：

1. 条件前提确认是否应该作为 Research `run()` 的固定逻辑；
2. 服务端能否根据 Evidence 字段判断“证据需求已经满足”，并据此强制结束；
3. Fast、Plan、Research 是否错误地被建模为三个模式，以及如何统一为一个 Agent Runtime。

三个问题的关系如下：

~~~text
问题一：具体研究任务被写入通用执行循环
  -> 需要把“做什么”交给 Planner

问题二：服务端把结构覆盖当成内容充分
  -> 需要拆分结构校验和语义判断

问题三：同一 PS Agent 的不同执行结果被拆成三个模式
  -> 需要删除模式分发，统一计划、执行、评估和回复框架
~~~

本文先确定设计方向，不直接给出完整实施细节。

# 1. 问题一：条件前提确认是否应该放在 Research `run()` 中

## 1.1 问题是什么

当前 Research 主循环在模型推理前包含条件前提确认逻辑：

~~~text
如果存在 premise_to_verify
  且当前没有 initial_plan
  且 premise_result 尚不存在
    -> 服务端构造并执行固定查询
    -> 判断前提是否成立
~~~

这一逻辑主要服务于带明确前提的问题，例如：

> 为什么销售额下降？

该问题隐含“销售额确实下降”的前提。如果数据表明销售额没有下降，就不应继续研究下降
原因。

但并非所有分析问题都有待验证前提，例如：

- “分析最近的销售情况”；
- “查看各地区销售额并分析差异”；
- “比较各渠道的订单量和客单价”；
- “查询最近七天销售额并给出总结”。

对于这些问题，`premise_to_verify` 为 `None`，代码不会执行前提查询。虽然没有额外查询，
但架构问题仍然存在：

> Research 的通用 `run()` 知道并直接实现了“前提确认”这一具体研究任务。

通用执行循环本应负责计划调度、节点执行、状态、预算和终态，不应直接决定某类问题必须
先查什么数据。

当前代码同时存在两条前提确认路径：

1. 有 `initial_plan` 时，通过首轮计划中的固定前提节点处理；
2. 没有 `initial_plan` 时，通过 `_run_premise_preflight()` 补做固定查询。

这说明当前实现处于新旧路径并存状态，前提确认还没有完全进入统一规划协议。

## 1.2 初步解决方案

将前提确认从 Research `run()` 的固定执行分支中移除，但不能只是把原有 `if` 逻辑搬到
另一个名为 Planner 的组件中。

`premise_to_verify` 可以继续保留。它表达的是用户问题中已经识别出的待验证语义，例如
“销售额下降”，不是固定查询的开关，也不规定必须生成某个节点 ID 或固定的
`query -> compute -> analyze` 流程。

更准确的表达是：

~~~text
premise_to_verify
  -> 转换为 Planner 必须处理的 Evidence Gap
  -> Planner 根据已有 Evidence、查询能力和后续分析目标决定如何处理
~~~

Planner 可以选择：

- 已有 Evidence 已经证明或否定前提：直接复用，不新增查询；
- 第一个原因分析查询能够同时确认前提：合并执行，不额外生成前提查询；
- 现有计划无法确认前提：新增独立查询或确定性计算任务；
- 当前 Scope 内无法确认：形成明确的数据缺口和限制，不伪造结论。

例如“为什么销售额下降”可以生成合并计划：

~~~text
query：查询本期和上期各地区销售额
  -> compute：汇总总变化并计算地区贡献
  -> analyze：同时判断总销售额是否下降，以及哪些地区贡献了变化
~~~

也可以在已有地区 Evidence 不能可靠汇总时生成独立计划：

~~~text
query：查询目标指标的当前值和对比值
  -> analyze：判断前提是否成立
~~~

是否需要 `compute` 节点取决于查询结果是否已经提供差值、增长率或变化方向，不能机械生成。

执行结果仍分为：

- 前提成立：允许提交依赖该前提的结论，并继续原因分析；
- 前提不成立：说明实际数据与用户描述不一致；
- 数据不足：说明限制，或者根据明确缺口追加计划。

当用户问题没有明确前提时，不产生该 Evidence Gap。Planner 直接围绕用户目标规划当前可
确定任务。

从数据执行顺序看，第一个可执行节点通常仍然是 `query`；从控制顺序看，必须先由 Planner
确定“查询什么、查询目的是什么、是否与其他分析合并”，再由 Runtime 执行。这里不是在
查询前增加一段前提处理，而是取消 Runtime 自行决定查询内容的权力。

完成迁移后，通用执行循环不再包含：

- `_run_premise_preflight()` 专用路径；
- 对固定 `premise-confirmation` 节点 ID 的业务判断；
- “有前提问题”和“无前提问题”的执行分支。

## 1.3 为什么这样设计

### 前提确认是计划内容，不是执行约束

“是否需要确认销售额下降”取决于用户问题；“如何安全执行一个查询节点”与具体问题无关。
前者属于 Planner，后者属于 Runtime。

这里的 Planner 不是固定规则的存放位置。所有请求都通过同一个 Planner 接口生成任务组合；
服务端负责把计划绑定到冻结语义资产，并校验 Scope、权限、依赖、预算和执行能力。简单请求
只会生成更少的节点，但不能因此进入另一个规划器或执行框架。

统一控制关系为：

~~~text
统一 Planner 生成首次计划或计划增量
  -> 服务端校验并编译计划
  -> Runtime 确定性执行 query / compute
  -> 模型根据 Evidence 判断内容和下一步方向
~~~

### 避免继续向 `run()` 增加研究类型分支

如果前提确认可以直接写入 `run()`，后续异常验证、反证检查、贡献度验证、趋势稳定性检查
也可能继续以条件分支进入通用循环，使 `run()` 同时承担规划和执行职责。

### 所有分析请求都可以使用同一计划协议

有前提的问题多一个待解决的 Evidence Gap，但不一定多一个独立任务；无前提的问题没有该
Gap。两类问题只需要不同计划，不需要不同执行过程。

### 避免把规则移动误认为完成计划化

以下实现不属于本阶段目标：

~~~python
if premise_to_verify:
    return build_fixed_premise_plan()
~~~

这种实现只是把原有固定分支从 `run()` 移到 Planner，仍由规则决定固定查询，没有获得根据
已有 Evidence 合并任务、复用结果或调整分析方向的能力。阶段三完成的判据应是 Runtime 不
识别前提类型，而 Planner 可以通过统一计划协议用多种合法方式解决同一个前提缺口。

# 2. 问题二：服务端如何确定 Evidence Requirement 已经满足

## 2.1 问题是什么

当前 `_requirements_satisfied()` 调用 `evaluate_completion()`，根据 Evidence 的结构字段
判断是否存在 Completion Gap。

当前评估主要检查：

- Evidence 是否包含指定指标或维度；
- Evidence 数量是否达到 `minimum_count`；
- Evidence 是否为受治理证据；
- 是否存在 contribution 或 reconciliation 依赖关系；
- `premise_result` 字段是否已经存在。

如果所有规则均通过，就将 `satisfied` 设为真，并提醒模型：

> 证据需求已全部满足，请尽快调用 `finish_research`。

超过宽限轮次仍未调用 `finish_research` 时，服务端会强制停止。

问题在于，服务端当前检查的是字段、数量和关系标签，只能确定 Evidence 是否满足结构契约，
不能判断 Evidence 内容是否真正回答了用户问题。

例如用户问：

> 为什么销售额下降？

一条 Evidence 如果包含销售额和地区字段，就可能覆盖目标指标和维度要求。但结果内容可能
只有各地区当前销售额，没有：

- 上期销售额；
- 销售额变化量；
- 各地区对总下降的贡献；
- 订单量、客户数或客单价等驱动信息；
- 能够支持原因解释的数据关系。

此时字段满足，内容并没有回答“为什么下降”。

服务端仅根据当前结构还无法判断：

- 查询结果是否真正回答用户问题；
- Evidence 是否支持准备提交的结论；
- 多条 Evidence 是否内容重复；
- 观察到的是共同变化、相关线索还是可以陈述的贡献关系；
- 是否遗漏明显的分析方向；
- 数据冲突是否已经处理；
- 是否还需要新增查询才能形成可信回复。

因此，当前“证据需求满足”的准确含义应该是：

> 预定义的最低结构覆盖条件已经满足。

它不能直接等同于：

> 当前内容已经足以回答用户问题。

## 2.2 初步解决方案

将完成判断拆成“服务端结构校验”和“模型内容判断”两个层次。

这里的“完成评估契约”不是增加一个固定执行步骤，也不是把现有
`evaluate_completion()` 换一个名称。它定义的是每轮 Evidence 产生后，Planner、模型评估
和 Runtime 之间如何交换“能否结束、缺什么、下一步做什么”的结构化信息。

控制过程为：

~~~text
Executor 产出 Evidence
  -> 服务端生成 StructuralCoverage
  -> 模型生成 SemanticAssessment
  -> Runtime 根据结构化状态选择 answer、追加计划或受限结束
~~~

### 服务端检查最低结构覆盖

服务端继续检查可以确定的事实：

- 是否执行过有效查询；
- 用户明确指定的指标和维度是否被覆盖；
- 必须执行的计算和对账是否完成；
- Evidence 是否真实存在并属于当前 Run；
- Evidence 是否符合冻结 Scope、权限和版本；
- Evidence 引用和依赖关系是否合法；
- 最终结论引用的 Evidence ID 是否有效。

建议将结果表达为：

~~~text
StructuralCoverage
  - minimum_requirements_met
  - covered_requirements
  - missing_requirements
  - invalid_evidence_refs
~~~

该结果是模型评估和结束申请的输入，不直接代表内容充分。

### 模型判断内容是否足够

模型需要同时读取用户问题和实际 Evidence 内容，输出结构化判断：

~~~text
SemanticAssessment
  - status: answerable | explicit_gap | no_new_direction | data_insufficient
  - supported_findings
  - unresolved_gaps
  - proposed_plan_additions
  - limitations
~~~

各状态的含义为：

| 状态 | 含义 | Runtime 行为 |
| --- | --- | --- |
| `answerable` | 当前 Evidence 足以回答用户问题 | 校验 Findings 和 Evidence 引用后生成回复 |
| `explicit_gap` | 存在明确、可描述的内容缺口 | 校验计划增量；策略允许时追加计划 |
| `no_new_direction` | 内容仍不充分，但没有新的合法执行方向 | 带限制结束 |
| `data_insufficient` | Scope 内数据或能力不足，无法补齐 | 说明数据限制后结束 |

`proposed_plan_additions` 不是任意工具调用列表。它必须描述目标、节点类型、输入依赖和预期
补齐的 Gap，并由服务端编译、校验后才能进入执行计划。

模型负责判断：

- 当前 Evidence 是否真正回答了用户问题；
- 结论是否能由 Evidence 支持；
- 是否存在重复、冲突或明显遗漏；
- 如果不足，具体还缺什么；
- 缺口能否在冻结 Scope 内通过新增计划补足；
- 如果不能继续，应如何说明限制。

如果模型认为内容不足，不能只返回“还不够”，必须说明明确缺口；如果建议继续，还必须输出
可执行的计划增量。

### 最终完成条件

提交“证据充分”的最终回复至少需要：

~~~text
最低结构覆盖满足
  AND
模型判断内容足以回答用户问题
  AND
最终结论通过 Evidence 引用校验
~~~

结构条件不满足时，模型不能提交充分结论；结构条件满足时，服务端也不能替代模型判断内容
已经充分。

### 调整提醒和强制停止

服务端可以在最低结构覆盖满足时提醒模型：

~~~text
当前 Evidence 已覆盖预设的最低结构要求。请结合实际数据判断是否足以回答用户问题：
如果足够，请提交结论；如果不足，请明确说明缺口并生成下一步计划。
~~~

但不能仅凭最低结构覆盖启动强制收口倒计时。

服务端强制停止只由通用且可确定的条件触发：

- 用户取消；
- 权限或 Scope 不允许继续；
- 最大迭代次数、模型调用次数或查询次数耗尽；
- 执行超时；
- 连续重复相同调用；
- 连续多轮没有新增 Evidence 或可执行方向；
- 查询、计算或持久化发生不可恢复错误。

只要模型能提出明确缺口，并持续产生有效新 Evidence，就允许在预算范围内继续。

## 2.3 为什么这样设计

### 字段符合不能证明内容符合

服务端可以可靠判断 Evidence 是否包含某个指标或维度，但无法仅凭这些字段判断具体数据是否
完成了原因解释、异常定位或业务对比。

### 模型能够读取语义内容，但不能控制硬约束

模型适合结合用户问题和查询结果判断内容充分性，但不应决定权限、预算、Evidence 所有权和
引用合法性。这些约束必须由服务端执行。

### 避免两种错误

拆分后可以同时避免：

1. 服务端根据简单字段规则过早结束研究；
2. 模型忽略预算和证据规则，无限制查询或无依据结束。

职责原则是：

> 模型判断内容是否足以回答问题，服务端判断执行和结束申请是否合法、安全并且没有超出预算。

## 2.4 阶段二的交付边界

阶段二只统一评估协议，不要求立即合并 Fast、Plan、Research 三套执行入口。完成标准至少
包括：

- `StructuralCoverage` 有稳定 DTO 和明确字段语义；
- `SemanticAssessment` 有稳定 DTO，状态互斥且终态行为明确；
- 内容不足但要求继续时，必须携带可校验的计划增量；
- `answerable` 不绕过 Findings、Evidence 引用、Scope 和版本校验；
- Runtime 不再从模糊自然语言判断“继续”或“结束”；
- 最低结构覆盖本身不能启动专用强制收口倒计时。

# 3. 问题三：为什么不能继续保留 Fast、Plan、Research 三种模式

## 3.1 问题是什么

进入模式路由前，系统已经获得：

- 用户原始问题和对话上下文；
- 问题理解和语义绑定结果；
- 目标指标、维度和筛选；
- 时间范围和比较关系；
- 数据源、数据集和租户；
- 可使用的语义资产和关系；
- 权限、版本和输出要求。

这些信息已经可以组成 Agent 的冻结上下文。当前系统随后将请求路由到 Fast、Plan 或
Research，并分别使用不同 Pipeline 或 Harness。这种三分法的问题不只是代码重复，更重要的
是概念本身不成立。

但当前三条路径都在完成同一个基本过程：

~~~text
理解目标
  -> 生成计划
  -> 执行查询和计算
  -> 观察结果
  -> 生成回复
~~~

所谓 Fast、Plan、Research 实际是同一个 AnalysisPlan 在不同运行中的事实表现：

- 单查询同样是 DAG，只是 DAG 中当前只有一个查询节点；
- 多查询和计算仍然是同一个 DAG，只是节点与依赖更多；
- 根据 Evidence 继续研究仍然是同一个 DAG，只是产生了新的计划修订并追加节点。

三者是互相包含的关系，不是互斥模式。一个运行可以从单节点 DAG 开始，执行后根据明确 Gap
追加计算或查询节点。这个过程不应发生 Fast 到 Plan 或 Plan 到 Research 的模式切换，因为
从始至终只有同一个计划和同一个 Runtime。

如果为三种模式分别维护流程，会带来：

- 状态、预算、取消和恢复机制重复；
- 查询、计算、Evidence 和错误协议不一致；
- 新能力需要在多个执行器中重复接入；
- Research 执行循环持续吸收专用业务逻辑；
- Router 被迫提前判断 Planner 执行后才能知道的计划结构；
- 单节点、多节点和多修订被错误建模为三个互斥枚举。

## 3.2 目标解决方案

系统只保留一个 Plan-and-Solve Agent、一个 AnalysisPlan 和一个 Runtime。Fast、Plan、
Research 不再是执行策略，也不参与路由、规划、预算或执行分发。

### 统一 Agent Context

进入执行阶段前，将已确定的信息冻结为统一上下文：

~~~text
AgentContext
  - goal
  - conversation_context
  - semantic_scope
  - target_metric_refs
  - dimension_refs
  - time_bindings
  - immutable_filters
  - permissions
  - available_capabilities
  - output_requirements
  - budget
  - version_snapshot
~~~

该上下文描述“要解决什么问题”和“允许使用什么”，不描述必须进入哪一套执行流程。

### 统一执行约束

执行前只冻结所有请求共用的硬性约束：

~~~text
ExecutionPolicy
  - max_plan_revisions
  - max_plan_nodes
  - max_model_calls
  - max_query_tasks
  - max_compute_tasks
  - timeout_seconds
  - allowed_task_types
  - require_explicit_gap_for_append
~~~

`ExecutionPolicy` 只限制 Agent 最多可以做什么，不预先声明计划形态，也不根据问题内容选择
Fast、Plan 或 Research。权限、租户 Scope、版本和产品资源档位可以影响这些上限，但不能
替 Planner 决定节点数量和分析路径。

### 统一计划节点

统一计划至少包含：

| 节点 | 职责 | 执行方 |
| --- | --- | --- |
| query | 执行语义查询 | 服务端查询运行时 |
| compute | 执行差值、增长率、贡献度和对账 | 确定性计算引擎 |
| analyze | 根据 Evidence 判断结论和缺口 | 模型 |
| answer | 根据合法 Evidence 生成回复 | 模型与服务端报告组件 |

所有计划始终使用同一个可追加 DAG 契约：

~~~text
AnalysisPlan
  - plan_id
  - revision
  - nodes
  - edges
~~~

没有 `linear`、`static_dag` 或 `dynamic_dag` 类型。单节点、多节点和追加节点都由同一个
结构表达。已有成功节点不可修改；后续计划只能增加新节点和合法依赖，并递增 revision。

### 明确计划生成与计划执行的责任

统一 Agent 不表示模型直接执行数据库查询。PS Agent Planner 负责生成首次计划和后续计划
增量，服务端不通过模式规则替 Planner 生成固定查询路径。

| 环节 | 责任方 | 说明 |
| --- | --- | --- |
| 生成分析方向和计划候选 | 统一 PS Agent Planner | 首次规划和后续增量使用同一个接口与协议 |
| 计划绑定与校验 | 服务端 | 绑定逻辑资产，校验 Scope、权限、依赖、预算和能力 |
| query / compute 执行 | 服务端 Runtime | 确定性执行，不接受模型直接 SQL |
| 内容充分性判断 | 模型 | 根据用户问题和 Evidence 生成 `SemanticAssessment` |
| 终态合法性判断 | 服务端 | 校验 Evidence 引用、预算、取消、状态和版本 |

因此，“Planner 根据 `premise_to_verify` 规划”不能实现为固定规则搬迁。模型 Planner 应看到
完整冻结上下文、已有 Evidence、StructuralCoverage 和未解决 Gap，并决定当前最小可执行
计划；服务端规则只负责拒绝非法计划，不替模型固定分析路径。

### 统一执行循环

~~~text
初始化冻结上下文、统一预算和空 AnalysisPlan

while 未进入终态:
  1. 检查取消、权限、预算和超时
  2. 如果没有待执行节点，Planner 根据 Context、Plan、Evidence 和 Gap 生成计划增量
  3. 服务端校验增量并追加到同一个 DAG
  4. Plan Executor 执行所有当前可执行节点
  5. 保存结果并注册 Evidence
  6. 计算最低结构覆盖和执行缺口
  7. 模型评估 Evidence 内容是否足以回答问题
  8. 根据评估处理：
       - answerable：校验并生成最终回复
       - explicit_gap：在统一预算允许时继续，由 Planner 生成下一次计划增量
       - no_new_direction：带限制结束
       - data_insufficient：说明数据限制后结束
  9. 连续无有效进展时由服务端停止
~~~

所有请求都执行这个循环，不存在 Fast、Plan 或 Research 专用分支。

## 3.3 为什么这样设计

### 不需要模式也能表达全部执行情况

~~~text
单节点运行
  Planner 追加一个 query -> 执行 -> 评估 -> 回复

多节点运行
  Planner 追加多个 query / compute -> DAG 执行 -> 评估 -> 回复

多修订运行
  执行当前节点 -> 评估得到 explicit_gap -> Planner 追加节点 -> 继续执行
~~~

这三种情况不需要不同策略、不同 Pipeline 或不同状态契约。

### 统一循环不会强制简单问题执行多轮

统一循环不等于每个问题都要追加计划。简单问题的 Planner 只生成一个查询节点，第一次
Assessment 已经 `answerable` 时立即结束。复杂问题是否继续由实际 Evidence 和明确 Gap 决定，
不是由执行前的模式判断决定。

### 通用能力可以统一复用

所有运行直接共用：

- Agent State；
- AnalysisPlan 和节点状态；
- 查询与计算执行；
- ResultStore；
- Evidence Registry；
- Trace 和恢复；
- 错误分类；
- 预算和取消；
- 最终回复校验。

Fast、Plan、Research 如果因外部兼容需要暂时保留，只能在 Runtime 外部根据执行历史生成展示
字段。本文不再定义这些标签的分类规则，避免分类逻辑重新进入 Runtime。标签不能反向影响
规划、预算、节点调度、完成判断或恢复。

# 4. 三个问题合并后的目标架构

三个问题分别解决后，整体流程为：

~~~text
用户问题与对话上下文
  -> Question Rewrite
  -> Semantic Binding
  -> 冻结 Agent Context
  -> 冻结统一 ExecutionPolicy 资源上限
  -> 统一 Plan-and-Solve Runtime
       -> Planner 生成 PlanRevision
       -> 服务端校验并追加 AnalysisPlan DAG
       -> Plan Executor 执行 ready nodes
            -> query
            -> compute
       -> Evidence Registry
       -> Structural Coverage
       -> Semantic Assessment
            -> 可以回答：Answer
            -> 明确缺口：Planner 生成下一次 PlanRevision
            -> 无新方向：带限制结束
  -> 服务端校验 Evidence 引用和终态
  -> 最终回复
~~~

职责边界为：

| 组件 | 职责 |
| --- | --- |
| Question Rewrite | 规范用户问题和对话指代 |
| Semantic Binding | 绑定指标、维度、时间、筛选和语义关系 |
| Planner | 根据冻结上下文、现有 Plan、Evidence 和 Gap 生成首次计划或计划增量 |
| ExecutionPolicy | 只控制统一预算、节点类型和资源上限，不决定计划形态 |
| Runtime / Executor | 调度节点、执行工具、记录状态和 Evidence |
| Structural Coverage | 检查最低结构条件和硬性事实 |
| Semantic Assessment | 判断实际内容是否足以回答问题 |
| Answer | 基于合法 Evidence 形成最终回复 |
| Server Guard | 权限、预算、取消、引用、恢复和强制终止 |

# 5. 初步迁移方向

## 阶段一：修正完成判断的含义

- 将“证据需求满足”改为“最低结构覆盖满足”；
- 修改提醒内容，要求模型判断内容充分性；
- 取消结构覆盖触发的专用强制收口；
- 保留预算、取消、停滞和错误终止；
- 补充 `evaluate_completion()` 当前能力边界的测试。

## 阶段二：统一完成评估契约

- 本阶段统一的是每轮 Evidence 之后的评估输入输出，不是统一执行器；
- 定义 `StructuralCoverage`；
- 定义 `SemanticAssessment`；
- 明确 `answerable`、`explicit_gap`、`no_new_direction` 和 `data_insufficient` 的终态行为；
- 规定内容不足时必须输出明确缺口；
- 规定继续研究时必须输出可编译、可校验的计划增量；
- 规定最终结论必须引用合法 Evidence。

## 阶段三：将前提确认计划化

- 将 `premise_to_verify` 表达为待解决的 Evidence Gap，而不是固定查询开关；
- 模型 Planner 根据已有 Evidence、查询能力和分析目标决定复用、合并或新增任务；
- 服务端只校验该计划是否能合法证明或否定前提，不固定节点结构；
- 删除 `_run_premise_preflight()` 专用路径；
- 移除对固定 `premise-confirmation` 节点 ID 的业务依赖；
- 无前提问题不产生该 Gap，直接生成普通计划；
- 禁止以 `if premise_to_verify: build_fixed_premise_plan()` 作为阶段完成实现。

实施结果：

- `premise_to_verify` 只在 Working State 中投影为 `premise_confirmation` Evidence Gap；
- `SemanticAssessment.resolved_gaps` 使用合法 Evidence ID 提交前提裁决；
- `explicit_gap` 可以提交同时包含前提指标、比较时间角色和原因分析维度的合并查询；
- 服务端校验 Evidence 是否能够确定前提方向，以及计划增量是否具备解决该 Gap 的输入；
- 已删除 `ResearchInitialPlanner`、`ResearchInitialPlan` 和固定首轮计划字段；
- Runtime 已删除 `_run_premise_preflight()` 和固定节点 ID 裁决逻辑；
- 前提不成立改为模型提交 `premise_not_supported` 结束请求，服务端验证后收口。

## 阶段四：统一计划和执行状态

- 所有请求只使用一个可追加的 `AnalysisPlan` DAG；
- 首次计划和后续计划增量使用同一个 `PlanRevision` 协议；
- 单查询、多查询、计算和后续研究节点都写入同一个 Plan；
- 所有节点共用 Plan Executor、Evidence Registry、预算、Trace 和恢复协议；
- 删除计划状态中的 `mode` 和 `plan_shape`，不再表达 `linear`、`static_dag`、
  `dynamic_dag` 三种互斥类型。

阶段四修正后的实现约束：

- 统一持久化 `plan_execution_state`，其中包含计划 ID、修订号、节点依赖、拓扑批次和节点
  终态；
- Planner 每次提交合法计划增量都会递增修订号，只允许新增节点，不允许替换既有节点或
  修改已完成节点；新增依赖形成环、引用未知节点或复用节点 ID 时由服务端拒绝；
- 已删除 `initial_plan_state` 和 `approved_plan_additions`；规范执行事实只保存在
  `plan_execution_state`；
- 工具提交、取消和恢复统一更新节点状态；快照携带统一计划状态，恢复后
  已完成节点保持终态，中断节点标记失败或取消后才允许受控重试；
- Fast、Plan、Research 仅允许作为执行后统计标签，不得存入规范计划状态并控制执行。

实施结果：

- `UnifiedPlanExecutionState` 已删除 `mode`、`shape` 和 `allow_append`，并统一使用
  `revision` 表达计划修订号；
- 单节点计划、多节点计划和后续计划增量共用同一个可追加 DAG 契约；
- Fast、Plan 和 Research 的现有入口均将节点状态写入 `plan_execution_state`；
- 旧快照中的 `mode`、`shape` 和 `allow_append` 只在统一加载入口被删除，不再恢复为执行事实；
- 追加节点仍统一校验节点 ID、依赖引用和 DAG 环路，已成功节点保持不可修改。

阶段四完成的是规范计划状态统一，旧 Pipeline 的执行分发仍将在阶段五删除。仅把三个
Pipeline 的状态投影到同一个 DTO 而保留模式不变量，不视为完成统一。

## 阶段五：删除模式路由并接入统一 PS Agent Runtime

- 删除 Mode Router 对 Fast、Plan、Research 的分类和 Pipeline 分发职责；
- Semantic Binding 后直接构建统一 `AgentContext` 和资源上限；
- 所有请求进入同一个 `PlanAndSolveRuntime`；
- 统一 Planner 接口同时负责首次规划和根据明确 Gap 生成计划增量；
- 统一 Plan Executor 执行 query 和 compute 节点，不按模式选择执行器；
- 每次节点执行后统一进入 Structural Coverage 和 Semantic Assessment；
- `explicit_gap` 且预算允许时继续规划，不发生模式升级或执行器切换；
- 旧 Fast、Plan、Research Pipeline 只允许作为迁移期适配层，最终删除；
- 如需兼容显式模式标签，只能在 Runtime 外生成展示字段，不能参与执行分发。

当前实施进展：

- 已删除确定性 `ResearchInitialPlanner` 及其 DTO、执行分支和状态投影；首次计划与后续修订
  都只能通过 `SemanticAssessment.proposed_plan_additions` 提交；
- Research Profile 只向模型暴露 `assess_research` 和 `finish_research`。模型不能直接调用
  query、compute 或 inspect；
- `ResearchPlanAddition` 使用显式 `dependency_node_ids` 表达 DAG 依赖，完整执行参数直接写入
  现有 `UnifiedPlanNode`，不再通过 Evidence ID 事后反推依赖；依赖边同时定义执行顺序和
  数据输入，声明依赖的 compute/inspect 节点不能再携带另一组 Evidence ID；
- Runtime 每轮优先从 `plan_execution_state` 选择依赖全部成功的 READY 节点，再使用现有
  `_execute_batch` 执行；不存在把 `decision.tool_calls` 事后包装成计划的路径；
- 计划批准后由 Runtime 自动执行，不再要求模型下一轮原样重放 `approved_plan_additions`；
- `explicit_gap` 产生的后续计划增量复用同一个追加入口和 DAG 校验；
- 原 `ModeRouter` 的生产入口已改为 `ExecutionRequirementBuilder`；它只冻结目标、Scope、
  时间条件、不可变筛选、输出要求和预算，不再生成固定查询 DAG；
- 旧 `execution_modes` 配置不再控制规划或执行分发，只作为迁移期输入字段保留；
- 普通单查询、固定多步和动态分析现在都生成同一种 Agent Requirement，均由模型 Planner
  在首轮提交 query、compute 或 inspect 节点；服务端只负责校验、追加 DAG 和执行；
- 无显式时间条件的请求使用 `single` 时间角色且不伪造时间过滤；`current`、`previous` 等
  显式角色仍要求每个实际查询模型都有冻结时间绑定；
- `RunOrchestrator` 已删除固定 Plan 与动态 Research 的执行分发，首次执行和澄清恢复都直接
  进入同一个 Agent Runtime；
- 生产组装已停止创建 `FastPipeline` 和根 `PlanPipeline`。query、compute 的确定性执行能力
  继续由 Agent Runtime 内部复用 `AnalysisExecutionService`，不再作为另一条业务入口；
- `route.mode` 只保留为快照兼容字段，新请求统一写入 `agent`，运行时不读取它决定执行路径。

阶段五的生产执行入口已经统一。旧 Fast、Plan 代码和旧快照字段仍可在后续清理阶段删除，
但它们不再参与新请求的规划或执行。

# 6. 与现有设计文档的关系

现有 `39A-unified-analysis-agent-planning-evidence-design.md` 已经提出统一 Plan Executor 和
统一 Evidence。本文进一步修正“不同计划形态对应不同模式”的残留设计：计划始终是同一个
可追加 DAG，差异只来自实际节点和修订历史。

1. 前提确认必须明确归入 Planner，而不是通用执行循环；
2. Evidence Requirement 满足只能表达最低结构覆盖；
3. 最低结构覆盖不能直接进入 Structured Findings；
4. 最低结构覆盖不能成为禁止重新规划或强制收口的充分条件；
5. 完成判断必须拆分为服务端结构校验和模型内容判断；
6. Fast、Plan、Research 不是三种执行策略，不能继续作为互斥模式分发；
7. 单节点、多节点和多修订必须由同一个 Plan-and-Solve Runtime 处理。

# 7. 结论

本次讨论形成三个独立结论：

1. **前提确认属于计划内容。** 有前提的问题由 Planner 解决对应 Gap，无前提的问题不产生
   该 Gap，通用 `run()` 不应直接包含该业务逻辑。这里的“计划化”不是搬迁固定规则，而是
   允许 Planner 复用已有 Evidence、合并查询或新增最小任务。
2. **结构覆盖不等于内容充分。** 服务端只能检查字段、数量、权限和引用等硬条件；模型需要
   结合用户问题和实际 Evidence 判断是否真正回答了问题。
3. **系统只应存在一个 Plan-and-Solve Agent Runtime。** Fast、Plan、Research 不是三种
   执行策略：单查询是 DAG 的一个节点，多查询是同一 DAG 的多个节点，继续研究是同一 DAG
   的下一次修订。任何请求都必须通过同一个规划、执行、Evidence、评估和回复循环。

最终职责原则是：

> Planner 通过同一个协议生成首次计划和计划增量；统一 Runtime 执行同一个可追加 DAG 并
> 负责硬性约束；模型判断内容是否足以回答用户问题；服务端校验计划和最终提交是否合法。
