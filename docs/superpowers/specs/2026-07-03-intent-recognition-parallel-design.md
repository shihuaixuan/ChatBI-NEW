# ChatBI 意图识别节点并行执行设计

## 背景

当前 `QuestionAdapter.recognize_intent()` 在意图识别节点内顺序执行 3 个模型子任务：

```text
分析形态识别 intent shape
→ 指标和时间线索识别 semantic mentions
→ 维度槽位识别 dimension slots
→ 合并结果
```

这 3 个子任务的输入基本相同：

- 标准化问题 `rewritten_question`
- 会话上下文 `conversation_context`
- 用户反馈 `user_feedback`
- 数据集约束信息，例如主题域、可用维度
- 规则兜底结果 `fallback`

从业务依赖看，后一个子任务不需要消费前一个子任务的输出。当前顺序执行会把 3 次模型调用耗时线性累加，是意图识别节点的主要延迟来源。

## 设计目标

1. 将 3 个模型子任务改为并行执行，降低 `recognize_intent` 节点整体耗时。
2. 保持工作流图结构和 `IntentRecognitionOutput` 对外契约不变。
3. 每个子任务独立重试、独立 fallback、独立记录错误和耗时。
4. 并行执行后仍保证合并顺序固定、输出确定。
5. 支持统一的取消、超时和限流策略。
6. 在日志和 trace 中能看清每个子任务的耗时、状态和结果来源。

## 非目标

- 不把 3 个子任务拆成工作流图上的 3 个节点。
- 不改变 `retrieve_knowledge` 对 `intent` 输出的消费方式。
- 不改变意图识别与知识检索的职责边界：意图识别仍只输出自然语言 mention 和槽位线索，不确认 Headless 资产。
- 不在本次设计中重写提示词语义或增加新的意图字段。

## 总体方案

采用“节点内部并行、图结构不拆分”的方案。

工作流层仍保留一个 `recognize_intent` 节点；节点内部在完成共享上下文准备后，并行调度 3 个子任务：

```text
prepare_context
  ├─ shape task
  ├─ semantic task
  └─ dimensions task
merge
→ apply_feedback
→ post_validate
```

共享上下文准备仍串行执行，因为这些步骤只需要做一次：

- 读取原始请求。
- 读取 `variables.rewrite.rewritten_question`。
- 读取 `variables.intent_response`。
- 加载数据集 schema。
- 生成 `subject_domains`。
- 生成 `available_dimensions`。
- 生成规则 fallback。
- 生成 `conversation_context`。

3 个子任务只读共享上下文，不修改共享对象。

## 方案选择

### 方案一：节点内部线程池并行（推荐）

当前模型客户端协议是同步调用：

```python
QuestionClassificationModelClient.__call__(prompt) -> str
```

因此推荐在 `QuestionAdapter` 内部使用固定大小线程池并行调度 3 个同步模型调用。

优点：

- 改动面小。
- 不需要把 adapter、gateway、workflow runtime 改为 async。
- 保持当前测试替身和模型客户端协议基本不变。
- 可以通过配置关闭并行，回退到现有顺序执行。

缺点：

- 底层模型调用如果不可中断，取消只能停止等待，不能强制终止正在执行的请求。
- 需要确认默认模型客户端和下游 SDK 支持并发调用。

### 方案二：工作流图拆 3 个节点

把 shape、semantic、dimensions 拆为图上的 3 个节点，再增加一个 merge 节点。

优点：

- trace 天然可见。
- 每个子任务可以独立重试和路由。

缺点：

- 改动 workflow definition、runtime 调度和节点输出变量。
- 需要处理 3 个分支的 join 语义。
- 会扩大对现有前端进度展示、条件路由和测试的影响。

当前不推荐。该方案适合未来图引擎已有稳定 join 能力后再考虑。

### 方案三：迁移为全 async 调用

把模型客户端协议和 adapter 改成 async，并使用 `asyncio.gather()`。

优点：

- 更适合真正的异步 HTTP 模型客户端。
- 取消和超时语义更清晰。

