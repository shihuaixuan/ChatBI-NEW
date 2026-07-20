# SQLBot 后端架构迁移计划（现行版）

> 状态：进行中（R0、R1 已完成，当前推进 R2）
> 更新：2026-07-20
> 定位：**边界清晰的模块化单体**。DDD 战略半边（限界上下文、数据所有权、公开契约、依赖方向）全局保留；战术模式按子域分级使用（见 `apps/AGENTS.md` v2）。
> 本文是唯一现行计划。历史批次日志（含 P0–P5 前 43 批全文）见 `DDD_MIGRATION_CHANGELOG.md`；兼容入口台账见 `COMPAT_LEDGER.md`；评审依据见 `docs/tech/12/13/14`。

## 1. 目标与全局不变量

迁移目标：每类业务数据一个权威归属；每项业务能力一个权威实现；跨域协作只经公开契约；ChatBI 成为唯一对外问数领域；Workflow Engine 恢复为无业务依赖的通用平台；旧目录、旧入口、兼容层全部按台账清偿。

全局不变量（适用所有模块，由架构测试守卫）：

1. **数据所有权唯一**：一类数据只有一个领域可写。
2. **跨域只走公开契约**：允许直接调用对方公开 Service 与公开 DTO；禁止导入对方 ORM、具体仓储、内部模块，禁止共享数据库 Session。
3. **依赖方向单向**：禁止循环依赖，禁止函数内导入掩盖循环。
4. **基线棘轮**：`tests/architecture/known_dependency_violations.json` 只减不增；历史违规清理后同批删除基线条目。

## 2. 领域地图与风格分级

| 模块 | 定位 | 架构风格（仪式上限） | 状态 |
| --- | --- | --- | --- |
| `semantic` | 核心领域：语义资产事实源 | 完整战术分层 | 稳定（P1 完成） |
| `chatbi` | 核心：应用编排层 + 管道核心 | 薄 Service + 函数管道；端口仅限可替换缝 | R1–R4 重组中 |
| `datasource` | 物理连接与元数据边界 | 六边形（公开 Service + 驱动适配器） | 稳定（P3 完成） |
| `knowledge` | SQL 示例/推荐问题资源 | 薄 Service + 仓储 | 稳定（P4 完成） |
| `retrieval` | 派生索引管道 | 管道分段（sources/projection/indexing/query） | R6 重组结构 |
| `access_control` | 身份、授权、数据策略 | 完整战术分层 | 稳定（P2 完成） |
| `ai_model` | 模型配置与运行时客户端 | 薄 Service + 仓储 | 稳定（P2 完成） |
| `assistant` | 助手发布配置 | 薄 Service + 仓储 | 稳定（P2 完成） |
| `dashboard` | 仪表板展示 | 薄 Service + 仓储 | R6 分层 |
| `workflow_engine` | 通用运行平台 | 自有分层，禁止业务依赖 | R5 隔离 |
| `chat` / `agent` / `workflow` / `capabilities` / `template` | ChatBI 的旧入口/执行器/兼容层 | — | R3/R4 迁入 chatbi 或删除 |
| `system` / `settings` / `swagger` / `terminology` / `data_training` / `mcp` | 残留与外部接口 | — | R4/R6 归位或删除 |

## 3. 迁移原则（保留自 v1，压缩）

1. **小步迁移**：每批可独立合并、独立回滚；代码、调用方、测试、旧实现清理同批完成。
2. **唯一实现**：同一能力只有一个权威实现；Graph、Agent、MCP、Web 只调用不复制。
3. **形状先于搬迁**：先定义目标结构与命名（见 AGENTS.md v2），再迁移实现；不再按旧函数边界产出新模块。
4. **表名与代码分离**：`headless_*`、`sys_*`、`core_*` 表名调整独立评审（R6），不与代码移动同批。
5. **事务边界**：单域写由该域仓储控制事务；跨域流程不共享 Session；跨域一致性优先"提交后事件"，否则顺序调用 + 可重试。
6. **错误处理**：领域错误不含 HTTP 状态码；未知异常直接失败；禁止宽泛捕获和静默回退。
7. **兼容入口**：只做转发；新增兼容必须登记 `COMPAT_LEDGER.md`（路径/调用方/删除条件/目标阶段），台账外禁止新增。

