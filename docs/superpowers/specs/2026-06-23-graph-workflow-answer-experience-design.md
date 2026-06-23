# Graph Workflow 智能问数回答体验设计

## 背景

当前 Graph Workflow 回答区默认展示 `GraphWorkflowTrace`，并把节点 `output` 中的 JSON 直接渲染给用户。截图中的体验暴露了两个问题：

- 用户看到的是后端内部节点名、路由原因和 JSON，而不是业务答案。
- 执行过程虽然可见，但没有明确表达“当前执行到哪一步”“为什么暂停”“用户需要补充什么”。

本设计目标是让智能问数回答符合用户心智：主区域优先展示最终答案和图表，执行过程作为可理解的进度，询问用户节点作为正常的业务澄清暂停态。

## 核心理解

图中的“询问用户节点”不是权限批准，也不是执行确认。它是工作流中的人机交互节点，用于业务澄清、槽位补全和歧义消解。

当前 v1 图里主要有三类：

- `ask_rewrite_clarification`：问题信息不足，需要用户补充指标、时间、维度等，回答写回 `variables.rewrite_response`。
- `ask_intent_clarification`：分析意图不明确或冲突，需要用户确认查数值、看趋势、看排名、看对比等，回答写回 `variables.intent_response`。
- `ask_metric_selection`：知识检索命中多个候选指标，需要用户选择真实要分析的指标，回答写回 `variables.metric_selection`。

这些节点的状态是 `waiting_input`，本质是 workflow 暂停等待业务信息。用户回答后，runtime 恢复运行并回到对应业务链路。

## 目标

- 主回答区默认展示自然语言答案、表格或图表，不展示 raw JSON。
- 用户能默认看到执行进度，并知道当前执行到哪个步骤。
- 当 workflow 进入 `waiting_input` 时，主回答区展示业务澄清卡片，让用户完成选择或补充。
- 用户补充后，进度从“等待用户补充”变成“已补充”，并继续执行后续步骤。
- raw JSON、trace id、节点 output 等仅作为技术详情折叠展示。

## 非目标

- 不把询问用户节点设计成权限审批或 approval。
- 不在第一版实现真正的 token 流式回答。
- 不重构整个 Graph Workflow runtime。
- 不移除现有 trace/debug 能力，只改变默认展示层级。

## 信息架构

回答区域分为四层：

1. **执行进度**
   - 默认可见。
   - 展示当前步骤、已完成步骤、等待用户补充、失败或完成状态。
   - 不展示内部 JSON。

2. **澄清交互卡片**
   - 仅当 `pending_interaction.status === "pending"` 时展示。
   - 位于主回答区，作为当前 workflow 的阻塞点。
   - 使用业务文案和候选选项，支持用户提交、跳过或取消。

3. **最终答案**
   - workflow 成功后优先展示。
   - 来源为 `final_reply`、`sql_answer`、`chart_answer`、图表配置和查询数据。
   - 表格/图表用已有 `ChartBlock` 等组件渲染。

4. **技术详情**
   - 默认折叠。
   - 展示节点 raw output、route reason、trace id、run id。
   - 面向开发调试，不面向普通用户。

## 执行进度设计

执行进度不是“思考过程”的全部折叠，而是常驻轻量状态。

执行中：

```text
正在执行：匹配数据资产...
```

等待用户补充：

```text
需要你补充信息：请选择要分析的指标
```

完成后：

```text
已完成 · 理解问题 → 匹配数据资产 → 生成 SQL → 查询数据 → 整理答案
```

展开后显示步骤明细：

- 理解问题：识别为数据查询。
- 问题重写：今天店铺的访问人数。
- 匹配数据资产：命中访问人数、店铺、日期。
- 生成 SQL：已完成。
- 查询数据：返回 1 行。
- 整理答案：已完成。

## 澄清交互卡片设计

澄清卡片参考 Codex 的 inline blocking action 形式，但语义是“需要补充信息”，不是“需要批准”。

指标选择示例：

```text
需要你补充信息

当前问题存在指标歧义，请选择要分析的指标：

[访问人数] [访客数] [浏览量] [其他，请补充]

[跳过并结束] [提交]
```

意图澄清示例：

```text
需要确认分析方式

你想进行哪类分析？

[查指标数值] [看趋势] [看排名] [看对比] [看明细]
```

信息补全示例：

```text
请补充要分析的时间范围

[今天] [最近 7 天] [本月] [手动输入]
```

用户提交后：

- 前端调用 `answerInteraction(runId, interactionId, response)`。
- 当前卡片进入“已提交，继续执行”状态。
- 前端刷新 run/trace。
- 进度中该步骤显示“已补充：访问人数”或“已确认：看趋势”。

用户跳过后：

