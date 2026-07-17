# ChatBI v1 Step 4 交互子系统定稿设计

## 1. 目标

本阶段完成 `chatbi-v1-graph-refactor-analysis-and-design.md` 中的 Step 4：

1. 建立标准 `variables.interactions.<ask_node>` 交互域；
2. 把交互回答的读取收敛到统一 accessor，迁移期兼容旧 `variables.*_response` 字段；
3. 退役 `ChatBIV1InteractionResponsePatcher`，不再由运行时在 resume 阶段改写 `intent` 或 `knowledge`；
4. 将交互回答的业务消费移动到对应能力节点内部；
5. 将澄清轮次配置写入交互节点 metadata，并让条件 gate 从 metadata 派生；
6. 补齐五类澄清、跳过、多轮组合的端到端测试。

本阶段不实现 Step 5 的结果校验、比较/占比多查询计划、HAVING/detail 模式，也不做 Step 6 的 trace 元数据全面派生。PR 上现有 `Typos Check` 拼写失败会作为发布卫生修复一并处理，但不改变业务行为。

## 2. 现状与问题

当前代码已经完成了 Step 4 的一部分：

- 五类交互节点已使用作用域化条件，例如 `interaction.metric.answered` 只读 `metric_selection`；
- 澄清轮次已通过 `ClarificationRoundGate` 控制，`max_loop_iterations` 已上调为安全网；
- 交互回答会被写入 `variables.rewrite_response`、`variables.intent_response`、`variables.slot_response`、`variables.metric_selection`、`variables.cross_model_response`。

尚未完成的关键点是：`GraphRuntime.resume()` 仍调用 `ChatBIV1InteractionResponsePatcher`，由运行时根据交互回答直接改写 `variables.intent` 和 `variables.knowledge`。这带来三个问题：

1. 运行时层包含 ChatBI 业务知识，甚至调用 `SemanticKnowledgeAdapter` 私有方法；
2. 用户回答既存在原始 response 字段，又被 patcher 改写进 intent/knowledge，生命周期不清楚；
3. `bind_query_plan` 本应是 evidence/plan 的收敛点，但指标选择仍在运行时提前改写 knowledge。

Step 4 的目标是把“交互回答是什么”和“业务如何消费回答”拆开：运行时只保存回答，业务节点在重跑时读取并消费回答。

## 3. 标准交互域

标准域写在 `variables.interactions.<ask_node>`：

```json
{
  "variables": {
    "interactions": {
      "ask_metric_selection": {
        "node_name": "ask_metric_selection",
        "round": 1,
        "response": {"metric": 239},
        "skipped": false,
        "answered_at": "2026-07-07T10:00:00Z"
      }
    }
  }
}
```

字段语义：

- `node_name`：交互节点名，用于 trace 与调试；
- `round`：该交互节点已被展示的轮次；
- `response`：用户提交的原始回答；
- `skipped`：`response.skipped is true` 的归一结果；
- `answered_at`：服务端接收回答的时间，便于审计。

迁移期保留旧字段双写：

| 交互节点 | 标准路径 | 旧兼容路径 |
| --- | --- | --- |
| `ask_rewrite_clarification` | `variables.interactions.ask_rewrite_clarification` | `variables.rewrite_response` |
| `ask_intent_clarification` | `variables.interactions.ask_intent_clarification` | `variables.intent_response` |
| `ask_slot_clarification` | `variables.interactions.ask_slot_clarification` | `variables.slot_response` |
| `ask_cross_model_split` | `variables.interactions.ask_cross_model_split` | `variables.cross_model_response` |
| `ask_metric_selection` | `variables.interactions.ask_metric_selection` | `variables.metric_selection` |

新代码必须优先读取标准域；旧字段只作为兼容回退，后续上下文清理阶段再删除。

## 4. 写入机制

交互节点仍返回 `WAITING_INPUT`，但 `allowed_update_paths` 改为同时包含标准路径和旧路径。引擎 resume 阶段只做两件事：

1. 校验 interaction 与用户权限；
2. 把用户 response 写入 `allowed_update_paths` 对应位置。

标准路径的写入值不是裸 response，而是交互记录对象。为避免把 ChatBI 特例硬编码进 workflow engine，`ChatBIV1InteractionNode` 负责把 `allowed_update_paths` 与标准交互 metadata 放入 interaction spec；`GraphRuntime.resume()` 使用通用规则写入：

- 路径以 `variables.interactions.` 开头时写入交互记录对象；
- 其他路径继续写入原始 response，保持旧字段兼容。

写入后不再调用 `interaction_response_patcher`。`GraphRuntime` 构造参数保留一段兼容期，但 ChatBI v1 runtime 不再传入 patcher。

## 5. 读取与消费

### 5.1 统一读取器

`ChatBIRunContext` 新增：

- `interaction(node_name)`：读取标准域；
- `interaction_response(node_name, legacy_key)`：优先返回标准域 `response`，否则回退旧字段；
- `rewrite_response`、`intent_response`、`slot_response`、`metric_selection`、`cross_model_response` 改为调用统一读取器。

这样所有 adapter 可以逐步切换，不需要直接知道双写细节。

### 5.2 rewrite / intent