## 4. 已完成阶段（详情见 changelog）

| 阶段 | 内容 | 完成时间 |
| --- | --- | --- |
| P0 | 依赖基线与守卫建立（40+29 条历史违规登记，棘轮生效） | 2026-07-18 |
| P1 | Semantic 边界完成；术语三事实源合并，旧表 088 删除 | 2026-07-18 |
| P2 | Access Control / AI Model / Assistant 拆分，system 瘦身 | 2026-07-18 |
| P3 | Datasource 收敛，`apps/db` 解散，14 种驱动统一契约 | 2026-07-19 |
| P4 | Knowledge 建立，SQL 示例/推荐问题迁入，Retrieval 来源隔离 | 2026-07-19 |
| P5（前 43 批） | ChatBI 统一 Service 链路：检索/编译/校验/权限/执行/生成/投影收敛 | 2026-07-19 |
| R0 | 规则与文档先行：AGENTS.md v2、计划/日志分离、兼容台账、表驱动守卫 | 2026-07-19 |

P5 的剩余工作由 R1–R4 承接（下节），完成 R3 即视为 P5 验收；R5=原 P6，R6=原 P7+P8。

## 5. 剩余阶段计划

### R1：chatbi 内部重组（3–4 批，纯结构、不改行为）

| 批 | 范围 | 要点 |
| --- | --- | --- |
| R1-a ✅ | `understanding/` + `conversation/` 分包（2026-07-20 完成） | Question* 七服务按共享/Graph 专用归位（validation/intent_projection/input_projection/intent_validation 函数化）；`orm/conversation.py` 拆 3 文件；`errors.py` 建立并迁入会话/理解错误；`QuestionModelService → StructuredModelService` 改名（旧名别名入台账） |
| R1-b ✅ | `generation/`（含 `context/`）分包（2026-07-20 完成） | 7 个生成 Service 迁入；context 家族归位（scope/runtime_settings/history 函数化，custom_prompt 并入 knowledge.py，answer_projection/final_reply 函数化）；dynamic/permission 与主 SQL 模块合并、adapters 技术分组、SchemaContextService 改名 → 顺延至 R2/R1-d |
| R1-c ✅ | `planning/` + `execution/` 分包（2026-07-20 完成） | QueryService→GuardedQueryService、SemanticQueryService→SemanticCompilationService（旧名别名入台账）；`adapters/execution/DatasourceQueryExecutor` 替代 `capabilities.SqlExecuteTool`，ToolResult/SqlValidateTool/PermissionTool 迁入 chatbi，**chatbi→capabilities 依赖清零**；取消 4 组 1:1 跨域包装端口（检索/编译/策略/元数据直连公开 Service）；execution_binding 函数化 |
| R1-d ✅ | 组装与公共面收口（2026-07-20 完成，R1 收官） | 包根 builder 并入 `composition.py`；领域公共面 26 符号（惰性解析，B2 删除后转直接导入）；执行子域错误/端口收口；SchemaContextService 改名；守卫测试 24 文件 → 3（122 条规则合并进 `test_boundaries.py`，新规则只进 `test_structure_rules.py`）；**全量回归 1,221 项通过** |

R1 验收达成：services 顶层业务文件 0（6 子域包）；chatbi→capabilities 依赖 0；1:1 包装端口 0；守卫文件 3；剩余内联 Protocol（生成子域 6 组 PromptBuilder/ModelClient）与生成子域错误随 R2 重写收口。

### R2：统一流式生成骨架（1–2 批）

`models/dto/streaming.py`（ModelMessage/ModelStreamChunk/GenerationEvent[R]）+ `generation/streaming.py::run_generation()`；六个生成 Service 切换共享类型，旧 5 套四件套改别名入台账；六个 LangChain 适配器合并。
验收：手写流消费循环仅剩一处；SSE 契约测试全绿。

### R3：legacy 有界收口（3–4 批；完成即 P5 验收）

关账表（封闭清单，禁止表外追加"下一批继续"）：

