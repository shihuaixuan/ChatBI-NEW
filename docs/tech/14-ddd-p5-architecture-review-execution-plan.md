# DDD 迁移（P5 阶段）架构评审 · 三：执行计划

> 日期：2026-07-19
> 前置文档：`12-…-problems.md`（问题分析）、`13-…-target-design.md`（目标设计，本文按其章节引用）
> 性质：替代原计划中 P5 的"继续收缩"式推进，并与 P6–P8 衔接；不推翻 P0–P4 成果

## 0. 阶段总览与既有计划的对应关系

| 新阶段 | 内容 | 对应原计划 | 预估批次* | 前置 |
| --- | --- | --- | --- | --- |
| R0 | 规则与文档先行（AGENTS.md v2、计划/日志分离、台账、守卫整合设计） | 无（补欠账） | 1 | 无 |
| R1 | chatbi 内部重组（分包、函数化、errors、ports、组装、公共面） | P5 补强 | 3–4 | R0 |
| R2 | 统一流式生成骨架与共享 DTO | P5 补强 | 1–2 | R1 |
| R3 | legacy 有界收口（llm.py / legacy_dependencies 关账，P5 验收） | P5 收尾 | 3–4 | R1（R2 可并行） |
| R4 | 领域收拢与入口统一（chatbi/api、orchestration 迁入、解散 capabilities/template） | P5 任务 9 + P7 部分 | 3–4 | R3 |
| R5 ✅ | Workflow Engine 隔离 | P6 原样 | 已完成 | R4 |
| R6 ✅ | 外部接口与最终清理（mcp/dashboard/system/兼容台账清偿/命名） | P7 + P8 | 已完成 | R5 |

\* 一个批次 = 一次可独立合并、可独立回滚的变更（原计划同口径）。总量约 16–23 批，对比 P5 前 43 批的节奏，批次单位变大（见 §8 治理）。

关键顺序逻辑：**R0 必须最先**（否则后续批次继续按旧模式产出）；R1/R2 在 R3 之前（先有目标形状，legacy 才有"迁入的地方"，避免再产生一批过渡模块）；R4 在 R3 之后（入口统一前先让业务能力全部落位）；R5、R6 沿用原计划顺序。

## 1. R0：规则与文档先行（1 批）

### 动作

1. 修订 `backend/apps/AGENTS.md` → v2：**改制为分级规则**——全局边界四条（所有权唯一、跨域走公开契约、依赖单向、基线棘轮）+ 目标设计 §1.0 子域风格分级表（仪式上限）+ 各风格具体结构规则；再按目标设计 §8 要点逐条落文。后续阶段口径同步从"全项目 DDD"调整为"边界清晰的模块化单体"（阶段代号与既有成果不变）。
2. 拆分计划文档：
   - `DDD_MIGRATION_PLAN.md` 重写为 ≤400 行：目标、领域划分结论（§3 保留）、阶段表（本表）、每阶段剩余任务清单与验收标准；
   - 新建 `DDD_MIGRATION_CHANGELOG.md`，把现有 P0–P5 的批次日志（含 43 批 189 条）原样移入，此后 append-only。
3. 新建 `backend/COMPAT_LEDGER.md` 兼容台账：清点现存 71 处"兼容"引用，逐条登记（路径 / 调用方 / 删除条件 / 目标阶段 / owner）。允许首版粗粒度（按模块），后续批次细化。
4. 设计守卫测试整合：新建 `tests/architecture/test_structure_rules.py` 骨架（表驱动规则引擎），本批只迁 2–3 条规则验证机制可行，其余在 R1 各批迁移。
5. 在 v2 规则中写入批次结构检查清单（见 §8）。

### 验收

- AGENTS.md v2 合并，且包含子域风格分级表与端口准入判据；新计划文档 ≤400 行且含全部剩余任务；changelog 完整无丢失（行数核对）。
- 台账条目数 ≥ 现存纯转发模块数（8）+ 壳目录数（5），每条有删除条件。
- 全量测试通过（本批无代码行为变化，只动文档与 1 个新测试文件）。

### 风险

低。唯一风险是规则写得过细导致 R1 执行时反复改规则——对策：v2 中标注"试行"条款，R1 结束后固化。

## 2. R1：chatbi 内部重组（3–4 批）

