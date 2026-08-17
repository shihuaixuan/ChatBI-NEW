# Agent 工具运行时完善与 `apps/tool` 抽取说明

**定位：** 沉淀 Agent 工具层对照 AgentScope 后的完善项，并给出是否抽取为 `apps/tool` 的架构结论与落位方案。  
**关联代码：** `backend/apps/chatbi/orchestration/agent/`  
**关联文档：** [Agentic-ChatBI技术文档.md](./Agentic-ChatBI技术文档.md)、[09-agentic-chatbi-tech-design.md](./09-agentic-chatbi-tech-design.md)、[10-graph-agentic-capability-overlap-analysis.md](./10-graph-agentic-capability-overlap-analysis.md)、[16-agent-event-and-observability-trace-design.md](./16-agent-event-and-observability-trace-design.md)、`backend/apps/AGENTS.md`  
**日期：** 2026-07-24  
**状态：** 已实施（阶段 A + B + P1：并行分批/offload/middleware 已落地）

---

## 1. 背景与结论

### 1.1 背景

当前 Agent 工具实现位于：

```text
apps/chatbi/orchestration/agent/
  loop.py          # AgentLoop：bind_tools 循环、澄清挂起、终态
  budget.py        # BudgetGuard 硬熔断
  tools/
    base.py        # AgentTool / ToolOutput / AgentToolContext
    registry.py    # 白名单分发
    core.py        # 6 个 P0 领域工具
    interaction.py # clarify / terminology / sql examples
```

设计主线正确：**厚工具、薄循环、白名单执行、守护内嵌、summary/payload 双通道、语义资产来源约束**。  
对照 `extra/agentscope` 后，缺口主要在**运行时工程能力**（收口、软着陆、并发元数据、结果 offload、横切 middleware），而不是业务工具清单本身。

### 1.2 总结论

1. **需要完善**的是工具运行时横切能力（见 §3），优先 P0 正确性，再做吞吐与上下文治理。
2. **可以**把工具相关能力抽到 `apps/tool`，但**只抽通用 runtime 内核**，不把 ChatBI 九工具整体搬过去。
3. **`apps/tool` 内部不必强 DDD**：按技术模块组织即可（函数 + dataclass + Protocol），仪式上限低于业务域。
4. **ChatBI 领域工具仍归属 `chatbi`**，继续做 tool-schema → 领域服务的薄翻译层，业务不变量不外溢。

一句话：

> 借 AgentScope 的“工具运行时形态”，不借它的“通用 agent 平台业务边界”。

---

## 2. 是否抽取 `apps/tool`：可以，但要分层

### 2.1 为什么“整体搬到 apps/tool”不合适

若把 `search_semantic_assets` / `compile_semantic_sql` / `execute_sql` 等一并放进 `apps/tool`，会出现：

| 问题 | 说明 |
| --- | --- |
| 边界污染 | 工具实现必然依赖 `chatbi.services` / `semantic` / `knowledge`，`apps/tool` 会变成第二个 ChatBI |
| 与历史教训冲突 | 旧 `capabilities` 曾因混装领域逻辑与运行时概念难复用；P5/R1 已把领域能力收回 `chatbi/services` |
| 依赖方向变差 | 通用层反向依赖业务域，或业务域与通用层双向纠缠 |
| 风格错配 | `apps/tool` 若既是平台又是业务工具仓，既做不好通用，也守不住问数不变量 |

因此：**可抽取的是“工具运行时”，不是“问数工具全集”。**

### 2.2 推荐分层

