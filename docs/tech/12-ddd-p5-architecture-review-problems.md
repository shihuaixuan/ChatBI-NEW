# DDD 迁移（P5 阶段）架构评审 · 一：现状问题分析

> 日期：2026-07-19
> 评审对象：`backend/apps/`（重点 `apps/chatbi` 及其周边）、`backend/apps/AGENTS.md`、`backend/DDD_MIGRATION_PLAN.md`
> 评审基线：分支 `codex/headless-dataset-chat`，P5 第 43 批之后的工作区状态
> 配套文档：`13-ddd-p5-architecture-review-target-design.md`（优化方案）、`14-ddd-p5-architecture-review-execution-plan.md`（执行计划）

## 1. 评审范围与方法

本文只做诊断，不给方案。所有结论均以当前代码为证据（文中给出文件与数字），不以计划文档的自述为准。

先说结论：**迁移的方向、依赖治理和测试纪律是对的，问题出在 ChatBI 领域内部的"形状"上**——按"旧函数"逐批搬迁的策略，把旧代码的轮廓固化成了新架构的结构，产生了贫血的微型 Service、复制粘贴的 DTO 家族、无语义规范的端口动物园和一份变成变更日志的计划文档。这些问题现在修正的成本最低；拖到 P6/P7 之后，守卫测试和调用方会把当前形状进一步焊死。

## 2. 先承认做对了什么

批判之前先记录不应被推翻的成果，后续优化不得回退这些底线：

1. **唯一实现收敛是真实的**：检索、语义编译、SQL 校验、权限应用、SQL 执行、记录状态机等能力，Agent / Graph / 旧 Chat 已实际调用同一实现（`apps/chatbi/composition.py` 可见装配路径）。
2. **依赖方向正确**：`apps/chatbi` 不导入 `apps/agent`、`apps/workflow`（已 grep 验证），方向是执行器 → ChatBI，符合目标设计。
3. **依赖基线棘轮有效**：`tests/architecture/known_dependency_violations.json` 从 P0 的 40+29 条降到 15+9 条，workflow_engine 业务依赖 6→5 条，且新增违规会被测试拦截。
4. **测试与迁移纪律扎实**：228 个测试文件、全量回归 1200+ 项、新代码严格 Mypy、Alembic 升降级带存量数据预检，兼容入口"只转发"的原则大体守住了。
5. **数据事实源合并完成度高**：术语、SQL 示例、推荐问题、权限、模型配置的多事实源问题已实质解决。

问题不在"做了什么"，在"做成了什么形状"。

## 3. 问题清单

### A. 宏观架构层

#### A1. ChatBI 领域仍分裂在 5 个顶级目录，且新领域缺少自己的入口

统计（当前工作区）：

| 目录 | 文件数 | 行数 | 与 ChatBI 的关系 |
| --- | --- | --- | --- |
| `apps/chatbi` | 93 | 10,246 | 领域本体（Service/DTO/仓储/适配器） |
| `apps/chat` | 16 | 3,793 | 旧入口 + 旧编排（`task/llm.py` 仍 1,724 行） |
| `apps/agent` | 16 | 2,796 | 执行方式一（Agent Loop + 自己的 API/ORM/CRUD） |
| `apps/workflow` | 35 | 6,738 | 执行方式二（Graph 定义/节点/适配器） |
| `apps/capabilities` | 13 | 570 | 名义上是兼容层，实际仍有活代码（见 A2） |

合计约 2.4 万行的一个业务领域分布在 5 个顶级目录。更关键的是：`apps/chatbi` **没有 `api/`**，问数入口仍由 `apps/chat/api/chat.py`（449 行）、`apps/agent/api.py`、`apps/workflow_engine/api` 三处分别注册（见 `apps/api.py:38-39` 与 `include_router(graph_workflow)`）。"ChatBI 是唯一对外问数领域"目前只在计划文档里成立，在路由表上不成立。

#### A2. 新领域反向依赖"待解散"目录

`apps/chatbi/composition.py:6` 直接导入 `apps.capabilities.sql.execution_gateway.SqlExecuteTool` 作为 `QueryService` 的执行端口实现；13 个 `apps/chatbi/adapters/*` 中多个导入 `apps.template.*`（如 `adapters/dynamic_sql_generation.py:11`）。

