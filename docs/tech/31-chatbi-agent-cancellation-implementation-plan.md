# ChatBI Agent 取消执行实施计划

## 1. 目标与范围

本计划按照 [30-chatbi-agent-cancellation-design.md](./30-chatbi-agent-cancellation-design.md) 实施 Agent 取消执行能力。

本次只实现“取消”，不实现取消后的恢复、用户干预或新的执行指令。

完成后必须满足：

- 用户点击取消后，服务端能够收到并持久化取消请求；
- 运行中的 LLM 返回后不会继续执行 Tool Call；
- 运行中的 Tool 返回后不会继续更新 Agent 上下文或进入下一轮；
- 尚未开始的 Tool 不会执行；
- 并行 Tool 能够统一收口；
- Run、Step、ChatRecord 和 Event 的终态一致；
- 前端断开 SSE 不会被误认为服务端已经取消；
- 取消与正常完成并发时，最终状态由数据库原子更新决定。

## 2. 实施原则

1. 先完成当前同步 SSE 架构下的正确取消语义，再迁移到独立 Agent Runner。
2. 取消请求和取消终态分开，运行中的 Run 先进入 cancellation_requested。
3. 不强制终止 Python 线程；底层支持取消时主动取消，不支持时等待返回并丢弃结果。
4. cancelled 终态只能由 AgentLifecycle 统一写入。
5. 保留当前 ToolStatus.INTERRUPTED，不另造一套重复的 Tool 结果状态。
6. 先扩展现有入口，避免一次性重构整个 Agent 目录。
7. 每个阶段都必须有可独立运行的测试和回滚边界。

## 3. 阶段总览

| 阶段 | 内容 | 主要产物 | 前置依赖 |
| --- | --- | --- | --- |
| P0 | 基线和执行边界确认 | 取消行为基线、测试夹具 | 无 |
| P1 | Run 状态和持久化 | 数据库迁移、Repository 原子更新 | P0 |
| P2 | 取消控制器和检查守卫 | 统一取消信号、取消阶段 | P1 |
| P3 | AgentLoop 和 LLM 收口 | LLM 结果丢弃、禁止后续调度 | P2 |
| P4 | Tool 和并行任务收口 | Tool 前中后取消、并行收口 | P2 |
| P5 | Lifecycle 和 Event | 统一终态、取消事件 | P1、P3、P4 |
| P6 | API、SSE 和前端 | 取消请求、取消中状态、事件补拉 | P5 |
| P7 | 独立执行任务 | Agent Runner 与 SSE 解耦 | P6 |
| P8 | 验证、灰度和清理 | 全链路验收、监控和文档收口 | P7 |

P0～P6 是第一期，完成后可以支持正确的取消语义。P7 是第二期，解决 SSE 断开后服务端执行生命周期不独立的问题。

## 4. P0：基线和执行边界确认

### 4.1 目标

在修改代码前固定当前行为，明确每个取消检查点和测试替身。

### 4.2 工作项

| 工作项 | 文件 |
| --- | --- |
| 梳理当前取消状态迁移 | backend/apps/chatbi/api/interactions.py |
| 梳理主循环检查点 | backend/apps/chatbi/orchestration/agent/loop.py |
| 梳理 Tool 取消处理 | backend/apps/chatbi/orchestration/agent/tool_execution.py |
| 梳理线程池行为 | backend/apps/tool/concurrency.py |
| 梳理前端停止行为 | frontend/src/views/chat/answer/AgentAnswer.vue |
| 增加测试夹具 | backend/tests/agent/conftest.py（如当前没有则新增） |

### 4.3 产出

- 确认以下测试替身：
  - 可阻塞、可检测取消的 LLM Client；
  - 返回后才发现取消的 LLM Client；
  - 可取消 Tool；
  - 不可取消 Tool；
  - 多个并行 Tool。
- 确认当前数据库测试使用的 Session 和事务方式。

## 5. P1：Run 状态和持久化

### 5.1 目标