按目标设计 §2/§3 执行。**纯结构性重组，不改任何行为与对外契约**。

### 批次切分建议

| 批 | 范围 | 要点 |
| --- | --- | --- |
| R1-a | `understanding/` + `conversation/` 分包 | Question* 七兄弟按 §3.2 归位（4 个服务函数化/合并）；orm/conversation.py 拆 3 文件；errors.py 建立并迁入会话/理解错误 |
| R1-b | `generation/`（含 context/）+ 5 套流式 DTO 位置迁移（暂不合并类型，R2 做） | Generation* 家族按 §3.3 归位（4 个函数化、custom_prompt 并入）；adapters 分组（langchain/prompts/embedding/xpack） |
| R1-c | `planning/` + `execution/` | QueryService→GuardedQueryService、semantic_query→SemanticCompilationService 改名；`adapters/execution/DatasourceQueryExecutor` 替代 `capabilities.SqlExecuteTool`（解除 A2 依赖）；execution_binding 函数化；按目标设计 §1.2 准入判据**取消 1:1 跨域包装端口**（语义检索/编译/权限策略/元数据改为直连对方公开 Service） |
| R1-d | 组装与公共面收口 | 包根 conversation.py / chat_record.py 并入 composition.py；services/`__init__` 改为兼容 re-export 层并入台账；chatbi/`__init__` 建立领域公共面；守卫测试全部迁入表驱动文件，删除逐批守卫文件 |

### 执行规范（每批适用）

- 迁移用 `git mv` 保持历史；旧模块路径保留 `from … import *` 兼容 re-export（登记台账，删除条件 = R4 结束）。
- 调用方（agent tools、workflow adapters、llm.py、workflow_gateway、各 composition）在同批切换到新路径；兼容 re-export 只为 xpack 与漏网调用兜底。
- 函数化的服务：类删除，函数保持原签名语义；对应测试同批改写（测函数而非类）；若被外部 import，兼容层保留同名类薄包装并登记台账。
- 每批跑：定向测试（chatbi + 调用方）+ 架构测试 + 应用导入 + OpenAPI 路径数核对（154）+ Ruff/Mypy。全量回归只在 R1-d 收尾跑一次。

### 验收

- `apps/chatbi/services/` 顶层无 .py 业务文件（仅子域包 + 兼容 `__init__`）；37 → ≤28 个模块、Service 类 ≤16。
- `grep -rn "class .*(Protocol)" apps/chatbi/services --include='*.py' | grep -v ports.py` 为空；端口后缀仅剩 4 种。
- 跨域 1:1 包装端口清零；保留端口均在 ports.py 且可归入技术缝/防腐/仓储三类之一。
- `apps/chatbi/errors.py` 存在，service 文件内无 `class …Error` 定义（兼容 re-export 除外）。
- chatbi 不再 import `apps.capabilities`。
- `tests/architecture/` 文件数 ≤ 4；规则条目数不少于原 24 个文件所含断言数（迁移清单核对）。
- 全量回归通过；依赖基线余额不增。

### 风险与回滚

- 最大风险是 import 面广（agent/workflow/chat 三方调用）。对策：兼容 re-export 兜底 + 每批合并前 `python -c "import main"` 冒烟 + OpenAPI 数量核对。
- 回滚单位 = 批（每批独立 PR）。结构重组不涉数据，revert 即可。

## 3. R2：统一流式生成骨架（1–2 批）

### 动作

1. 新建 `models/dto/streaming.py`（ModelMessage / ModelStreamChunk / GenerationEvent[R]，目标设计 §4.1）与 `generation/streaming.py` 的 `run_generation()`（§4.2）。
2. 六个生成 Service 逐个切换到共享类型与骨架；旧 5 套 `{X}Message/Chunk/Event` 变为共享类型的别名（登记台账，R4 删）；顺带消除 DynamicSQL 双重校验一类抄写噪声。
3. 六个 LangChain 适配器合并为 `adapters/langchain/generation.py` 的一个 ModelClient 实现 + 各能力 PromptBuilder。
4. 事件到 SSE / 工具载荷的投影处（llm.py、agent tools）类型引用同批更新。

### 验收