缺点：

- 改动范围大。
- 当前 gateway 是同步 `invoke()`，需要向上传染 async。
- 容易把局部性能优化变成运行时架构改造。

当前不推荐作为第一阶段。

## 子任务结果结构

每个子任务不应只返回业务 payload，还应返回执行元信息，便于日志、trace 和测试断言。

内部结构建议：

```text
IntentSubtaskResult
- name: "shape" | "semantic" | "dimensions"
- payload: dict
- status: "succeeded" | "fallback" | "failed"
- source: "model" | "validation_repair" | "exception_fallback" | "timeout_fallback"
- error_code: str | None
- retry_count: int
- duration_ms: int
```

其中：

- `payload` 参与最终合并。
- `status/source/error_code/retry_count/duration_ms` 写入日志和节点 trace。
- 对外 `IntentRecognitionOutput` 字段保持不变。

## 并发调度设计

新增内部调度方法：

```text
_run_intent_subtasks(...)
```

职责：

1. 接收共享上下文、fallback 和配置。
2. 根据配置决定并行或顺序执行。
3. 启动 3 个子任务。
4. 对每个子任务应用独立超时和 fallback。
5. 返回按固定 key 组织的结果：

```text
{
  "shape": IntentSubtaskResult,
  "semantic": IntentSubtaskResult,
  "dimensions": IntentSubtaskResult
}
```

并行模式下，线程池 worker 数量固定为 3 或配置值：

```text
intent_subtask_parallel_enabled = true
intent_subtask_max_workers = 3
intent_subtask_timeout_seconds = 20
intent_subtask_overall_timeout_seconds = 25
```

顺序模式下，仍通过同一个调度方法执行，保证并行和顺序模式使用相同的结果封装和 fallback 语义。

## fallback 和错误语义

每个子任务独立 fallback。

| 子任务 | fallback 字段 |
| --- | --- |
| shape | `intent_type`、`confidence`、`required_slot_types`、`query_shape`、`subject_domain`、`ambiguous_slots`、`conflict_slots` |
| semantic | `metric_mentions`、`time_mentions`、`time_range`、`ambiguous_slots`、`conflict_slots` |
| dimensions | `dimension_mentions`、`dimension_slots`、`residual_filter_mentions`、`ambiguous_slots`、`conflict_slots` |

整体节点成功条件：

- 能合并出合法的 `IntentRecognitionOutput`。
- 即使 3 个子任务都使用 fallback，也允许节点成功，但必须记录 `all_subtasks_fallback=true`。

整体节点失败条件：

- 合并后无法通过 `IntentRecognitionOutput.model_validate()`。
- fallback payload 本身结构非法。
- 共享上下文准备阶段发生不允许降级的系统错误。

模型调用异常、单个子任务超时、单个子任务校验失败超过重试上限，不应直接导致整个节点失败，而应让对应子任务使用 fallback。

## 重试策略

保留当前 `_recognize_subtask()` 的重试语义：

- 子任务内部根据 validator 结果决定是否带 repair feedback 重试。
- 每个子任务独立维护 retry feedback。
- 一个子任务的 repair hint 不传给其他子任务。

并行化后不能共享可变的 `retry_feedback`、`user_feedback` 或 fallback payload。每个任务启动前应拿到自己的浅拷贝或重新构造后的 payload。

## 合并确定性

并发只改变执行时机，不改变合并规则。

最终仍固定调用：

```text
_merge_intent_parts(shape, semantic, dimensions)
```

合并来源保持现有边界：

- `intent_type`、`query_shape`、`subject_domain` 来自 shape。
- `metric_mentions`、`time_mentions`、`time_range` 来自 semantic。
- `dimension_mentions`、`dimension_slots`、`filter_mentions` 来自 dimensions。
- `ambiguous_slots`、`conflict_slots` 三方合并去重。
- `confidence` 使用三方 confidence 的最小值。

确定性要求：

