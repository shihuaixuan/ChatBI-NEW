# 34. Agentic ChatBI P1 评审修复记录

> 日期：2026-08-17  
> 依据：docs/tech/32、docs/tech/33  
> 结论：代码缺口和自动化测试缺口已修复；真实数据黄金集与 FAST/legacy 双跑仍需在可访问数据源和模型的环境执行。

## 已修复

1. `CHAT_AGENT_EXECUTION_MODES` 生产默认值切换为 `fast,plan`，保留环境变量恢复 `react_legacy`。
2. PLAN 接入一次规划模型调用，输出不合法时最多重试一次；两次失败后使用规则计划，并在计划验证报告记录降级来源。
3. 规划模型只能引用当前语义范围已经绑定的指标和维度。
4. 自定义双时段生成两个独立 QueryTask 和一个 compare/growth ComputeTask；非 STRICT 查询也会把各自时间范围写回编译计划。
5. composition/share 意图生成 SHARE ComputeTask；无分组的两个单行结果允许无键比较，多行结果缺少连接键时明确拒绝。
6. 多查询且无需 ComputeTask 时，AnswerComposer 同时消费全部结果集，不再只读取第一个结果。
7. 回答正文中的每个数字必须出现在通过结果行校验的 claim 中；claim 文本中的数字也必须来自引用行。
8. STRICT `pre_aggregation_required` 已接入 verified plan 编译，基础模型先按安全粒度聚合再连接；不能安全推导的口径明确拒绝。
9. 前端时间线消费 plan/task/compute 事件，回答投影消费 claims、caliber_card 和 chart_spec。
10. 并发工具批次在真正开始任务前再次检查取消状态，关闭排队任务的取消竞态。

## 自动化验证

- 后端完整测试：`1719 passed, 2 skipped`。
- 后端 Ruff：通过。
- 前端事件投影：`12 passed`。
- 前端生产构建：通过。
- P1 黄金用例：新增 12 条，覆盖 comparison、composition、cross_model、derived_metric、preaggregation、snapshot，JSONL 格式校验通过。

## 尚未完成的阶段验收

以下项目依赖真实数据源、模型和 legacy 对照环境，本次未伪造结果：

1. P1 黄金集真实跑批正确率不低于 80%。
2. FAST 与 react_legacy 同题双跑的结果等价报告。
3. 真实链路的 FAST 单题模型调用次数不超过 4 次报告。
4. 数据集 243 上 CROSS_MODEL、双时段、占比和预聚合 SQL 的真实执行记录。

在上述四项完成前，P1 可以判定为“实现与自动化测试完成”，不能判定为“阶段验收完成”。