- `grep -rn "ModelChunk(" apps/chatbi/models/dto | grep -v streaming` 为空（别名除外）。
- 生成类 Service 中不再有手写"累计 content/reasoning/token"循环（唯一实现于 streaming.py）。
- SSE 契约不变：既有 `sql-result`/`chart-result`/`analysis-result` 等契约测试全绿；全量回归通过。

### 风险

流式行为（chunk 粒度、token 累计口径）差异会直接反映到前端。对策：以现有 SSE 契约测试为准绳；R2 拆两小批（先 SQL 系、后 chart/analysis/recommend），出现口径差异立即修 parse 函数而不是给骨架加分支。

## 4. R3：legacy 有界收口（3–4 批，完成即 P5 验收）

先建清单、后关账。**本阶段开始前，把下表写入新版计划文档作为 P5 的封闭剩余范围**——此后不允许出现表外的"下一批继续"：

| # | 剩余职责（现位置） | 目标（目标设计 §7） | 批 |
| --- | --- | --- | --- |
| 1 | 模型运行时装配 `LegacyModelRuntime`（legacy_dependencies） | `ai_model` 公开运行能力 `build_default_llm_runtime()` | R3-a |
| 2 | 数据源运行时/连接检测（legacy_dependencies） | Datasource 公开 Service 直调，删除包装 | R3-a |
| 3 | 外部助手 Schema 读取（legacy_dependencies） | 显式挂到 orchestration 边界的 legacy 适配模块，随旧外部流程删除或保留至 Assistant 外部契约重构 | R3-a |
| 4 | `run_task` 主流程编排（llm.py:1066–1440） | 改写为对子域 Service 的顺序调用，落 `chatbi` 应用层；SSE 输出经 legacy_sse | R3-b |
| 5 | 分析/预测/推荐任务编排（llm.py:1440–1616） | 同上 | R3-b |
| 6 | SSE 协议与错误格式（legacy_adapter + llm.py 投影） | `chatbi/api/legacy_sse.py` | R3-c |
| 7 | `validate_history_ds`、图片请求、语言工具（llm.py 尾部） | 归入对应子域或 legacy_sse | R3-c |
| 8 | `api/chat.py` 路由与鉴权 | 迁 `chatbi/api/`（与 R4 首批合并执行亦可） | R3-c/R4 |
| 9 | `curd/chat.py`、`models/chat_model.py` 兼容转发 | 调用方核对后删除，台账销账 | R3-d |
| 10 | `services/deletion.py`、`semantic_binding.py` 及其 ORM 跨域依赖（基线 4 条） | 迁 `conversation/`，改走公开契约，基线销账 | R3-d |

### 验收（P5 完成标准的可测化）

- `apps/chat/task/` 目录删除；`legacy_dependencies.py` 删除；SSE 协议代码集中单文件且 ≤300 行。
- 依赖基线中 `apps/chat/*` 相关条目清零（现 15 条内部模型依赖中占 5 条）。
- 原 P5 完成标准逐条核对通过（Graph/Agent 同输入同实现、无独立校验/权限/执行、删除级联、旧入口仅协议转换）。
- 全量回归 + SSE 契约测试通过；OpenAPI 路径数不变。
- 计划文档中 P5 标记完成，changelog 记录关账表逐项销账。

### 风险

`run_task` 改写是本阶段唯一高风险动作（374 行状态机 + 线程/缓存交互）。对策：R3-b 单独成批；改写前为 run_task 现行为补齐特征测试（终态顺序、SSE 事件序列、异常分支——第 32 批已有基础）；保留旧函数一个批次作为可切换实现，验证后删除。

## 5. R4：领域收拢与入口统一（3–4 批）

| 批 | 动作 |
| --- | --- |
| R4-a | 建立 `chatbi/api/`（conversations / queries / interactions / legacy_sse），`apps/api.py` 收敛为注册 chatbi 单 router（旧路径不变）；`apps/chat` 目录删除 |
| R4-b ✅ | `apps/agent → chatbi/orchestration/agent`：ORM 入 `models/orm/agent_run.py`，CRUD 职责入仓储，API 并入 `chatbi/api/interactions.py`；xpack 无旧路径引用，旧目录已删除（2026-07-23 完成） |
| R4-c ✅ | Graph 整体迁入 `chatbi/orchestration/graph`，旧目录删除；1,483 行 `adapters/question.py` 按分类/重写、意图、维度、共享契约与编排拆为 5 文件；runtime 对引擎 infrastructure 的 4 条基线依赖改经公开端口和组合入口，B7/E4 兼容项清偿（2026-07-23 完成） |
| R4-d ✅ | 解散 `apps/capabilities`（validator/repair 归位，兼容导出按台账删除）与 `apps/template`（生成器入 `chatbi/adapters/prompts/`）；平台参数迁出 `system`；R1/R2 的兼容 re-export 与别名清偿（2026-07-23 完成） |

