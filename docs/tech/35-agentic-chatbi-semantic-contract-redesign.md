# 35. 智能问数语义运行时重构设计（理解→绑定→规划契约 v2 · 合并稿）

> 状态：设计评审稿 **v3 + R0 实施中** ｜ 日期：2026-08-17 ｜ 基线：`codex/agentic-chatbi-redesign` 工作区
> 底稿：本文由两份独立设计稿合并——《语义契约重构设计》（本文件 v1）与《智能问数运行时重构设计》（`35-agentic-chatbi-runtime-refactoring-design.md`，已停止维护，仅存档）。合并取舍与勘误见附录 B。
> 变更记录：v3（2026-08-17）——按实施评审意见（架构有条件通过、实施暂不通过）闭合六项契约缺口：① 复合指标拆解契约（DecompositionHint/RatioSpec，§4.1.1/4.3.3）；② 分组候选收敛决策表与 tie-break（§4.4.2）；③ SemanticRuntimeViewSnapshot Run 级冻结（§4.7）；④ temporal 旧字段投影契约（§4.2.8）；⑤ 验收门禁拆分为结构/业务双层（§7.3）；⑥ 分层黄金固定输入与 DTO 补全（§4.4.1/7.2）。另：R2 排期调整 3-4 周、R2 任务合并去重、Trace 快照治理规则（§6-R0）、跨模型 ratio 失败行为固定（§4.3.3）、补 §3.3 三模式编排范式澄清（FAST=确定性流水线 / PLAN=规划-执行 / RESEARCH=有界 agentic 循环，react_legacy 仅回滚）。闭环对照见附录 C。
> 输入：docs/tech/32（架构方案）、33（实施计划）、34（P1 修复记录）；P1 黄金集 12 题三轮真实跑批（docs/test-results/p1-golden-2026-08-17{,-fixed,-fixed2}）；外部架构评审意见（2026-08-17）
> 性质：**doc 32 的修订与补全**，不是推翻。本文重设计的是 doc 32 §5.2（问题理解）、§5.3（检索绑定）、§5.4（规划输入契约）与澄清/修复/验收机制；doc 32 §4 的 AnalysisPlan / ResultStore / ComputeEngine / 三模式编排、§5.1 语义资产层、权限与执行安全**全部确认保留**。
> 编号说明：doc 33 §8 曾预留 35 号给 "analysis-plan-and-modes"，该文档未落笔，本文启用 35 号；后续计划模型契约文档顺延。

---

## 0. 执行摘要

1. **定性**：P1 黄金集 12 题三轮跑批（严格通过 1/12 → 修两轮后仍无一轮超过 4/12，且失败在轮次间**漂移**：G002 从理解失败修成多余澄清、G003 从 finished 修成 `PLAN_STRICT_QUERY_PLAN_MISSING`）证明失败不是实现 bug 集合，而是**上游语义链路的契约缺陷**。逐例打补丁只会把失败从一个阶段挤到另一个阶段。
2. **五个结构性缺陷**（§1 附代码证据）：
   - **C1 输出契约拒绝合法语义**：理解 DTO 用窄枚举 + `extra="forbid"` + 新旧字段并存，把模型正确理解的语义在格式层杀死（G001/G002/G009）；
   - **C2 修复机制重写语义**：格式修复 = 整份 JSON 重生成，无语义不变量校验，修复过程丢弃 comparison/time_ranges（G010 首轮）；
   - **C3 提及与资产之间缺少语义对象**：`metric_mentions` 是裸字符串，同时承担原话候选、检索文本、基础指标、派生表达四种职责；检索层只能用启发式猜短语边界；派生指标（客单价/增长率）在类型上不可表达（G001/G003/G004/G007/G011）；
   - **C4 绑定单结果 vs 计划多任务**：`RetrievalBundle` 是全局单份槽位决策，跨模型互斥约束在全局执行，而 AnalysisPlan 需要每个 QueryTask 一组资产——两层抽象不一致（G004/G005/G006/G010）；
   - **C5 澄清责任不分层**：`ambiguous_slots` 非空即澄清，把"系统尚未绑定"当成"用户问题歧义"，制造大量伪澄清（G002/G005 第三轮 waiting_user）。
3. **两项机制缺陷**：证据链缺失（G003/G008 "finished 但口径错误"无法被机器发现）；验收只看终态（"代码完成"被误报为"能力完成"，doc 33 的 P1 "已完成首版" 与 12 题 1/12 的落差）。
4. **六项核心设计决策**：
   - D1 理解层改产出**带跨度的语义提及（SemanticMention）+ 分析表达（AnalysisExpression）**，指标的"原话短语 / 基础指标 / 计算表达 / 排序引用"在类型上分离；复合指标携带结构化拆解假设（DecompositionHint）；指标阈值条件成为一等结构，聚合阶段（WHERE/HAVING）由服务端确定性裁决；
   - D2 时间与比较收敛到 **temporal 域唯一权威**，理解模型不再输出 comparison / time_ranges，双字段与三处比较来源退役；旧字段全部由确定性投影契约供给（§4.2.8）；
   - D3 检索只消费 mention（跨度保证短语完整），**删除边界猜测启发式**；
   - D4 绑定改产出 **QueryGroup（每查询组一份资产绑定）**，候选收敛按决策表与固定 tie-break 确定性执行，跨模型正确性由结构保证而非名称匹配启发式；
   - D5 澄清引入**三态责任模型**（用户欠指定 / 资产真歧义 / 系统待绑定），澄清门整体后移到绑定完成之后；
   - D6 修复协议改为**确定性归一化 → 字段级补丁 → 语义不变量校验**，禁止整份重生成。
5. **配套机制**：SemanticRuntimeViewSnapshot——Run 级冻结的运行时语义事实源，全阶段只读同一快照（§4.7）；SQL 证据断言与 claims 数值归一（§4.8/4.9）；分层黄金测试（固定输入 fixture）与 DoD 重定义（§7）；**观测冻结先行**（R0 把 12 案例转成阶段级 fixture、全链快照落 Trace，任何失败可归因到具体阶段）与**影子运行 + 灰度切换**（R5，新旧链路差异只记录不影响答案，控制题等价后才切默认）。
6. **落地**：R0 止血 + 观测冻结（≈1 周双轨并行）→ R1 提及契约（2-3 周）→ R2 QueryGroup 绑定（3-4 周）→ R3 事实源与证据链（2 周）→ R4 分层评测与门禁（1-2 周，自 R1 起并行）→ R5 影子灰度与默认切换（1-2 周，尾部重叠）。**全程不新增数据库表**，每阶段挂开关可独立回滚，总计约 10-13 周。验收采用结构/业务双层门禁（§7.3）：结构断言必须 12/12，业务结果 R3 后 ≥11/12。

---

## 1. 失败根因与代码证据

### 1.1 三轮跑批事实

| 用例 | 问题要点 | 第 1 轮（02:14） | 第 3 轮（11:57，两轮修复后） | 根因类 |
|------|---------|----------------|---------------------------|-------|
| G001 | 两日 GMV 对比+增长率 | failed：`comparison.target` 额外字段 | failed：`comparison.method='percent_change'` 不在枚举 | C1 |
| G002 | 两月订单数对比 | failed：`method='difference'` 不在枚举 | **waiting_user（伪澄清）**：问题本身完全清晰 | C1→C5 |
| G003 | 各店铺 GMV 占比 | finished 2/3：无 share 节点、SQL 无 6 月过滤 | failed：`PLAN_STRICT_QUERY_PLAN_MISSING`（回归） | C3/C4 + 证据链 |
| G004 | 渠道×销售件数占比 | failed：`semantic_metric_dimension_incompatible` | 同左 | C3/C4 |
| G005 | 跨模型双指标并列 | failed：跨模型组合不可执行 | **waiting_user（伪澄清）** | C4→C5 |
| G006 | 跨模型双口径对比 | waiting_user（澄清本身合理） | waiting_user | C4 |
| G007 | 平均客单价（派生） | failed：`SEMANTIC_QUERY_METRIC_REQUIRED` | 同左 | C3 |
| G008 | GMV>10000 的店铺（HAVING） | finished 0/2：SQL 无 HAVING、无 6 月过滤 | finished：claims 校验失败降级表格，HAVING 仍缺 | 证据链 |
| G009 | 逐月 GMV+环比 | failed：新旧时间字段并存 | finished：claims 校验失败降级表格 | C1→证据链 |
| G010 | 两个半年区间同比 | failed：`PLAN_STRICT_QUERY_PLAN_MISSING`（此前轮次修复曾删掉 comparison/time_ranges） | finished：compute join 空结果，"无法计算同比" | C2/C4 |
| G011 | 预聚合指标 | failed：`SEMANTIC_QUERY_METRIC_REQUIRED` | 同左 | C3 |
| G012 | 快照期末库存排序 | **finished ✅ 2/2** | **finished ✅（claims 通过）** | — |

漂移即证据：每一轮修复都把失败换了一个错误码继续存在，说明缺陷在阶段之间的**契约**上，不在阶段内部。

### 1.2 五个结构性缺陷的代码定位

**C1 输出契约拒绝合法语义**

- `apps/chatbi/models/dto/question_understanding.py:131-138`：`ComparisonSpec.method: Literal["yoy","mom","custom"]`——模型对"变化了多少/差值/增长率"自然产出 `percent_change`、`difference`，被 pydantic literal 直接拒绝；配合全 DTO `extra="forbid"`，一个多余子字段（G001 首轮 `comparison.target`）就废掉整份正确理解。
- `question_understanding.py:227-247`：`time_range`（旧单区间）与 `time_ranges[]` 并存，靠 `synchronize_time_ranges` 校验器缝合；模型同时输出两者的合法组合空间极窄（G009 首轮"新旧字段并存"即死于此）。
- 比较语义有**三个来源**：统一理解的 `comparison`、temporal 解析的 `comparison`（`understanding/prompts.py:95`）、`query_shape.comparison_type`（`question_understanding.py:170`）。三处可互相矛盾，没有唯一权威。

**C2 修复机制重写语义**

- `apps/chatbi/services/understanding/understanding_service.py:819-968` `_invoke_validated_model`：校验失败后把 `repair_feedback` 附回原 payload，让模型**重新生成整份 JSON**。指令写着"保持原问题语义不变"，但没有任何服务端不变量检查——G010 首轮修复把 `comparison` 和 `time_ranges` 整体删掉，产物通过校验被静默接受。格式错误被实际处理成了"重新理解问题"。

**C3 提及与资产之间缺少语义对象**

- `question_understanding.py:72`：`metric_mentions: list[str]`。裸字符串同时被当作：① 用户原话候选；② 检索文本（`apps/retrieval/projection/planner.py:47-59`）；③ 严格计划的基础指标（`SEMANTIC_QUERY_METRIC_REQUIRED` 来自 `apps/semantic/services/query/planning.py:38`）；④ 派生/排序表达（"环比增长率"整串进 mentions）。
- `apps/retrieval/projection/planner.py:163-188` `_metric_retrieval_text`：靠"指标前后是否紧邻 CJK 字符"猜测限定词边界，命中则**整句**作为指标检索文本——检索层在替理解层补短语边界信息，因为契约里根本没有原文跨度。
- 派生指标不可表达：语义层已支持 `metric_refs`/ratio 编译（`apps/semantic/services/compilation/metric_expansion.py`），但理解→检索→计划全程没有"这是一个需要拆解的计算表达"的类型位置，"平均客单价"只能作为普通指标短语去撞检索（G007），"需要预聚合的销售额"同理（G011）。
- 对照组：**temporal 域已经是正确形态**——`TEMPORAL_INTERPRETATION_SYSTEM_PROMPT`（`understanding/prompts.py:49-102`）要求每个时间表达输出 `raw + start_offset + end_offset + role(query_filter|metric_definition)`，且经 P0 真实验证可行。本文 D1 就是把这个已被证明的模式推广到指标与维度。

**C4 绑定单结果 vs 计划多任务**

- `apps/retrieval/query/policy.py:112-171`：`SemanticBindingPolicy.apply` 产出**一份全局** `RetrievalBundle`（每槽位一个决策）。
- `policy.py:568-690` `_constrain_metric_dimension_candidates`：单模型场景下执行"所有指标 × 所有维度全局互斥收敛"，收敛到空即抛 `SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE`（G004：销售件数与交易渠道本可在同一模型内成立，被全局规则联合其他槽位杀空）。
- 跨模型拆分被推迟到投影层用启发式补救：`apps/retrieval/projection/payload.py:823-950` `_cross_model_query_plans` / `_assets_for_model` 按**名称子串匹配**（`normalized in value or value in normalized`，`payload.py:953-966`）把全局选中的维度"翻译"到每个模型——这正是"绑定层只支持单结果"的补丁证据。
- `apps/chatbi/services/planning/analysis_planner.py:170-229`：规划器消费的是 `semantic_state`/`semantic_scope` 弱类型 dict，多任务与时间区间做笛卡尔展开（`_expand_time_range_tasks`），ComputeTask 的 `join_on` 用 `shape.join_on or package.dimensions` 兜底（`analysis_planner.py:416-419`）——G010 第三轮 compute join 空结果由此而来。

**C5 澄清责任不分层**