- 按 `shape/semantic/dimensions` 固定 key 合并，不按任务完成顺序合并。
- list 去重继续使用现有 `_unique_strings()`。
- 不让子任务修改共享 dict。
- fallback 在调度前构造完成，子任务内只读或复制后使用。

## 超时、取消和限流

### 超时

设置两级超时：

- 子任务超时：单个任务超过阈值后，该任务使用 fallback。
- 节点总超时：整体超过阈值后，未完成任务使用 fallback，已完成任务保留。

建议默认：

```text
subtask_timeout_seconds = 20
overall_timeout_seconds = 25
```

### 取消

如果上游请求取消：

- 对未开始的 future 执行 cancel。
- 对已在执行的同步模型调用停止等待，并丢弃迟到结果。
- 日志记录 `source=timeout_fallback` 或 `source=cancelled_fallback`。

同步模型调用是否能真正中断，取决于底层 SDK。第一阶段不要求强制杀掉正在执行的请求。

### 限流

需要通过配置支持降级：

```text
intent_subtask_parallel_enabled = false
```

适用场景：

- 模型客户端不支持并发调用。
- 下游 provider 对同一请求链路有限流风险。
- 线上压测发现并行导致错误率上升。

如果需要更细粒度限流，可在模型客户端层增加 semaphore，而不是把限流逻辑散落在每个子任务中。

## 日志和 trace

每个子任务至少记录：

```text
run_id
node_name=recognize_intent
subtask=shape|semantic|dimensions
status
source
duration_ms
retry_count
error_code
```

节点最终 trace 建议增加内部摘要：

```json
{
  "intent_parallel": {
    "enabled": true,
    "all_subtasks_fallback": false,
    "subtasks": {
      "shape": {
        "status": "succeeded",
        "source": "model",
        "duration_ms": 812,
        "retry_count": 0
      },
      "semantic": {
        "status": "fallback",
        "source": "exception_fallback",
        "duration_ms": 2031,
        "retry_count": 1,
        "error_code": "MODEL_CALL_FAILED"
      },
      "dimensions": {
        "status": "succeeded",
        "source": "model",
        "duration_ms": 940,
        "retry_count": 0
      }
    }
  }
}
```

该摘要用于排查质量和耗时，不改变下游业务消费结构。

## 测试建议

### 单元测试

1. 三个子任务都成功时，输出与顺序执行一致。
2. shape 失败时，只 shape 使用 fallback，semantic 和 dimensions 结果保留。
3. semantic 失败时，只 semantic 使用 fallback。
4. dimensions 失败时，只 dimensions 使用 fallback。
5. 三个子任务都失败时，节点仍输出合法 fallback，并记录 `all_subtasks_fallback=true`。
6. 子任务完成顺序随机时，最终合并结果稳定。
7. dimensions 第一次校验失败且可重试时，只重试 dimensions。
8. 关闭并行配置后，仍走同一合并和 fallback 逻辑。

### 集成测试

1. `recognize_intent` 输出结构不变，`retrieve_knowledge` 继续按 mention 分槽位召回。
2. 意图澄清分支仍能根据 `ambiguous_slots` 正常路由。
3. trace 中包含每个子任务的耗时、状态和来源。
4. 模拟模型客户端慢调用，验证超时任务 fallback，未超时任务结果保留。

### 性能验证

在模型客户端支持并发的前提下，对比同一批问题的节点耗时：

- 顺序模式耗时约等于 3 个子任务耗时之和。
- 并行模式耗时约等于最慢子任务耗时，加少量调度开销。

性能验证只作为优化证据，不应牺牲输出稳定性和错误可观测性。

## 实施顺序建议

1. 增加内部结果封装和调度方法，但先保持顺序执行，确保行为不变。
2. 增加并行配置开关，默认可先在测试中开启。
3. 用线程池并行执行 3 个子任务。
4. 增加子任务级日志和 trace 摘要。
5. 补齐失败、超时、随机完成顺序和配置降级测试。
6. 线上灰度开启并行，观察模型错误率、限流和节点耗时。

