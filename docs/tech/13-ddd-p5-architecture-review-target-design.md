# DDD 迁移（P5 阶段）架构评审 · 二：优化方案（目标设计）

> 日期：2026-07-19
> 前置文档：`12-ddd-p5-architecture-review-problems.md`（问题分析，本文按其编号 A1–E6 引用）
> 后续文档：`14-ddd-p5-architecture-review-execution-plan.md`（执行计划）

## 0. 方案总纲

本方案不推翻既有迁移成果（唯一实现、依赖基线、测试纪律全部保留），只做五类修正：

0. **架构定位修正**（2026-07-20 补充）：目标从"全项目 DDD"改为"**边界清晰的模块化单体**"——DDD 战略半边（限界上下文、所有权、公开契约、依赖方向）全局保留；战术模式按子域性质分级使用（§1.0）。ChatBI 定位为**应用编排层 + 函数式管道核心**，不再按战术 DDD 领域的标准塑形。→ 治 E7
1. **规则先行**：修订 `AGENTS.md`，改为分级规则：全局只管边界，子域按风格分级约束仪式上限；把 P5 实践中已经存在但无规范的概念（端口、适配器、组合根、编排层）收编成文；拆分计划文档与变更日志。→ 治 E1/E6
2. **ChatBI 领域内部重组**：`services/` 按业务子域分包；小服务降级为函数或合并；统一流式生成骨架与共享 DTO；收敛错误、端口、组装到规定位置；取消 1:1 跨域包装端口。→ 治 B1–B7、C1–C5、D1–D2
3. **有界收口 legacy**：把 `llm.py` 剩余职责列成有限清单逐项关账，替代"继续收缩"的开放式表述。→ 治 E5
4. **领域收拢与入口统一**：建立 `chatbi/api/`，`agent`/`workflow` 迁入 `chatbi/orchestration/`，解散 `capabilities`/`template` 对新领域的上游地位；随后按原计划执行 P6–P8。→ 治 A1–A6

指导原则两句话：**形状先于搬迁**——先定义目标结构与命名规则，再把剩余旧代码迁进去；**风格跟随子域性质**——不变量密集处用完整分层，管道处用函数，CRUD 处保持薄，不为合规而加层。

## 1. 设计规则（先于目录结构）

以下规则将写入 AGENTS.md v2（见 §8），是后文所有结构决定的依据。

### 1.0 架构风格分级（全局规则）

全局统一约束只有四条：数据所有权唯一、跨域只走公开契约、依赖方向单向、基线棘轮不回退。在此之上，每个子域按其复杂度本质选择风格，并以下表为**仪式上限**（允许更简单，不允许更复杂）：

| 子域 | 定位 | 风格与仪式上限 |
| --- | --- | --- |
| `semantic` | 实体 + 不变量密集的核心领域 | 完整战术分层（api/services/repository/orm/dto），仓储接口 + Fake 测试 |
| `access_control` | 规则/策略领域 | 同上 |
| `datasource` | 物理连接与元数据的技术边界 | 六边形：公开 Service + 驱动适配器；不追求领域建模 |
| `retrieval` | 派生数据管道 | 按管道分段组织（sources / projection / indexing / query），不强制仓储抽象 |
| `chatbi` | **应用编排层 + 管道核心** | 薄 Service（仅限有端口/状态/事务者）+ 纯函数管道阶段；端口仅开在可替换缝（§1.2） |
| `knowledge` / `ai_model` / `assistant` / `dashboard` | 带校验的 CRUD | 薄 Service + 仓储封顶，禁止继续加层 |
| `workflow_engine` | 通用运行平台 | 自有分层，禁止业务依赖 |

判断依据（写入 AGENTS.md v2）：一个模块配得上完整战术分层，当且仅当它拥有密集的业务不变量和真正的业务通用语言；当模块的词汇表以 Projection/Adapter/Pipeline 等技术词为主时，它是管道或适配层，应按数据流组织。