- `apps/chatbi/services/understanding/validation.py:263-274`：`ambiguous_slots` 非空 → 无条件产出 `intent_ambiguous`。
- 统一理解模型的输入包含整个数据集的 `available_dimensions` + `time_dimensions`（`understanding_service.py:471-486`）：模型在**指标还没绑定**时就看到多个时间字段，于是"善意地"申报歧义；校验器把"可由绑定阶段默认解决的信息"当成用户歧义。默认时间维度的确定性绑定明明存在（`apps/retrieval/query/policy.py:954` `bind_default_time_dimensions`），但发生在澄清判定**之后**。
- 结果：G002（"对比店铺100021在2026年6月与2026年5月的总订单数"——毫无歧义）第三轮进了 waiting_user。

**证据链缺失（贯穿）**

- G003/G008 的 caliber_card SQL 缺 6 月过滤、缺 HAVING，但 Run 终态 finished——编译产物没有"声明的语义必须出现在 SQL"的服务端断言。
- G008/G009 第三轮 claims 数值校验系统性失败后一刀切降级表格：claims 校验对百分比/单位换算/派生值没有归一化规则，把"校验机制不完善"表现成"回答降级"。
- 评测统计侧同样失真：fixed2 概览显示"SQL 执行 0/12"，但 G008/G009/G012 实际生成并执行了 SQL——跑批脚本按旧事件口径计数，未消费 `task.finished`（§7.4 随 R0 修正）。
- 黄金题只有 `expected_points` 散文，机器只能判 `finished/failed/waiting_user`，无法判定"该澄清还是错澄清""执行对还是仅仅产出了答案"。

### 1.3 对外部评审九项结论的裁决

| # | 评审结论 | 裁决 | 说明 |
|---|---------|------|------|
| 1 | 问题理解是过重的单一模型输出 | **修正后采纳** | 调用结构其实已拆分（重写 / 统一理解 / 时间解析三次调用，`understanding_service.py:419-579`）；真正的问题在**输出契约**把多种语义压进同一批字段。本文重设计契约而非调用数 |
| 2 | 检索放在错误抽象层（启发式猜边界） | **采纳** | `planner.py:163` 实证；方案见 D3 |
| 3 | 理解→绑定→计划存在循环依赖，绑定须按 QueryTask | **采纳** | 方案见 D4（QueryGroup）；"循环"以两阶段绑定（先指标分组、后组内维度）拆解，不引入真循环 |
| 4 | 缺信息与用户歧义混为一谈 | **采纳** | 方案见 D5 三态模型 |
| 5 | 严格 DTO+修复破坏语义保真 | **采纳** | `extra="forbid"` 保留，但前置确定性归一化与路径级剥离；修复改字段补丁；见 D6 |
| 6 | 缺少统一运行时语义事实源 | **采纳** | §4.7 SemanticRuntimeViewSnapshot；确认 doc 32 "存储模型够、运行时契约不够"的判断需要修订 |
| 7 | P1 完成定义有问题 | **采纳** | doc 34 已承认"实现完成≠验收完成"；§7 给出 DoD 双栏制度 |
| 8 | 评测无法验证架构可靠性 | **采纳** | §7 分层黄金 schema v2 + 固定输入 fixture |
| 9 | AnalysisPlan/ResultStore/Compute/编译器/权限方向正确 | **采纳** | G012 全程通过是执行层健康的直接证据 |

---

## 2. 保留 / 重设边界

### 2.1 保留（本文不动）

AnalysisPlan/QueryTask/ComputeTask DTO 与计划校验、ResultStore、DuckDB ComputeEngine 及操作白名单、语义 SQL 编译器与 PROVEN 链、compilation 模块组（derived/ratio/time_offset/preagg/snapshot）、权限三道闸、temporal 域解析器与渲染器、事件/Trace 双轨、三模式编排骨架（mode_router/fast/plan_mode 的流程宿主）、confidence 四档函数、记忆域、instructions 资产。

### 2.2 重设（本文范围）

| 对象 | 现状 | 目标 |
|------|------|------|
| 理解输出契约 | `metric_mentions: list[str]` + 三处比较来源 + 双时间字段 | SemanticMention（带跨度）+ AnalysisExpression + 拆解假设/指标条件/排序引用 + temporal 唯一权威 |
| 理解模型输入 | 注入全数据集维度/时间字段 | 提及抽取不见 schema（§4.1.5） |
| 检索输入 | 字符串 + `_metric_retrieval_text` 启发式 | mention 直接消费，删启发式 |
| 绑定输出 | 全局单份 `RetrievalBundle` + payload 层名称匹配拆模型 | `ResolutionOutcome{query_groups[]}`，决策表驱动的组内绑定 |
| 澄清触发 | `ambiguous_slots` 即澄清（理解阶段） | 三态责任 + 绑定后统一澄清门 |
| 格式修复 | 整份 JSON 重生成 | 归一化 → 字段补丁 → 不变量 |
| 规划输入 | `semantic_state` 弱类型 dict | ResolutionOutcome 强类型 DTO |
| 运行时语义规则 | 分散五层（默认时间/兼容性/派生/HAVING/拆分各自为政） | SemanticRuntimeViewSnapshot 单一事实源（Run 冻结） |
| 验收 | 终态 + expected_points 人工 | 分层黄金断言（固定输入）+ 结构/业务双层门禁 + 实现/验收双栏 DoD + 影子对照 |

### 2.3 非目标

- 不重写语义资产数据库与 SQL 执行权限链路；
- 不引入新的自由生成物理 SQL 兜底路径（ASSISTED 既有机制不扩大）；
- **不通过固定问题、固定指标或固定模型 ID 修复单个案例**——G001–G012 只作为回归样本，禁止任何 case 特判逻辑进入主链路；
- 不在缺少中间证据时，仅用最终答案判断链路正确；
- 不在本轮扩展新的复杂分析能力（归因模板、预测、跨模型比率等仍按 doc 32 P2 排期）。

---

## 3. 目标链路总览

### 3.1 管线与状态机

```
用户提问
  ↓ ① 重写（沿用，含 plan_patch 短路）
  ↓ ② 提及抽取与表达组装（1 次 LLM，不见 schema）
  │     产出 MentionGraph：SemanticMention[]（带跨度）+ AnalysisExpression[]
  │     + 拆解假设 + 指标条件 + 排序引用 + intent_type/query_shape（仅组织方式）
  ↓ ③ 时间解析（沿用 temporal 权威，1 次 LLM，独立调用保留）
  │     TemporalPlan = 时间与「时段比较」的唯一权威；旧字段由投影契约供给（4.2.8）
  │     门 A：USER_UNDERSPECIFIED（表达本身缺信息）→ 立即澄清
  ↓ ④ 确定性归一化（无 LLM）：字段迁移/枚举别名/路径级剥离/语义不变量
  ↓ ⑤ 提及解析（检索，无 LLM）
  │     每个 mention → 候选资产（沿用混合召回/门控/证据分层）
  │     computed 表达不进指标检索；composite 先整体后拆解（4.3.3）
  ↓ ⑥ 查询分组与组内绑定（无 LLM，确定性，决策表驱动）
  │     指标收敛 → 约束传播 → 裁决表 → QueryGroup[]；组内绑定维度/维值/默认时间维度
  │     互斥约束降为组内规则；产出 ResolutionOutcome；全程只读同一 RuntimeSnapshot
  ↓ ⑦ 澄清门（唯一出口）
  │     三态判定：PENDING_BINDING 禁止出门 / ASSET_AMBIGUOUS 选项澄清
  │     / USER_UNDERSPECIFIED 澄清；接 confidence 四档
  ↓ ⑧ 计划装配（规则直出；复杂形态可选 1 次规划 LLM，沿用）
  │     QueryGroup ↔ QueryTask 结构同构；AnalysisExpression → ComputeTask 映射表
  ↓ ⑨ 计划校验 + 证据断言（时间谓词/HAVING 必须出现在编译产物）
  ↓ ⑩ 执行 → ResultStore → ComputeEngine → AnswerComposer（沿用，claims 归一修正）
```

LLM 调用预算不变：FAST = 重写 + 提及抽取 + 时间解析 + 作答 ≤4 次；PLAN +1 次规划调用。**时间解析保持独立调用**——它是已验证组件，不并回抽取调用。

**mention 生命周期状态机**：

```
EXTRACTED ─解析→ CANDIDATES ─分组绑定→ BOUND(group_id, asset_id | ratio)
                    │                      │
                    ├─ MISSED ────────────→ 四档路由（拒答/兜底）
                    └─ AMBIGUOUS(组内仍多候选且口径不同) → 澄清门
时间/比较：TemporalPlan 独立状态机（resolved / clarification_required / unsupported，已有）
```

### 3.2 阶段职责表（允许做 / 禁止做）

| 阶段 | 允许做的事情 | 禁止做的事情 |
|------|------------|------------|
| 上下文重写 | 补全追问上下文、保持当前问题语义、产出 plan_patch 意图 | 选择指标、维度或资产 ID |
| 提及抽取 | 抽取连续短语与跨度、角色分类、表达组装、拆解假设、指标条件、排序引用 | 绑定资产、猜测物理字段、输出比较方法或时间区间结构、读取 schema |
| 时间解析 | 时间表达与时段比较的唯一权威解释 | 业务口径判断、资产选择 |
| 确定性归一化 | 字段迁移、枚举别名、路径级剥离、语义不变量检查 | 重新解释自然语言 |
| 提及解析（检索） | 按完整短语召回候选 | 把整个问题临时当作指标名、重新切分短语 |
| 分组绑定 | 指标定模型 → 组内绑定维度/值/默认时间维度（决策表驱动） | 向用户暴露尚未收敛的内部候选、临时发明 tie-break |
| 澄清门 | 只处理真实用户歧义或真实业务口径歧义 | 处理 DTO 错误、内部待绑定状态、系统默认值缺失 |
| 计划装配 | 把已绑定资产组织成 QueryTask/ComputeTask | 发明资产 ID、改写指标口径、把 HAVING 降级为 WHERE |
| 计划校验与证据断言 | PROVEN 校验 + 声明口径必须出现在编译产物 | 放行缺声明口径的 SQL |
| 编译执行 | 只编译 PROVEN 计划并过三道闸 | 接受未绑定或未验证的计划 |
| 结果计算 | 白名单算子确定性计算 | LLM 心算 |
| 回答组装 | 读取结果集并绑定数字引用 | 重新计算数字、补造缺失结果 |

### 3.3 三模式的编排范式（doc 32 §4.3 的补充澄清）

doc 32 §4.3 定义了三模式的触发条件与调用预算，但未点名各自的**编排范式**；且"Workflow"一词在本仓库另有所指（待退役的 Graph/Workflow 旧链路，doc 33 P2-6），易生混淆。此处成文，作为权威口径：

| 模式 | 编排范式 | agentic 循环 | LLM 的职责 | 流程驱动者 | 代码宿主 | 状态 |
|------|---------|-------------|-----------|-----------|---------|------|
| FAST | **确定性流水线**（固定阶段序列） | 无 | 阶段内函数调用：重写/提及抽取/时间解析/作答（≤4 次），不驱动流程 | pipeline 代码（阶段顺序写死） | `orchestration/pipeline/fast.py` | 已实现，生产默认之一 |
| PLAN | **规划-执行**（plan-and-execute） | 无（规划一次性产出，失败重试 1 次后规则降级） | FAST 四类 + 至多 1 次结构化规划产出完整 AnalysisPlan（规则直出通道 0 次）；模型不逐步决定下一动作 | 计划 DAG（服务端逐节点 PROVEN 校验后确定性执行） | `pipeline/plan_mode.py` + `planning/analysis_planner.py` | 已实现，生产默认之一 |
| RESEARCH | **有界 agentic 循环**（ReAct 式：提子问题→生成 QueryTask→读结果摘要→决定下一步） | 有，预算封顶（≤8 查询 / ≤15 LLM / ≤300s，可配） | 循环内决策 + 报告组装；每个查询仍走绑定/校验/编译全链 | 研究模型（受限动作空间内） | `pipeline/research.py`（占位）+ 复用 `agent/` 预算/取消基建 | **未实现**（doc 33 P2-1） |
| react_legacy | 旧 ReAct 循环（tool_visibility 状态机驱动；doc 32 §3.2 判定为"伪 ReAct"） | 有（每步一次 LLM） | 每步选择工具 | 工具可见性状态机 | `orchestration/agent/` | 仅回滚路径 + 暂承载非问数收口 |

三点澄清：

1. **范式差异是按问题难度分配算力的刻意决策**（doc 32 §1.2 混合编排共识："纯 ReAct 与纯 pipeline 都不是答案"）：FAST/PLAN 把模型自由度压缩为"阶段内函数"与"一次计划产出"，RESEARCH 是唯一保留自由循环的地方——agentic 成本只花在归因/开放问题上；
2. **与旧 Graph/Workflow 链路无关**：三模式均不使用 workflow_engine；若"workflow 范式"指固定阶段编排，那正是 FAST/PLAN 的流水线本质，与待退役链路是两回事；
3. **react_legacy 的两个残留职责需在退役前收口**：mode_router 目前把 `category != data_query`（闲聊/元问题/越界收口）与少数未覆盖形态回落 react_legacy（`mode_router.py:48-50, 73-76`）——非问数收口应随 R2 澄清门改造迁入 pipeline 直答阶段，使 react_legacy 成为纯回滚路径。