```text
┌──────────────────────────────────────────────────────────┐
│ apps/chatbi/orchestration/agent                          │
│   AgentLoop / Budget 策略落地 / 澄清挂起恢复 / SSE         │
│   domain tools: search/compile/execute/finish/clarify…   │
│   （薄翻译 + 业务硬门：understanding / asset / finish）    │
└────────────────────────────┬─────────────────────────────┘
                             │ 依赖方向唯一向下
                             ▼
┌──────────────────────────────────────────────────────────┐
│ apps/tool   （通用工具运行时，弱仪式、非 DDD）              │
│   Tool / ToolOutput / Registry / Middleware              │
│   concurrency batching / unfinished tool-call close      │
│   result fold/offload helpers / soft-budget primitives   │
│   禁止依赖 chatbi / semantic / knowledge / datasource    │
└──────────────────────────────────────────────────────────┘
```

### 2.3 命名选择：`apps/tool` 可用

| 候选 | 评价 |
| --- | --- |
| `apps/tool` | **推荐采用**（与用户意图一致）。作为技术边界模块，与 `retrieval` 类似偏“机制”而非业务子域 |
| `platform/tool` | 语义更“平台”，但当前 `platform/` 仅有 `workflow_engine`，且 agent 工具运行时尚未证明跨产品复用；可后续再迁 |
| 继续放在 `chatbi/orchestration/agent/tools` | 短期成本最低，但运行时与领域工具继续耦合，后续完善会越来越挤 |

**决策：采用 `apps/tool`，但以“运行时内核”为边界，不接收 ChatBI 领域工具实现。**

### 2.4 内部不需要强 DDD —— 可以，且应该

`apps/tool` 的词汇表是技术词：Registry / Middleware / Chunk / Fold / Budget primitive。  
按 `AGENTS.md` 风格判断，它更接近：

- **技术边界模块**（类似 datasource 的“可替换缝”）或
- **轻平台能力**（类似 workflow_engine 的子集）

因此内部约定：

| 允许 | 不允许 |
| --- | --- |
| 模块级函数、dataclass、少量 Protocol | `services/repository/models/orm` 全套战术分层 |
| 无状态纯辅助 | 业务不变量（语义资产来源、SQL 权限、问题理解门） |
| 同步优先；并发原语可提供 | 依赖任何业务 apps |
| 单测覆盖契约 | 为“抽象而抽象”的多层 port |

**结论：内部弱仪式完全可以，也更符合模块性质。**  
需要守住的不是 DDD 仪式，而是**依赖边界与职责边界**。

### 2.5 与现有架构规则的对齐

1. **数据所有权不变**：run/step/clarification/ChatRecord 仍归 chatbi；`apps/tool` 无持久化。
2. **跨域公开契约**：chatbi 领域工具继续直连 chatbi 公开 Service；tool runtime 不包一层 1:1 业务 port。
3. **依赖单向**：`chatbi → apps.tool`，禁止反向。
4. **Graph 不强制接入**：Graph 已有自己的 adapter 面；若未来要复用，只复用 runtime 信封/中间件，不复用问数工具清单。

---

## 3. 需要完善与修改的清单

按 ROI 分批。P0 可先在现位修，再随抽取迁入 `apps/tool`；也可与抽取同批完成。

### 3.1 P0 — 正确性与可用性（优先）

#### P0-1 未完成 tool_calls 统一收口

**问题：** 澄清挂起、失败、直接文本回答时，若本轮存在未配对的 `tool_calls`，消息历史可能非法（dangling tool_calls）。  
**借鉴：** AgentScope `_close_unfinished_tool_calls`。  
**改法：**

- 在 `AgentLoop` 的 suspend / fail / direct-answer / 异常路径前统一调用收口函数
- 为每个未产生 `ToolMessage` 的 `tool_call_id` 注入 synthetic 结果

```text
ToolMessage(
  content="skipped: run suspended/failed before this tool executed",
  tool_call_id=...
)
```

**落位：** 收口函数进 `apps/tool`（通用）；调用点在 `AgentLoop`。

#### P0-2 预算软着陆（soft wrap-up）

**问题：** 当前 `BudgetGuard` 以硬失败为主，接近上限时用户体验脆。  
**借鉴：** AgentScope `ReplyBudgetControlMiddleware`（hint + `tool_choice=none`）。  
**改法：**

