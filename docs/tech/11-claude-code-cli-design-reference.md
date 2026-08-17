# Claude Code CLI 工具与智能体设计参考 —— 对 ChatBI Agent 的对比与启发

> 研究对象：`extra/cli/restored-src/src/` —— 从 npm 包 source map 还原的 **Claude Code CLI v2.1.88** 完整 TypeScript 源码（约 1884 个源文件，非官方还原，仅供研究）。
>
> 对比基准：`backend/apps/agent/` —— 本项目的 **LLM 驱动问数智能体**（`AgentLoop`）。
>
> 说明：仓库中还有一个更早的 `backend/apps/agentic_chat/`（规则状态机 Planner-Executor，LLM 只在工具内部使用），属于上一代方案，不是本文对比主体，仅在附录说明其定位。

---

## 1. 为什么值得对比

Claude Code 是目前工程化程度最高的 LLM Agent 产品之一。它与 `agent` 属于**同一范式**：

- LLM 拥有决策权：选择工具、组织参数、决定顺序、决定何时结束；
- 工具经白名单受控执行，LLM 只能"提名"，不能越权；
- 消息历史即状态，失败信息回灌给模型让它自我纠正；
- 硬预算护栏兜底（步数 / token / 超时）。

两者的差异不在范式，而在**成熟度细节**：并发调度、上下文管理、失败恢复、子智能体分工、渐进披露。这些细节正是 Claude Code 在海量真实使用中打磨出来的，对我们最有参考价值。

---

## 2. Claude Code 核心架构速览

### 2.1 主循环（query loop）

位置：`src/query.ts`（约 68KB）、`src/QueryEngine.ts`。

一个"turn"的流程：组装消息（system prompt + 历史 + 附件注入）→ 调模型（流式）→ 收集 `tool_use` 块 → 权限检查 → 分发执行工具 → 结果以 `tool_result` 回灌 → 下一轮；模型不再产生 `tool_use` 即自然结束。`maxTurns` 与 token 预算做硬上限。

关键细节：

- **失败也是信息**：工具报错不终止循环，错误文本作为 `tool_result` 喂回模型，模型自行改参数重试或换路。
- **悬空调用过滤**：恢复/派生会话时，`filterIncompleteToolCalls()`（`tools/AgentTool/runAgent.ts:866-904`）剔除"有 `tool_use` 但没有对应 `tool_result`"的消息，防止 API 直接拒绝请求。
- **上下文管理分层**：microcompact（按 `tool_use_id` 折叠旧工具结果）→ auto-compact（接近窗口上限时用 LLM 生成摘要重建上下文）→ 大结果落盘只留预览。

### 2.2 工具系统（Tool 接口）

位置：`src/Tool.ts`（`Tool<>` 接口 + `buildTool()`）。

每个工具除了 `name / description / inputSchema / call()` 之外，还声明一组**自描述元数据**，供调度层与权限层使用：

| 元数据 | 用途 |
|---|---|
| `isReadOnly(input)` | 是否只读（决定权限与并发策略） |
| `isConcurrencySafe(input)` | 是否可与其他工具并发 |
| `isDestructive(input)` | 是否不可逆（删除/覆盖/外发），触发确认 |
| `validateInput(input, ctx)` | 参数校验，失败信息喂回模型 |
| `checkPermissions(input, ctx)` | 工具粒度的授权决策 |
| `maxResultSizeChars` | 结果超限落盘，上下文只留预览+路径 |
| `searchHint` / `shouldDefer` | 延迟加载：工具多时只发清单，命中才展开完整 schema |

`buildTool()`（`Tool.ts:757-793`）为省略的方法填默认值，且默认值 **fail-closed**：`isConcurrencySafe→false`、`isReadOnly→false`、`isDestructive→false`——"不确定就当危险的"。

### 2.3 并发调度

位置：`src/services/tools/toolOrchestration.ts`。

模型一次返回多个 `tool_use` 时，调度器把它们**按连续并发安全段分批**：