LLM **选型**（各阶段用哪一档具体模型）与范式无关，仍是 doc 32 开放问题 5——建议 R3 重验收后按分层评测数据定档。

---

## 4. 分域设计

### 4.1 理解层：SemanticMention 与 AnalysisExpression（D1）

#### 4.1.1 契约

```python
# apps/chatbi/models/dto/mention.py [新]

MentionKind = Literal[
    "metric_phrase",      # 指标短语（完整原文，含限定词）
    "dimension_phrase",   # 维度/业务对象短语
    "filter_value",       # 筛选值（"华东"、"100023"）
    "time_expression",    # 时间表达（仅登记跨度，语义归 temporal 域）
]

class DecompositionHint(BaseModel):
    """composite_unknown 指标的拆解假设。numerator_text / denominator_text 是
    模型给出的『假设基础指标短语』——它们通常不在用户原文中（用户只说了"客单价"），
    因此不是 mention、没有跨度、不用 mention_id 引用；它们只能作为检索文本使用，
    绝不携带、也绝不直接绑定任何资产。裁决规则见 4.3.3。"""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ratio"]               # v2 仅支持比率拆解
    numerator_text: str = Field(min_length=1)    # 假设分子短语，如"销售订单金额"
    denominator_text: str = Field(min_length=1)  # 假设分母短语，如"销售订单数"

class SemanticMention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mention_id: str                      # m1, m2, ...
    text: str                            # rewritten_question[start:end] 的严格切片
    start_offset: int                    # Unicode 字符，左闭右开——与 TemporalPlan.expressions 同规则
    end_offset: int
    kind: MentionKind
    # 仅 metric_phrase 使用：
    metric_role: Literal["base", "computed", "composite_unknown"] | None = None
    # 仅 metric_role="composite_unknown" 时允许非空：
    decomposition: DecompositionHint | None = None
    # 仅 dimension_phrase 使用（沿用现有 role 语义）：
    dimension_role: Literal["group_by", "filter", "display", "unresolved"] | None = None
    # 仅 filter_value 使用：所属 dimension_phrase 的 mention_id（可为空=待绑定推断）
    attached_to: str | None = None

class AnalysisExpression(BaseModel):
    """用户要的『计算结果』，与可检索的基础指标在类型上分离。
    注意：op=ratio 仅用于分子分母都在原文中的显式比率（of 引用两个 mention）；
    单短语复合指标（"客单价"）不建 expression，由 mention.decomposition 承载假设、
    解析层裁决（4.3.3）。"""
    model_config = ConfigDict(extra="forbid")
    expr_id: str                         # e1, e2, ...
    op: Literal["growth", "share", "ratio", "diff", "topn"]
    display_name: str                    # 用户原话（"环比增长率"/"占比"）
    of: list[str]                        # 引用 mention_id 或 expr_id
    over: Literal["time_comparison", "dimension_total", "explicit"] | None = None
    # 一个表达可要求多个产出（G001"变化了多少+增长率是多少"= growth 一个表达两个产出）：
    outputs: list[Literal["difference", "rate", "share", "value"]] = []

class MetricCondition(BaseModel):
    """指标阈值条件（"总GMV高于10000元"）。模型只表达条件本身；
    WHERE/HAVING 的归属由服务端按聚合层级确定性裁决（4.1.4），模型不输出 stage。"""
    model_config = ConfigDict(extra="forbid")
    metric_ref: str                      # 引用 mention_id 或 expr_id
    operator: Literal[">", ">=", "<", "<=", "=", "!="]
    value: float | int
    raw: str                             # 原文片段

class OrderRef(BaseModel):
    """排序引用已有指标/表达，不产生第二个指标槽位（G012"库存量降序"）。"""
    model_config = ConfigDict(extra="forbid")
    ref: str                             # mention_id 或 expr_id
    direction: Literal["asc", "desc"]
    selection: Literal["all", "top_n", "bottom_n"] = "all"
    limit: int | None = None

class MentionGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mentions: list[SemanticMention]
    expressions: list[AnalysisExpression]
    metric_conditions: list[MetricCondition] = []
    order: OrderRef | None = None
    intent_type: IntentType              # 沿用
    query_shape: QueryShape              # 沿用（去掉 comparison_type，见 4.2）
    unresolved_notes: list[str] = []     # 模型无法分类的原文片段，供澄清门参考
```

四个概念的对应（评审意见一的直接回应）：

| 概念 | 载体 | 例：G001 "总GMV…变化了多少，增长率是多少" | 例：G007 "销售订单平均客单价" | 例：G012 "期末库存并按库存量降序" |
|------|------|------------------------|------------------------------|------------------------------|
| 用户原话短语 | `SemanticMention.text` | m1="总GMV"(base) | m1="销售订单平均客单价"(composite_unknown, decomposition={ratio, "销售订单金额", "销售订单数"}) | m1="当前期末库存"(base) |
| 基础语义指标 | mention 解析后的资产绑定 | m1 → metric:总GMV | (a) 整短语命中派生资产；或 (b) 拆解假设各自检索命中 → RatioSpec | m1 → metric:期末库存 |
| 计算/比较表达 | `AnalysisExpression` | e1=growth(of=[m1], over=time_comparison, outputs=[difference, rate]) | 无 expression（拆解在解析层裁决，见 4.3.3） | — |
| 排序/条件 | `OrderRef` / `MetricCondition` | — | — | order.ref=m1 desc（"库存量"不生成第二个指标） |

#### 4.1.2 服务端强制的跨度不变量

沿用 temporal 域已验证的机制：`normalizer` 在校验前执行——

1. `text` 必须等于 `rewritten_question[start:end)`；不等时先在原文查找该 text 的唯一出现并**确定性修正偏移**；找不到或多处出现则该 mention 降级进 `unresolved_notes`，不作废整份输出；
2. mentions 两两跨度不重叠（`filter_value` attached 关系除外）；
3. `computed` 类 metric_phrase 必须被至少一个 expression 引用，否则确定性降级为 `composite_unknown`；
4. `decomposition` 仅允许出现在 `composite_unknown` 上；`numerator_text == denominator_text` 或任一为空 → 剥离该 hint（记账），mention 保留；
5. expression / metric_conditions / order 引用的 mention_id 必须存在；
6. **排序去重**：order 的目标短语若与已有 metric mention 指向同一对象（跨度重叠或绑定后同资产），不得存在独立的重复 metric mention——normalizer 先按跨度合并，绑定层再按"同组同资产合并"兜底（4.4.2 第 10 步）。

跨度带来的直接收益：**短语完整性从"提示词恳求"变成"可校验事实"**——检索层不再需要猜限定词（4.3 删启发式的前提）。

#### 4.1.3 metric_role 分类规则（写进提示词 + 服务端兜底词表）

- `computed`：增长率/降幅/环比/同比/占比/比例/贡献/差值等**对其他数值再计算**的表达。不进指标检索；必须挂 expression。服务端维护一个小型确定性词表兜底（模型漏标时后置修正），词表只做降级不做升级；
- `base`：可直接对业务事实聚合的指标短语；
- `composite_unknown`：既可能是已建模派生资产、也可能需要拆解的短语（客单价/毛利率/人效）。模型**可附** `decomposition` 假设（不强制；无假设时只走整短语检索）。解析顺序见 4.3.3。

#### 4.1.4 指标条件的阶段裁决（G008）

`MetricCondition` 的 WHERE/HAVING 归属**不由模型或规划 LLM 决定**，由服务端按聚合层级确定性裁决（依据 = RuntimeSnapshot.aggregation_stage，§4.7）：

- 条件目标是聚合指标（引用 base/composite 指标或表达） → `stage=having`，落 `QueryTaskSpec.having`；
- 条件目标是聚合前物理字段（明细过滤） → `stage=where`；
- 无法判定聚合层级 → 拒绝生成计划并给出明确错误（`METRIC_CONDITION_STAGE_UNRESOLVED`），**绝不静默放错位置**。

裁决结果落 `ResolvedMetricCondition`（4.4.1），配套计划校验断言"声明 having 的条件不得出现在 WHERE"（4.8），编译产物必须含对应 HAVING 子句。

#### 4.1.5 提及抽取不再注入 schema

现状统一理解注入 `available_dimensions`/`time_dimensions`（`understanding_service.py:471-486`），诱导模型做 premature binding 并对"多个时间字段"申报伪歧义（C5 的源头之一）。v2：

- 提及抽取输入 = 重写问题 + 会话继承上下文 + `question_categorization` instructions。**不含任何资产清单**；
- schema 的消费点整体后移到解析层（⑤）；
- `DimensionSlot` 现有的 `role=ambiguous` 语义改由 `dimension_role="unresolved"` 承载，**它不是澄清理由，是绑定层的待办**（见 4.5）。

#### 4.1.6 兼容

- `IntentRecognitionOutput` 保留为**由 MentionGraph 派生的只读投影**（`metric_mentions = [m.text for m in mentions if kind==metric_phrase]` 等），Graph 链路与存量 derived_state 快照消费不动；时间/比较旧字段的投影规则见 §4.2.8；
- 新旧理解通过 `CHATBI_MENTION_CONTRACT_ENABLED` 切换，旧路径冻结不迭代（与 doc 33 §7 的 react_legacy 策略一致）。

### 4.2 时间与比较：temporal 域唯一权威（D2）

**决策**：`ComparisonSpec` 从模型输出契约中移除，改为服务端从 `TemporalPlan.comparison` 派生的只读结构。这是与被合并稿的关键分歧点，裁决理由：被合并稿的抽取输出仍携带 `time.method`，只靠归一化兜底——而 G001 三轮全部死在抽取模型的比较枚举上；**字段在抽取契约中不存在，才结构性杜绝这类失败**。

1. 提及抽取模型对时间只输出 `time_expression` 提及（跨度登记）；**不输出** `time_range` / `time_ranges` / `comparison` / `query_shape.comparison_type`；
2. temporal 解析（已有独立调用与提示词，`prompts.py:49-102`）继续产出 `comparison={method: yoy|mom|custom, base, compare[]}` + 每个可执行区间一个 expression——它已经过 P0 真实验证，且自带跨度与角色（`query_filter` vs `metric_definition`）；
3. 服务端派生 `ComparisonSpec`（保持现有下游消费接口不变），来源标记 `source="temporal_authority"`；
4. **枚举防御**（R0 就做，先于契约切换）：在归一化层加确定性别名映射——`percent_change|difference|change|增长率 → 表达层语义`（即：这些不是比较方法，是 AnalysisExpression.op=growth/diff + outputs；比较方法只有 yoy/mom/custom 三种时段推导方式）。映射不丢信息：`method` 落 `custom` 或按 temporal 区间推导，"要百分比还是差值"落 expression.outputs；
5. `time_range`（旧单区间）字段进入退役流程：R0 起模型侧禁产（提示词+归一化剥离），服务端投影继续双写一个迭代，R2 移除模型契约字段，快照读兼容保留；
6. **比较权威的边界**：时段比较归 temporal；**维度值对比**（"对比 A 店和 B 店"）不是 ComparisonSpec，表达为 dimension filter 多值 + group_by（现有 `validation.py:59-60` 的处理原则保留并成文）；
7. `comparison` 缺 `base` 时：仅当多个明确时间表达可唯一推导时由 temporal 域自动补齐；否则按 temporal 既有状态机进入 `clarification_required`（USER_UNDERSPECIFIED），不猜测。

G001/G002/G009 类失败在此设计下**结构性消失**：模型不再被要求在窄枚举里表达开放语义。

#### 4.2.8 旧字段投影契约（消除双重事实源的落地规则）

现有代码与 doc 33 计划仍消费 `time_range`/`time_ranges`/`ComparisonSpec`/`query_shape.comparison_type`。这些字段全部改由**同一个确定性投影点**供给——`_apply_temporal_interpretation`（`understanding_service.py:553-557`）升格为唯一写入方，现有 `TemporalInterpretationResult` 的映射机制（`question_understanding.py:190-212`）升格为投影实现：

| 旧消费方字段 | 投影来源与确定性规则 |
|---|---|
| `time_ranges[]` | 每个 `role=query_filter` 且已解析的时间表达 → 一个 `TimeRange{raw=表达原文, value_status="provided", normalized=解析 AST, interpretation_source="model"或"user_confirmation"}`，按表达 `start_offset` 升序排列；`role=metric_definition` 的表达**不投影**（属指标口径，交由绑定/编译消费） |
| `time_range`（旧单区间） | 恒等于 `time_ranges[0]`；无区间时 `value_status="not_provided"`。仅供旧消费方与快照兼容，**不参与任何新决策** |
| `ComparisonSpec` | `TemporalPlan.comparison` 直投：`method` 直传，`base`/`compare` 按原文匹配到对应 TimeRange（匹配不到时保留原文字符串）。temporal 无 comparison、但 resolved 区间 ≥2 且存在 growth/diff 表达时 → `method="custom"`，**base = 起点最早的区间**（确定性规则），compare = 其余按起点升序 |
| `query_shape.comparison_type` | = 派生 ComparisonSpec.method 的只读投影，服务端回填；模型输出的该字段在归一化层剥离 |
| 冲突裁决 | **不存在运行时冲突裁决**：模型输出的 time_range/time_ranges/comparison/comparison_type 属『模型禁产字段白名单』，归一化层一律剥离入 dropped_fields（预期剥离，记账但不触发 4.6 的关键路径保护）；temporal 权威是这些字段的唯一写入方 |
| 旧快照 | derived_state 中旧结构只读回放；plan_patch/澄清恢复要求 v2 结构存在，缺失则重新走完整理解，**不做旧→新反向构造** |