### 1.1 类还是函数：Service 的准入判据

一个类可以叫 `XxxService` 当且仅当满足以下至少一条，否则写成模块级纯函数：

1. 依赖至少一个端口/仓储（需要注入、需要在测试中替换）；
2. 承载跨多次调用的状态或事务边界；
3. 表达一个有多个入口方法的内聚业务概念。

按此判据处理现有 37 个服务的映射见 §3。推论：**"投影/映射/规则判断"默认是函数**（`project_xxx()` / `resolve_xxx()` / `validate_xxx()`），放在所属子域包的模块里，与 AGENTS.md"纯计算、转换和规则判断应与外部访问分离"一致。函数同样可以有单元测试和严格类型，不需要类作外壳。

### 1.2 端口准入判据与后缀语义表

**先问要不要端口，再问叫什么**（2026-07-20 修订）。跨领域调用的默认方式是**直接使用对方领域的公开 Service 与公开 DTO**——这本来就是计划 §4.5 允许的协作方式。只有满足以下条件之一才允许建立端口（Protocol）：

1. **可替换技术缝**：LLM/Embedding 客户端、数据库执行、xpack 许可能力等确实存在多实现或需在测试中替换的技术设施；
2. **防腐需要**：对方契约与本域模型存在真实转换（不是字段改名），且对方契约不稳定；
3. **本领域持久化**（仓储接口，仅限 §1.0 分级允许仓储抽象的子域）。

1:1 转发对方公开 Service 的包装端口一律取消。按此判据处理 chatbi 现有 36 个 Protocol：

| 处置 | 端口 | 理由 |
| --- | --- | --- |
| **取消，直连公开 Service** | `SemanticRetrievalGateway`、`SemanticCompilationGateway`、`PermissionPolicyProvider`（改用 access_control 公开策略入口）、`DatasourceMetadataReader` 及 `PhysicalTableView`/`PhysicalFieldView` | 1:1 包装，无契约转换 |
| **保留（技术缝）** | 各 `*ModelClient`（收敛为统一 `GenerationModelClient`，见 §4）、`SQLExecutor`、Embedding 排序、`GenerationCustomPromptProvider → *Client`（xpack 许可门控） | 真实可替换 |
| **保留（防腐）** | `ResultArtifactGateway`（引擎 Artifact 到 `ChatBIResultArtifactRef` 有契约转换与清理语义） | 真实转换 |
| **保留（仓储）** | `ConversationRepository`、`ChatRecordRepository`、推荐问题历史（Provider → Repository 改名） | 持久化端口 |

保留下来的端口后缀只允许 4 种：

| 后缀 | 语义 | 例 |
| --- | --- | --- |
| `*Repository` | 本领域持久化端口 | `ConversationRepository` |
| `*Gateway` | 需要防腐转换的跨领域端口（准入条件 2） | `ResultArtifactGateway` |
| `*Client` | 外部技术设施端口（LLM、Embedding、xpack、HTTP） | `GenerationModelClient` |
| `*PromptBuilder` | 提示词装配端口（LLM 能力专用，成对出现于 `*Client`） | `ChartGenerationPromptBuilder` |

废止 `Provider`、`Applier`、`Reader`、`Ranker`、`View` 等后缀。**端口定义收拢到各子域包的 `ports.py`**，不再内联在 service 文件中；实现放 `adapters/` 或 `repository/<technology>/`。直连公开 Service 的可测性不受影响：Python 鸭子类型下测试注入 Fake 不需要 Protocol，类型标注直接用对方公开 Service 类型。

### 1.3 DTO 后缀语义表

| 后缀 | 语义 | 例 |
| --- | --- | --- |
| `*Input` | 用例入参（调用方 → Service） | `SQLGenerationInput` |
| `*Result` | 用例出参（Service → 调用方） | `SQLGenerationResult` |
| `*Ref` | 跨边界的最小引用（只含 ID/版本类字段） | `ChatBIResultArtifactRef` |
| `*Snapshot` | 某时点的完整只读投影 | `DatasourceConnectionSnapshot` |
| 裸名 | 领域内稳定值对象 | `GenerationContextScope`、`ModelMessage` |