`capabilities` 和 `template` 在计划 §3.5 中都是"不作为领域保留"的目录，但新领域的组合根和适配器把它们变成了**长期上游**。这不是兼容转发（旧调新），而是新依旧——解散这两个目录时会反过来动新领域的代码。`SqlExecuteTool` 还携带旧 Agent 世界的 `ToolResult` 契约（`apps/capabilities/schemas.py`），等于把废弃方案的数据形状注入了新 Service 的端口边界。

#### A3. `workflow_gateway.py` 位置暴露了引擎依赖倒置未完成

`apps/chatbi/workflow_gateway.py` 是给 `workflow_engine/api/service.py` 用的入站投影网关（引擎 API import 它，见基线 `workflow_engine_business_imports`）。它游离在 chatbi 包根，不属于 services/repository/adapters 任何一层。本质问题是 P6 未做：通用引擎的 API 仍然主动 import 业务代码（5 条基线），ChatBI 被迫在自己包里放一个"专供引擎调用"的模块。方向应当反过来（ChatBI 注册投影回调/组装引擎路由），目前的文件位置是这个未完成事项的显性症状。

R5（2026-07-23）已清偿：引擎改为声明 `WorkflowApiExtension`，ChatBI 在应用组合根注册
图定义、运行时、会话校验与历史投影实现；`workflow_gateway.py` 删除，
`workflow_engine_business_imports` 清零，引擎迁入 `backend/platform/workflow_engine`。

#### A4. 兼容层在累积而不是在收敛

- 全仓 71 个 `.py` 文件出现"兼容"字样；纯转发模块至少 8 个（`apps/capabilities/question_understanding.py`、`time_slots.py`、`apps/workflow/capabilities/adapters/intent_validation.py`、`time_slots.py`、`apps/system/crud/assistant*.py`、`apps/datasource/models/datasource.py` 等）。
- 壳目录仍在：`terminology`（3 文件 13 行）、`data_training`（3 文件 21 行）、`settings`、`swagger`、`template`。
- 计划 §5.7 要求兼容入口"有明确的调用方迁移清单和删除条件"，但仓库里**没有一份兼容入口台账**：每条兼容出现在各批次日志里，删除条件写的是"待外部调用确认"这类不可执行的表述，没有 owner、没有截止条件、没有清点机制。按现在的节奏，兼容层只增不减。

#### A5. `system` 正在被"复活"而不是被拆掉

本批工作区新增了 `apps/system/composition.py`、`apps/system/repository/`、`apps/system/services/`（参数服务）。给平台参数建 Service/Repository 分层本身正确，但它落在 `system` 这个计划明文要拆除的宽泛目录里，等于给该目录重新注入了"正统"的新代码。计划说"系统参数保留为平台配置"，却从未给平台配置指定归属目录——这是计划的一个空洞，执行时就地填进了 `system`。

#### A6. P4/P7 的结构欠账仍挂在原地

- `retrieval`（30 文件 8,685 行）仍是平铺结构：`service.py`、`planner.py`、`policy.py`、`indexing.py` 等 20 个顶层模块，计划 §7 中"迁入 `services/`"的动作未执行，与 `semantic`（103 文件、services/builders/matching/rules 分包）形成鲜明对比。
- `dashboard/crud/dashboard_service.py` 仍直接 import `apps.chat.curd.chat`（基线在案）。
- `agent`、`workflow` 内部完全不遵循 AGENTS.md 分层（`agent/crud.py`、`agent/models.py` 平铺 ORM；`workflow/capabilities/adapters/question.py` 单文件 1,483 行、52 个方法）。这两个目录按计划终将迁入 `chatbi/orchestration/`，但"将来要搬"不该成为"现在不分层"的理由——搬迁时这些内部混乱会原样带过去。

### B. ChatBI 领域内部分层与结构

#### B1. `services/` 平铺 37 个文件，没有任何业务分组