```text
正常        → 全量工具
接近预算    → soft mode：只暴露 finish（可选 clarify），并注入收口提示
预算耗尽
  ├─ 已有 last_execution → 直接投影 finish，不再调 LLM
  └─ 无可用结果       → hard fail（诚实失败）
```

**落位：**

- 预算原语（阈值、snapshot、soft/hard 判定）→ `apps/tool` 或暂留 `budget.py` 再迁
- 策略落地（改 allowlist / 写答案）→ 仍在 `AgentLoop`

#### P0-3 `ToolOutput` 状态枚举

**问题：** `success: bool` 混杂参数错误、业务拒绝、执行失败、中断。  
**改法：**

```python
status: Literal["success", "error", "denied", "interrupted"]
success: bool  # 兼容属性：status == "success"
error_code: str | None
summary: str
payload: dict
offload_ref: str | None = None
```

**行为约定：**

| status | 语义 | 对预算/重试 |
| --- | --- | --- |
| success | 正常完成 | 不计失败 |
| denied | 业务/白名单拒绝（如未检索语义包） | 默认不计 SQL retry |
| error | 执行/系统错误 | 可计 retry / fuse |
| interrupted | 取消/挂起中断 | 走挂起或取消路径 |

---

### 3.2 P1 — 吞吐、上下文、横切

#### P1-1 工具元数据 + 有限并行

为工具增加：

```python
is_read_only: ClassVar[bool] = True
is_concurrency_safe: ClassVar[bool] = False
```

Loop / Registry 调度规则：

1. `clarify` / `finish` 等终止动作优先串行处理
2. `is_concurrency_safe=True` 的只读工具可同批并行（注意 session/事务边界）
3. 写 `ctx.state` 或副作用工具保持串行

建议默认标注：

| 工具 | read_only | concurrency_safe |
| --- | --- | --- |
| search_terminology | yes | yes |
| get_sql_examples | yes | yes |
| get_dataset_schema | yes | yes |
| validate_sql | yes | yes |
| search_semantic_assets | yes | no（写 state） |
| compile_semantic_sql | yes | no |
| execute_sql | no | no |
| clarify / finish | no | no |

#### P1-2 结构化结果 offload

现状：`summary` 截断 + 粗 fold 占位。  
目标：大结果归档可引用，而不是只丢字符串。

```text
summary     → 进 LLM 上下文（短）
payload     → 前端/步骤结果
offload_ref → 全量归档引用（artifact / derived store）
fold        → 用稳定 ref 替换旧 ToolMessage，而不是纯自然语言占位
```

优先覆盖：`execute_sql`、大语义包、`get_dataset_schema`。

#### P1-3 轻量 Tool Middleware

只做横切，不做业务门：

| Middleware | 作用 |
| --- | --- |
| Timeout | 单工具超时 |
| Tracing | latency / error_code 记录 |
| ErrorNormalize | 异常 → ToolOutput(status=error) |

**明确不放进 middleware 的内容：**

- understanding / clarification gate
- asset_id 白名单
- finish 必须有执行结果
- SQL 权限与只读校验

这些是领域不变量，继续内嵌领域工具或 Loop 前置门。

#### P1-4 动态 tool allowlist（轻量 ToolChoice）

不必完整实现 AgentScope `ToolChoice` 对象。提供：

```python
registry.tool_specs(allowed=["finish"])  # 或 exclude=...
```

用于：

- 预算 soft mode
- 首轮可选强制 `search_semantic_assets`（当问题理解已 valid）
- 测试夹具

---

### 3.3 P2 — 可选增强