### 4.3 解析层：mention → candidates（D3）

1. `SemanticBindingQueryPlanner`（`planner.py`）输入从 `intent.metric_mentions` 字符串改为 `MentionGraph.mentions`：
   - `metric_phrase(base | composite_unknown)` → METRIC 槽，检索文本 = `mention.text`（完整性由跨度保证）；
   - `dimension_phrase` → DIMENSION 槽（role 透传）；
   - `filter_value` → VALUE 槽（沿用 `value_lookup_slots` 的可检索性判断，`planner.py:227-237`）；
   - `metric_phrase(computed)` **不生成 METRIC 槽**；`metric_conditions`/`order` 只含引用，不产生新槽；
2. **删除** `_metric_retrieval_text`（`planner.py:163-188`）及其 CJK 邻接启发式——G003/G004/G008/G011 类"整句当指标检索文本→召回发散→无法收敛"的通道关闭。完整问题可作为重排（rerank）上下文传入，但**不得作为任何槽位的检索文本**；
3. `composite_unknown` 的分段确定性解析（顺序固定、逐步可审计；(a) 优先于 (b) 的理由：语义资产定义是治理过的事实源，模型假设只是线索）：
   - **(a) 整短语解析**：整短语 METRIC 检索——命中派生指标资产（`metric_refs`/ratio 已建模）即按普通指标走（RatioSpec.origin="asset_definition" 由资产定义展开，或直接派生编译）；
   - **(b) 拆解解析**（仅当 (a) 未命中且 `mention.decomposition` 存在）：
     1. `numerator_text` / `denominator_text` **各自作为独立 METRIC 槽的检索文本**发起检索——hint 只是检索文本，不含任何资产信息，模型无法借 hint 指定资产；
     2. 两槽必须**各自达到 RESOLVED**（与普通指标同一门控阈值与歧义带）；任一 MISSED 或 AMBIGUOUS → 整体按 (c) 处理。**不为用户没有说过的假设短语发起澄清**——向用户询问其从未提及的词是不可解释的交互；
     3. 两资产必须属于**同一模型**（RuntimeSnapshot.models_of 判定）；跨模型 → 固定失败码 `RATIO_CROSS_MODEL_UNSUPPORTED`，按 (c) 处理；
     4. 全部满足 → 服务端先执行**方向校验**：优先使用复合指标资产定义中的有序 `metric_refs`，否则使用 RuntimeSnapshot 已确认的 `numerator` / `denominator` 角色；反向绑定固定失败 `RATIO_DIRECTION_MISMATCH`，无法证明方向固定失败 `RATIO_DIRECTION_UNPROVEN`，两者都不进入用户澄清；
     5. 方向校验通过后生成 `RatioSpec{origin="decomposition_hint"}`（4.4.1），composite mention 记为 BOUND（绑定到 ratio 而非单资产），编译期强制 NULLIF 保护；
   - **(c) 统一失败处置**（固定行为，进入 confidence 四档路由，不进澄清门）：
     - STRICT 数据集：拒答，reason_code ∈ {`COMPOSITE_METRIC_UNRESOLVED`, `RATIO_CROSS_MODEL_UNSUPPORTED`}，回答话术固定——未命中："未找到『销售订单平均客单价』的认证口径，可分别查询『销售订单金额』『销售订单数』"；跨模型："该口径的分子分母分属不同语义模型，当前不支持跨模型比率，建议分别查询"；
     - ASSISTED 数据集：受控兜底并标注非认证口径；
     - 该行为写入黄金 behavior 断言（此类题 behavior=reject 或 assisted_fallback，机器可判）。
4. 指标槽 MISSED 时，**不得**让维度槽先进入用户澄清（门控顺序规则，4.5.2）；
5. 召回、门控阈值、证据分层、RRF、value 归一全部沿用——本节只改**输入**，不动检索内核。

### 4.4 绑定层：QueryGroup（D4，本文核心）

#### 4.4.1 契约

`AssetReference` 沿用 `apps/retrieval/models/dto` 现有定义（`asset_type` / `asset_id` / `model_id`），不重复定义。以下为完整新增 DTO：

```python
# apps/retrieval/models/dto/resolution.py [新]

EvidenceLevel = Literal[
    "exact", "alias", "rerank", "lexical", "dense",
    "constraint_resolved",   # 非分数取胜：由约束传播唯一化（4.4.2 第 3 步）
]

class MentionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mention_id: str
    asset_id: int
    asset_type: RetrievalResourceType
    evidence: EvidenceLevel
    canonical_value: str | None = None          # VALUE 槽归一结果

class TimeBindingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension_id: int                           # 本组时间维度资产
    grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    source: Literal["explicit", "model_default"]
    mention_id: str | None = None               # source="explicit" 时对应的提及

class RatioOperand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str                                   # 实际使用的检索文本（资产定义名或假设短语）
    asset_id: int
    evidence: EvidenceLevel

class RatioSpec(BaseModel):
    """composite 指标的比率落地。构造时校验：分子分母资产同属本组 model_id；
    编译期分母强制 NULLIF 保护。"""
    model_config = ConfigDict(extra="forbid")
    source_mention_id: str                      # composite 短语的 mention
    origin: Literal["asset_definition", "decomposition_hint"]
    numerator: RatioOperand
    denominator: RatioOperand

class ResolvedMetricCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: MetricCondition                  # 理解层原始条件（含引用与原文）
    target_group_id: str
    target_asset_id: int | None                 # 绑定后的指标资产；表达类条件为 None
    stage: Literal["where", "having"]           # 4.1.4 裁决结果

class QueryGroupBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str                               # g1, g2, ...
    dataset_id: int
    model_id: int
    metric_bindings: list[MentionBinding]
    dimension_bindings: list[MentionBinding]    # 同一 mention 在不同组可绑不同资产（G005 的解）
    value_bindings: list[MentionBinding]
    time_binding: TimeBindingSpec
    ratio_specs: list[RatioSpec] = []

class UnresolvedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mention_id: str
    state: Literal["PENDING_BINDING", "ASSET_AMBIGUOUS", "USER_UNDERSPECIFIED", "MISSED"]
    candidates: list[AssetReference] = []       # ASSET_AMBIGUOUS 时的澄清选项（≤3）
    reason_code: str
    blocking: bool                              # False = 可继续部分执行

class ResolutionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[QueryGroupBinding]
    expressions: list[AnalysisExpression]       # 透传 + 解析批注（ratio 落组等）
    metric_conditions: list[ResolvedMetricCondition]
    order: OrderRef | None
    unresolved: list[UnresolvedItem]
    snapshot_fingerprint: str                   # 本次绑定读取的 RuntimeSnapshot 指纹（4.7）
    diagnostics: RetrievalDiagnostics           # 沿用
```

#### 4.4.2 分组与绑定算法（确定性，无 LLM，决策表驱动）

回应实施评审第 2 项："取第一候选还是全部拆分"不允许在开发时临时决定，下列规则与 tie-break 是穷尽的。

```
输入（全部随 Run 冻结）：
  每个 mention 的候选集（4.3 产出：证据层级 + 分数，已过门控硬阈值）
  SemanticRuntimeViewSnapshot（4.7，本次绑定唯一事实源）
配置（不新增拍脑袋阈值，全部沿用既有配置）：
  歧义带阈值 = profile 现有 _ambiguity_reason 同源阈值
  组数上限 = CHATBI_PLAN_MAX_QUERY_TASKS（默认 5）
  澄清选项上限 = 3

第 1 步　指标槽独立判定（沿用 _apply_slot 证据分层）
  RESOLVED（top1 与 top2 证据层级差 ≥1 级，或同级分差超出歧义带）→ 锁定资产
  AMBIGUOUS → 保留达标候选集 C_m，排序键 =（证据层级, 分数降序, asset_id 升序）——顺序确定
  MISSED → 该 mention 按四档路由处置（blocking=True）

第 2 步　约束传播收敛（迭代至不动点；禁止候选组合枚举，永不做笛卡尔积——
        复杂度 O(候选数 × 迭代轮数)，无组合爆炸，故不需要组合数上限）
  2a 同模型收敛：已锁定指标的模型集合 M*；对每个 AMBIGUOUS 指标，
     若 C_m 与 M* 的模型有交集 → C_m := 交集内候选
  2b 兼容收敛：对每个 AMBIGUOUS 指标，按显式 dimension mention 的
     capability 兼容过滤 C_m（事实源 = Snapshot.compatible）
  2c 任一 C_m 被某步收缩为空 → 回退该步收缩前的集合并停止对该槽收缩
     （约束只允许缩小到非空，不允许清空后静默换路径）；重复 2a/2b 直至整体不动点

第 3 步　剩余多候选裁决（决策表，按行序命中即停——这是唯一的"自动绑 vs 澄清"裁决点）

  | # | 条件 | 结果 |
  |---|------|------|
  | 1 | C_m 唯一 | 自动绑定；由约束唯一化的记 evidence="constraint_resolved" |
  | 2 | C_m 内候选业务口径等价（Snapshot.metric_identity 相同，或名称+聚合表达式指纹一致——同一指标在多模型的副本） | 自动绑定，tie-break 顺序：① 与 M* 同模型 ② 对显式维度的 capability 兼容数多者 ③ model_id 升序 ④ asset_id 升序；口径卡片披露"存在等价口径副本" |
  | 3 | C_m 内候选口径不同 | ASSET_AMBIGUOUS：选项 = C_m 前 3（第 1 步排序），blocking=True，展示各候选口径定义与所属模型 |

第 4 步　分组
  按锁定指标的 model_id 分组 → QueryGroup[]（同模型多指标合一组；无指标的 detail 查询单组）
  组数 > 上限 → 不执行，产出 `PLAN_QUERY_GROUP_LIMIT_EXCEEDED`（"查询组数量超过系统计划上限，请拆分提问"）；这是计划预算错误，不进入 USER_UNDERSPECIFIED 澄清门

第 5 步　组内维度绑定：每个 dimension mention 在组的 model 语境下重新选择候选
  候选先按「与本组指标的 capability 兼容」过滤（Snapshot.compatible）；
  剩余多候选复用第 3 步决策表，tie-break 改为：① capability 兼容 ② 证据层级 ③ asset_id 升序
  同名维度多模型歧义在此自然消解：每组选自己模型的那个资产

第 6 步　组内互斥收敛：现 _constrain_metric_dimension_candidates 的迭代收敛逻辑
  保留算法、缩小作用域到组内；收敛到空 → 该组 ASSET_AMBIGUOUS 或 MISSED，
  不再全局抛 SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE 杀死整个请求

第 7 步　组内时间绑定：显式时间维度提及优先（source="explicit"）；
  否则 Snapshot.default_time_dimension(model_id)（source="model_default"）
  （bind_default_time_dimensions 迁入本步）；模型确有多个时间维度且无默认标记
  且查询需要时间 → ASSET_AMBIGUOUS（这是唯一允许就时间字段澄清的情形）

第 8 步　值绑定：VALUE 槽结果挂到组内对应维度（canonical 替换沿用）

第 9 步　ratio 落组：RatioSpec 挂到分子分母所属组（4.3.3 已保证同模型）

第 10 步　同资产合并：同组内绑定到同一资产的多个 metric mention 合并为一个绑定
  （记录合并审计；排序引用改指向合并后 binding）——G012 双识别的绑定层兜底

第 11 步　条件裁决：MetricCondition 按 4.1.4 规则（Snapshot.aggregation_stage）
  产出 ResolvedMetricCondition 并挂到目标所在组
```

#### 4.4.3 消解的现存机制

| 现存机制 | 处置 |
|---------|------|
| `_constrain_metric_dimension_candidates` 全局互斥（`policy.py:568`） | 算法保留，作用域缩到组内（第 6 步） |
| `_cross_model_query_plans` / `_assets_for_model` 名称匹配（`payload.py:823-966`） | **退役**——跨模型拆分由分组结构直接产出，不再事后翻译 |
| `_add_intent_dimensions_for_model`（`payload.py:876`） | 退役（组内绑定天然覆盖） |
| `bind_default_time_dimensions`（`policy.py:954`） | 迁入组内时间绑定（第 7 步），消费 RuntimeSnapshot |
| `multi_query_plans` payload 契约 | 保留一个迭代作为 ResolutionOutcome 的投影（Graph/旧消费方），R3 后评估移除 |

#### 4.4.4 规划层对接