```
[Read, Grep, Glob, Edit, Read, Read]
→ [Read, Grep, Glob] 并发（上限 10，CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY）
→ [Edit] 串行
→ [Read, Read] 并发
```

`isConcurrencySafe` 抛异常时按"不安全"处理（`toolOrchestration.ts:99-108`），依旧 fail-closed。系统提示词同时引导模型"独立操作放在同一条消息里并行发起"。

### 2.4 工具描述（prompt）的写法

以 `tools/BashTool/prompt.ts` 为范本，可总结为一套风格：

- **祈使句 + 密集示例**：给出 HEREDOC 提交模板、并行调用示例等可直接模仿的样例；
- **负面清单**（When NOT to use）：明确"读文件别用 cat、找文件别用 find，用专用工具"；
- **危险操作内联分级强调**：`IMPORTANT:` / `NEVER` / `CRITICAL:`，安全协议写进描述而非只靠运行时拦截；
- **引导批量/后台**："独立命令并行发、长任务用 run_in_background、不要 sleep 轮询"。

即约束尽量**前置进 prompt**，运行时校验只是第二道防线。

### 2.5 子智能体系统（AgentTool）

位置：`tools/AgentTool/`（`runAgent.ts`、`prompt.ts`、`built-in/`、`loadAgentsDir.ts`）。

- **Agent 定义即配置**：`agentType / whenToUse / tools / disallowedTools / model / effort / permissionMode / maxTurns / skills / mcpServers / memory`（`loadAgentsDir.ts:73-98`）。内置 `Explore`（快速只读搜索，外部用户走 haiku 小模型）、`Plan`、`general-purpose` 等，用户可用 markdown frontmatter 自定义。
- **隔离与回流**：子 agent 有独立消息历史、独立工具池、独立权限模式与模型；最终一条消息回流父级；可选 git worktree 隔离文件系统。
- **只读双重锁**：`Explore` 既在 system prompt 声明 READ-ONLY 禁令，又用 `disallowedTools` 物理移除写工具（`built-in/exploreAgent.ts:26-36, 67-73`）——prompt 约束 + 能力剥夺双保险。
- **给子 agent 写任务书的准则**（`AgentTool/prompt.ts:99-113`）："像给刚进门的聪明同事交代任务"——说明目标与原因、已排除的方向、给足做判断的上下文；并明确 **Never delegate understanding**（不要把综合推理甩给下游）。
- **省 token 的取舍**：只读子 agent 不注入 CLAUDE.md 与 git 状态（`runAgent.ts:387-410`），fleet 级每周省数 G token——上下文注入按角色裁剪。

### 2.6 支撑系统

- **权限**：模式（default / plan / acceptEdits / bypass）× 规则（`Bash(git *)` 模式匹配的 allow/deny/ask 三表）× 工具自身 `checkPermissions`，任何一层可否决。
- **技能（Skills）与渐进披露**：技能清单只占上下文 **1%** 预算、每条描述封顶 **250 字符**（`tools/SkillTool/prompt.ts:20-31`），完整内容在调用时才加载。
- **Plan mode**：先只读探索并产出计划，用户批准后才动手——"探索与行动分离"的制度化。
- **Todo/任务系统**：`TodoWrite` 等结构化任务清单，帮模型在长任务里保持进度与依赖。

---

## 3. agent 架构速览

位置：`backend/apps/agent/`。

```
api.py            # /chat/agent/question、/clarification 入口，SSE 流式
loop.py           # AgentLoop：LLM 自主规划 + 受控工具循环
budget.py         # BudgetGuard：步数/token/重复熔断/SQL 重试/超时/澄清次数
prompts.py        # system prompt：标准路径 / 兜底路径 / 澄清规则 / 硬性规则
tools/base.py     # AgentTool 基类：pydantic args_model + execute；ToolOutput = summary + payload
tools/registry.py # 白名单注册表；未注册/参数非法 → 错误 ToolMessage 回灌
tools/core.py     # 六个 P0 工具（语义检索/看表结构/语义编译/校验/执行/finish）
tools/interaction.py # clarify（挂起）/ search_terminology / get_sql_examples
models.py         # ChatbiAgentRun（messages/budget_snapshot/derived_state）、Step、Trace、Clarification
```