- 提交 `{ skipped: true }`。
- 图根据 `interaction.skipped` 路由到兜底回复或结束。
- UI 显示“已跳过补充，无法继续精确分析”。

## 节点到用户步骤映射

前端不应直接展示所有内部节点，而应映射为用户步骤。

| 内部节点 | 用户步骤 |
| --- | --- |
| `classify_question` | 理解问题 |
| `rewrite_question` | 整理问题 |
| `ask_rewrite_clarification` | 补充问题信息 |
| `recognize_intent` | 识别分析意图 |
| `ask_intent_clarification` | 确认分析方式 |
| `retrieve_knowledge` | 匹配数据资产 |
| `ask_metric_selection` | 选择分析指标 |
| `generate_sql` | 生成查询 |
| `execute_sql` | 查询数据 |
| `handle_sql_error` | 修复查询 |
| `generate_question_answer` | 生成答案 |
| `recommend_questions` | 推荐追问 |
| `compose_final_reply` | 整理回复 |

多个内部节点可聚合成一个用户步骤，例如 `classify_question`、`rewrite_question` 和 `recognize_intent` 可在收缩态显示为“理解问题”。

## 状态模型

前端展示层统一处理以下状态：

- `running`：当前步骤执行中。
- `waiting_input`：workflow 暂停，等待用户补充业务信息。
- `answered`：用户已补充，workflow 恢复运行。
- `skipped`：用户跳过补充，进入兜底路径。
- `failed`：节点或 run 失败。
- `cancelled`：用户手动停止。
- `succeeded`：workflow 完成。

`waiting_input` 不是错误状态，也不是审批状态。

## 数据契约建议

第一版可以复用现有接口：

- `GraphRunResponse.context_summary.pending_interaction`
- `GraphTraceResponse.nodes`
- `GraphPendingInteractionResponse.prompt`
- `GraphPendingInteractionResponse.options`
- `GraphPendingInteractionResponse.response_schema`

同时建议后端逐步补充更稳定的展示字段：

```json
{
  "answer": "...",
  "visualization": {},
  "table": [],
  "progress_summary": [
    {
      "key": "retrieve_knowledge",
      "label": "匹配数据资产",
      "status": "succeeded",
      "summary": "命中访问人数、店铺、日期"
    }
  ],
  "pending_interaction": {
    "interaction_id": "...",
    "type": "metric_selection",
    "title": "请选择要分析的指标",
    "prompt": "...",
    "options": []
  },
  "technical_trace": {}
}
```

这样前端不需要长期从 raw node output 中猜测展示内容。

## 组件边界

建议新增或拆分以下前端组件：

- `GraphWorkflowAnswer.vue`
  - 保留编排职责：创建 run、刷新 run、提交 interaction、写回 record。
- `GraphWorkflowProgress.vue`
  - 展示执行进度和可展开步骤摘要。
- `GraphWorkflowInteractionCard.vue`
  - 渲染 `pending_interaction`，处理业务澄清和选项提交。
- `GraphWorkflowFinalAnswer.vue`
  - 展示自然语言答案、表格、图表。
- `GraphWorkflowTechnicalTrace.vue`
  - 折叠展示 raw trace，替代当前默认展开的 JSON trace。

## 错误处理

- 没有 `dataset_id`：主回答区展示“当前会话没有可用数据集，无法启动分析”。
- interaction 提交失败：卡片保留用户输入并显示可重试提示。
- trace 刷新失败：进度区显示“进度刷新失败”，不影响已生成答案展示。
- run 失败：进度停在失败节点，主回答区展示失败原因和“查看技术详情”。
- 用户停止：进度显示“已停止”，保留已完成步骤。

## 测试设计

前端单元测试或组件测试：

- `waiting_input` 时展示澄清卡片，不展示 raw JSON。
- `ask_metric_selection` options 能被渲染为业务按钮。
- 用户选择指标后调用 `answerInteraction`，payload 正确。
- 用户跳过时提交 `{ skipped: true }`。
- `succeeded` 时主回答展示最终答案，技术详情默认折叠。
- `failed` 时显示失败步骤和错误信息。

后端测试：

- `ask_rewrite_clarification` 生成 prompt/options/schema。
- `ask_intent_clarification` 生成分析方式选项。
- `ask_metric_selection` 使用 knowledge ambiguity 或 candidate groups 生成指标选项。
- 用户提交 interaction 后 run 从 `waiting_input` 恢复到目标节点。

## 验收标准

- 普通用户默认看不到后端 raw JSON。
- 用户能看到当前执行到哪个业务步骤。
- 当流程暂停时，用户清楚知道需要补充什么信息。
- 用户补充后 workflow 自动继续执行。
- 技术详情仍可用于排查，但默认折叠。
- 最终回答以自然语言、表格或图表为主，而不是节点输出 JSON。