- `AnalysisPlanner` 输入从 `semantic_state` dict 改为 `ResolutionOutcome`（强类型）：
  - QueryGroup → QueryTask **结构同构映射**（group 的 metric/dimension/value/time_binding 直接落 `QueryTaskSpec` 对应字段，不再有 `_multi_query_task` 的字典挖掘）；QueryTask 元数据记录 `group_id` 与时间区间，供 compute join 与作答引用；
  - 多时段（temporal 派生的 N 个区间）× QueryGroup 的展开保留，但对应关系显式落任务元数据；
  - ComputeTask 的 `join_on` = **组间共同已绑定维度的交集**（确定性推导，替代 `shape.join_on or package.dimensions` 兜底）；无分组双时段对比 → 显式无键对齐（doc 34 修复 #5 保留）；
  - **无计算关系的多结果**：多个 QueryTask 且无任何表达要求合并时，全部结果集交给 AnswerComposer 并列呈现（doc 34 修复 #6 升格为契约规则），**禁止默认只展示第一个结果**；
  - AnalysisExpression → ComputeTask 映射表：

| 表达 | 计划形态 |
|------|---------|
| growth + temporal.comparison 固定周期（yoy/mom 单粒度） | 单 QueryTask + `time_offset`（P1-6 LAG 渲染），复杂区间 → 双 QueryTask + GROWTH（沿用既定决策）；outputs 决定产出差值列/比率列 |
| growth + custom 双区间 | 每区间一个 QueryTask + GROWTH |
| share | QueryTask + SHARE（分母语义显式化：`within=dimension_total` 时同结果集聚合，全量合计时免分母查询） |
| ratio（显式双对象或 RatioSpec） | origin="asset_definition" → 编译期 ratio（metric_expansion）；origin="decomposition_hint" → 同 QueryTask 双指标 + EXPR（NULLIF 保护） |
| diff | COMPARE |
| topn | OrderRef 进 QueryTaskSpec 排序/limit；"TopN+其他" → TOPN_OTHER |

- STRICT 门槛修正：`SEMANTIC_QUERY_METRIC_REQUIRED` 的判定对象从 "metric_mentions 非空" 改为 "存在已绑定 base 指标或已落地 RatioSpec"——computed 表达不再触发该错误（G001/G009 的计划期风险解除）。

### 4.5 澄清门：三态责任模型（D5）

#### 4.5.1 三态定义与处置

| 状态 | 定义 | 判定者 | 处置 |
|------|------|--------|------|
| `USER_UNDERSPECIFIED` | 用户表达本身缺必需信息："最近"无数量（temporal 已识别）、排名无方向且无默认 | 理解/时间解析/澄清门 | 澄清（立即，这是唯一允许在绑定前出门的澄清） |
| `ASSET_AMBIGUOUS` | **绑定完成后**，组内某 mention 仍有 ≥2 个候选，且候选间业务口径确实不同（4.4.2 第 3 步决策表第 3 行、第 5/7 步引用），证据分差在歧义带内 | 澄清门 | 选项式澄清，选项=C_m 前 3（沿用现有卡片机制，展示口径定义与模型范围） |
| `PENDING_BINDING` | 系统内部未完成状态：指标未绑定时的维度同名多候选、默认时间维度未加载、值未归一 | 绑定各步 | **禁止澄清**。继续绑定；绑定后自动消解或转前两态 |
| `PLAN_QUERY_GROUP_LIMIT_EXCEEDED` | 已绑定查询组数量超过系统计划预算 | 计划校验 | 计划拒答或受控部分执行，不进入用户澄清 |

**明确禁止向用户澄清的状态**（全部属于内部错误或 PENDING_BINDING，出现即按对应内部错误码处理）：DTO 字段/格式错误、字符串时间未归一化、比较字段缺兼容转换、指标尚未绑定导致的维度候选过多、默认时间维度尚未按模型确定、拆解假设短语的检索歧义（4.3.3-b2）、计划校验内部错误。

#### 4.5.2 门控顺序规则（防止伪澄清的硬约束）

1. 维度歧义**不得**在其所属组的指标绑定完成前判定（指标定模型 → 同名维度自然消解）；
2. 时间字段多候选**不得**在查询模型默认时间维度之前判定（`is_default_time` 已建模；仅 4.4.2 第 7 步的兜底情形可澄清）；
3. 指标槽 MISSED 时不得先就维度/值发起澄清（先解决主语义对象）；
4. 理解层 `dimension_role="unresolved"`、`unresolved_notes` 一律入 `PENDING_BINDING`，不直接映射澄清——`validation.py:263-274` 的 `ambiguous_slots → intent_ambiguous` 无条件分支**删除**；
5. 每个 Run 澄清预算沿用；超预算或用户拒绝 → confidence 四档的拒答/披露分支（`planning/confidence.py` 接口不变，输入信号加 `unresolved` 摘要）；
6. `blocking=False` 的 unresolved 项（如某组 MISSED 但其他组完整）→ 部分执行 + 作答披露（doc 32 §4.4 部分作答机制复用）。

#### 4.5.3 效果对照

- G002（两月订单数对比）：时间两区间 temporal 已解析、指标唯一命中、无维度歧义 → 无任何状态可出澄清门 → 直接执行。第三轮的 waiting_user 不再可能；
- G005（跨模型双指标）：两组各自完整 → 直接执行双 QueryTask；
- G006（跨模型双口径对比）：两组完整但表达要求 compare、口径不同 → 属"中置信直答 + 口径披露"（confidence disclose 档），**不澄清**——把"不同口径不能错误合并"做成披露而非拦截；若产品坚持确认，配置为数据集级 clarify 可选项。

### 4.6 语义保真修复协议（D6）

替换 `_invoke_validated_model` 的"整份重生成"，四层递进：

```
第 1 层 结构化输出约束（已有 json_mode=STRICT，保留）
第 2 层 确定性归一化（校验前，无 LLM）
   - 枚举别名表：per-stage 注册（comparison.method 别名→表达语义迁移等）
   - 字段迁移：已知的新旧结构搬运（time_range→time_ranges 等）
   - 模型禁产字段白名单：4.2.8 所列 temporal 专属字段一律剥离（预期剥离，
     记账但不触发下述关键路径保护）
   - 路径级剥离：其余 extra 字段按【路径】剥离并记入 dropped_fields 账本，
     不再让一个多余子字段作废整个对象（G001 首轮 comparison.target 场景）
   - 剥离审计：dropped_fields 命中语义关键路径（mentions/expressions/
     metric_conditions/order 前缀，且不在禁产白名单内）时 → 不得静默丢弃，
     转第 3 层或显式失败
第 3 层 字段级补丁修复（至多 1 次 LLM，替代整份重生成）
   - 输入：仅失败路径 + 原值 + pydantic 错误 + 该路径的子 schema
   - 输出：{"patches": {"<json-path>": <new-value>}}，服务端 deep-merge 后重校验
   - 模型物理上无法"顺手"删掉别的字段；补丁再失败即终止，
     走确定性收敛或澄清，不做二次补丁
第 4 层 语义不变量校验（补丁合并后，确定性）
   - mention 文本集合不变（顺序无关）
   - 时间表达数量不减；temporal comparison 存在性不变
   - expressions / metric_conditions 数量不无理由减少
   - 排序引用与条件引用仍可解析；decomposition 假设不被改写为其他短语
   - 违反 → 按理解契约错误处理并留痕（understanding_contract_error），
     不进检索、不向用户伪装成歧义，绝不静默接受"语义变了的合法 JSON"
```

目标是第 3 层的调用率随归一化表扩充趋近于零（门禁 <5%，§7.3）；JSON 完全不可解析时允许一次"仅输出合法 JSON"的格式重试（现有 `repairable_format_errors` 机制保留），但产物仍过第 2/4 层。

落点：`_invoke_validated_model`（`understanding_service.py:819`）重构为 `normalize → validate → patch-repair → invariants` 流水线，规划模型调用（`analysis_planner.py:64-130` 的 retry）同样接入第 2/3/4 层。`extra="forbid"` 在 DTO 上保留——它仍是最终防线，只是不再是第一道接触面。

### 4.7 SemanticRuntimeViewSnapshot：Run 级冻结的运行时语义事实源

**现状**：五类运行时语义规则散落五层，各自推断——默认时间维度在检索层（`policy.py:954`）、指标-维度兼容在策略层（`policy.py:807` + capability 契约）、派生指标在编译层（`metric_expansion.py`）、HAVING 在理解 DTO（`query_shape.having`）、跨模型拆分在投影层（`payload.py:823`）。上游认为已识别、检索认为无法命中、计划认为可生成、编译器缺必要条件——G004/G007/G011 的"层间互相否定"由此而来。

**目标**：一个 Run 级冻结的不可变快照，检索、绑定、澄清门、计划、校验、编译**只读同一实例**：

```python
# apps/semantic/services/runtime_view.py [新]

@dataclass(frozen=True)
class SemanticRuntimeViewSnapshot:
    """一次 Run 的运行时语义事实快照。构建后不可变；管线 ④-⑨ 只读同一实例。"""
    dataset_id: int
    schema_version: int                   # DatasetSchema 版本（payload.py:_schema_version 同源）
    contract_version: int                 # 语义契约版本
    permission_version: str | None        # 构建时的权限版本
    fingerprint: str                      # 上述版本 + 构建输入的哈希

    # 查询接口（纯函数，构建后无任何 IO）：
    def default_time_dimension(self, model_id: int) -> TimeDimensionSpec | None: ...
    def compatible(self, metric_id: int, dimension_id: int) -> CapabilityVerdict: ...
        # 唯一事实源 = headless_metric_dimension_capability 契约（落实 doc 32 §5.1.5）
    def metric_composition(self, metric_id: int) -> MetricComposition: ...
        # base | derived(metric_refs) | ratio | snapshot | preagg 要求
    def metric_identity(self, metric_id: int) -> str: ...
        # 业务身份/口径指纹——4.4.2 第 3 步"口径等价"判定依据
    def aggregation_stage(self, target: ConditionTarget) -> Literal["where", "having"] | None: ...
        # 4.1.4 条件裁决依据；None = 无法判定（触发 METRIC_CONDITION_STAGE_UNRESOLVED）
    def models_of(self, asset_ids: Sequence[int]) -> dict[int, int]: ...
```

**构建与生命周期**（回应实施评审第 3 项）：

| 关切 | 规则 |
|------|------|
| 数据来源 | **权限裁剪后的** `DatasetSchema`（`schema_loader` 现有租户/可见性裁剪；P2-4 资产级授权落地后自动收紧）+ `headless_metric_dimension_capability` 契约行 + 指标 ORM（`metric_refs`/expr/`snapshot_aggregation`/预聚合要求）+ 维度 `ext_info.is_default_time` + 关系契约。**不引入新表** |
| 构建者 | semantic 域 `build_semantic_runtime_snapshot(dataset_id, principal)`，在 Run 准备阶段（提及解析之前）调用一次 |
| 缓存 | 进程内按 `(dataset_id, schema_version, contract_version, permission_version)` 键缓存；语义资产发布使 schema_version 变化自然失效 |
| Run 冻结 | Run 开始时解析一次，`fingerprint` 写入 derived_state 并回填 `ResolutionOutcome.snapshot_fingerprint`；同一 Run 的所有阶段经 pipeline state 传递同一实例，**不得重复加载** |
| 澄清恢复/重试 | 按 fingerprint 复核：版本未变 → 复用；已变 → 重建快照并从提及解析阶段重跑（旧计划作废），**禁止新旧版本混用** |
| 权限 | 快照本身是权限裁剪后的视图并携带 permission_version；执行前第三道闸照旧全量复验——快照不是安全边界（doc 32 公理 7） |

消费方：绑定（4.4.2 第 2b/5/6/7/11 步）、澄清门（默认时间判定）、规划（表达式落组）、计划校验、编译前检查。**各层自有的兼容启发式在消费方接入后逐个删除**（`compatible_dimension_ids` 宽口径启发式退位，doc 32 已有此计划，本文给它一个物理落点）。

### 4.8 计划校验增强：证据断言

针对 G003/G008 "finished 但口径缺失"，在计划校验（执行前）追加**编译产物结构断言**（不是 SQL 正则）：

1. QueryTask 声明了 `time_range` → 编译计划的过滤集合中必须存在对应时间维度谓词，且区间与声明一致；
2. QueryTask 声明了 `having` → 编译产物必须含 HAVING 子句且阈值一致；**声明为 having 的条件不得出现在 WHERE**（降级即失败）；
3. 时间区间数量与比较关系在"理解→计划→编译"全程不减（与 4.6 不变量衔接）；
4. 断言失败 → `PLAN_COMPILE_EVIDENCE_MISSING`，走修复子循环或部分作答，**绝不带着缺口径的 SQL 进执行**。

### 4.9 回答证据链修正

第三轮 G008/G009 的 claims 校验系统性失败暴露两个问题，修正如下：

1. **数值归一**：claim 校验比较前统一归一——百分比（`12.3%` ↔ `0.123`）、中文单位（`4.32万` ↔ `43200`）、有效数字（沿用 4 位规则）；
2. **派生数值必须来自结果列**：占比/增幅等展示值必须存在于 SHARE/GROWTH compute 结果集的列中，composer 不做算术（G003 首轮"回答侧推导百分比"关闭）；表达链路（4.4.4）保证这些列一定存在；
3. 校验失败的降级粒度从"整答案表格直出"细化为"仅该 claim 降级"，保留其余已验证结论；
4. 多结果集并列呈现时（4.4.4 无计算关系规则），每个结果集独立引用，不合并口径。

---

## 5. 12 案例 → 设计项映射（回归验收对照表）