核心设计（`loop.py` 模块注释的原话）：**"LLM 拥有：选择工具、组织参数、决定顺序、决定何时澄清与结束。LLM 没有：越出白名单、绕过守护、超出预算。状态即消息历史。"**

流程：前置 `QuestionUnderstandingService`（问题重写 + 意图识别 + 确定性校验，产出"已确认的问题理解"作为唯一意图事实源）→ 进入工具循环（bind_tools function calling）→ 标准路径 `search_semantic_assets → compile_semantic_sql → execute_sql → finish`，兜底路径 `get_dataset_schema → 手写 SQL → validate_sql → execute_sql`；歧义影响 SQL 正确性时 `clarify` 挂起，用户回答以 `ToolMessage` 回填后恢复。

---

## 4. 逐维度对比

| 维度 | Claude Code CLI | agent | 差距评估 |
|---|---|---|---|
| 决策范式 | LLM 驱动循环 | LLM 驱动循环（前置确定性理解） | ✅ 同范式；前置理解是合理的领域化增强 |
| 工具契约 | `Tool<>` 接口 + 丰富元数据 + fail-closed 默认值 | `name/description/args_model/execute` | ⚠️ 缺 `read_only/concurrency_safe/destructive` 声明 |
| 工具白名单与参数校验 | inputSchema + validateInput，错误回灌 | registry 白名单 + pydantic 校验，错误回灌 | ✅ 已对齐 |
| 多工具并发 | 连续并发安全段批量并发（上限 10） | `for tool_call in tool_calls` 串行；prompt 要求"一次只做一个动作" | ⚠️ 独立检索无法并行 |
| 结果体积控制 | maxResultSizeChars 落盘 + 预览 | summary（≤4000 字）进上下文 / payload 落库 双通道 | ✅ 已对齐（形式不同，思想相同） |
| 上下文压缩 | microcompact（按 id 折叠）→ auto-compact（LLM 摘要） | `_fold_messages`：超 30k 字符把旧 ToolMessage 换成占位符 | ⚠️ 占位符全量丢信息，只能靠重新调工具找回 |
| 失败处理 | 错误回灌 + API 重试 + 悬空调用过滤 | 错误回灌 + SQL 重试计数；**模型调用无重试**；**挂起时可能留悬空 tool_call** | ⚠️ 两个正确性/可用性缺口 |
| 预算护栏 | maxTurns + token 预算 + 上下文低水位提醒 | BudgetGuard 六维护栏 + 快照恢复，**比 CLI 更细**（重复熔断/SQL 重试/澄清上限） | ✅ 我们更强；但**耗尽即硬失败**，无优雅收敛 |
| 人机交互 | AskUserQuestion / 权限确认 / plan mode 审批 | clarify 挂起-恢复（结构化选项） | ✅ 已对齐；可推广为通用"确认-挂起" |
| 反幻觉护栏 | 权限系统 + prompt 约束 | `_execution_gate`、asset_id 必须来自语义包、time_range.normalized 强制、finish 必须有真实执行 | ✅ 领域化护栏比 CLI 更严，是亮点 |
| 子智能体 | AgentTool 完整体系（隔离/分模型/只读双锁） | 无 | ⭕ 远期可选，非当前瓶颈 |
| 渐进披露 | 技能清单 1% 预算、工具延迟加载 | 9 个工具全量注入（当前规模足够小） | ⭕ 工具增多后再考虑 |
| 流式输出 | 模型 token 级流式 | `invoke` 阻塞，整段 thinking 事件 | ⚠️ 体验差距 |

---

## 5. 已经做对的（保持并坚持）