`apps/chatbi/services/` 共 37 个模块、6,533 行，全部平铺。会话、问题理解、数据源选择、SQL 生成、图表、执行、回答、上下文组装……全部混在一个命名空间里，只能靠文件名前缀猜分组。AGENTS.md §1 明确允许"在所属层下继续按职责建立子目录"，`semantic` 已经示范（`services/builders`、`matching`、`rules`），chatbi 没有跟进。37 个文件已经超过了人能一眼建立结构感的规模。

#### B2. 端口 Protocol 内联散落，后缀是一个"动物园"

chatbi 内 36 个 `Protocol` 类全部内联定义在各 service 文件里，后缀用了至少 9 种：`Gateway`、`Provider`、`Client`、`Builder`、`Ranker`、`Applier`、`Executor`、`Reader`、`View`。同为"另一个领域的公开能力"，检索叫 `SemanticRetrievalGateway`，推荐问题历史叫 `RecommendedQuestionHistoryProvider`，权限策略叫 `PermissionPolicyProvider`，编译叫 `SemanticCompilationGateway`——同一语义四种叫法。没有任何文档规定哪个后缀表达什么依赖类型。后果：读者无法从名字判断"这是跨领域端口、外部技术端口还是本地仓储"，新批次只能继续即兴发明。

#### B3. 没有 `errors.py`，错误类型散落且退化为字符串码

AGENTS.md §2.7 要求领域公共错误集中在 `errors.py`。chatbi 没有该文件；`ChartGenerationError`、`DatasourceSelectionError`、`DynamicSQLGenerationError`、`ExecutionBindingError`、`ConversationError` 家族等 10+ 个错误类散落在各 service 文件内，几乎全部是 `class XxxError(ValueError)` + 大写字符串码（如 `"DYNAMIC_SQL_GENERATION_PROMPT_INVALID"`）。错误码本身没有注册表，拼写正确性靠测试字符串断言保证；API 层做错误映射时需要 import 十几个模块。

#### B4. 组装逻辑分散在 3 类位置，全仓 95 个 `build_*` 工厂

chatbi 的装配分散在：`composition.py`（152 行）、包根游离的 `chat_record.py`（13 行，只有一个 builder）、包根游离的 `conversation.py`（36 行，两个 builder）。全仓 `def build_*` 达 95 个、分布在 52 个文件。"组装保留在最外层"的原则（计划 §4.6）事实上演化成了"每个模块都可以长一个 build_ 函数"。包根还有第 4 个游离模块 `workflow_gateway.py`（见 A3），chatbi 包根共 4 个不属于任何层的散文件。

#### B5. ORM 文件命名与内容不符

`apps/chatbi/models/orm/conversation.py` 一个文件承载 `ChatLog` + `Chat` + `ChatRecord` 三张表和 4 个枚举。文件叫 conversation，内容主体是记录与日志。AGENTS.md §2.4 要求"按业务资源拆分，避免一个文件承载整个领域的全部模型"。同时包内出现四个同名异物：`chatbi/conversation.py`（组装）、`services/conversation_service.py`（服务）、`models/orm/conversation.py`（三张表）、`models/dto/conversation.py`（DTO）——`conversation` 这个词在同一个包里有四种含义。

#### B6. `services/__init__.py` 是 270 行的大桶导出

聚合导出约 130 个符号：Service、端口 Protocol、错误类、常量、辅助函数无差别平铺在同一公共面上。后果有三：任何单点导入都会加载全部 37 个 service 模块（含其传递依赖）；外部调用方（agent tools、workflow adapters、旧 llm.py）与 chatbi 的耦合点全部指向同一个巨型命名空间，无法从 import 看出依赖了哪个子域；`__init__` 的 235 行 import 本身成为高频合并冲突点。`models/dto/__init__.py`（252 行）同样。

#### B7. 提示词知识有三个家

同为"提示词"，当前分三处维护：`apps/template/`（文件模板，13 个生成器模块）、`apps/chatbi/services/question_understanding_prompt.py`（提示词规则以代码形式放在 services 层）、`apps/chatbi/adapters/*`（模板装配 + LangChain 消息转换）。判断"改一个提示词要动哪里"需要同时了解三处的分工，而这个分工没有文档表述。