| 用例 | 根因（§1.2） | 修复设计项 | 最早修复阶段 | 回归断言（分层黄金） |
|------|------------|-----------|------------|-------------------|
| G001 | C1 枚举拒绝 + computed 混入 mentions | 4.2 枚举防御（R0）→ 4.1 表达分离（outputs=[difference,rate]） | **R0** | 理解层：m1=总GMV(base)、e1=growth(outputs 双产出)；计划层：2 QueryTask + GROWTH |
| G002 | C1→C5 伪澄清 | 4.2（R0）+ 4.5 门控 | **R0**（枚举）/R2（澄清门） | 行为=direct；两 QueryTask 各带区间 |
| G003 | C3/C4 + 证据断言 | 4.4.4 share 映射 + 4.8 时间谓词断言 | R2/R3 | 计划层：SHARE 节点存在；SQL 层：含 2026-06 谓词；占比值来自 compute 结果列 |
| G004 | C4 全局互斥 | 4.4.2 组内收敛 + 4.7 capability 事实源 | **R2** | 绑定层：g1(model=渠道模型) 含销售件数+交易渠道 |
| G005 | C4 单 bundle | 4.4 QueryGroup | **R2** | 绑定层：2 组；计划层：2 QueryTask；行为=direct；两结果集全部进入回答 |
| G006 | C4 + 澄清定位 | 4.4 + 4.5.3（disclose 档） | R2 | 行为=direct+口径披露（或配置化 clarify_allowed）；两口径不合并 |
| G007 | C3 派生不可表达 | 4.1.1 DecompositionHint + 4.3.3 分段解析 + RatioSpec | **R1** | 理解层：m1=composite_unknown 携拆解假设；绑定层：整短语命中派生资产，或 RatioSpec(分子分母同模型、各自 RESOLVED)；SQL 含 NULLIF |
| G008 | 证据断言 + claims | 4.1.4 条件契约 + 4.8 HAVING 断言 + 4.9 | R1（契约）/R3（断言） | 理解层：MetricCondition(m1,>,10000)；绑定层：stage=having；SQL 层：HAVING gmv>10000 且含 6 月谓词，条件不在 WHERE |
| G009 | C1 双字段 + claims | 4.2 单一权威（R0 禁旧字段）+ 4.9 归一 | **R0** | 理解层：仅 temporal 产出区间；结果层：环比列来自 time_offset/GROWTH |
| G010 | C2 修复重写 + C4 join | 4.6 补丁协议 + 4.4.4 join 推导 | R0（修复协议）/R2（join） | 不变量：修复后区间数=2、comparison 保留；结果层：同比值非空 |
| G011 | C3 检索污染 + 全局互斥 | 4.3 删启发式 + 4.4 组内绑定（预聚合编译已有） | R1/R2 | 绑定层：预聚合指标命中；SQL 层：先聚合子查询后 join |
| G012 | —（已通过） | 全程回归保护 + 4.1.2 排序去重 | 持续 | 三层断言全绿不回退；仅一个库存基础指标 + order.ref 引用，无重复指标槽位 |

---

## 6. 分阶段落地

约定沿用 doc 33（`[新]/[改]/[删]/[测]` 图例；DoD=代码+测试+黄金题+文档+开关可回退）。**本重构不新增 alembic 迁移**——所有新契约生存于 DTO、服务层与 `derived_state` JSON。

**工程约定**（沿用仓库既有分层规范）：跨模块只依赖公开 DTO 与 Service；纯归一化/绑定/校验规则用模块级函数，不新增无状态单方法 Service；不在检索层导入语义 ORM、不在理解层绑定物理字段；每笔兼容投影登记 `backend/COMPAT_LEDGER.md` 并绑定删除条件。

### R0 止血 + 观测冻结（≈1 周，双轨并行）——目标：C1/C2 类失败清零 + 任何失败可归因到阶段

**止血轨**（行为变更，低风险可回退）：

| # | 事项 | 文件 |
|---|------|------|
| 1 | 枚举别名归一表：`comparison.method` 别名（percent_change/difference/…）确定性迁移；`normalizer` 前置于校验 | `[新]` `apps/chatbi/services/understanding/normalization.py`；`[改]` `understanding_service.py`（`_normalize_unified_payload` 接入） |
| 2 | 路径级 extra 剥离 + dropped_fields 账本 + 禁产白名单 + 语义关键路径保护 | 同上 |
| 3 | 修复改字段级补丁：`_invoke_validated_model` 重构为 normalize→validate→patch→invariants；不变量最小集（时间表达数不减 / comparison 存在性不变 / mention 文本集不变） | `[改]` `understanding_service.py:819-968` |
| 4 | 模型侧禁产 `time_range` 旧字段与 `comparison`（提示词收缩 + 归一化剥离），`_apply_temporal_interpretation` 升格为旧字段唯一投影点（§4.2.8 契约落码） | `[改]` `understanding/prompts.py`、`understanding_service.py` |
| 5 | `ambiguous_slots → intent_ambiguous` 无条件分支降级：仅 `USER_UNDERSPECIFIED` 白名单 reason 可在理解层出澄清，其余标记 pending 交由绑定 | `[改]` `understanding/validation.py:263-274`、`orchestration/agent/preparation.py` preflight 消费处 |
| 6 | 规划模型 retry 接入同一补丁协议 | `[改]` `services/planning/analysis_planner.py:64-130` |

**观测轨**（无行为变更，建立测量地基——吸收自被合并稿 R0）：

| # | 事项 | 文件 |
|---|------|------|
| 7 | G001–G012 转为阶段级 fixture（每题落理解/绑定/计划三层期望骨架，先填已知层） | `[改]` `backend/scripts/p1_golden_cases.jsonl` |
| 8 | 全链快照落 Trace：原始模型输出、归一化结果与 dropped_fields、检索请求、候选集、绑定结果、计划快照逐阶段持久化（多数已有 trace 节点，补齐缺口并统一命名）。**快照治理**：input_detail 沿用结构化截断（默认单节点 ≤32KB，可配）；保留期对齐事件保留期（doc 16 §10，归档策略 P2 落地前默认 30 天）；VALUE 槽原值/canonical 值与结果行样本按数据源列 `sensitive_level` 脱敏后入快照；dropped_fields 账本只存字段路径与值摘要，不含结果行数据 | `[改]` `understanding_service.py`、`pipeline/{fast,plan_mode}.py` trace 节点 |
| 9 | Run 落 `semantic_contract_version` / `binding_contract_version`（derived_state），影子对照与评测分组依据 | `[改]` `agent_run` derived_state 写入点 |
| 10 | 评测脚本修正（§7.4）：`task.finished` 计入 SQL 执行、区分应澄清/错澄清、保存阶段产物为 fixture | `[改]` `backend/scripts/run_mall_store_agent_fresh_20.py` |
| 测 | 每条别名/剥离/不变量一个 case；G001/G002/G009/G010 理解层黄金断言 | `[新]` `tests/chatbi/test_semantic_normalization.py`、`test_repair_invariants.py` |

**开关**：`CHATBI_SEMANTIC_REPAIR_V2`（默认开，关=回旧行为）。
**验收门禁**：结构门槛（§7.3）——`understanding_failed`=0、不变量违反=0、失败归因=100%；业务预期（非门禁）——12 题约 5 通过，G001/G002/G009 进入执行阶段。

### R1 提及契约（2-3 周）——目标：理解输出携带跨度与表达，检索消费 mention

| # | 事项 | 文件 |
|---|------|------|
| 1 | `SemanticMention` / `DecompositionHint` / `AnalysisExpression` / `MetricCondition` / `OrderRef` / `MentionGraph` DTO + 跨度不变量 normalizer + computed 兜底词表 + 排序去重 + 拆解假设校验 | `[新]` `apps/chatbi/models/dto/mention.py`；`[改]` `question_understanding.py`（投影字段） |
| 2 | 提及抽取提示词（替换统一理解的指标/维度部分；schema 注入移除；temporal 提示词不动；拆解假设的产出规则与示例） | `[改]` `understanding/prompts.py`、`understanding_service.py`（`QUESTION_UNDERSTANDING` 阶段输出改 MentionGraph） |
| 3 | 指标条件的 stage 裁决规则（4.1.4，先落服务函数，R3 接 RuntimeSnapshot） | `[新]` `services/understanding/condition_stage.py`（或并入 normalization） |
| 4 | 检索规划器消费 mention；**删除 `_metric_retrieval_text`**；computed 不建 METRIC 槽 | `[改]` `apps/retrieval/projection/planner.py`（:47-59 输入、**:163-188 删除**） |
| 5 | composite 分段解析（整体→拆解→固定失败处置，含 `RATIO_CROSS_MODEL_UNSUPPORTED` 行为与话术）与 RatioSpec 生成 | `[改]` `apps/retrieval/query/semantic_binding.py` 或独立 `resolution.py` 前置步 |
| 6 | STRICT 指标门槛修正（`SEMANTIC_QUERY_METRIC_REQUIRED` 判定对象改为"已绑定 base 或 RatioSpec 已落地"） | `[改]` `apps/semantic/services/query/planning.py:38` 调用侧 |
| 7 | `IntentRecognitionOutput` 投影与 Graph/快照兼容层（含 §4.2.8 时间/比较投影） | `[改]` `question_understanding.py`、`orchestration/agent/semantic_projection.py` |
| 测 | 跨度不变量、词表降级、拆解各分支（命中/单边失败/歧义/跨模型）、条件裁决、排序去重 | `[新]` `tests/chatbi/test_mention_graph.py`、`tests/retrieval/test_mention_resolution.py` |

**开关**：`CHATBI_MENTION_CONTRACT_ENABLED`（灰度；关=R0 形态）。
**验收门禁**：结构门槛——理解层结构断言 12/12（mentions/expressions/conditions/order/temporal，含 G007 拆解结构与 G008 条件结构）；检索层无整句指标检索文本（trace 断言）。

### R2 QueryGroup 绑定（3-4 周）——目标：跨模型正确性内建，澄清门后移

| # | 事项 | 文件 |
|---|------|------|
| 1 | `ResolutionOutcome` 及全部子 DTO（4.4.1） | `[新]` `apps/retrieval/models/dto/resolution.py` |
| 2 | 分组与组内绑定算法（4.4.2 十一步，含约束传播、裁决表与全部 tie-break）；互斥收敛缩域；`bind_default_time_dimensions` 迁入；同资产合并 | `[新]` `apps/retrieval/query/grouping.py`；`[改]` `policy.py`（:568 缩域、:954 迁移） |
| 3 | 生产侧统一切换：payload 层跨模型启发式退役（`_cross_model_query_plans`/`_assets_for_model`/`_add_intent_dimensions_for_model` 改为 ResolutionOutcome 投影）+ 语义工具改产 ResolutionOutcome（`semantic_state` dict 双写一个迭代，COMPAT_LEDGER 记账） | `[改]` `apps/retrieval/projection/payload.py:823-966`、`apps/tool/tools/semantic.py`、`semantic_contracts.py` |
| 4 | 澄清门服务（三态判定 + 门控顺序 + 禁止清单 + 预算 + confidence 接线）；非问数收口（chitchat/meta/out_of_scope 直答）自 react_legacy 迁入 pipeline 直答阶段（§3.3-3），使 react_legacy 成为纯回滚路径 | `[新]` `apps/chatbi/services/planning/clarification_gate.py`；`[改]` `pipeline/{mode_router,fast,plan_mode}.py`、`agent/preparation.py` |
| 5 | AnalysisPlanner 输入切换 ResolutionOutcome；QueryGroup↔QueryTask 同构映射；join_on 确定性推导；表达映射表落码；多结果全消费规则 | `[改]` `services/planning/analysis_planner.py`（`_rule_plan_from_semantic_state`/`_multi_query_task` 重写） |
| 测 | 分组算法矩阵（单模型/跨模型/同名维度/候选等价副本/约束传播回退/组数超限/无默认时间/同资产合并）；伪澄清回归（G002/G005 行为=direct）；G004/G005/G006 绑定层黄金；resolution-only fixture 回放 | `[新]` `tests/retrieval/test_query_grouping.py`、`tests/chatbi/test_clarification_gate.py` |

**开关**：`CHATBI_QUERY_GROUP_BINDING_ENABLED`。
**验收门禁**：结构门槛——伪澄清=0、绑定层结构断言 12/12、`SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE` 全局抛出点删除、指标未命中时无维度/值澄清；业务门槛——12 题业务结果 ≥9。

### R3 事实源与证据链（2 周）

| # | 事项 | 文件 |
|---|------|------|
| 1 | SemanticRuntimeViewSnapshot（构建/缓存/Run 冻结/fingerprint 落 derived_state）+ 各消费方接入；分散启发式逐个删除（删除清单进 PR 描述逐条勾销）；条件 stage 裁决接入 Snapshot | `[新]` `apps/semantic/services/runtime_view.py`；`[改]` grouping/planner/plan_validation/compilation 消费点、`pipeline/stages` 传递 |
| 2 | 编译证据断言（时间谓词/HAVING/不降级/区间数不减） | `[改]` `services/planning/plan_validation.py`、`apps/semantic/services/sql_compiler.py`（产物结构暴露断言接口） |
| 3 | claims 数值归一 + 派生值必须来自 compute 结果列 + 降级粒度细化 + 多结果集并列引用 | `[改]` `services/generation/answer_composer/{claims,composer}.py` |
| 4 | `ResolvedMetricCondition` → QueryTask.having 传递链贯通（G008） | `[改]` `pipeline/fast.py`、`analysis_planner.py` |
| 测 | 断言各失败路径；快照冻结/版本变更重建；G003/G008 SQL 层黄金；G009 结果层黄金 | `[新]` `tests/chatbi/test_compile_evidence.py`、`tests/semantic/test_runtime_view.py` |