1. **summary / payload 双通道**（`tools/base.py`）：回给 LLM 的摘要受字数上限约束，完整结果落库给前端。与 CLI 的"大结果落盘 + 预览"同一思想，且更贴合 BI 场景（前端要全量数据画表）。
2. **错误即教学**：`registry.execute` 对未注册工具回"不在白名单内，可用工具: …"；`compile_semantic_sql` 对编造 asset_id 回"以下 asset_id 未出现在语义包中，禁止编造口径，请只使用检索结果里的资产"。错误信息**告诉模型下一步怎么做**，这正是 CLI 工具错误设计的精髓。
3. **BudgetGuard 六维护栏 + 快照恢复**（`budget.py`）：重复调用熔断（同名同参 3 次）、SQL 重试上限、澄清次数上限，比 CLI 的通用护栏更贴业务。
4. **领域化反幻觉门**（`tools/core.py`）：`_execution_gate`（理解未确认禁止进 SQL 阶段）、语义包资产闭包校验、`time_range.normalized` 原样透传强制、`finish` 无真实执行则拒绝。CLI 的 fail-closed 哲学在这里被领域化得更彻底。
5. **`search_semantic_assets` 零参数、从 state 读意图**：不给模型重写检索意图的机会，防止意图漂移。这是 CLI 没有的领域创新，值得保留。
6. **prompt 结构**（`prompts.py`）：标准路径 / 兜底路径 / 澄清规则 / 硬性规则的分节 + "能编译就绝不手写 SQL"的工具优先级引导，与 BashTool prompt 的"专用工具优先"引导风格一致。
7. **消息即状态 + 挂起恢复**：`run.messages` 持久化、澄清答案以 `ToolMessage` 回填、`derived_state` 保住语义包/白名单表避免恢复后重检索——与 CLI 的会话恢复思想一致。

---

## 6. 差距与改进建议（按优先级）

### P0-1 挂起/恢复的悬空 tool_call（正确性）

**问题**：`loop.py:314` 逐个执行 `tool_calls`；若模型一次返回多个调用且 `clarify` 不在末位，`_suspend_for_clarification` 直接 `return`，**clarify 之后的调用永远没有 ToolMessage**。恢复时消息历史里存在"有 tool_call 无 tool_result"的 AIMessage，多数模型服务会直接拒绝该请求（400）。当前靠 prompt"一次只做一个动作"压制概率，但这不是保证。

**CLI 对策**：`filterIncompleteToolCalls()`（`runAgent.ts:866-904`）在派生/恢复会话前过滤悬空调用。

**建议**（二选一，成本都很低）：
- 挂起时为剩余未执行的调用合成 ToolMessage（"本轮已挂起等待澄清，此调用未执行，请在恢复后按需重发"）；
- 或在 `resume` 反序列化后做一次 `filterIncompleteToolCalls` 等价过滤。

### P0-2 模型调用无重试（可用性）

**问题**：`DefaultAgentModelClient.invoke`（`loop.py:57`）一次瞬态网络/限流错误就会让整个 run 进入 FAILED（被最外层 `except Exception` 收敛）。

**CLI 对策**：模型 API 调用带指数退避重试，区分可重试错误（429/5xx/超时）与不可重试错误。

**建议**：在 `model_client.invoke` 外加 2~3 次指数退避重试（仅限可重试错误类），重试的 token 用量照常记入 BudgetGuard。

### P1-3 预算耗尽的优雅收敛（体验）

**问题**：`check_before_step` 不通过 → `_fail`，用户拿到的是一条错误，此前所有检索/执行成果全部作废。

**CLI 思想**：接近上限时给模型注入提醒（上下文低水位警告），让它主动收敛；耗尽也倾向"带着已有成果结束"而非报错。

**建议**：
- 步数/token 接近上限（如 80%）时，在下一轮消息前注入一条提示："预算即将耗尽，请尽快基于已有信息收敛作答"；
- 真正耗尽时，若 `ctx.state.last_execution` 已存在，给模型**最后一轮无工具（或仅 finish）调用**做尽力回答，而不是直接 FAILED。