废止把 `*Data` 同时用作输入和载荷（现状 C5）。存量 DTO 改名随子域重组一次完成，跨领域已发布契约（被 agent/workflow/chat 引用的）保留旧名 alias 一个阶段。

### 1.4 错误规则

- 每领域一个 `errors.py`：领域基类（`ChatBIError`）+ 按子域分节的具体错误。
- 错误码字符串集中为常量（或 `StrEnum`），service 内不再出现裸字符串码；API 错误映射只 import `errors.py`。
- 兼容期允许旧类名从 `errors.py` re-export。

### 1.5 组装规则

- 每领域**唯一**组合根 `composition.py`；包根不允许再出现独立 builder 模块（现有 `chatbi/conversation.py`、`chat_record.py` 并入）。
- `build_*` 函数只出现在 `composition.py` 与 `adapters/`（技术装配）里；service 模块内禁止定义 build 工厂。
- 跨领域组装（完整问数链）只发生在 ChatBI 的 composition 与未来的 `chatbi/api/`。

### 1.6 公共面规则

- `services/__init__.py` 大桶导出废止：外部调用方按子域导入（`from apps.chatbi.services.generation import SQLGenerationService`）。
- 领域对外公共面收敛到 `apps/chatbi/__init__.py`，**只导出**其他领域允许使用的 Service/DTO/错误（预计 ≤40 个符号），其余为领域私有。
- 迁移期在 `services/__init__.py` 保留旧符号 re-export，并在台账登记删除条件（见 §9）。

## 2. ChatBI 目标目录结构