### C. 服务粒度与 DTO

#### C1. 无状态单方法类冒充 Service，37 个里 15 个不足 130 行

典型样本（均为完整文件）：

- `generation_runtime_settings_service.py`（40 行）：`GenerationRuntimeSettingsService.project()` 解析 3 个配置值。无状态、无端口、单方法——这是一个纯函数，被包成了类。
- `generation_context_scope_service.py`（43 行）：`project()` 按助手类型做一次分支映射。同上。
- `generation_custom_prompt_service.py`（41 行）：包装一个 Provider，增加一个 if。
- `execution_binding_service.py`（42 行）、`final_reply_projection_service.py`（73 行）、`generation_context_service.py`（62 行）同属此类。

37 个 service 文件中 15 个 ≤130 行，多数单方法、单调用方。这直接违反 AGENTS.md §2.2"不为了拆分而建立大量只调用一次的方法或类"和 §10"新增抽象必须能够形成稳定边界、减少真实重复或明显降低复杂度"。**Service 的数量正比于迁移批次数，而不是正比于业务概念数**——这是"一函数一批"策略在结构上的直接投影（见 E2）。

#### C2. `Question*` 七兄弟：概念重叠、名字不表达归属

| 服务 | docstring 自述 | 实际服务对象 |
| --- | --- | --- |
| `QuestionModelService` | 模型调用与 JSON 解析边界 | Agent + Graph 共用 |
| `QuestionUnderstandingService` | 问题理解应用服务（重写/意图/维度） | Agent |
| `QuestionUnderstandingValidationService` | 统一校验已识别的理解结果 | Agent + Graph |
| `QuestionIntentProjectionService` | 意图子任务结果的确定性投影 | Graph |
| `QuestionIntentValidationService` | 修复、重试和澄清投影 | Graph |
| `QuestionInputProjectionService` | 分类和重写的输入输出投影 | Graph |
| `QuestionIntentFallbackService` | 模型不可用时的降级推断 | Graph |

七个名字里 "Understanding / Intent / Input"、"Projection / Validation / Fallback" 交叉组合，无法从名字回答两个基本问题：这个服务属于问题理解流程的哪一步？它是共享能力还是某个执行器的专用投影？其中 4 个是 Graph 专用的契约投影，却与共享能力平铺在同一命名空间，天然诱导误用。

#### C3. 五套复制粘贴的流式 DTO 家族

`models/dto/` 下 `sql_generation`、`chart_generation`、`analysis_prediction`、`recommended_question`、`datasource_selection` 五个模块各自定义结构完全同构的四件套：

```
{X}Message        (role / content / system_context)
{X}ModelChunk     (content / reasoning_content / token_usage)
{X}Event          (kind / content / reasoning_content / result / error / token_usage)
{X}Data           (输入)
```

`DynamicSQLGenerationService` 已经在复用 `SQLGenerationMessage/Chunk/Event`（`dynamic_sql_generation_service.py:6-15`），证明这些类型本可共享。五套重复意味着：新增一种生成能力要再抄一套四件套 + 一个 `Iterator[XxxEvent]` 循环 + 一个 LangChain 适配器；跨能力写通用逻辑（如统一 token 统计、统一 SSE 投影）时没有公共类型可依。

#### C4. 六个生成 Service 是同一个骨架的六次手写

`sql / dynamic_sql / permission_sql / chart / analysis_prediction / recommended_question` 六个生成服务的主体都是同一循环：build prompt → 校验非空 → 遍历模型流 → 累计 content/reasoning/token → yield chunk 事件 → 终止时 parse → yield completed 事件。差异只有输入 DTO、解析函数和个别校验。骨架被手写了六遍，且已出现抄写走样：`DynamicSQLGenerationService.generate()` 在 `prepare()` 内外做了两次完全相同的消息校验（`dynamic_sql_generation_service.py:52-56` 与 `63-68`）——重复代码开始产生自己的噪声。

#### C5. DTO 后缀语义不一致