| 项 | 说明 | 建议 |
| --- | --- | --- |
| 慢工具 progress 事件 | `tool-progress` SSE | 有体验收益，非正确性关键 |
| schema 动态扩展 | 按 dataset 能力增删参数 | 工具稳定前不做 |
| ToolGroup / Skill 渐进披露 | AgentScope 动态激活 | 九工具规模不需要 |
| 通用 PermissionEngine | allow/deny/ask | ChatBI 服务端受控链路不需要用户逐工具确认 |
| MCP 作为主工具源 | 外部工具扩展 | 仅扩展通道，不做主路径 |
| 完整 ToolChunk 流式协议 | 流式累积 | 现有 SSE 足够；先 offload |

---

## 4. 目标目录与职责

### 4.1 `apps/tool`（新，弱仪式）

```text
apps/tool/
  __init__.py                 # 仅导出公开运行时符号
  base.py                     # Tool 协议、Params 辅助、元数据字段
  output.py                   # ToolOutput / status 枚举
  registry.py                 # 白名单注册、参数校验、tool_specs(allowed)
  middleware.py               # Middleware Protocol + 组合执行
  concurrency.py              # 按 is_concurrency_safe 分批
  messages.py                 # close_unfinished_tool_calls 等消息工具
  fold.py                     # summary 截断 / 上下文 fold 辅助
  budget.py                   # 可选：通用预算原语（非 ChatBI 策略）
  errors.py                   # 运行时错误类型（无 HTTP 语义）
```

公开面建议只导出：

```text
Tool, ToolOutput, ToolStatus, ToolRegistry,
ToolMiddleware, close_unfinished_tool_calls, fold_tool_messages
```

### 4.2 `chatbi/orchestration/agent/tools`（保留，领域工具）

```text
apps/chatbi/orchestration/agent/tools/
  base_context.py             # AgentToolContext（业务上下文，不进 apps/tool）
  core.py                     # 领域工具实现
  interaction.py
  registry_factory.py         # build_chatbi_agent_tools() / 组装默认注册表
```

说明：

- 领域工具 **继承/实现** `apps.tool` 的 Tool 协议
- `AgentToolContext` 含 session、dataset、chatbi services，属于 chatbi，不放进 `apps/tool`
- `AgentLoop` 继续负责：问题理解 preflight、澄清状态机、SSE、run 持久化

### 4.3 依赖规则（必须写进架构守卫）

```text
apps.tool            → 不得 import apps.chatbi / semantic / knowledge / datasource ...
apps.chatbi          → 可 import apps.tool
apps.chatbi.tools    → 只调用 chatbi 公开 services / 已注入 port
apps.tool            → 无 ORM、无 API、无业务表
```

建议在 `tests/architecture/test_structure_rules.py` 增加表驱动规则，而不是新建守卫文件。

---

## 5. 迁移步骤（建议）

### 阶段 A：现位补正确性（可不挪目录）

1. 落地 P0-1 dangling tool_calls 收口  
2. 落地 P0-2 预算软着陆  
3. 落地 P0-3 ToolOutput status  

验收：澄清挂起恢复、失败路径、直接文本回答后的消息历史均可被下一轮 LLM 合法消费；预算将尽时优先收口而非裸失败。

### 阶段 B：抽取 `apps/tool` runtime

1. 新建 `apps/tool`，迁入 base/output/registry/middleware/messages/fold  
2. `chatbi` 领域工具改为依赖 `apps.tool`  
3. 删除 chatbi 内重复运行时实现  
4. 加依赖守卫：tool 不依赖业务域  

验收：行为无变化；全量 agent 测试通过；架构守卫绿。

### 阶段 C：P1 增强

1. 元数据 + 只读工具有限并行  
2. offload_ref + fold 引用化  
3. timeout/tracing middleware  

验收：并行不破坏 state 一致性；大结果不再撑爆上下文；工具耗时可观测。

### 阶段 D（可选）

- progress 事件  
- allowlist 首步策略  
- 若未来多产品复用，再评估 `apps/tool → platform/tool`

---

## 6. 明确不做什么