### 验收

- `ls backend/apps` 中 ChatBI 相关仅剩 `chatbi`；`capabilities`、`template`、`chat`、`agent`、`workflow` 目录不存在。
- 问数入口在路由表中唯一（chatbi router），`/chat`、`/chat/agent`、`/graph` 兼容路径行为不变（契约测试）。
- 台账清偿率 ≥ 80%（剩余项全部有 R5/R6 归属）。
- 全量回归、xpack 导入、OpenAPI 通过。

### 风险

目录大迁移与 xpack 硬编码路径冲突（P4 已遇到 data_training 先例）。对策：迁移前 grep sqlbot_xpack 包内引用清单；不可控的保留最小 alias 模块并登记台账，其余全部真移。

## 6. R5：Workflow Engine 隔离（= 原 P6）✅

R5 已于 2026-07-23 完成：

1. 引擎新增 `WorkflowApiExtension` 注册端口，Graph API 通过它取得图定义、运行时、
   数据集解析、会话准备和 ChatRecord 投影网关；引擎源码不再 import 业务包。
2. ChatBI 实现集中在 `orchestration/graph/api_extension.py`，由 `apps/api.py`
   在组合根注册；Semantic 的旧数据源到数据集解析规则进入公开目录服务。
3. `apps/chatbi/workflow_gateway.py` 删除，`workflow_engine_business_imports` 基线清零；
   结构守卫覆盖整个引擎目录以及 `tests/workflow_engine`。
4. 引擎迁至 `backend/platform/workflow_engine`。由于 Python 标准库占用 `platform`
   模块名，运行时通过 `sqlbot_platform.workflow_engine` 命名空间导入，不保留
   `apps.workflow_engine` 兼容路径。
5. 联合回归 316 项、架构测试 137 项、完整后端回归 1,215 项、严格 Mypy 与变更范围
   Ruff 通过；应用与 xpack 初始化成功，OpenAPI 保持 154 条路径；无数据库变更。

## 7. R6：外部接口与最终清理（= 原 P7+P8，3–5 批）

沿用原计划 §6.8/§6.9 任务清单，评审补充的增量要求：

1. `retrieval` 平铺结构按**管道分段**重组（sources / projection / indexing / query），取代原计划"迁入 services/"的模板化目标（P4 欠账，放本阶段首批；风格依据目标设计 §1.0——派生数据管道不强制仓储抽象）。
2. `dashboard` 分层 + 解除 `chat.curd` 依赖（基线销账）。
3. `mcp → backend/interfaces/mcp`；`swagger`、`settings` 按原计划归位；`terminology`、`data_training` 壳目录随 xpack 确认删除。
4. **兼容台账清零**：R6 结束时 `COMPAT_LEDGER.md` 无未销账条目（或每条挂明确外部阻塞原因）。
5. `headless_*` 表名与历史品牌命名单独评审（保持原计划口径：独立设计迁移窗口，不与代码移动同批）。
6. 终局验收沿用原计划 §8.5，追加三条：chatbi 领域公共面 ≤40 符号；`tests/architecture` 文件数 ≤4；依赖基线全项清零。

R6 已于 2026-07-23 完成：

1. Retrieval 按 `sources / projection / indexing / query` 四段重组，旧平铺模块及
   `schemas.py`、`models/__init__.py` 兼容入口删除；Semantic Binding 改经 Semantic
   公开组合入口读取 Schema。
2. Dashboard 建立 API、Service、Repository、SQLModel、ORM、DTO 和 composition 分层，
   图表加载只调用 ChatBI 公开能力；旧 CRUD 目录删除。
3. MCP 迁至 `backend/interfaces/mcp`；失败文件下载迁至 `interfaces/http`；
   i18n 实现与 locales 迁至 `common/interfaces`；无引用的 Settings 模型和 Schema 删除。
