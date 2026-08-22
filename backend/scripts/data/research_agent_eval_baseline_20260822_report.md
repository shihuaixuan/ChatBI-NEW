# Research Agent 评测基线报告（阶段 0）

- 日期：2026-08-22
- 架构版本：旧 `ResearchAction` 路径（自即日起按 doc38 冻结，仅允许阻断性修复，提交标记 `research-baseline-fix`）
- 环境：tenant 1 / dataset 243 / datasource 13，真实 LLM（deepseek-v4-flash），真实数据源执行

## 1. 工件

| 工件 | 路径 |
| --- | --- |
| 用例集 v2 | `backend/scripts/data/research_agent_eval_cases.json` |
| 统一评测入口 | `backend/scripts/run_research_agent_eval.py` |
| 历史明细（v1） | `backend/data/research_agent_eval_results.json` |
| 基线快照（v1，保留） | `backend/data/research_agent_eval_baseline_20260822.json` |
| v2 正式基线 | `backend/scripts/data/research_agent_eval_baseline_20260822.json` |

用例集 v2 的 11 个用例显式覆盖 13 类；已注册的全局判分器另外覆盖 13、15、18、19、20、23，合计
18/24 类。类别 5（当前数据层级无法构造真实跳级下钻）、11（语义能力不支持）、14（执行失败超时
注入）、17（部分查询成功部分失败）、21（取消恢复）、24（租户隔离）记录在 `uncovered_categories`，
待后续补齐。

## 2. 方法与口径

- 生产入口 `create_agent_start_events`：问题重写 → 候选检索 → 语义解析 → 模式路由 → Research 闭环 → 报告收口全真实组件。
- 结果五分类：`pass / correct_reject / explicit_failure / silent_error / harness_error`。
- 自动判分不变量：时间角色保持、目标指标封闭、越界维度/驱动泄漏、不可变筛选保持、引用不悬空、发现必须引用、动作指纹去重、相关性不得表述为因果；外加逐用例声明的必需证据模式（premise/baseline/drilldown/contribution/driver_validation/parallel 等）与预算约束。
- v2 输出区分 observed、lower_bound 和 unavailable；路由/需求准备拒绝不会写成已消耗满额。
  本次 legacy Run 的逐查询和逐模型调用事实没有持久化入口，因此对应字段为 `null`，并记录
  `unavailable_reason`；Evidence 数量、Plan 数量、Trace 节点、Tool Call 数和 Artifact 大小按
  现有持久化事实读取。

## 3. 基线结果

汇总：**7/11 可接受**（pass 5 + correct_reject 2）；explicit_failure 3（27.3%）；silent_error 1（9.1%）；harness_error 0。

| 用例 | 结果 | mode | 运行状态 | 时长 | 关键误差 |
| --- | --- | --- | --- | --- | --- |
| research-cause-001 premise_open_cause | pass | research | finished | 19.6s | - |
| research-cause-002 open_exploration_no_premise | explicit_failure | research | failed | 3.0s | `RESEARCH_GOVERNANCE_CONTRACT_INVALID` |
| research-cause-003 result_driven_hierarchy_drilldown | pass | research | finished | 15.8s | - |
| research-cause-004 contribution_reconciliation | explicit_failure | plan | failed | 3.9s | 路由误选 plan + `COMPUTE_CONTRIBUTION_DIFFERENCE_COLUMN_REQUIRED` |
| research-cause-005 driver_same_model_validation | pass | research | finished | 10.6s | - |
| research-cause-006 driver_cross_model_ungoverned | explicit_failure | research | failed | 3.5s | `RESEARCH_TARGET_METRIC_SINGLE_MODEL_REQUIRED` |
| research-cause-007 empty_result_graceful | **silent_error** | research | finished | 9.1s | 不可变筛选丢失 |
| research-cause-008 scope_violation_goods_rejected | correct_reject | research | failed | 3.1s | `RESEARCH_DIMENSION_MODEL_MISMATCH` |
| research-cause-009 governance_missing_channel_contribution | correct_reject | plan | failed | 2.9s | `SEMANTIC_EXECUTION_ASSET_NOT_COVERED:DIMENSION:309:249` |
| research-cause-010 multi_direction_parallel | pass | research | finished | 11.7s | - |
| research-cause-011 broad_analysis_budget_pressure | pass | research | finished | 10.0s | - |

运行时摘要：平均 8.5s/run，最长 19.6s；逐查询和逐模型调用事实不可观测，均为 `null`；Tool Call、Trace、Artifact
字段按 availability 输出。执行层动作失败码出现两次 `RESEARCH_ACTION_DUPLICATED`，未改变最终分类。

元数据限制：本轮 legacy Run 没有提供模型名称、真实 `schema_fingerprint` 或
`scope_fingerprint`；正式基线将这些字段写为 `null`，并记录对应的
`*_unavailable_reason`，不能用 `research_max_model_calls` 或 asset_refs 哈希代替。