```text
apps/chatbi/
├── api/                          # R4 阶段建立：统一问数入口
│   ├── conversations.py          #   会话 CRUD（收编 chat/api 中会话部分）
│   ├── queries.py                #   发起问数/流式/状态（收编 agent api + graph 业务入口）
│   ├── interactions.py           #   澄清、恢复、取消
│   └── legacy_sse.py             #   旧 SSE 协议兼容入口（有删除条件）
├── orchestration/                # R4 阶段迁入，内部结构保持先行整顿后的样子
│   ├── agent/                    #   现 apps/agent（loop/tools/budget/events…）
│   └── graph/                    #   现 apps/workflow（definitions/nodes/adapters…）
├── services/
│   ├── conversation/             # 会话与记录生命周期
│   │   ├── conversation_service.py
│   │   ├── chat_record_service.py
│   │   ├── record_limits.py      #   大小边界规则（从 chat_record_service 拆出的纯规则）
│   │   └── ports.py              #   ConversationRepository / ChatRecordRepository / 绑定与删除 Gateway
│   ├── understanding/            # 问题理解（Question* 七兄弟的归宿，见 §3.2）
│   │   ├── understanding_service.py      # 共享：重写/意图/维度编排（Agent 直用）
│   │   ├── validation.py                 # 共享：确定性校验规则（函数集）
│   │   ├── intent_projection.py          # 共享：意图清洗/合并/降级（函数集 + FallbackService）
│   │   ├── graph_contracts.py            # Graph 专用：输入/意图/校验三类契约投影（函数集）
│   │   ├── prompts.py                    # 共享提示词规则
│   │   ├── time_range.py                 # 时间表达规则（现 services/time_range.py）
│   │   └── ports.py                      # QuestionModelClient（结构化模型调用端口）
│   ├── planning/                 # 从问题到可执行查询的规划
│   │   ├── datasource_selection.py       # 选择 + 候选（两个现服务合一个模块）
│   │   ├── execution_binding.py          # 函数化
│   │   ├── semantic_retrieval.py
│   │   ├── semantic_compilation.py       # 现 semantic_query_service 改名
│   │   ├── physical_schema.py
│   │   └── ports.py                      # 仅技术缝端口（选择模型、排序）；语义检索/编译/元数据直连对方公开 Service
│   ├── generation/               # 所有 LLM 生成能力
│   │   ├── streaming.py                  # 统一骨架：run_generation()（见 §4）
│   │   ├── sql_generation.py             # 主/动态/权限 SQL 三入口共用解析（三服务并一模块）
│   │   ├── chart_generation.py
│   │   ├── analysis_prediction.py
│   │   ├── recommended_questions.py
│   │   ├── answer_generation.py          # 现 answering 内容并入
│   │   ├── answer_projection.py          # 回答上下文投影（函数集）
│   │   ├── final_reply.py                # 最终回复组合（函数集）
│   │   ├── context/                      # 生成上下文（现 Generation* 家族归宿，见 §3.3）
│   │   │   ├── scope.py                  #   resolve_generation_scope()（函数）
│   │   │   ├── runtime_settings.py       #   resolve_runtime_settings()（函数）
│   │   │   ├── history.py                #   project_generation_history()（函数）
│   │   │   ├── schema_context.py         #   SchemaContextService（保留类：有 4 个端口依赖）
│   │   │   └── knowledge.py              #   GenerationContextService + 自定义提示词（并入）
│   │   └── ports.py                      # 各 *ModelClient / *PromptBuilder
│   ├── execution/                # 受控执行与结果
│   │   ├── guarded_query_service.py      # 现 query_service 改名 GuardedQueryService
│   │   ├── sql_permission.py
│   │   ├── result_projection.py          # 现 query_result_projection_service
│   │   ├── result_artifacts.py           # 现 result_artifact_service
│   │   └── ports.py                      # SQLExecutor（技术缝）/ ResultArtifactGateway（防腐）；权限策略直连 access_control 公开入口
│   └── __init__.py               # 迁移期兼容 re-export（带删除条件），新代码禁止使用
├── adapters/                     # 技术实现，按技术分组
│   ├── langchain/                #   各 ModelClient 实现 + 消息转换（现 13 个平铺文件重组）
│   ├── prompts/                  #   模板装配（吸收 apps/template 的生成器）
│   ├── embedding/                #   相关性排序（现 embedding_ranking）
│   ├── execution/                #   DatasourceQueryExecutor（替代 capabilities.SqlExecuteTool）
│   └── xpack/                    #   自定义提示词等 xpack 适配
├── repository/                   # 不变（接口在 services/*/ports.py 或此处，实现在 sqlmodel/）
│   └── sqlmodel/
├── models/
│   ├── orm/
│   │   ├── chat.py               #   Chat（拆 conversation.py）
│   │   ├── chat_record.py        #   ChatRecord + 枚举
│   │   └── chat_log.py           #   ChatLog
│   └── dto/                      # 子目录镜像 services 分包；streaming.py 放共享四件套
├── composition.py                # 唯一组合根（吸收包根 conversation.py / chat_record.py）
├── errors.py                     # ChatBIError + 子域错误 + 错误码常量
└── __init__.py                   # 领域公共面（≤40 符号）
```

要点说明：

- **6 个业务子域包**（conversation / understanding / planning / generation / execution / orchestration）对应问数流程的真实阶段，取代 37 文件平铺。任何人看目录即可回答"问题理解的代码在哪、执行边界在哪"。
- `answering` 不单独成包：回答生成本质是 LLM 生成能力，与 chart/analysis 并列放 `generation/`，其纯投影（answer_projection/final_reply）是函数模块。
- `models/dto` 镜像 services 分包，避免 28 个平铺 DTO 文件重演。
- `orchestration/` 与 `api/` 在 R4 阶段落位（见执行计划），目录先在方案中定死，避免中途再改。

## 3. 现有 37 个服务的处置映射