4. Semantic 对 Datasource ORM 的七处跨域依赖全部改用公开 DTO 与组合入口；Agent
   函数内应用导入移至模块级；依赖基线五类全部清零。
5. 最终决定项目不再依赖 `sqlbot_xpack`。terminology、data_training、system、chat、
   dashboard、datasource 和 swagger 的 Xpack 专用兼容入口全部删除；License 管理、
   非本地认证和自定义提示词同时下线。
6. `headless_*` 表名与历史品牌命名没有在本批强制修改，继续按独立设计和迁移窗口处理。
7. 本地加解密、文件、平台参数、外观配置、嵌入页和数据权限由仓内实现；依赖声明与
   锁文件不再包含 `sqlbot-xpack`，架构守卫禁止恢复该依赖。ChatBI 公共面保持 40
   符号以内，架构测试文件保持 3 个，无数据库迁移。最终完整后端回归 1,211 项、
   严格 Mypy、变更范围 Ruff 和前端生产构建通过。

## 8. 过程治理（全程适用）

### 8.1 批次结构检查清单（合并前自查，写入 AGENTS.md v2）

```
[ ] 本批未新增无状态单方法 Service 类（如需投影/规则 → 函数）
[ ] 新端口满足准入判据（技术缝/防腐/仓储；跨域默认直连对方公开 Service），定义在 ports.py，后缀 ∈ {Repository, Gateway, Client, PromptBuilder}
[ ] 本批未超出所在子域的风格分级仪式上限（AGENTS.md v2 §1.0 表）
[ ] 新错误定义在 errors.py，错误码走常量注册
[ ] 新 DTO 后缀 ∈ {Input, Result, Ref, Snapshot, 裸名值对象}
[ ] 未新增 build_* 于 composition/adapters 之外
[ ] 未新增守卫测试文件（规则进表驱动文件）
[ ] 兼容导出/别名已登记台账（含删除条件）
[ ] changelog 追加本批记录；计划文档剩余任务表同步勾销
```

### 8.2 回归策略分层

- **结构批**（只动位置/命名/组装）：定向测试 + 架构测试 + 应用导入 + OpenAPI 核对；全量回归在每个 R 阶段收尾批执行一次。
- **行为批**（R3-b 等触及执行路径）：全量回归 + 契约特征测试。

### 8.3 停止与升级规则

- 任何批发现需要"表外新增剩余工作"（R3 关账表之外的 legacy 职责、台账之外的兼容层），必须先改计划文档再动代码——防止回到滚动式"下一批继续"。
- 连续两批出现规则冲突（AGENTS.md v2 条款无法执行）→ 停下修规则，不带病推进。

### 8.4 度量看板（每阶段收尾更新到计划文档）

| 指标 | 基线（2026-07-19） | R1 后目标 | R4 后目标 | R6 后目标 |
| --- | --- | --- | --- | --- |
| chatbi services 文件数 / Service 类数 | 37 / 37 | ≤28 / ≤16 | 同左 | 同左 |
| ChatBI 占用顶级目录数 | 5 | 5 | 1 | 1 |
| 内联 Protocol 数（services 内） | 36 | 0 | 0 | 0 |
| 守卫测试文件数 | 24 | ≤4 | ≤4 | ≤4 |
| llm.py 行数 | 1,724 | 1,724 | 0（已删） | 0 |
| 依赖基线余额（内部模型/具体实现/引擎/函数内） | 15/9/5/1 | 不增 | ≤10/≤5/5/0 | 0/0/0/0 |
| 兼容台账未销账条目 | —（建账） | 全量登记 | ≤20% | 0 |
| 计划文档行数 | 2,030 | ≤400 | ≤400 | ≤400 |

## 9. 与原计划文本的处置

- 原 `DDD_MIGRATION_PLAN.md` §1–§5、§7–§10（目标、领域划分、原则、清单、测试、风险、决策）内容仍然有效，R0 重写时保留其结论、压缩其篇幅。
- 原 §6 各阶段：P0–P4 标记完成并移日志入 changelog；P5 剩余范围由 R1–R4 的清单与关账表**取代**；P6/P7/P8 分别由 R5/R6 承接（任务不变，验收增强）。
- 本评审三份文档（12/13/14）在 R0 完成后归档为决策依据，不作为日常推进文档——日常只看新版计划 + 台账 + changelog。