**验收门禁**：结构门槛——计划层结构断言 12/12、SQL 证据缺失=0、修复第 3 层触发率 <5%；业务门槛——12 题业务结果 ≥11 且唯一允许失败必须"结构断言全绿、仅数据/数值原因"并附归因；claims 通过率恢复（表格一刀切降级=0）。

### R4 分层评测与门禁（1-2 周，自 R1 起并行建设）

| # | 事项 | 文件 |
|---|------|------|
| 1 | 黄金题 schema v2（§7.2）；存量 12+50 题补全四层期望（R0 已起骨架）；`validate_golden_cases.py` schema 校验进 CI | `[改]` `backend/scripts/p1_golden_cases.jsonl`、`golden_cases.jsonl`；`[新]` `backend/scripts/validate_golden_cases.py` |
| 2 | 分层 runner：understanding-only（真实模型，每日跑批）/ resolution-only（**固定输入 fixture 三元组**，§7.2）/ plan-only（固定 ResolutionOutcome）/ e2e 四档；阶段产物自动落盘为回放 fixture | `[新]` `backend/scripts/run_layered_golden.py`（复用现有跑批脚本骨架） |
| 3 | 行为判定（direct/clarify_allowed/clarify_required/reject/assisted_fallback）机器断言 | 同上 |
| 4 | PR 门禁：绑定/计划层黄金进 CI（fixture 回放，无外部依赖）；理解层每日真模型跑批 | `[改]` CI 配置（评测平台本体仍按 doc 33 留在 P2-3） |

**验收门禁**：四档 runner 可独立执行并出分层报告；12 题全部具备四层期望；R0-R3 的门禁数字由该 runner 复核。

### R5 影子灰度与默认切换（1-2 周，与 R3/R4 尾部重叠；吸收自被合并稿 R4）

| # | 事项 | 说明 |
|---|------|------|
| 1 | 总闸 `CHATBI_SEMANTIC_RUNTIME_V2 = off \| shadow \| canary:<pct> \| on` | 统一激活 R1-R3 全链（要求各阶段开关已开）；canary 按 user/dataset 哈希分桶 |
| 2 | 影子执行器 | shadow 档：用户答案仍由旧链路产出；新链路静默跑到**计划装配为止**（默认不执行 SQL，比对理解/绑定/计划指纹三层 diff），控制数据集上可开全执行影子比对结果集。复用 `TemporalShadowObservation` 的旁路观察模式 |
| 3 | 差异报表 | 按 `semantic_contract_version` 分组输出逐阶段 diff 率（理解一致率/绑定一致率/计划指纹一致率），进入切换评审材料 |
| 4 | 控制题等价验证 | 50 题黄金集中已稳定通过的子集，新旧链路**结果集等价**（列序无关、数值 4 位有效）后才允许 canary → on |
| 5 | 切换与回滚纪律 | 新链路契约错误可即时回 shadow/off，但**回滚必须记录原因**（事件落 Run）；不允许静默降级为错误答案；旧链路只作为回滚路径，**不作为新架构的验收路径**；稳定一个迭代后按 COMPAT_LEDGER 清偿旧字段与投影 |

**验收门禁**：shadow 期理解/绑定一致率 ≥95%（三连跑）；控制题结果集等价 100%；canary 期无新增 P0 级 badcase 后切 on。

### 依赖与并行

```
R0（止血∥观测）──► R1 ──► R2 ──► R3 ──► R5（影子→canary→默认）
                      ▲              ▲
R4（题集与 runner）自 R1 起并行，R2/R3/R5 门禁依赖其产出
人力 2-3 人：R0 双轨各一人一周；R1 与 R4 并行；R2 集中攻坚（绑定算法 + 澄清门，3-4 周）
总计 10-13 周
```

---

## 7. 验收标准重定义

### 7.1 实现状态 / 验收状态双栏制度

吸取 doc 33 "已完成首版"与 12 题 1/12 落差的教训（doc 34 已作自我修正），自本文起所有阶段文档强制双栏：

| 栏 | 判定标准 |
|----|---------|
| **实现状态** | 代码合入 + 单元/集成测试通过 + lint 通过 |
| **验收状态** | 对应层分层黄金全绿 + e2e 黄金达标 + **真实环境跑批报告链接**（docs/test-results/ 落档） |

规则：验收状态未达标的能力项，文档表述一律为"实现完成，验收未通过"，禁止"已完成"；模式默认开关（如 `CHATBI_SEMANTIC_RUNTIME_V2=on`）只允许在验收状态达标后切换。

### 7.2 黄金题 schema v2 与分层 runner 的固定输入

```jsonc
// 展示格式为 JSONC（含注释便于阅读）；落盘格式为 JSONL——每行一个严格 JSON 对象，
// 禁止注释与尾逗号，由 validate_golden_cases.py 做 schema 校验（必填层/枚举/引用一致性）。
{
  "case_id": "P1-G001",
  "question": "店铺100023在2026年6月30日的总GMV相比6月29日变化了多少，增长率是多少？",
  "dataset": {"dataset_id": 243, "dataset_biz_name": "stall_dataset", "schema_version": 6},
  "behavior": "direct",              // direct | clarify_allowed | clarify_required | reject | assisted_fallback
  "understanding_expect": {
    "mentions": [
      {"text": "总GMV", "kind": "metric_phrase", "metric_role": "base"}
    ],
    "expressions": [{"op": "growth", "over": "time_comparison", "outputs": ["difference", "rate"]}],
    "metric_conditions": [],
    "order": null,
    "temporal": {"comparison_method": "custom", "range_count": 2}
  },
  "binding_expect": {
    "groups": [{"model_biz_name": "fct_stall_order_daily", "metric_biz_names": ["gmv_total"],
                "dimension_biz_names": ["stall_id"],
                "filters": [{"dimension_biz_name": "stall_id", "canonical": "100023"}]}]
  },
  "plan_expect": {"query_tasks": 2, "compute_ops": ["growth"]},
  "sql_expect": {"per_task_time_predicate": true, "having": false},
  "result_expect": {"non_empty": true, "columns_include": ["difference", "growth_rate"]},
  "tags": ["comparison", "growth", "dual_query"]
}
```

**分层 runner 的固定输入**（回应实施评审第 6 项——隔离"算法变化"与"输入变化"）：

**R0 当前实现状态（2026-08-17）**：问题理解链路已接入
`CHATBI_SEMANTIC_REPAIR_V2`，完成“确定性归一化 → 字段级补丁 → 语义不变量校验”、顶层及嵌套 extra 剥离、Temporal 旧字段唯一投影和理解阶段澄清止血；对应单元测试已通过。查询组超限已归类为
`PLAN_QUERY_GROUP_LIMIT_EXCEEDED`，复合比率方向校验已提供确定性拒答函数。Run 的 `derived_state` 已写入
`semantic_contract_version` / `binding_contract_version`，评测脚本可读取 v2 黄金用例并按 `behavior` 判定是否误入澄清门。P1 12 题已迁移到真实稳定业务名引用的 v2 JSONL，并由
`backend/scripts/validate_p1_golden_cases.py` 校验。分层 runner、全链 Trace 候选集/绑定快照和绑定/计划 R1-R3 能力仍未完成，不能据此宣称 P1 验收通过。

P1-G007 的当前资产快照已包含认证派生指标 `fct_stall_order_daily.aov_sale`；黄金集中的
`gmv_sale / order_cnt_sale` 仅作为理解层拆解假设，绑定期望以整短语命中的 `aov_sale` 为准。

| runner | 输入 | 模型依赖 | 用途 |
|--------|------|---------|------|
| understanding-only | 原始问题 | 真实模型（每日跑批） | 抽取质量；波动由三连跑一致率门槛约束 |
| resolution-only | **fixture 三元组**：`{mention_graph, candidate_sets（每 mention 的召回候选+分数快照）, runtime_snapshot（序列化）}` | 无 | 分组/绑定/澄清门算法的确定性回归——输入全固定，输出必须逐字段可复现 |
| plan-only | 固定 ResolutionOutcome fixture | 无（规则通道）/真实模型（LLM 通道单独跑） | 计划装配与映射表 |
| e2e | 原始问题 | 全真 | 端到端业务结果 |

fixture 来源 = R0 观测轨保存的阶段快照导出；**fixture 更新纪律**：candidate_sets 或 runtime_snapshot 变更必须独立 PR 并说明来源（索引重建/资产变更/权限变化），禁止与算法改动混在同一 PR——保证 diff 可归因。

判分原则：每层期望独立断言、独立报告——一个问题从理解错误"转移"成检索错误时，分层报告立刻暴露，整体状态不可能再"看起来完成"。`behavior` 断言使伪澄清（direct 题出 waiting_user）成为机器可判失败。

### 7.3 双层门禁（对 doc 32 §2.4 的修订；回应实施评审第 5 项）

门禁分两类。**硬性结构门槛**验证"契约能表达、各层产出正确结构"，自生效阶段起永久生效、违反即失败——结构能力必须 12/12，不允许用"11/12 通过"掩盖 G007 类契约表达缺陷。**业务结果门槛**验证数值与数据正确性，允许非契约类残余。

**硬性结构门槛**：

| 门槛 | 生效阶段 |
|------|---------|
| 理解契约失败（understanding_failed / understanding_contract_error）= 0 | R0 |
| 修复不变量违反 = 0；时间/比较语义丢失 = 0 | R0 |
| 失败可归因到具体阶段 = 100% | R0 |
| 理解层结构断言 **12/12**（mentions/expressions/conditions/order/temporal，含 G007 拆解、G008 条件） | R1 |
| 伪澄清（behavior=direct 题 waiting_user）= 0 | R2 |
| 绑定层结构断言 **12/12**（含 G004 组内兼容、G005 双组、G012 无重复指标） | R2 |
| 计划层结构断言 **12/12**；SQL 证据缺失 = 0 | R3 |
| 修复第 3 层（补丁 LLM）触发率 < 5% | R3 |

**业务结果门槛**：

| 指标 | R0 后（预期值，非门禁） | R2 后 | R3 后（P1 重验收） | R5 切默认前 |
|------|------|------|------|------|
| P1 黄金 12 题业务结果正确 | ≈5 | ≥9 | **≥11（≥90%）**，唯一允许的失败必须"结构断言全绿、仅数据/数值原因"并附书面归因 | ≥11 |
| 50 题黄金集（doc 33 口径） | 不回退 | ≥80% | ≥80% 且分层报告齐备 | ≥80%（趋 85%，接 doc 32 P2 轨道） |
| 三连跑理解+绑定一致率 | — | 入观测 | ≥95% | ≥95% |
| P0 控制题新旧链路结果集等价 | — | — | — | **= 100%** |
| G012 回归 | 不回退 | 不回退 | 不回退 | 不回退 |

### 7.4 评测脚本即时修正（随 R0 落地；吸收自被合并稿 §9.5）

- `task.finished` 且任务成功的 QueryTask 计入 SQL 执行统计（修正 fixed2 概览"0/12 执行"的假象）；
- 判分以结果集与结构化断言为主，答案文本关键词为辅；
- 区分"应澄清"与"错误澄清"（behavior 断言）、"计划生成成功"与"业务结果正确"（分层断言）；
- 每条运行记录 `execution_mode`（react_legacy/fast/plan）与两个契约版本号，支持分组对比；
- 保存原始运行、归一化语义、绑定结果、AnalysisPlan 与 SQL 指纹，作为回放 fixture。

---

## 8. 兼容与迁移

1. **Graph 链路**：`NaturalLanguageIntentOutputBase` 泛型基类不动；Graph 继续消费旧投影（`IntentRecognitionOutput` 由 MentionGraph 派生，字段值语义不变；时间/比较投影按 §4.2.8）。Graph 退役仍按 doc 33 P2-6 条件触发，本重构不提前删；
2. **derived_state 快照**：旧 Run 的 understanding 结构只读兼容（不做旧→新反向构造，旧快照只支持回放不支持 patch 续跑）；
3. **`multi_query_plans` / `semantic_state`**：R2 起双写 ResolutionOutcome 与旧结构一个迭代，消费方切换完成后旧结构停写（COMPAT_LEDGER 记账）；
4. **开关矩阵**：`CHATBI_SEMANTIC_REPAIR_V2`（R0）、`CHATBI_MENTION_CONTRACT_ENABLED`（R1）、`CHATBI_QUERY_GROUP_BINDING_ENABLED`（R2）、`CHATBI_COMPILE_EVIDENCE_ASSERT`（R3）、总闸 `CHATBI_SEMANTIC_RUNTIME_V2`（R5，off/shadow/canary/on）。逐级依赖（后者开启要求前者开启），回滚=关开关；
5. **react_legacy**：保持 doc 33 策略——冻结不迭代，只作回滚路径不作验收路径；R5 验收达标后再讨论摘除；本重构的 FAST/PLAN 行为变更不回灌 legacy。