### 3.1 总表（37 → 16 个 Service 类 + 12 个函数模块）

| 现文件 | 处置 | 去处 / 新形态 |
| --- | --- | --- |
| conversation_service | 保留类 | conversation/ |
| chat_record_service | 保留类（拆出 record_limits 规则） | conversation/ |
| question_understanding_service | 保留类 | understanding/understanding_service |
| question_understanding_validation_service | **函数化** | understanding/validation.py |
| question_intent_projection_service | **函数化** | understanding/intent_projection.py |
| question_intent_fallback_service | 保留类（规则量大、内聚） | understanding/intent_projection.py 同模块 |
| question_intent_validation_service | **函数化，并入** | understanding/graph_contracts.py |
| question_input_projection_service | **函数化，并入** | understanding/graph_contracts.py |
| question_model_service | 保留类，改名 `StructuredModelService` | understanding/ports.py + adapters/langchain |
| question_understanding_prompt | 保留模块 | understanding/prompts.py |
| time_range | 保留模块 | understanding/time_range.py |
| datasource_selection_service | 保留类 | planning/datasource_selection.py |
| datasource_selection_candidate_service | **并入** datasource_selection 模块（独立类可保留） | planning/ |
| execution_binding_service | **函数化** | planning/execution_binding.py |
| semantic_retrieval_service | 保留类 | planning/ |
| semantic_query_service | 保留类，改名 `SemanticCompilationService` | planning/semantic_compilation.py |
| physical_schema_service | 保留类 | planning/ |
| sql_generation_service | 保留类 | generation/sql_generation.py |
| dynamic_sql_generation_service | **并入** sql_generation 模块（薄入口） | generation/ |
| permission_sql_generation_service | **并入** sql_generation 模块（薄入口） | generation/ |
| chart_generation_service | 保留类 | generation/ |
| analysis_prediction_service | 保留类 | generation/ |
| recommended_question_service | 保留类 | generation/ |
| answer_generation_service | 保留类 | generation/ |
| answer_projection_service | **函数化** | generation/answer_projection.py |
| final_reply_projection_service | **函数化** | generation/final_reply.py |
| generation_context_service | 保留类（吸收 custom_prompt） | generation/context/knowledge.py |
| generation_custom_prompt_service | **并入** 上行 | — |
| generation_context_scope_service | **函数化** | generation/context/scope.py |
| generation_runtime_settings_service | **函数化** | generation/context/runtime_settings.py |
| generation_history_projection_service | **函数化** | generation/context/history.py |
| generation_schema_context_service | 保留类，改名 `SchemaContextService` | generation/context/schema_context.py |
| query_service | 保留类，改名 `GuardedQueryService` | execution/guarded_query_service.py |
| sql_permission | 保留类 | execution/sql_permission.py |
| query_result_projection_service | 保留类（有大小边界联动） | execution/result_projection.py |
| result_artifact_service | 保留类 | execution/result_artifacts.py |

### 3.2 Question* 七兄弟的收敛逻辑

按"共享能力 vs 执行器专用契约"两轴归位：

- **共享能力**（Agent/Graph 都消费）：`understanding_service`（编排）、`validation.py`（确定性规则）、`intent_projection.py`（清洗/合并/降级）、`prompts.py`、`time_range.py`、`StructuredModelService`（模型调用边界）。
- **Graph 专用契约投影**：`graph_contracts.py` 一个模块收拢现 `question_input_projection` + `question_intent_validation` 两个服务的函数——它们都是"把共享结果投影成 Graph 节点 Schema"的纯映射，属于同一变化原因（Graph 契约变了才变）。
- 命名从此表达归属：模块名带 `graph_` 的是 Graph 专用；不带的是共享。7 个同前缀服务 → 1 个服务类 + 1 个模型端口类 + 4 个函数/规则模块。

### 3.3 Generation* 家族的收敛逻辑