### P1-4 独立检索并发（性能）

**问题**：`search_terminology`、`get_sql_examples`、`get_dataset_schema` 是相互独立的只读检索，但循环串行执行，且 prompt 明确"一次只做一个动作"，模型连并行请求的机会都没有。

**CLI 对策**：工具声明 `isConcurrencySafe`，调度器把连续安全段并发（上限 10）；prompt 反向鼓励"独立操作在一条消息里并行发起"。

**建议**：
1. `AgentTool` 加 `concurrency_safe: ClassVar[bool] = False`（fail-closed，与 CLI 一致）；只读检索工具置 True；
2. `_loop` 对连续 `concurrency_safe` 的 tool_calls 用线程池并发执行（保持结果按调用顺序回填 ToolMessage）；
3. prompt 从"一次只做一个动作"放宽为"**写操作/执行类一次一个；相互独立的检索类可以在同一轮并行发起**"。

注意与 P0-1 联动：并发化会提高"一轮多个调用"的频率，必须先修悬空 tool_call。

### P1-5 折叠占位符 → 折叠摘要（质量）

**问题**：`_fold_messages` 把旧 ToolMessage 内容整体替换为 `FOLDED_PLACEHOLDER`，模型丢失全部信息，只能靠"重新调用工具"找回（费步数、费 token）。

**CLI 对策**：microcompact 折叠时保留摘要；auto-compact 用 LLM 生成结构化摘要重建上下文，关键事实不丢。

**建议**：折叠时保留一行结构化摘要而非纯占位符，如 `（已折叠：search_semantic_assets 返回 5 指标/8 维度，asset_ids=[…]；详情可重新调用）`。`_result_summary()` 已经会算每个工具的短摘要，可直接复用。

### P2-6 直接文本回答路径的护栏（正确性评估）

**问题**：`_loop` 中模型不带 tool_calls 直接输出文本时走"宽松 finish"（`loop.py:301-312`），**绕过了 `finish` 工具的 `execution_required_before_finish` 门**——理论上模型可以不执行任何 SQL 就"回答"数字。

**建议**：评估收紧——若 `ctx.state` 无 `last_execution` 且问题理解为数据查询类，对首次直接回答注入一条纠偏消息（"请通过工具完成查询后再用 finish 作答；若无法完成请如实说明"），第二次才放行（防死循环）。闲聊/解释类问题保留宽松路径。

### P2-7 工具元数据声明化（为写操作铺路）

参照 `Tool.ts`，给 `AgentTool` 增加 `read_only / destructive` 声明。当前 9 个工具全是只读，价值在**未来**：一旦出现回写类工具（保存看板、写批注、数据导出），`destructive=True` 应自动触发"确认-挂起"——把现有 clarify 挂起机制推广为通用的用户确认关卡，等价于 CLI 的权限确认。

### P2-8 流式输出（体验）

`model_client.invoke` 阻塞到整段生成完才发 `thinking` 事件。LangChain 支持 `.stream()`；将 thinking 增量推送到 SSE 可显著改善长回答的等待体验。工具调用参数的流式解析可以不做（复杂度高、收益低）。

### P2-9 子智能体（远期）

当前 12 步/单一问数场景不需要多 agent。两个未来触发点：
- **宽 schema 数据源**：几百张表时 `get_dataset_schema` 的摘要会挤爆上下文——可引入"schema 探索子 agent"（只读、独立上下文、只回传相关表的紧凑摘要），完全复制 CLI Explore 的模式：小模型 + 只读双重锁（prompt 禁令 + 工具白名单剥夺）+ 不注入无关上下文；
- **复合问题分解**：一问多查（"对比 A 和 B 两个数据源的销售额"）时父 agent 分解、子 agent 各自执行。

届时直接参考 `runAgent.ts` 的隔离模型与 `AgentTool/prompt.ts` 的任务书准则（给足上下文、Never delegate understanding）。