`*Data` 有时是输入（`DynamicSQLGenerationData`、`GenerationContextScopeData`），有时是写入载荷（`ResultArtifactWriteData`、`ChatRecordCreateData`）；输出混用 `*Result`、`*Projection`、裸名（`GenerationRuntimeSettings`）；引用类型有 `*Ref`。没有一张后缀语义表，每批自行选择。

### D. 命名

#### D1. `Generation*` 前缀六服务：前缀含义漂移，三个 `*Context*` 无法区分

`GenerationContextService`（取 SQL 示例 + 术语 + 自定义提示词）、`GenerationContextScopeService`（解析工作空间/助手范围）、`GenerationSchemaContextService`（组装物理 Schema 文本与样例数据）——三个名字只差一个中缀，职责完全不同。且 `Generation` 在这里实际指"旧 Chat 的 SQL 生成流程支撑"，与 `AnswerGenerationService`、`ChartGenerationService` 里表示"LLM 生成"的 Generation 是两个概念，读者无从分辨。

#### D2. `QueryService` 名字过宽，端口实现沿用旧世界命名

`QueryService` 实际是"权限改写 → 只读校验 → 受控执行 → 采样"的守护执行服务，但名字宽到可以指任何查询。它的执行端口由 `SqlExecuteTool` 实现——"Tool" 是旧 Agent 工具体系的命名（连同 `ToolResult`），出现在新领域的核心链路上（见 A2）。

#### D3. 历史品牌与旧方案名仍在认知路径上

语义层表名 `headless_*`（如 `headless_term`）与领域名 `semantic` 不一致，计划已把表名调整推迟到 P8，这个决策本身合理；但代码注释、审计逻辑中"读取 `headless_term`"之类表述持续制造双名词负担。`Legacy*` 前缀（`LegacyDatasourceRuntime`、`LegacyModelRuntime` 等）作为显式临时标记是好实践，但 `legacy_dependencies.py` 里包着的模型运行时（`LLMFactory`/`get_default_config`）是长期能力而非旧流程私有物，用 Legacy 包装长期能力会误导删除判断（计划第 189 条已自我识别，尚未执行）。

### E. 过程与治理（问题的根源层）

#### E1. 计划文档已经变成变更日志

`DDD_MIGRATION_PLAN.md` 现 2,030 行，其中 P5 一节按 43 个批次流水记录了 189 条编号事项。"剩余工作是什么"这个计划文档最该回答的问题，答案埋在第 176、184、189 条日志里。计划的"状态定义"表（§1.4）设计了 已实现/部分实现/目标设计 的区分，但 P5 章节实际以追加日志的方式演进，目标设计与实施记录已不可分离。新成员或评审者无法在 10 分钟内从该文档得知：还剩哪些事、按什么顺序、何时算完。

#### E2. "一函数一批"策略把旧代码轮廓固化成新架构形状

P5 的批次单位是"旧 `LLMService` 的一个方法"或"Graph Adapter 的一段逻辑"。每批的产出模式固定：1 个 DTO 模块 + 1 个 Service（内联 2 个 Protocol + 1 个 Error）+ 1 个 adapter + 1 个边界守卫测试。这个模式保证了每批可验证、可回归，但也意味着：**新架构的模块划分 = 旧代码的方法划分**。`sql_answer` 字符串、SSE 字典形状、图表 JSON 字符串等旧契约原样成为新 Service 的输出契约（各批明确记录"SSE 契约保持不变"）。保持外部契约兼容是对的，问题是内部结构也照着旧函数边界切，没有先设计目标形状再迁移——计划 §5.3"先建立契约，再迁移实现"在 P5 执行中变成了"先复刻旧契约，再迁移实现"。

#### E3. 逐批守卫测试正在把当前形状焊死

`tests/architecture/` 有 24 个文件，其中 20+ 个是逐批新增的 `test_chatbi_*_boundary.py`，各自硬编码"某 Service 不得 import 某模块"、"旧方法必须转调新入口"等断言。机制本身优秀（棘轮防回退），但以每批一文件的方式生长后：任何结构重组（改包名、并服务、换端口位置）都要同步改动一二十个守卫文件。守卫测试从"防止倒退的护栏"变成了"阻止演进的水泥"。规则应当表驱动集中维护，而不是按批次分文件。