`Generation` 前缀歧义（D1）通过**包位置**消除而不是靠更长的类名：全部移入 `generation/context/`，模块名直接说事（scope / runtime_settings / history / schema_context / knowledge）。三个 `*Context*` 服务从此变成：`resolve_generation_scope()` 函数、`SchemaContextService`、`GenerationContextService`（知识上下文，吸收自定义提示词开关）——名字、位置、职责一一对应。

## 4. 统一流式生成骨架（治 C3/C4）

### 4.1 共享 DTO（`models/dto/streaming.py`）

```python
@dataclass(frozen=True, slots=True)
class ModelMessage:            # 取代 5 套 {X}Message
    role: str
    content: str
    system_context: bool = False

@dataclass(frozen=True, slots=True)
class ModelStreamChunk:        # 取代 5 套 {X}ModelChunk
    content: str = ""
    reasoning_content: str = ""
    token_usage: Mapping[str, int] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class GenerationEvent(Generic[R]):   # 取代 5 套 {X}Event；R 为各能力解析结果类型
    kind: Literal["chunk", "completed"]
    content: str = ""
    reasoning_content: str = ""
    result: R | None = None
    error: str | None = None
    token_usage: Mapping[str, int] = field(default_factory=dict)
```

### 4.2 统一执行骨架（`generation/streaming.py`）

```python
def run_generation(
    messages: list[ModelMessage],
    client: GenerationModelClient,          # 唯一 Client 端口（取代 6 个同构 Protocol）
    parse: Callable[[str], R],              # 各能力的解析函数（唯一真实差异点）
) -> Iterator[GenerationEvent[R]]:
    """累计正文/思考/token → yield chunk → 结束时 parse → yield completed。"""
```

各生成 Service 保留：输入校验（业务规则）+ 提示词端口调用 + `run_generation(...)` + 能力特有的结果后处理（如图表字段小写归一化、SQL 空校验）。**六个手写循环变成一个**；`DynamicSQLGenerationService` 的双重校验一类抄写噪声随之消失。

### 4.3 防过度抽象边界

骨架只统一"流消费循环"这一无业务语义的部分。以下内容明确**不**进骨架：提示词构造、解析规则、事件到 SSE/工具载荷的投影、错误分类。若未来出现第 7 种生成能力需要不同的流形态（如工具调用流），另立骨架而不是给 `run_generation` 加参数。

## 5. 端口与适配器落位（治 A2/B2）

1. 先按 §1.2 准入判据处理存量端口：取消清单内的 1:1 包装端口（改为直连对方公开 Service），保留项移至各子域 `ports.py` 并改后缀。涉及改名的关键项：`RecommendedQuestionHistoryProvider → *Repository`（实现是本领域仓储）、`GenerationSchemaTableRanker → SchemaRankingClient`、`GenerationCustomPromptProvider → GenerationCustomPromptClient`（xpack 技术缝）。
2. `adapters/` 从 13 个平铺文件改为按技术分组（langchain / prompts / embedding / execution / xpack）。
3. **解除对 `apps.capabilities` 的依赖**：在 `adapters/execution/` 新建 `DatasourceQueryExecutor` 实现 `SQLExecutor` 端口（内部直接调用 Datasource 连接服务），`composition.py` 改用之；`ToolResult` 不再出现在新链路。`capabilities/sql/validator|permission|repair` 中仍被引用的实现同批归位（validator 已属 ChatBI SQL 能力 → execution/；repair 若仅 Agent 用 → orchestration/agent）。
4. **吸收 `apps/template`**：模板文件与生成器迁入 `chatbi/adapters/prompts/`（问题理解的共享提示词规则仍在 `understanding/prompts.py`，两者分工写入 AGENTS.md：*规则在 services、渲染在 adapters*）。`template` 目录随 R4 删除。

## 6. 领域收拢与入口统一（治 A1/A3）