让取消请求可以持久化、幂等，并与正常完成形成明确竞争关系。

### 5.2 数据模型调整

文件：

- backend/apps/chatbi/models/orm/agent_run.py
- backend/alembic/versions/103_agent_run_cancellation.py

建议增加到 ChatbiAgentRun：

~~~text
cancel_requested_at: datetime | None
cancelled_at: datetime | None
cancel_reason: str | None
cancel_stage: str | None
cancel_request_id: str | None
~~~

建议给 AgentStepStatus 增加：

~~~text
CANCELLED = "cancelled"
~~~

迁移文件的 revision 使用 103_agent_run_cancellation，down_revision 使用当前最新迁移 102_repair_semantic_tables。

### 5.3 Repository 接口

文件：backend/apps/chatbi/repository/sqlmodel/agent_run_repository.py

新增或调整：

~~~python
def request_cancel(
    session: Session,
    run_id: int,
    *,
    request_id: str,
    user_id: int,
    reason: str,
) -> ChatbiAgentRun: ...

def mark_cancelled(
    session: Session,
    run: ChatbiAgentRun,
    *,
    stage: str,
    reason: str,
) -> None: ...
~~~

request_cancel 的状态规则：

- created、waiting_user：直接进入 cancelled；
- running：进入 cancellation_requested；
- cancellation_requested：返回已有取消请求；
- finished、failed、cancelled：不修改，返回当前终态。

### 5.4 验收

- 数据库升级和降级成功；
- 重复 request_id 不产生重复状态变更；
- 完成和取消并发时只有一个状态迁移成功；
- 既有历史 Run 可以正常读取。

## 6. P2：取消控制器和检查守卫

### 6.1 目标

将“是否取消”的判断和“在什么阶段取消”的记录集中起来，避免 API、Loop、ToolExecutor 各自判断。

### 6.2 代码结构

第一期先保持文件数量较少：

~~~text
backend/apps/chatbi/orchestration/agent/
├── cancellation.py        # DatabaseCancellationController、取消阶段和快照
└── cancellation_guard.py  # AgentLoop / ToolExecutor 共用的检查守卫
~~~

如果后续状态判断、执行器和持久化逻辑明显增长，再拆成 cancellation/ 目录。

### 6.3 接口设计

cancellation.py：

~~~python
class CancellationStage(StrEnum):
    BEFORE_LLM = "before_llm"
    DURING_LLM = "during_llm"
    AFTER_LLM = "after_llm"
    BEFORE_TOOL = "before_tool"
    DURING_TOOL = "during_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_NEXT_STEP = "before_next_step"

class DatabaseCancellationController:
    def is_requested(self) -> bool: ...
    def requested_at(self) -> datetime | None: ...
    def mark_stage(self, stage: CancellationStage) -> None: ...
~~~

cancellation_guard.py：

~~~python
class CancellationGuard:
    def check(self, stage: CancellationStage) -> bool: ...
    def require_not_requested(self, stage: CancellationStage) -> None: ...
~~~

取消守卫只负责判断和抛出明确的内部取消结果，不负责写 Run 终态。

### 6.4 验收

- 多次查询仍使用独立 Session；
- 取消阶段能够被记录；
- 没有宽泛异常吞掉取消信号；
- apps/tool 不依赖 ChatBI ORM。

## 7. P3：AgentLoop 和 LLM 收口

### 7.1 目标

保证取消后的模型结果不会执行 Tool，也不会进入下一轮。

### 7.2 修改位置

| 文件 | 修改内容 |
| --- | --- |
| backend/apps/chatbi/orchestration/agent/loop.py | 在 LLM 前、LLM 后、下一轮前调用 CancellationGuard |
| backend/apps/chatbi/orchestration/agent/reasoning.py | 向模型调用传递取消上下文；模型返回后不负责继续调度 |
| backend/apps/chatbi/orchestration/agent/model_client.py | 保留同步调用兼容；为支持取消的模型客户端预留请求关闭入口 |
| backend/apps/chatbi/orchestration/agent/state.py | 将取消控制器放入 AgentRuntimeState |
| backend/tests/agent/test_agent_loop.py | 增加模型返回前后取消测试 |