#### E4. 批次成本结构激励碎片化

每批要求全量回归（现已 1,217 项）+ 定向回归 + Ruff + Mypy + OpenAPI 核对。单批固定成本高 → 理性选择是把批切小 → 批越小、Service 越碎（C1）→ 服务越多、下一批的装配与守卫成本越高。这是一个自增强循环，43 批走到现在，边际产出（每批消除的旧行数）在下降，边际结构成本（新增文件数/批）不降。

#### E5. P5 没有有界的完成定义

P5 的完成标准里"旧 HTTP/SSE 入口只保留协议转换"是定性表述；`llm.py` 仍 1,724 行、`run_task()` 单方法 374 行、`legacy_dependencies.py` 192 行，剩余工作以"继续收缩"（第 176 条）、"下一批继续……"（第 184、189 条）滚动表述，没有一张"剩余职责 → 目标去处 → 完成判据"的清单。开放式的收敛在实践中等于没有终点。

#### E6. AGENTS.md 与 P5 实际发明的概念脱节

AGENTS.md 只规定了 `api/services/repository/models/utils/errors` 六件套；P5 实际大量使用的概念——端口（Protocol）、技术适配器（`adapters/`）、组合根（`composition.py`）、网关（`*Gateway`）、编排层（`orchestration/`，计划 §4.3 提到但规则未收编）——在规则文档里**一个都没有定义**。每批只能即兴决定放哪、叫什么，于是有了 B2 的后缀动物园、B4 的组装分散、B7 的提示词三个家。规则文档没有跟上实践，实践就各自为政；反过来，AGENTS.md 里已有的规则（errors.py、ORM 按资源拆分、不为拆分而拆分）又在 chatbi 被违反而无人拦截——因为守卫测试只检查依赖方向，不检查结构规则。

#### E7. 战术 DDD 被当作全项目统一的合规动作，与子域性质错配

（2026-07-20 评审讨论补充，是 C 类问题更深一层的根因。）

DDD 有两半：**战略设计**（限界上下文、数据所有权、公开契约、依赖方向）与**战术模式**（实体/聚合、仓储接口、领域服务、DTO/ORM 分离）。本项目迁移前的病灶——13 模块循环依赖、术语 3 个事实源、问数 3 套链路——全部是边界病，P0–P4 用战略半边治好了它们，这部分成立且已回本。

问题出在 P5 把战术套餐当成每个模块必须完成的合规动作统一套用，而各子域的复杂度性质并不相同：

| 子域 | 复杂度本质 | 战术 DDD 契合度 |
| --- | --- | --- |
| `semantic` | 实体 + 不变量密集（命名唯一性、Join 校验、术语权威性），有真正的业务通用语言 | 高 |
| `access_control` | 规则密集、默认拒绝 | 较高 |
| `datasource` | 14 种数据库驱动的技术适配 | 中（本质是六边形端口，不是 DDD 特有） |
| `retrieval` | 派生数据管道（投影→索引→代次→召回） | 低 |
| `chatbi` | **LLM 编排管道**：上下文组装→生成→解析→校验→执行→投影；真实不变量只有记录状态机、大小边界、执行绑定一致性一小撮 | **低** |
| ai_model / assistant / knowledge / dashboard | 带校验的 CRUD | 低 |

chatbi 的直接证据：37 个服务中 **10 个以 Projection / Validation / Fallback 结尾**——这些是管道阶段（stage）的名字，不是领域服务的名字。当一个"领域"的通用语言全是 Projection、Adapter、Gateway 这类技术词而没有业务词，它就不是 DDD 意义上的领域，而是应用编排层 + 数据流。拿实体领域的模式（每阶段包成 Service + 端口 + 适配器 + DTO）去套管道问题，贫血仪式（C1–C5）是必然产物。

另一个维度是经济性：战术 DDD 的最大红利是组织性的（多团队按上下文自治）。本项目由 1–2 人推进，间接层税（四道手续、95 个工厂、24 个守卫文件）全额缴纳，组织红利为零。

## 4. 问题之间的因果链