1. `chatbi/api/` 按 §2 建立；`apps/api.py` 只注册 chatbi 一个问数 router（旧 `/chat`、`/chat/agent`、`/graph` 路径由 chatbi/api 内部路由保持兼容）。
2. `apps/agent → chatbi/orchestration/agent`、`apps/workflow → chatbi/orchestration/graph`，迁移时同步执行内部最小分层（agent 的 ORM 入 `chatbi/models/orm/agent_run.py`，`crud.py` 职责入仓储；graph 的 1,483 行 `question.py` 适配器按节点拆分）。
3. `workflow_gateway.py` 依赖倒置：R5 已完成。引擎声明
   `WorkflowApiExtension`，ChatBI 的实现位于 `orchestration/graph/api_extension.py`
   并由 `apps/api.py` 注册；旧模块已删除。
4. 平台配置从 `system` 剥离：新落地的参数 Service/Repository 迁至 `apps/platform_config/`（或 `common/platform_config/`，执行计划里定夺），`system` 目录进入只减不增状态，P8 删除。

## 7. legacy 收口的目标形态（治 E5）

`apps/chat` 的终局：

| 现文件 | 终局 |
| --- | --- |
| `task/llm.py`（1,724 行） | 拆为 `chatbi/api/legacy_sse.py`（协议转换 ≤300 行）+ `chatbi/services/…` 已有能力的编排调用；`run_task` 的流程控制改写为对子域 Service 的顺序调用 |
| `task/legacy_dependencies.py` | 模型运行时 → `ai_model` 公开运行能力；外部助手 Schema → orchestration 边界的显式 legacy 适配（随旧外部流程删除）；其余删除 |
| `task/legacy_adapter.py`（SSE 编码） | 并入 `legacy_sse.py` |
| `api/chat.py` | 路由声明迁 `chatbi/api/conversations.py` + `legacy_sse.py` |
| `curd/chat.py`（纯转发） | 调用方切换后删除 |
| `services/deletion.py`、`semantic_binding.py` | 迁 `chatbi/services/conversation/` 并解决其 ORM 跨域依赖（基线在案） |
| `models/chat_model.py`（兼容导出） | 登记台账，按删除条件清除 |

完成判据（可测）：`apps/chat` 目录删除；`grep -r "legacy" apps/chatbi` 仅剩 `legacy_sse.py` 与台账内条目。

## 8. AGENTS.md v2 修订要点（治 E6/E7）

**总纲改制**：文档从"领域通用架构规则"（单一模板套所有模块）改为**分级规则**——第一层是全局边界四条（所有权唯一、跨域走公开契约、依赖单向、基线棘轮）；第二层是 §1.0 的子域风格分级表（仪式上限）；第三层才是各风格下的具体结构规则。现有 10 节内容归入第三层"完整战术分层"风格的规则，仅对 semantic、access_control 全量适用。

在此基础上新增/修订：

1. **新增"端口与适配器"节**：端口准入判据（§1.2 三条：技术缝/防腐/仓储，默认直连对方公开 Service）；后缀语义表；实现位置（`repository/<tech>/` 管持久化，`adapters/<tech>/` 管其余技术设施）。
2. **新增"组合根"节**：每领域唯一 `composition.py`；`build_*` 只许出现在 composition 与 adapters；API 层通过 composition 组装。
3. **新增"编排层"节**：`orchestration/` 的准入条件（同一领域内存在多种执行方式时）、其只能调用本领域 Service 的约束。
4. **修订"services"节**：加入 §1.1 类/函数判据；明确"投影/校验/规则默认是函数"（管道型子域尤其如此）；允许并鼓励按子域分包，单目录 Python 文件数给出软上限（建议 12）。
5. **修订"models/dto"节**：加入 §1.3 后缀语义表；禁止大桶 `__init__` 无差别导出，公共契约集中领域 `__init__.py`。
6. **修订"errors"节**：错误码常量注册表要求。
7. **新增"守卫测试"节**：依赖/结构规则表驱动集中维护（一个规则文件 + 数据表），禁止按批次新增守卫文件。
8. **新增"提示词"节**：规则在 services、模板与渲染在 adapters/prompts。