1. **不把 ChatBI 九工具搬进 `apps/tool`**  
2. **不在 `apps/tool` 内重建 DDD 分层**  
3. **不引入 AgentScope 作为运行时依赖**（只借鉴设计）  
4. **不把 Graph adapter 与 Agent tool 强行合并成一个“万能工具层”**  
5. **不把业务硬门改成可配置 middleware 开关**（防幻觉门必须硬编码）  
6. **不为未来可能的 Skill/MCP 生态提前设计复杂分组系统**

---

## 7. 设计不变式（抽取后仍必须成立）

1. LLM 只提名工具与参数，执行权在白名单 Registry。  
2. 工具厚、循环薄；领域逻辑在 `chatbi.services` / `semantic` 等，不在 tool runtime。  
3. 语义资产来源约束、问题理解门、finish 执行门继续生效。  
4. `summary` 进模型上下文，`payload`/artifact 进存储与前端。  
5. `clarify` / `finish` 仍是一等终止动作，由 AgentLoop 处理挂起与终态。  
6. `apps/tool` 无业务依赖、无持久化、无 API。

---

## 8. 决策记录

| 议题 | 决策 | 理由 |
| --- | --- | --- |
| 是否抽取 `apps/tool` | **是** | 运行时横切将膨胀；需要稳定技术边界 |
| 是否强 DDD | **否** | 技术模块，弱仪式足够 |
| 领域工具放哪 | **仍在 chatbi** | 避免通用层业务化 |
| 是否照搬 AgentScope | **否** | 只借鉴收口/软着陆/并发/offload/middleware |
| 是否先完善后抽取 | **可** | P0 可现位修；与抽取同批亦可，但不要只搬家不补正确性 |

---

## 9. 建议落地顺序（一句话）

> 先补 P0 正确性与软着陆，再抽 `apps/tool` 运行时内核，最后做并发与 offload；领域工具始终留在 chatbi，runtime 保持无业务依赖。

---

## 10. 实施记录（2026-07-24）

已完成：

1. 新建 `backend/apps/tool/`：`base` / `output` / `registry` / `budget` / `messages` / `middleware`
2. ChatBI 领域工具改继承 `apps.tool.Tool`；`AgentToolContext` 仍留在 chatbi
3. `AgentLoop` 接入：
   - `close_unfinished_tool_calls`（fail / finish / clarify / direct answer）
   - 预算 soft mode（allowlist + 收口提示）与有执行结果时的 soft wrap-up
   - `ToolOutput.status`（denied 不计 SQL retry）
4. 工具元数据：`is_read_only` / `is_concurrency_safe` 已标注（并行调度尚未启用）
5. 架构守卫：`tool-runtime-no-business-imports`
6. 兼容转发登记：`COMPAT_LEDGER` E9
7. 测试：`tests/tool` + `tests/agent` + structure rules 通过

P1 续（2026-07-24 同日）：

8. `apps/tool/concurrency.py`：`batch_tool_calls` + `execute_tool_batch`（保持调用顺序）
9. `AgentLoop` 按批次执行；`AgentConfig.tool_parallel_workers` / `tool_timeout_seconds`
10. 共享 Session 的 ChatBI 工具默认 `is_concurrency_safe=False`（避免 SQLAlchemy 竞态）；纯工具可并行
11. `maybe_offload_output` + fold 保留 `offload_ref`；`tool_offloads` 不进 derived_state 持久化
12. Registry 默认挂载 ErrorNormalize / Timeout / Tracing middleware
13. 新增 `tests/tool/test_tool_runtime_p1.py`；全量 agent+tool 回归通过

E9 清偿（2026-07-24）：

14. 删除 `chatbi/.../budget.py` 与 `tools/registry.py` 转发
15. `tools/base.py` 仅保留 `AgentTool` / `AgentToolContext`
16. 测试与 Loop/领域工具直连 `apps.tool`
17. `COMPAT_LEDGER` E9 → 已清

待续：

- 若未来工具支持 session-per-call，可再放开 ChatBI 只读工具并行