### P2-10 工具描述加少量 few-shot 示例

CLI 的工具描述大量使用 `<example>` 块。我们最易出错的 `compile_semantic_sql`（filters/time_bucket 结构较复杂）可以在 description 里加一个最小示例调用，降低参数格式错误率。`args_summary`/报错回灌已能兜底，此项是锦上添花。

---

## 7. 不建议照搬的部分

- **完整权限系统（模式×规则×确认 UI）**：CLI 面向"任意 shell 命令"的开放世界，我们的工具面是封闭的 9 个只读工具，现有白名单 + 领域门足够。等写操作出现再做 P2-7 的最小版本。
- **技能系统 / 工具延迟加载**：为几十上百个工具设计的机制，9 个工具的 schema 开销可以忽略。
- **多 agent 协作（teammates/coordinator/workflow）**：CLI 为通用编程任务设计的重型机制，问数场景的任务粒度用单循环 + 未来少量子 agent 即可。
- **microcompact/auto-compact 全套**：我们的 run 生命周期短（≤12 步、30k 折叠阈值），`_fold_messages` 升级为"折叠摘要"（P1-5）就够，不需要 LLM 摘要重建。

---

## 8. 建议落地顺序

| 优先级 | 事项 | 改动面 | 性质 |
|---|---|---|---|
| P0 | 悬空 tool_call 防护（挂起合成/恢复过滤） | loop.py ~20 行 | 正确性 |
| P0 | 模型调用重试 | loop.py ~15 行 | 可用性 |
| P1 | 预算耗尽优雅收敛 + 低水位提醒 | loop.py/budget.py ~40 行 | 体验 |
| P1 | 只读检索并发 + prompt 放宽 | base/registry/loop/prompts ~60 行 | 性能 |
| P1 | 折叠占位符 → 折叠摘要 | loop.py ~15 行 | 质量 |
| P2 | 直接回答护栏、工具元数据、流式、few-shot、子 agent | 按需 | 演进 |

---

## 附录 A：CLI 关键源码索引（extra/cli/restored-src/src/）

| 文件 | 内容 |
|---|---|
| `Tool.ts` | Tool<> 接口全貌、buildTool fail-closed 默认值 |
| `services/tools/toolOrchestration.ts` | 并发调度器（分批/并发上限/fail-closed） |
| `query.ts`、`QueryEngine.ts` | 主循环、maxTurns、流式、上下文管理 |
| `tools/AgentTool/runAgent.ts` | 子 agent 生命周期、上下文裁剪、filterIncompleteToolCalls |
| `tools/AgentTool/prompt.ts` | 子 agent 任务书准则（smart colleague / Never delegate understanding） |
| `tools/AgentTool/built-in/exploreAgent.ts` | 只读 agent 双重锁范本 |
| `tools/AgentTool/loadAgentsDir.ts` | agent 定义 schema（tools/model/effort/permissionMode/…） |
| `tools/BashTool/prompt.ts` | 工具描述写作范本（负面清单/示例/分级强调） |
| `tools/SkillTool/prompt.ts` | 渐进披露预算（1% 上下文 / 250 字符每条） |
| `Task.ts`、`tasks/` | 后台任务基座（bash/agent/workflow 统一生命周期） |

## 附录 B：`agentic_chat`（上一代）的定位

`backend/apps/agentic_chat/` 是规则状态机方案：`RuleBasedPlanner` 用 `if/elif` 固定管线（理解→取证→路由→生成→校验→权限→执行→回答），LLM 只在工具内部使用。它与 `agent` 的关系：

- 工具实现已下沉到共享能力层 `apps/capabilities/`（validator/executor/permission/repair），被两代共用；
- 其 `strategies/recovery.py`、`strategies/sql_repair.py` 未接入自身循环（死代码）；`agent` 通过"错误回灌 + BudgetGuard SQL 重试计数"以 LLM 驱动方式实现了等价能力；
- 若 `agent` 全量替代确认后，`agentic_chat` 可按迁移计划退役。
