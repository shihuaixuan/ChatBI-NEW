# P1 黄金用例真实数据测试报告

## 结论

P1 黄金集本轮未通过阶段验收。

- 用例总数：12
- 终态 `finished`：3/12
- 终态 `failed`：8/12
- 终态 `waiting_user`：1/12
- 严格满足全部预期点：1/12（8.3%）
- 预期点满足：4/26（15.4%）
- 可回放事件：12/12；持久化 trace 完整：12/12

唯一严格通过的是 P1-G012（快照期末库存排序）。P1-G003 和 P1-G008 虽然产生了答案，但分别缺少 `share` 计算节点、`HAVING` 过滤，且生成 SQL 未体现题目给出的月份过滤，因此不能计为通过。

## 测试条件

- 时间：2026-08-17 02:07–02:14（Asia/Shanghai）
- API：`http://127.0.0.1:8000/api/v1`
- 登录：`admin` 账号登录接口返回 HTTP 200；密码未写入报告或测试产物
- 数据集：商城店铺数据集，ID 243，`semanticEnforcement=STRICT`
- 题集：`backend/scripts/p1_golden_cases.jsonl`，12 条
- 编排开关：`CHAT_AGENT_EXECUTION_MODES=fast,plan`
- 题集运行时关闭 `AGENT_TRACING_ENABLED`，原因是本地虚拟环境缺少 `opentelemetry`；应用因此未执行 OTel exporter，但记录 API 的持久化 trace 仍可完整回放
- 澄清分支：本轮按主问题执行，未自动选择澄清候选；P1-G006 保留其真实 `waiting_user` 终态

## 逐例结果

| 用例 | 模式 | 终态 | 计划/结果证据 | 预期点 | 判定与原因 |
|---|---|---|---|---:|---|
| P1-G001 | react_legacy | failed | 无计划；record 10528/run 862 | 0/3 | `understanding_failed`：模型输出把 `comparison.target` 作为额外字段。 |
| P1-G002 | react_legacy | failed | 无计划；record 10530/run 863 | 0/2 | `understanding_failed`：`comparison.method=difference` 不符合 DTO 允许值。 |
| P1-G003 | fast | finished | plan `fast-864` → PROVEN；task `q1`；result `result:fast-864:q1` | 2/3 | 按档口分组且展示占比，合计约 100%；未发现 `share` ComputeTask，`caliber_card.sql` 也没有 2026-06 的 WHERE 过滤。 |
| P1-G004 | fast | failed | plan 创建后失败；record 10534/run 865 | 0/2 | `plan_invalid`：`semantic_metric_dimension_incompatible`。 |
| P1-G005 | plan | failed | 未进入计划任务；record 10536/run 866 | 0/2 | `plan_invalid`：跨模型指标/维度资产组合不可执行。 |
| P1-G006 | react_legacy | waiting_user | clarification.required；record 10538/run 867 | 0/2 | 正确识别为需要确认比较方式和时段，但未继续选择，未产生两个独立结果集。 |
| P1-G007 | fast | failed | plan 创建后失败；record 10540/run 868 | 0/2 | `SEMANTIC_QUERY_METRIC_REQUIRED`，严格语义资产未形成完整计划。 |
| P1-G008 | fast | finished | plan `fast-869` → PROVEN；task `q1`；result `result:fast-869:q1` | 0/2 | 返回答案但 SQL 没有聚合后的 `HAVING`，且 SQL 未体现 2026-06 过滤；不能证明指标条件在正确阶段执行。 |
| P1-G009 | react_legacy | failed | 无计划；record 10544/run 870 | 0/2 | `understanding_failed`：同比/月比结构同时出现旧字段和新字段，DTO 校验失败。 |
| P1-G010 | plan | failed | plan 创建后失败；record 10546/run 871 | 0/2 | `PLAN_STRICT_QUERY_PLAN_MISSING`。 |
| P1-G011 | fast | failed | plan 创建后失败；record 10548/run 872 | 0/2 | `SEMANTIC_QUERY_METRIC_REQUIRED`，未形成预聚合查询计划。 |
| P1-G012 | plan | finished | plan `plan-873` → PROVEN；task `q1_stall_stock`；result `result:plan-873:q1_stall_stock` | 2/2 | 使用 `snap_product_inventory`，按库存降序，结果集未跨时间累加；claims 覆盖全部结果行。 |

## 结果与引用核验

P1-G003、P1-G008、P1-G012 的 `task.finished` 均带有 `result_set_id`，答案事件带 `chart_spec`、`caliber_card` 和 claims。三条用例的事件回放顺序均完整，且 claims 能定位到结果行。

但 G003 的百分比没有结果列或 `compute.finished` 证据，属于基于 GMV 的回答侧推导；G008 的答案中“6 个”和“高于 10000 元”等数字没有对应的独立 claim，且结果 SQL 未体现 HAVING。因此答案存在表面完成但口径证据不足的问题。

## 主要阻断项

1. 理解 DTO 与模型输出契约不一致，直接造成 G001、G002、G009 三条比较/趋势用例失败。
2. 严格语义检索无法组合渠道、跨模型和派生指标资产，造成 G004、G005、G007、G011 失败。
3. PLAN 严格模式未生成完整双时段或预聚合计划，造成 G010、G011 失败。
4. 已完成答案的 SQL 证据显示时间过滤和 HAVING 没有落到编译 SQL，G003、G008 不能作为正确通过。
5. 本地缺少 `opentelemetry` 可选依赖；本轮通过关闭 tracing 才能执行，因此 tracing exporter 本身未验收。

## 原始证据

- [机器结果 JSON](./mall_store_agent_fresh_20_results_2026-08-15.json)
- [脚本生成概览](./mall_store_agent_fresh_20_overview_2026-08-15.md)
- [P1 题集](../../../backend/scripts/p1_golden_cases.jsonl)