## 9. 治理配套（治 E1–E4、A4）

1. **计划/日志分离**：`DDD_MIGRATION_PLAN.md` 收缩为 ≤400 行的现行计划（目标、阶段、剩余任务、验收）；43 批日志整体移入新建 `DDD_MIGRATION_CHANGELOG.md`（append-only）。每阶段在计划中只保留一个状态行 + 剩余任务清单。
2. **兼容台账**：新建 `COMPAT_LEDGER.md`（或计划附录）：每条兼容入口一行——路径、调用方、删除条件、目标阶段。CI 守卫按台账检查"台账外禁止新增兼容导出"。A4 的 71 处存量在 R1 建账时一次清点。
3. **批次单位改革**：批次单位从"旧函数"改为"目标结构中的一个接缝"（一个子域包、一个骨架、一份台账清偿）。每批验收增加结构检查项：无新增单方法无状态类、端口进 ports.py、错误进 errors.py、DTO 后缀合规。
4. **守卫测试整合**：24 个逐批守卫文件合并为 `tests/architecture/test_structure_rules.py`（表驱动：规则 = 数据行）+ 既有 `test_dependency_baseline.py`。规则数量不减，文件数量收敛为 2–3 个。
5. **回归策略分层**：结构性重组批（不改行为）跑定向 + 架构 + 冒烟；只有触及行为/契约的批才跑全量 1,217 项。打破 E4 的成本循环。

## 10. 明确不做的事（Non-goals）

1. 不合并 Graph 与 Agent 的运行机制，不重写 Graph 编排逻辑（只搬位置、拆大文件）。
2. 本轮不改数据库表名（`headless_*` 等留 P8 单独评审），不新增任何 Alembic 迁移。
3. 不动已收敛的跨领域契约（Semantic/Retrieval/Datasource/Access Control 公开 Service 签名不变）。
4. 不追求"每个 DTO 都完美命名"的一次性大改名：只改 §1.3 冲突项与 §3 涉及模块，其余随后续批次自然收敛。
5. 不引入新的技术组件（无事件总线、无 DI 框架）；组合仍是手写工厂。
6. **不追求全项目战术 DDD 合规**（§1.0）：semantic、access_control 既有完整分层保持不动（不反向拆除仓储抽象）；CRUD 类子域不再补层；chatbi 不再以"像一个 DDD 领域"为目标。

## 11. 方案效果预估

| 维度 | 现状 | 目标 |
| --- | --- | --- |
| chatbi services 结构 | 37 文件平铺 | 6 子域包，16 个 Service 类 + 12 个函数模块 |
| 流式 DTO | 5 套 × 4 类同构 | 1 套共享 + 各能力 Result |
| 生成循环实现 | 6 份手写 | 1 个 `run_generation` |
| 端口定义位置/后缀 | 36 个内联 / 9 种后缀 | 各包 ports.py / 4 种后缀 |
| 跨域 1:1 包装端口 | ≥6 个（检索/编译/策略/元数据等） | 0（直连对方公开 Service） |
| 错误定义 | 散落 10+ 文件、裸字符串码 | errors.py + 错误码常量 |
| 组装 | composition + 3 个游离模块 | 唯一 composition.py |
| 公共面 | ~130 符号大桶 | 领域 `__init__` ≤40 符号 |
| 问数入口 | 3 处路由 | chatbi/api 一处（含兼容路径） |
| ChatBI 顶级目录占用 | 5 个 | 1 个（chatbi），legacy 删除后 |
| 守卫测试文件 | 24 个 | 2–3 个（规则表驱动） |
| 计划文档 | 2,030 行混合日志 | ≤400 行计划 + 独立 changelog |

分阶段落地顺序、每步验收与风险控制见文档三（执行计划）。