| # | 剩余职责 | 目标 | 批 |
| --- | --- | --- | --- |
| 1 | 模型运行时装配（legacy_dependencies） | `ai_model` 公开运行能力 | R3-a |
| 2 | 数据源运行时/连接检测（legacy_dependencies） | Datasource 公开 Service 直调 | R3-a |
| 3 | 外部助手 Schema 读取（legacy_dependencies） | orchestration 边界显式 legacy 适配 | R3-a |
| 4 | `run_task` 主流程编排（llm.py:1066–1440） | 改写为子域 Service 顺序调用 | R3-b |
| 5 | 分析/预测/推荐任务编排（llm.py） | 同上 | R3-b |
| 6 | SSE 协议与错误格式（legacy_adapter + 投影） | `chatbi/api/legacy_sse.py` | R3-c |
| 7 | `validate_history_ds`/图片/语言工具 | 归入子域或 legacy_sse | R3-c |
| 8 | `api/chat.py` 路由与鉴权 | 迁 `chatbi/api/` | R3-c/R4 |
| 9 | `curd/chat.py`、`chat_model.py` 兼容转发 | 调用方核对后删除、台账销账 | R3-d |
| 10 | `services/deletion.py`、`semantic_binding.py` 及其 ORM 跨域依赖 | 迁 `conversation/`，走公开契约，基线销账 | R3-d |

验收：`apps/chat/task/` 与 `legacy_dependencies.py` 删除；SSE 协议集中单文件 ≤300 行；基线中 `apps/chat/*` 条目清零；原 P5 完成标准逐条通过。
风险控制：R3-b（run_task 374 行状态机）单独成批，改写前补终态/事件序列特征测试，旧函数保留一批作为可切换实现。

### R4：领域收拢与入口统一（3–4 批）

| 批 | 动作 |
| --- | --- |
| R4-a | 建 `chatbi/api/`（conversations/queries/interactions/legacy_sse），`apps/api.py` 收敛为单 router（旧路径不变）；删除 `apps/chat` |
| R4-b | `apps/agent → chatbi/orchestration/agent`（ORM 入 models/orm，crud 入仓储，api 并入） |
| R4-c | `apps/workflow → chatbi/orchestration/graph`；1,483 行 `adapters/question.py` 按节点拆分；runtime 对引擎 infrastructure 依赖改端口 |
| R4-d | 解散 `capabilities` 与 `template`（生成器入 `chatbi/adapters/prompts/`）；平台参数迁出 `system`；清偿 R1/R2 兼容 re-export |

验收：ChatBI 相关顶级目录仅剩 `chatbi`；问数入口路由唯一（兼容路径行为不变）；台账清偿率 ≥80%；xpack 导入与 OpenAPI 通过。

### R5：Workflow Engine 隔离（= 原 P6，2–3 批）

引擎 API 剩余 5 条业务依赖（图定义注册、ChatRecord 投影、Semantic ORM）改由 ChatBI 注册回调/装配注入；`chatbi/workflow_gateway.py` 删除；`workflow_engine_business_imports` 基线清零；引擎测试环境不 import 任何业务包；依赖清零后目录迁 `backend/platform/workflow_engine`。

### R6：外部接口与最终清理（= 原 P7+P8，3–5 批）

1. `retrieval` 按管道分段重组（sources/projection/indexing/query）。
2. `dashboard` 分层，解除 `chat.curd` 依赖（基线销账）。
3. `mcp → backend/interfaces/mcp`；`swagger`、`settings` 归位；`terminology`、`data_training` 壳目录随 xpack 确认删除。
4. 兼容台账清零（或每条挂明确外部阻塞原因）；`system` 删除。
5. `headless_*` 表名与历史品牌命名单独评审（独立迁移窗口）。
6. 终局验收：v1 计划 §8.5 全项（见 changelog 存档）+ chatbi 公共面 ≤40 符号 + `tests/architecture` 文件数 ≤4 + 依赖基线全项清零。

## 6. 度量看板（每阶段收尾更新）