### 7.3 处理规则

1. LLM 调用前取消：不创建模型请求。
2. LLM 调用中取消且客户端支持关闭：关闭请求，等待返回。
3. LLM 调用中取消但客户端不支持关闭：等待同步调用返回。
4. LLM 返回后再次检查取消。
5. 已取消时丢弃 ModelDecision、Reasoning 文本和 Tool Call。
6. 当前 Step 标记为取消，不调用 AgentToolExecutor.execute()。

### 7.4 验收

- LLM 返回 Tool Call 后发生取消，不会执行 Tool；
- LLM 返回直接回答后发生取消，不会调用 finish 或保存成功结果；
- 取消过程中发生模型异常时，最终优先收口为 cancelled，原始异常写入审计信息；
- 未取消路径的现有 Agent Loop 测试全部通过。

## 8. P4：Tool 和并行任务收口

### 8.1 目标

保证 Tool 取消后不会更新 Agent 上下文，不会继续触发下一轮。

### 8.2 修改位置

| 文件 | 修改内容 |
| --- | --- |
| backend/apps/chatbi/orchestration/agent/tool_execution.py | Tool 前、Tool 返回后和结果投影前检查取消 |
| backend/apps/tool/context.py | 保留通用取消协议，必要时增加取消时间信息 |
| backend/apps/tool/base.py | 保留 supports_cancellation，明确具体 Tool 的能力声明 |
| backend/apps/tool/result.py | 扩展 interrupted 的 metadata 约定 |
| backend/apps/tool/concurrency.py | 取消尚未开始的 Future，等待已开始任务收口 |
| backend/tests/agent/test_agent_tool_cancellation.py | 新增 Tool 场景测试 |
| backend/tests/tool/test_tool_runtime.py | 新增通用运行时取消测试 |

### 8.3 处理规则

1. Tool 执行前发现取消：不调用 Registry。
2. Tool 执行中发现取消：由 Tool 自身决定是否能够停止底层操作。
3. Tool 返回后先保存执行事实，再检查是否已经取消。
4. 已取消时不执行状态补丁、不追加 Tool Message、不发布成功结果。
5. 尚未开始的并行任务标记为 interrupted。
6. 已开始的并行任务全部返回后，才允许关闭执行上下文。

### 8.4 数据库查询专项处理

execute_sql 需要单独确认底层 QueryService 是否能拿到数据库游标或连接，并调用对应数据库驱动的取消方法。若当前 QueryService 不能提供该能力，第一期只实现：

- Run 进入 cancellation_requested；
- 查询返回后丢弃结果；
- 记录 underlying_operation_may_have_completed=true；
- 不声称数据库查询已经停止。

不能为了满足界面上的“已取消”而强制关闭共享数据库连接。

### 8.5 验收

- Tool 未开始时不会进入真实 Tool 实现；
- 可取消 Tool 能收到取消信号；
- 不可取消 Tool 返回后不更新 Agent 上下文；
- 并行任务不会因为第一个任务返回而提前关闭其他任务；
- 取消后不调用下一轮 LLM、SQL 重试或 finish。

## 9. P5：Lifecycle 和 Event

### 9.1 目标

把 Run、Step、ChatRecord、Tool Call 和事件收口集中到一个生命周期入口。

### 9.2 修改位置

| 文件 | 修改内容 |
| --- | --- |
| backend/apps/chatbi/orchestration/agent/lifecycle.py | 新增 finalize_cancellation() |
| backend/apps/chatbi/orchestration/agent/loop.py | 只返回取消结果，不重复写终态 |
| backend/apps/chatbi/orchestration/agent/tool_execution.py | 只关闭 Tool Call 并返回取消结果 |
| backend/apps/event/models/dto.py | 注册 run-cancel-requested 和 run-cancelled |
| backend/apps/event/service.py | 保持事件序号和持久化行为一致 |
| backend/tests/agent/test_agent_cancel_lifecycle.py | 校验终态和事件顺序 |