这些问题不是 21 个独立缺陷，主线因果是：

```
E7 战术 DDD 统一套用（子域性质错配）─┐
E6 规则缺位 ──────────────────────┤
E1 计划变日志（无形状设计）          ├─→ E2 按旧函数切批 ─→ C1 微型服务 / C3 DTO 复制 / C4 骨架六抄
E4 批次成本激励碎片化 ──────────────┘            │
                                      ├─→ B1 平铺 37 文件 / B2 端口动物园 / B6 大桶导出
                                      └─→ E3 逐批守卫焊死形状 ─→ 重构成本随时间上升
A1/A3 入口与引擎倒置未完成 ─→ B4 workflow_gateway 等游离模块
A4 兼容无台账 ─→ 兼容层净增长 ─→ P8 删除阶段风险积累
```

要点：**根因在 E 层（过程与规则），症状在 B/C/D 层（结构与命名）**。E7 是最深一层：方法论与子域性质错配决定了"每阶段一套四件套"的产出模式；E2/E4 决定了切分粒度。只修症状（比如合并几个小服务）而不改方法论定位和规则文档，下一批还会长出同样的东西。

## 5. 量化摘要

| 指标 | 数值 | 判断 |
| --- | --- | --- |
| ChatBI 业务分布的顶级目录数 | 5 个（chatbi/chat/agent/workflow/capabilities），约 24k 行 | 领域未收拢 |
| chatbi `services/` 文件数 / 行数 | 37 / 6,533，平铺无分组 | 过碎 + 无结构 |
| ≤130 行的 service 文件 | 15 / 37 | 粒度失衡 |
| 内联端口 Protocol / 后缀种类 | 36 个 / 9 种后缀 | 无规范 |
| 同构流式 DTO 家族 | 5 套 × 4 类 | 重复 |
| `services/__init__.py` 导出符号 | ~130 个，270 行 | 公共面失控 |
| 组装工厂 `build_*` | 95 个 / 52 文件 | 组装分散 |
| chatbi 包根游离模块 | 4 个（composition 之外含 conversation.py / chat_record.py / workflow_gateway.py） | 层次不明 |
| 领域内 `errors.py` | 不存在；错误类散落 10+ 文件 | 违反自身规则 |
| 旧编排残留 | `llm.py` 1,724 行（`run_task` 374 行）、`legacy_dependencies.py` 192 行 | P5 未见底 |
| 架构守卫测试文件 | 24 个（逐批增长） | 护栏变水泥 |
| 提及"兼容"的文件 | 71 个；纯转发模块 ≥8 个；壳目录 5 个 | 只增不减、无台账 |
| 依赖基线余额 | 15 内部模型 + 9 具体实现 + 5 引擎业务 + 1 函数内 | 收敛中（正向） |
| 计划文档 | 2,030 行，P5 章 43 批 189 条日志 | 计划/日志不分 |
| 问数入口 | 3 处路由（chat / agent / graph） | 入口未统一 |

## 6. 诊断结论

1. P0–P4 的领域拆分与依赖治理成果真实有效，应保持并继续棘轮收紧。**DDD 的战略半边（边界、所有权、公开契约、依赖方向）适合本项目且已回本；不适合的是把战术套餐全项目统一套用**（E7）——后续应按子域性质分级选择架构风格。
2. P5 的**迁移方法论（一函数一批 + 逐批守卫 + 日志式计划）已到达收益拐点**：它擅长安全地"搬空旧代码"，但不能产出好的目标结构；继续用它走完 P5 只会把碎片化推得更深。
3. ChatBI 领域当前是"**贫血服务集合 + 组合根**"，不是有内聚子域结构的领域：37 个平铺服务、130 个导出符号、9 种端口后缀之间没有可导航的概念层次。其正确定位是**应用编排层 + 函数式管道核心**，而不是再造一个战术 DDD 领域。
4. 规则文档（AGENTS.md）与计划文档（DDD_MIGRATION_PLAN.md）双双失守：前者缺概念、后者失焦点。**先修文档与规则（含架构风格分级），再修代码结构，最后再继续搬迁**——这是配套优化方案（文档二）的出发点。