| 指标 | 基线 2026-07-19 | R1 后（实际 2026-07-20） | R4 后 | R6 后 |
| --- | --- | --- | --- | --- |
| chatbi services 文件数 / Service 类数 | 37 / 37 | 0 顶层（6 子域包）/ ~21 | 同左 | 同左 |
| ChatBI 占用顶级目录数 | 5 | 5 | 1 | 1 |
| services 内联 Protocol 数 | 36 | ~17（R2 重写对象） | 0 | 0 |
| 跨域 1:1 包装端口 | ≥6 | 0 ✅ | 0 | 0 |
| 守卫测试文件数 | 24 | 3 ✅ | ≤4 | ≤4 |
| llm.py 行数 | 1,724 | 1,724 | 0 | 0 |
| 依赖基线余额（模型/实现/引擎/函数内） | 15/9/5/1 | 15/9/5/1（不增 ✅） | ≤10/≤5/5/0 | 0/0/0/0 |
| 兼容台账未销账条目 | 全量登记 | 33 条活跃 | ≤20% | 0 |
| 计划文档行数 | ≤400 | ≤400 ✅ | ≤400 | ≤400 |

## 7. 测试与验收

- **分层测试**：Service 用 Fake 仓储；Repository 测租户隔离/事务/级联；API 测契约与错误映射；纯规则单测；跨域只测公开契约；数据迁移测升级/重复执行/降级。
- **架构守卫**：依赖基线（`test_dependency_baseline.py`）+ 表驱动结构规则（`test_structure_rules.py`）两个入口；禁止按批次新增守卫文件。
- **回归分层**：结构批跑定向 + 架构 + 应用导入 + OpenAPI 核对（154 路径）；行为批跑全量（1,217+ 项）；每个 R 阶段收尾跑一次全量。
- **重点业务回归**：登录与工作空间、数据源全流程、Semantic 资产与索引、Graph/Agent 问数与澄清取消重试、SQL 白名单与行列权限、会话历史与删除级联、Dashboard、Assistant/MCP 流式。
- **数据库**：R1–R5 无数据结构变更；R6 涉及删表/改名时按 v1 §8.4 标准执行（见 changelog 存档）。

## 8. 风险与回滚

| 风险 | 控制 | 回滚 |
| --- | --- | --- |
| R1 大范围 import 变更 | 兼容 re-export 兜底 + 应用导入冒烟 + OpenAPI 核对 | 按批 revert，无数据变更 |
| R2 流式口径差异影响前端 | SSE 契约测试为准绳；分两小批 | revert |
| R3-b run_task 改写 | 特征测试先行；旧实现保留一批可切换 | 切回旧实现 |
| R4 目录迁移与 xpack 硬编码路径冲突 | 迁移前 grep xpack 引用清单；不可控者留最小 alias 入台账 | revert + alias |
| R5 引擎隔离影响 Run 恢复 | Run 表与事件协议不变；恢复与续传测试 | 恢复旧装配入口 |
| 兼容删除误伤外部调用 | 台账删除条件逐条核对；外部路由保留期加调用量日志 | 恢复转发路由 |

## 9. 治理

**批次检查清单**（合并前自查）：

```
[ ] 未新增无状态单方法 Service 类（投影/规则 → 函数）
[ ] 新端口满足准入判据（技术缝/防腐/仓储；跨域默认直连公开 Service），在 ports.py，后缀 ∈ {Repository, Gateway, Client, PromptBuilder}
[ ] 未超出所在子域风格分级仪式上限（AGENTS.md v2）
[ ] 新错误在 errors.py，错误码走常量注册
[ ] 新 DTO 后缀 ∈ {Input, Result, Ref, Snapshot, 裸名值对象}
[ ] 未在 composition/adapters 之外新增 build_*
[ ] 未新增守卫测试文件（规则进表驱动文件）
[ ] 兼容导出/别名已登记台账（含删除条件）
[ ] changelog 追加本批记录；本文剩余任务表同步勾销
```

**停止规则**：需要表外新增剩余工作（R3 关账表外的 legacy 职责、台账外的兼容层）时，先改本文再动代码；连续两批规则条款无法执行时，停下修 AGENTS.md，不带病推进。

**文档纪律**：本文 ≤400 行，只保留现行计划；实施细节一律进 changelog；评审三文档（docs/tech/12/13/14）为决策依据存档，不做日常推进文档。