## 4. 失败根因（冻结前留档，供新 Harness 对照）

### R1 · 单期基线问题被驱动关系契约硬阻断（case 002）

`apps/chatbi/services/research/requirements.py:587-595` 要求关系的 `time_roles ⊆ 本次需求 time_roles`。认证驱动关系声明 `supported_time_roles=['current','previous']`（见 `headless_metric_relationship` 全部 3 条）；单期问题需求 time_roles 为 `('single',)`，集合包含检查恒假 → 直接抛 `GOVERNANCE_CONTRACT_INVALID`，研究未启动即失败。
正确语义应为"该关系在本运行不可用于验证"而非契约非法。所有无对比期的研究型问题当前均不可用。

### R2 · 贡献度问题被误路由到 plan 且 plan 执行崩溃（case 004）

“计算各档口对下降的贡献”被路由为 plan 模式（期望 research/fixed_attribution 语义）；plan 管线随后因缺差值列抛 `COMPUTE_CONTRIBUTION_DIFFERENCE_COLUMN_REQUIRED`。双层缺陷：路由边界（contribution-on-change 应进研究语义）与 plan 计算能力缺口。

### R3 · 用户点名的跨模型驱动被提升为共同目标（case 006）

解析器把库存指标 `METRIC:280:248`（模型 248）写入 measures，与 GMV（模型 246）并列成多模型目标，触发 `requirements.py:76-79` 的 `TARGET_METRIC_SINGLE_MODEL_REQUIRED` 显式拒绝。期望行为是 GMV 保持唯一目标、库存验证作为治理外方向显式收口（受限继续或以受认可的拒绝码拒绝）。当前行为虽显式报错，但不属于用例认可的收口方式。

### R4 · 空结果后丢弃前提筛选并宣称部分成功（case 007，唯一 silent_error）

档口 88888 无数据后，后续证据在触及档口维度时未携带 `stall=88888` 筛选（`applied_filters` 为空），转而分析全部档口，并以 `partial/budget_exhausted` 宣称部分成功。这正是"不可变筛选保持"不变量设计要捕获的静默错误——自动判分器在无人工参与下命中。两轮运行分别有 2 条/1 条违规证据，属 LLM 非确定性下的稳定缺陷模式。

## 5. 判分器修正记录（评测基础设施，不在冻结约束内）

v2 运行和反例测试暴露并修复以下问题：

1. 按用例 `target_metric_refs` 判分，并对必要目标和时间角色做存在性检查；数据不足/明确拒绝必须有明确终止语义。
2. 报告数字按引用 Evidence 的 `top_rows`、`bottom_rows`、`statistics` 做通用溯源；日期、资产编号和假设编号不作为结果数字。
3. Evidence 所有权和引用依赖写入运行快照；引用型动作依赖必须来自前一轮，同轮依赖判定失败。
4. 评测器异常被隔离为 `harness_error`；`correct_reject` 要求失败状态、错误码和阶段同时匹配。
5. 用例预算作为判分上限；查询/模型调用没有事实表时输出 `null` 与 availability，不再用剩余预算反推。
6. 增加 ResearchAction 冻结守卫和 17 个确定性评测测试；阶段 0 相关测试合计 48 个通过。

本轮复核在不重新调用真实模型的前提下，对已保存的 11 个 Run 做了纯重评分：更新了类别注册
校验、贡献度对账断言、预算耗尽断言、拒绝阶段分类和元数据可用性口径。真实运行结果和五分类
结果未改变；正式基线中的 checks、类别声明和元数据按新口径同步更新。

首轮结果（5/11）作废，以本轮 7/11 为准。

## 6. 局限与口径假设

- n=1：每用例单次采样，LLM 非确定性使 pass 边缘用例可能波动；确定性字段（错误码、不变量判定）跨轮稳定。
- legacy 路径没有逐查询/逐模型调用持久化事实，v2 明确标记为 unavailable；新 Harness 必须补齐同名
  observed 字段后再比较真实消耗。
- 类别 5/11/14/17/21/24 未建有满足判分契约的用例（见用例集 `uncovered_categories`）。
- case 006 的"认可收口"定义依赖新架构落地后的显式限制语义，届时可能需要修订该用例的 `allowed_error_codes`（走用例集版本化流程，v2）。

## 7. 对后续阶段的输入

R1–R4 即新 Research Agent Harness 的对照验收点：

- R1/R3 属需求准备层的过严契约 → 新工具层应把"关系不可用"降级为受限收口；
- R2 需要路由边界重划（贡献度归因进 research 语义）；
- R4 是不可变筛选不变量在新 Harness 中必须原生保证的核心场景，case 007 为其回归用例。

阶段 0 v2 验收完成：11 例可重复运行、`harness_error=0`、判分反例测试通过、冻结守卫通过、
正式基线已写入统一路径。类别 11、14、21、24 和 legacy 的不可观测消耗字段按报告中的限制
留给后续阶段补齐，不能据此宣称这些能力已覆盖。