---

## 9. 风险与开放问题

| 风险 | 缓解 |
|------|------|
| 提及抽取不看 schema 后，口语化短语（"卖得最好的"）识别率下降 | mention 允许 `unresolved_notes` 兜底进澄清门而非丢失；R1 理解层黄金先行标定；必要时对 `dimension_phrase` 保留轻量候选提示（只给名称不给字段，作为妥协开关） |
| 跨度约束对模型输出格式要求高，抽取失败率上升 | temporal 域同规则已真实验证；normalizer 的"查找唯一出现自动修偏移"吸收大部分偏差；失败降级单 mention 而非整份输出 |
| 拆解假设短语质量差（模型给的分子分母不对应任何资产） | 假设只是检索文本，失败走固定拒答话术并给出可查建议（4.3.3-c），不澄清不猜测；badcase 沉淀为派生指标资产建模建议（接 doc 32 运营闭环） |
| 组内绑定使召回压力集中在指标槽（指标错→整组错） | 指标槽保留歧义带与澄清选项（ASSET_AMBIGUOUS 正是为此设计）；verified query 命中提升指标置信（doc 32 既有机制） |
| 多模型结果缺可比较键 | join_on 交集为空且表达要求合并 → 计划校验拒绝 ComputeTask，但各 QueryTask 结果仍分别展示（部分作答），不整体失败 |
| 字段补丁协议对小模型太难（补丁本身格式错） | 补丁失败一次即终止走确定性收敛/澄清，不做二次补丁；观测第 3 层触发率（§7.3）持续压低对修复的依赖 |
| Trace 快照体量与敏感信息 | R0-8 治理规则：单节点截断上限、保留期对齐事件归档、`sensitive_level` 脱敏、账本不含结果行数据 |
| 新旧链路行为不一致引发信任问题 | R5 影子运行先行，差异只记录不影响答案；控制题等价 100% 才切换；回滚留痕 |
| 评测结果受模型随机性影响 | 三连跑一致率门禁 + resolution/plan 层固定输入 fixture（零模型依赖）+ 结果集比较三者并用 |
| fixture 漂移导致回归误判 | fixture 更新纪律（§7.2）：输入 fixture 变更独立 PR、注明来源，禁止与算法改动混提 |
| 双写与投影期的维护面 | 每项投影登记 COMPAT_LEDGER 并绑定删除条件；R5 稳定后统一清偿 |
| G006 类"跨口径对比"的产品语义（披露 vs 确认）未定 | 默认 disclose 档直答+披露，数据集级可配置 clarify；开放问题①提交产品裁决 |

**开放问题**
1. 跨口径对比（G006 形态）的默认交互：披露直答 or 强制确认？（建议披露，保留配置）
2. 跨模型 ratio 的 R 阶段行为已固定（`RATIO_CROSS_MODEL_UNSUPPORTED` + 固定拒答话术，§4.3.3）；开放的仅是 **P2 是否投资支持跨模型比率**（需要跨模型对齐粒度的编译语义，建议按真实需求频次决策）；
3. computed 兜底词表的维护归属（instructions 资产 or 代码常量）——建议先代码常量，P2 随 instructions 资产化；
4. 提及抽取与表达组装是否拆成两次调用（当前设计合一次）——按 R1 理解层黄金的真实错误分布再定，不预先拆分；
5. shadow 档在生产流量上的采样比例与保留时长（建议 100% 采样一个迭代，仅计划级不执行 SQL，成本可控）。

---

## 10. 设计原则（十二条）

1. LLM 负责抽取用户表达，不负责猜测资产和物理字段；拆解假设只是检索线索，绝不直接绑定；
2. 检索负责召回候选，不负责重新拆分指标短语；
3. 绑定负责按模型形成查询组，不返回无法用于计划的全局单槽；自动绑定与澄清之间只有决策表，没有临时判断；
4. 时间与时段比较只有一个权威（temporal 域）；抽取契约里不存在的字段不会校验失败；旧字段只从投影契约获得；
5. 默认语义由资产与能力契约确定（RuntimeSnapshot，Run 级冻结），不让用户为系统内部缺失的信息澄清；
6. 格式修复只能做确定性迁移或受限字段补丁，不能重新生成完整语义；修复后必须过语义不变量；
7. 计划只能消费已经绑定的资产和规范计算关系；声明的口径必须在编译产物中留下证据；
8. 任何失败必须归因到具体阶段，不用"歧义"掩盖内部错误；
9. 评测必须验证中间契约，不能只看最终是否生成答案；绑定与计划层回归使用固定输入 fixture；黄金案例只作回归样本，禁止 case 特判；
10. 能确定性完成的绝不交给模型（归一化、分组、绑定、澄清分类、stage 裁决、计划校验、SQL 编译全部确定性）；
11. 旧链路只作为灰度和回滚路径，不作为新架构的验收依据；
12. 先完成语义契约闭合与分层验收，再继续扩展新的复杂分析能力；"实现完成"与"验收完成"永远分开陈述。

---

## 附录 A. 术语对照（相对 doc 32 附录 B 的增量）

| 术语 | 含义 | 替代/关联 |
|------|------|----------|
| SemanticMention | 带原文跨度的语义提及（理解层输出单元） | 替代裸 `metric_mentions`/`dimension_slots` 字符串语义 |
| DecompositionHint | composite 指标的拆解假设（假设短语，非跨度、非资产） | G007 类问题的一等契约；仅作检索文本 |
| AnalysisExpression | 用户要求的计算/比较表达，与基础指标类型分离；outputs 表达差值/比率等多产出 | 映射到 ComputeTask / time_offset / 派生编译 |
| MetricCondition / ResolvedMetricCondition | 指标阈值条件；WHERE/HAVING 由服务端按聚合层级裁决 | G008 类问题的一等契约 |
| OrderRef | 排序引用已有指标/表达，不产生重复指标槽位 | G012 双识别的契约级防护 |
| MentionGraph | 一次提问的提及+表达+条件+排序全集 | 理解层新输出契约 |
| ResolutionOutcome | 绑定层新输出：QueryGroup 集合 + 未决项 + 快照指纹 | 替代全局单份 `RetrievalBundle` 语义（Bundle 保留为诊断载体） |
| QueryGroupBinding / RatioSpec / TimeBindingSpec | 每查询组一份资产绑定（模型/指标/维度/值/时间/比率） | 与 QueryTask 结构同构 |
| 三态责任模型 | USER_UNDERSPECIFIED / ASSET_AMBIGUOUS / PENDING_BINDING | 替代 `ambiguous_slots` 单一态 |
| 澄清门 | 绑定完成后的唯一澄清出口 | 收编 preflight 澄清（temporal 例外前置） |
| SemanticRuntimeViewSnapshot | 数据集级运行时语义事实源，Run 级冻结、全阶段只读同一实例 | 收敛五处分散规则 |
| 语义保真修复 | 归一化→字段补丁→不变量的修复协议 | 替代整份 JSON 重生成 |
| 分层黄金 | understanding/binding/plan/sql/result/behavior 六层机器断言；绑定/计划层固定输入 fixture | 替代 expected_points 散文判分 |
| 影子运行 | 新链路静默跑至计划装配并记录逐阶段 diff，不影响用户答案 | 复用 TemporalShadowObservation 旁路模式 |

## 附录 B. 合并对照与勘误

本文由两份底稿合并：《语义契约重构设计》（本文件 v1，基底）与《智能问数运行时重构设计》（`35-agentic-chatbi-runtime-refactoring-design.md`，已停止维护）。

**自被合并稿吸收的内容**：R0 观测冻结（fixture 化、全链快照、契约版本号、"失败可归因到阶段"退出条件）；R5 影子运行与百分比灰度切换；`MetricCondition` 阶段裁决契约（其 `conditions.stage`）；`OrderRef` 排序引用（其 `order_target` 角色）；阶段职责表（§3.2）；评测脚本修正清单（§7.4）；设计原则（§10，扩为十二条）；非目标清单（§2.3）；"无计算关系多结果全消费"规则；若干风险条目。

**命名对照**：被合并稿 `QuestionSemanticDraft`/`CanonicalQuestionSemantic` ≈ 本文 `MentionGraph`（归一化前/后同一 DTO，以 dropped_fields 账本区分）；`BindingResult`/`QueryBinding` ≈ `ResolutionOutcome`/`QueryGroupBinding`；`user_semantic_ambiguous`/`asset_binding_ambiguous`/`asset_not_found` ≈ `USER_UNDERSPECIFIED`/`ASSET_AMBIGUOUS`/`MISSED`（MISSED 走四档路由而非澄清）。

**对被合并稿的裁决性分歧**（合并时以本文为准）：
1. **抽取模型的时间/比较输出**：被合并稿的抽取契约仍含 `time.method`/比较结构，靠归一化兜底；本文裁定抽取模型完全不产出比较结构（G001 三轮死于该枚举，字段不存在才结构性免疫），temporal 域为唯一权威（§4.2），旧字段由投影契约供给（§4.2.8）；
2. **模型调用边界**：被合并稿"最多三类模型调用"未明确时间解析独立调用的去留；本文明确保留独立时间解析调用（已验证组件不合并）；
3. **修复模型的触发条件**：被合并稿限定"仅 JSON 不可解析时"允许模型修复，但其补丁示例（remove_unknown_field）实为确定性操作；本文以四层协议统一（确定性归一化优先，路径级补丁兜底，触发率入门禁）。

**对被合并稿 §1 的事实勘误**（其归因沿用了外部评审文本，与三轮真实数据不符）：
- G012 被列为"期末库存/库存量重复识别"现存缺陷——实际 G012 是三轮**唯一严格通过**的用例；该场景保留为回归断言（§5），不作为现存缺陷归因；
- "G003/G004/G005/G008/G011/G012 的档口ID澄清"——三轮数据中这些案例死于 `plan_invalid`/`SEMANTIC_QUERY_METRIC_REQUIRED` 等，并非档口ID澄清；
- "G002 的字符串时间范围无法进入 DTO"——G002 首轮实际死于 `comparison.method='difference'` 枚举拒绝，第三轮为伪澄清；
- "问题理解是一次性单模型输出"——实际已是重写/统一理解/时间解析三次调用，缺陷在输出契约（§1.3 裁决 #1）。

## 附录 C. v3 实施评审闭环对照

| # | 实施评审阻塞项 | 闭环位置 | 闭环要点 |
|---|--------------|---------|---------|
| 1 | 复合指标拆解契约不完整（分子分母/来源/命中要求/同模型/示例与注释矛盾） | §4.1.1 DecompositionHint、§4.3.3、§4.4.1 RatioSpec | 假设短语为自由文本（非 mention_id——用户未说过的词没有跨度，v2 示例的矛盾即源于此）；仅作检索文本；(a) 资产定义优先 (b) 双短语各自必须 RESOLVED 且同模型 (c) 失败走固定拒答/兜底话术，不澄清 |
| 2 | QueryGroup 候选收敛不确定 | §4.4.2 | 十一步算法：独立判定→约束传播（禁笛卡尔积、收缩空则回退）→三行裁决决策表（唯一/等价副本/口径不同）+ 穷尽 tie-break（同模型>兼容数>model_id>asset_id）+ 组数上限=CHATBI_PLAN_MAX_QUERY_TASKS+澄清选项≤3 |
| 3 | SemanticRuntimeView 只是接口草图 | §4.7 | 更名 SemanticRuntimeViewSnapshot：数据来源（权限裁剪 DatasetSchema+capability+指标 ORM+默认时间标记）、构建者与时机、进程缓存键、Run 冻结与 fingerprint 落 derived_state、恢复时版本复核禁混用、快照非安全边界 |
| 4 | Temporal 旧链路适配无具体契约 | §4.2.8 | 六字段投影表：time_ranges（query_filter 表达按 offset 排序）、time_range=首项、ComparisonSpec 直投+缺省推导规则（base=最早区间）、comparison_type 只读回填、冲突以"禁产白名单剥离"消除（无运行时裁决）、旧快照只读不反向构造 |
| 5 | 验收门槛不一致（12/12 vs 11/12 vs 5/12） | §7.3 | 拆双层：硬性结构门槛（分阶段生效、永久有效、结构断言必须 12/12——含 G007/G008 契约表达能力）；业务结果门槛（R3 ≥11/12 且唯一失败必须结构全绿+书面归因）；R0 的 ≈5 明确为预期值非门禁 |
| 6 | 黄金测试缺固定输入与格式约束；DTO 不完整 | §7.2、§4.4.1 | JSONC 仅文档展示、落盘 JSONL+schema 校验脚本；resolution-only 固定 fixture 三元组（mention_graph/candidate_sets/runtime_snapshot）+ fixture 更新纪律；补全 TimeBindingSpec/RatioOperand/RatioSpec/ResolvedMetricCondition/EvidenceLevel，AssetReference 沿用现有 DTO |
| — | 非阻塞项 | §6-R2（任务 3+6 合并、排期 3-4 周）、§6-R0-8（Trace 治理）、§4.3.3+§9 开放问题 2（跨模型 ratio 失败码与话术固定） | |