`QuestionAdapter.rewrite()` 和 `QuestionAdapter.recognize_intent()` 继续读取 `ctx.rewrite_response`、`ctx.intent_response`。因为 accessor 已经优先读取标准域，这两个 adapter 的业务行为保持不变。

### 5.3 slot clarification

原 patcher 的 slot 逻辑迁移到能力层。`SemanticKnowledgeAdapter.retrieve()` 在使用 `ctx.intent` 前先构造“交互增强后的 intent 视图”：

- `subject_domain` 澄清回答写入视图中的 `subject_domain`；
- `dimension_usage = ignore` 从视图中移除对应维度；
- `dimension_usage = group_by` 把对应维度设置为 group_by；
- `dimension_values` 或 `dimension_value/filter_value` 把对应维度设置为 filter；
- 已解决的 `dimension` / `subject_domain` 从 `ambiguous_slots` 移除。

该视图只在当前节点内使用，不直接回写 `variables.intent`。这保证运行时不再改业务上下文，同时重跑 `retrieve_knowledge` 时仍能根据用户回答产出正确 knowledge。

### 5.4 metric selection

指标选择不再提前改写 `variables.knowledge`。`QueryPlanBinder.bind()` 在绑定计划时读取 `ctx.metric_selection`：

- 若用户跳过，则保持原有 `metric_ambiguous` 路由到回答；
- 若用户选择了指标，则从 `knowledge.ambiguities` 或 `knowledge.candidate_groups.metrics` 中定位候选；
- 计划的 `metrics` 只使用用户选中的指标；
- 维度裁剪逻辑迁移到 planning 模块，并复用公开 helper，避免调用 adapter 私有方法；
- 若选中指标携带 `model_id/payload`，计划绑定保留必要字段。

迁移后，`knowledge.status` 不再被改成 `user_selected`。用户选择事实体现在 `variables.interactions.ask_metric_selection` 和 `variables.plan.strategy` 中。

### 5.5 cross model split

`CrossModelSplitRequestedCondition` 改为通过 `ctx.cross_model_response` 或标准交互读取器判断用户是否确认拆分。旧字段继续兼容。

## 6. 条件与轮次 metadata

交互节点 metadata 增加：

```json
{
  "interaction": {
    "response_key": "metric_selection",
    "standard_path": "variables.interactions.ask_metric_selection",
    "legacy_path": "variables.metric_selection",
    "max_rounds": 2
  }
}
```

`register_chatbi_conditions()` 不再手写 `clarification_gates` 的轮次值，而是从图定义 metadata 生成：

- `clarify.<name>.allowed`
- `clarify.<name>.exhausted`

作用域化 answered/skipped 条件也从 metadata 读取 response path。这样新增交互节点时只需要在节点 metadata 中声明一次。

本阶段不要求 API 和前端完全从 metadata 派生显示文案；那属于 Step 6。

## 7. 错误处理与兼容

- 标准交互记录构造失败时，resume 返回引擎级错误，不写半截上下文；
- 旧字段缺失但标准域存在时，所有新 accessor 正常工作；
- 标准域缺失但旧字段存在时，兼容旧 run；
- 用户提交 `skipped=true` 时，answered 条件不命中，skipped 条件命中；
- `ChatBIV1InteractionResponsePatcher` 删除后，运行时不再依赖 ChatBI 私有业务方法。

## 8. 测试与验收

采用 TDD，至少覆盖：

1. resume 后同时写入标准交互域与旧兼容字段；
2. `ChatBIRunContext` 优先读取标准域，旧字段作为回退；
3. 五类 answered/skipped 条件读取标准域并兼容旧字段；
4. rewrite 澄清恢复后继续重写；
5. intent 澄清恢复后继续识别；
6. slot subject_domain 澄清在 `retrieve_knowledge` 节点内生效；
7. slot dimension value/group_by/ignore 澄清在 `retrieve_knowledge` 节点内生效；
8. metric selection 在 `bind_query_plan` 内生成用户选中指标的 plan；
9. metric selection 维度裁剪保持现有行为；
10. `ChatBI v1` runtime 不再注入 `ChatBIV1InteractionResponsePatcher`；
11. `Typos Check` 命中的 `unparsable` 拼写问题被修正；
12. 相关 workflow、workflow_engine 测试通过。

验收时需确认 Step 3 的执行域、artifact、answer projection 测试不回归。

## 9. 非目标

- 删除旧 `variables.*_response` 字段；
- 删除 `slot_bindings` 双写；
- 实现结果校验节点；
- 实现比较/占比多查询计划；
- 重构 trace API 与前端节点 label；
- 引入多轮 conversation 历史。

## 10. 迁移顺序

1. 增加交互域模型与读取器测试；
2. 修改交互节点输出与 resume 写入逻辑，实现标准域双写；
3. 条件改读 metadata/标准域，保留旧字段回退；
4. 将 slot clarification 逻辑迁移到 knowledge adapter 的本地 intent 视图；
5. 将 metric selection 逻辑迁移到 QueryPlanBinder；
6. 从 ChatBI v1 runtime 移除 `ChatBIV1InteractionResponsePatcher`；
7. 删除 patcher 类与直接测试，替换为节点级消费测试；
8. 修复 typos 检查；
9. 运行目标测试并更新主设计文档 Step 4 状态。