### 9.3 finalize_cancellation 负责内容

1. 回滚失败事务，确保使用干净 Session。
2. 关闭当前 Step 中仍为 running 的 Tool Call。
3. 将当前 Step 标记为 cancelled。
4. 关闭未完成的消息 Tool Call。
5. 将 ChatRecord 设置为 cancelled。
6. 将 Run 设置为 cancelled。
7. 保存消息、预算和派生上下文快照。
8. 保存取消阶段、原因和时间。
9. 发布 run.cancelled。

事件顺序必须满足：

~~~text
run.cancel-requested
    ↓
Tool / Step 收口事件
    ↓
run.cancelled
~~~

### 9.4 验收

- run.cancelled 只出现一次；
- run.finished、answer.completed 不会在取消后出现；
- Run 和 ChatRecord 状态一致；
- Timeline 可以显示取消发生阶段；
- 事务失败后不会留下半提交终态。

## 10. P6：API、SSE 和前端

### 10.1 后端 API

文件：backend/apps/chatbi/api/interactions.py

调整 agent_cancel：

1. 使用 Repository 的原子取消接口。
2. 运行中的 Run 返回 cancellation_requested，HTTP 状态建议使用 202。
3. 创建态和等待用户态可以直接返回 cancelled。
4. 已结束 Run 返回现有状态，不返回错误。
5. 不在 API 中等待 LLM 或 Tool。

### 10.2 Event 补拉

继续复用现有：

- GET /chat/agent/runs/{run_id}/events
- agent_run_repository.build_timeline_response()

需要确保新事件写入现有事件表并带有连续 sequence。

### 10.3 前端调整

| 文件 | 修改内容 |
| --- | --- |
| frontend/src/api/agent-chat.ts | 明确取消响应类型和取消请求方法 |
| frontend/src/views/chat/answer/AgentAnswer.vue | 点击取消时调用后端接口，不只调用 AbortController.abort() |
| frontend/src/views/chat/answer/agentEventReducer.ts | 增加 run.cancel-requested 和 run.cancelled 状态处理 |
| frontend/src/views/chat/execution-component/agentTimelineProjection.ts | 显示“正在取消”和“已取消” |
| frontend/src/views/chat/execution-component/AgentTimeline.vue | 取消期间禁止误显示为运行中或失败 |

前端状态：

~~~text
running
    ↓ 点击取消
cancelling
    ↓ run.cancelled
cancelled
~~~

前端网络断开只表示 SSE 不可用，不修改 Run 状态。

### 10.4 验收

- 点击取消后服务端一定收到取消请求；
- 页面显示“正在取消”，而不是立即显示“已取消”；
- 收到 run.cancelled 后才解除执行 loading；
- 断开 SSE 后通过事件补拉仍能显示最终取消状态；
- 重复点击取消不会重复生成终态事件。

## 11. P7：Agent 执行与 SSE 解耦

### 11.1 目标

解决当前 Agent 在 SSE 生成器中直接运行的问题，使服务端执行不依赖浏览器连接。

### 11.2 当前结构

~~~text
POST /chat/agent/stream
    └── StreamingResponse
        └── create_agent_start_events()
            └── loop.run()
~~~

### 11.3 目标结构

~~~text
POST /chat/agent/stream
    ├── 创建 Run
    ├── 提交 Agent Runner
    └── 订阅 Event Log

POST /chat/agent/runs/{run_id}/cancel
    └── 写入 cancellation_requested

Agent Runner
    ├── 独立拥有 Session
    ├── 执行 AgentLoop
    ├── 处理 LLM / Tool 收口
    └── 发布 Event Log
~~~

### 11.4 代码结构

~~~text
backend/apps/chatbi/orchestration/agent/
├── runner.py                # 独立运行一次 Agent Run
├── service.py               # 创建 Run、提交任务、订阅事件
├── loop.py                  # Agent 调度循环
├── cancellation.py          # 取消控制器
└── lifecycle.py             # 终态和一致性保存
~~~

执行器方案按项目现有基础设施选择：

- 如果已有可靠的后台任务组件，复用该组件；
- 如果没有，第一期可以使用进程内后台任务验证结构，但不能作为跨进程可靠队列；
- 生产环境需要保证服务重启后不会留下永久的 cancellation_requested Run。

### 11.5 验收

- 关闭 SSE 后 Agent 仍能执行取消收口；
- 取消接口不依赖原始 HTTP 请求仍然存在；
- 服务重启后能扫描并处理未完成的取消请求；
- 一个 Run 不会被多个 Runner 同时执行。

## 12. P8：验证、灰度和清理

### 12.1 自动化测试

建议新增：

~~~text
backend/tests/agent/
├── test_agent_cancel_api.py
├── test_agent_cancellation_signal.py
├── test_agent_cancel_llm.py
├── test_agent_cancel_tool.py
├── test_agent_cancel_parallel.py
└── test_agent_cancel_lifecycle.py
~~~

必须覆盖：

- LLM 调用前取消；
- LLM 调用中取消；
- LLM 返回后、Tool 执行前取消；
- Tool 执行前取消；
- 可取消 Tool；
- 不可取消 Tool；
- 并行 Tool；
- 取消和完成竞争；
- 重复取消；
- SSE 断开后取消；
- 取消后禁止 finish、重试和成功事件。

### 12.2 验证命令

~~~bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/agent/test_agent_stream_api.py -q
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/agent/test_agent_loop.py -q
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/tool/test_tool_runtime.py -q
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/event/test_sse_context.py -q
~~~

完成 P6 后运行 Agent、Tool、Event 相关测试；完成 P7 后增加真实 SSE 断开和后台 Runner 测试。

### 12.3 监控指标

至少记录：

~~~text
agent_cancel_requested_total
agent_cancelled_total
agent_cancel_latency_ms
agent_cancel_by_stage
agent_cancel_underlying_operation_may_have_completed_total
agent_cancel_timeout_total
~~~

日志字段至少包含：

~~~text
run_id
record_id
request_id
cancel_stage
active_step_id
active_tool_call_id
underlying_operation_may_have_completed
~~~

### 12.4 灰度顺序

1. 仅后端开启取消状态和事件记录，前端暂不展示新状态。
2. 开启运行中取消请求，但保留详细日志和指标。
3. 开启前端“正在取消 / 已取消”展示。
4. 开启并行 Tool 取消收口。
5. 切换到独立 Agent Runner。
6. 稳定后移除前端只断开 SSE 的旧停止逻辑。

## 13. 回滚策略

### P1 之前

直接回滚代码，不涉及数据库结构。

### P1～P6

- 保留新增数据库字段，停止新逻辑读取；
- API 可以继续返回旧状态；
- 新增事件字段保持向后兼容，旧前端忽略未知事件；
- 不删除已有 cancellation_requested 和 cancelled 数据。

### P7

保留旧 SSE 直连执行入口作为临时开关，但只能用于紧急回退。后台 Runner 的任务必须具备唯一执行租约，避免新旧执行器同时消费同一个 Run。

## 14. 完成标准

满足以下条件才认为取消能力完成：

1. 用户点击取消后，后端返回 cancellation_requested 或已确认的 cancelled。
2. LLM 返回的 Tool Call 在取消后不会执行。
3. Tool 返回结果在取消后不会推动 Agent 继续执行。
4. 并行 Tool 不会因提前关闭 Session 产生未声明异常。
5. Run、Step、ChatRecord 和事件最终状态一致。
6. run.cancelled 只发布一次，且不会同时出现 run.finished。
7. 前端断开 SSE 不会影响服务端取消收口。
8. 重复取消和取消完成竞争场景具有确定结果。
9. 取消阶段和底层操作可能已完成的信息可在 Timeline 和日志中追踪。
10. Agent、Tool、Event 现有测试及新增取消测试全部通过。
