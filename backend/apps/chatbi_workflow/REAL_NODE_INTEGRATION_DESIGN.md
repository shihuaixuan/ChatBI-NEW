# ChatBI v1 真实节点能力接入设计

本文档用于真实节点能力接入前的技术分析。目标不是重写图运行时，而是在现有 `chatbi/v1` 图已经跑通的基础上，说明每个节点当前实现程度、可复用的旧能力、真实逻辑设计、迁移顺序和验收标准。

## 1. 当前结论

`chatbi/v1` 当前已经完成的是图架构和流程闭环：

- 图定义、节点、边、路由条件已经声明。
- API 可以选择 `definition_version="v1"` 并同步执行。
- 交互节点可以进入 `waiting_input`，API 回答后可以 `resume` 并继续执行。
- Trace API 可以展示节点状态、路由原因、脱敏输出。
- `node_execution` 已记录节点状态、输入摘要、输出摘要、路由摘要。
- 节点输出已有 Pydantic schema 校验。

真实业务能力已经进入分批接入阶段。当前需要区分两类节点：

```text
ChatBIV1CapabilityNode
  -> RealChatBICapabilityGateway.invoke(...)
      -> 已接入真实 adapter 的节点：直接执行真实 adapter
      -> 尚未接入真实 adapter 的节点：临时 fallback 到 PlaceholderChatBICapabilityGateway
  -> schema 校验
  -> 写入 variables.*
```

重要边界：

- 已接入真实 adapter 的节点，执行异常必须暴露为节点失败，不能再 fallback 到 placeholder。
- 只有尚未接入真实 adapter 的节点，才允许临时使用 placeholder 保持图闭环。
- `knowledge.retrieve` 已接入 Headless adapter，因此不能再和 placeholder knowledge 兜底连在一起。

也就是说，当前节点具备稳定协议和可观察性，真实能力会逐个替换 gateway 后面的 adapter；替换完成的节点要保持真实失败语义，避免 trace 看起来成功但实际使用了假数据。

## 2. 当前 v1 图节点实现程度

| v1 节点 | handler | 当前实现程度 | 真实能力状态 |
| --- | --- | --- | --- |
| `classify_question` | `question.classify` | 已接入 `QuestionAdapter`：大模型结构化分类，模型异常直接节点失败 | 当前使用默认大模型配置，输出 `category/reason/risk_level/confidence`；失败时不继续图执行 |
| `reject_answer` | `answer.reject` | 已接入 `AnswerAdapter`：大模型生成安全拒绝回复，失败时稳定降级 | 后续可细化权限/安全原因映射 |
| `chitchat_answer` | `answer.chitchat` | 已接入 `AnswerAdapter`：大模型生成闲聊引导回复，失败时稳定降级 | 后续可补模板兜底和产品能力介绍口径 |
| `rewrite_question` | `question.rewrite` | 已接入 `QuestionAdapter`：大模型结构化重写，失败时保留原问题；明显澄清场景保留 waiting_input 兜底 | 后续可接 `QueryUnderstandingService` 的 normalized question、confirmed slots |
| `ask_rewrite_clarification` | `interaction.ask_rewrite_clarification` | 已接入 `InteractionAdapter`：根据缺失槽位生成澄清 prompt/options/schema | 优先使用 `knowledge.candidate_groups`；没有 knowledge 时可轻量加载 Headless schema；失败时回退内置示例 |
| `draw_image_profile` | `question.draw_image_profile` | 固定图表候选 | 需要基于 intent、metric、dimension、result schema 推断展示类型 |
| `recognize_intent` | `intent.recognize` | 已接入 `QuestionAdapter`：大模型结构化意图识别，失败时规则兜底 | 后续可接 `QueryUnderstandingService` 的 intent、confidence、slot issues |
| `ask_intent_clarification` | `interaction.ask_intent_clarification` | 已接入 `InteractionAdapter`：根据低置信度/歧义/冲突生成意图澄清选项 | 后续可根据数据集能力动态裁剪意图选项 |
| `retrieve_knowledge` | `knowledge.retrieve` | 已接入 `HeadlessKnowledgeAdapter`：基于 `dataset_id` 加载 Headless schema，做 schema mapper + asset document top-k 检索 + `CandidateGate` 判定 | 已能输出真实候选、slot bindings、metric ambiguity；异常不再 fallback 到 placeholder |
| `ask_metric_selection` | `interaction.ask_metric_selection` | 已接入 `InteractionAdapter`：从 knowledge ambiguity 或 candidate groups 生成指标选择项 | 已和 interaction response patcher 打通，用户选择可合并回 knowledge |
| `generate_sql` | `sql.generate` | 已接入 `SqlAdapter.generate()`：基于 `dataset_id` 加载 Headless schema，并复用 `SemanticSQLCompiler` 生成 SQL | 后续补 SQL validator、fallback schema generator、生成失败分支 |
| `execute_sql` | `sql.execute` | 已接入 `SqlAdapter.execute()`：复用 `SqlExecuteTool` 执行 SQL，并只保留 sample rows、row_count、fields | 后续补真实 artifact 存储和更细错误分类 |
| `handle_sql_error` | `sql.handle_error` | 已接入 `SqlAdapter.handle_error()`：归一化 SQL 执行错误，并通过 `SQLRepairStrategy` 输出 repair hint/plan | `regenerate_sql` 计划已通过图边回到 `generate_sql`，由 `max_loop_iterations` 控制重试上限 |
| `generate_question_answer` | `answer.generate` | 已接入 `AnswerAdapter`：大模型生成业务回复，失败时稳定降级 | 后续可补 result artifact/sample rows 的更细粒度摘要 |
| `recommend_questions` | `question.recommend` | 已接入 `RecommendationAdapter`：基于已选指标/维度和执行状态生成上下文追问 | 当前为规则版，后续可接 LLM 或推荐策略服务 |
| `compose_final_reply` | `answer.compose` | 已接入 `AnswerAdapter.compose()`：本地汇总 answer/recommendations/chart | 作为稳定前端响应 composer 保留，不依赖 LLM |
| `finish` | `answer.finish` | 标记 completed | 已完成，无需接业务能力 |

## 3. 可复用旧能力盘点

### 3.1 `agentic_chat` 可复用能力

| 旧模块 | 当前能力 | 适合迁移到 |
| --- | --- | --- |
| `tools/query_understanding.py` | 组装候选、调用 `QueryUnderstandingService` | `QuestionAdapter` |
| `services/query_understanding.py` | 规则 + 可选 LLM 的意图、槽位、歧义、冲突识别 | `question.rewrite`、`intent.recognize` |
| `tools/schema.py` | 按 datasource 读取 checked table/field | `KnowledgeAdapter` |
| `tools/semantic_asset.py` | 当前是空骨架，返回 degraded | 不建议直接复用，应改用 semantic service |
| `tools/terminology.py` | 当前是空骨架 | 后续可接术语表后并入 KnowledgeAdapter |
| `tools/sql_example.py` | 当前是空骨架 | 后续接 SQL 示例库后并入 KnowledgeAdapter |
| `tools/semantic_sql_compiler.py` | 基于 Headless dataset 编译 SQL | `SqlAdapter.generate` 的优先策略 |
| `tools/sql_generator.py` | 基于 allowed tables 的保守 SQL 生成骨架 | `SqlAdapter.generate` 的 fallback |
| `tools/sql_validator.py` | 只读 SQL、多语句、安全关键词、allowed tables 校验 | `SqlAdapter.generate` 后置校验 |
| `tools/permission.py` | 当前透传 SQL，预留权限改写位置 | 已由 `PermissionAdapter` 接入 `SqlAdapter.execute` 前置校验 |
| `tools/sql_executor.py` | 获取 datasource 并执行 SQL | `SqlAdapter.execute` |
| `tools/answer_generator.py` | 基于执行摘要生成简单回答 | `AnswerAdapter.generate` |
| `orchestrator.py` | 旧线性编排、clarification、public summary | 只作为迁移参考，不建议直接嵌入 v1 图 |

### 3.2 `semantic` 可复用能力

| 语义模块 | 当前能力 | 适合迁移到 |
| --- | --- | --- |
| `semantic/services/semantic_search.py` | 检索已审核 metric/dimension，支持 alias/embedding 混合打分 | `KnowledgeAdapter.retrieve` |
| `semantic/services/semantic_context.py` | 将语义资产拼成 prompt context | `SqlAdapter.generate` 的上下文输入 |
| `semantic/assets/runtime_service.py` | 加载 dataset profile、asset documents、candidate groups | `KnowledgeAdapter.retrieve` 的主数据源 |
| `semantic/assets/document_builder.py` | 构建 metric/dimension/term/example/field 文档 | `KnowledgeAdapter.retrieve` |

`KnowledgeAdapter` 不建议只复用旧 `SemanticAssetTool`，因为它目前只是 P1 空实现。更合理的是直接接入 `RuntimeAssetService` 与 `retrieve_semantic_assets()`。

## 4. 推荐接入架构

推荐采用 Adapter-first，而不是把旧 `AgenticOrchestrator` 放进某个节点。

```text
ChatBIV1CapabilityNode
  -> RealChatBICapabilityGateway
      -> QuestionAdapter
      -> KnowledgeAdapter
      -> SqlAdapter
      -> AnswerAdapter
      -> RecommendationAdapter
```

### 4.1 为什么不直接复用旧 orchestrator

旧 `AgenticOrchestrator` 已经包含 planner、executor、clarification、step、SSE、record 写入等编排职责。如果直接套到图节点里，会形成：

```text
GraphRuntime -> v1 node -> AgenticOrchestrator -> planner/executor/tool chain
```

这会让图运行时失去节点级可观测性，也会让 retry、resume、trace 的责任边界变混乱。旧 orchestrator 应作为迁移参考，真实能力应下沉到 adapter。

### 4.2 Gateway 设计

新增：

```text
backend/apps/chatbi_workflow/capabilities/real.py
backend/apps/chatbi_workflow/capabilities/adapters/question.py
backend/apps/chatbi_workflow/capabilities/adapters/knowledge.py
backend/apps/chatbi_workflow/capabilities/adapters/sql.py
backend/apps/chatbi_workflow/capabilities/adapters/answer.py
backend/apps/chatbi_workflow/capabilities/adapters/recommendation.py
```

`RealChatBICapabilityGateway.invoke()` 只做能力分发和临时能力兜底：

```text
question.classify -> QuestionAdapter.classify
question.rewrite -> QuestionAdapter.rewrite
intent.recognize -> QuestionAdapter.recognize_intent
knowledge.retrieve -> KnowledgeAdapter.retrieve
sql.generate -> SqlAdapter.generate
sql.execute -> SqlAdapter.execute
sql.handle_error -> SqlAdapter.handle_error
answer.* -> AnswerAdapter
question.recommend -> RecommendationAdapter
```

规则：

- 已接入 adapter 的能力，例如 `question.classify`、`question.rewrite`、`intent.recognize`、`knowledge.retrieve`、`answer.*`，由真实 adapter 负责成功或失败。
- 已接入 adapter 的能力如果抛异常，应该由 `ChatBIV1CapabilityNode` 转换成节点失败，而不是在 gateway 内吞掉异常。
- 尚未接入的能力，才允许 fallback 到 `PlaceholderChatBICapabilityGateway`；`sql.*`、`question.recommend` 已接入真实 adapter，不应再 fallback。
- `build_real_chatbi_v1_runtime(session)` 必须给依赖数据库的 adapter 注入 session，例如 `HeadlessKnowledgeAdapter(schema_builder=HeadlessSchemaBuilder(session))`。

所有 adapter 输出必须符合 `schemas/v1.py` 中的 output model。这样真实能力上线时，不会污染图上下文结构。

## 5. 每个真实节点的逻辑设计

### 5.1 `classify_question`

职责：在重写和知识检索前，快速判断问题是否可进入问数链路。

输入：

- `question`
- `tenant_id`
- `user_id`
- `datasource_id`
- `conversation_context`

输出：

- `category`: `forbidden | chitchat | data | followup`
- `reason`
- `risk_level`
- `confidence`

真实逻辑：

1. 由 `QuestionAdapter.classify()` 调用大模型做主分类。
2. prompt 明确限定节点职责：
   - 只做问题分类。
   - 不回答用户问题。
   - 不生成 SQL。
   - 不解释业务指标。
3. prompt 强制模型只返回 JSON 对象，字段固定为：

   ```json
   {
     "category": "forbidden | chitchat | data | followup",
     "reason": "不超过 40 个中文字符的分类原因",
     "risk_level": "low | medium | high",
     "confidence": 0.0
   }
   ```

4. 分类语义：
   - `forbidden`：越权、绕过权限、危险操作、请求访问无授权数据、明显不应进入问数链路的问题。
   - `chitchat`：问候、闲聊、能力咨询、非业务数据分析问题。
   - `data`：完整的业务数据分析、统计、查询、趋势、排名、对比、归因问题。
   - `followup`：依赖上文才能理解的追问，例如“那上个月呢”“按地区看一下”“继续分析利润”。
5. 稳定性要求：
   - `category` 只能取 `forbidden/chitchat/data/followup`。
   - `risk_level` 只能取 `low/medium/high`。
   - `confidence` 必须在 0 到 1 之间。
   - 不确定但像业务数据问题时，优先进入 `data`，保证流程继续推进。
   - 不确定但明显依赖上文时，优先进入 `followup`。

当前落地文件：

- `capabilities/adapters/question.py`
  - `build_question_classification_prompt()`
  - `DefaultQuestionClassificationModelClient`
  - `QuestionAdapter.classify()`
- `capabilities/real.py`
  - `RealChatBICapabilityGateway`
- `runtime.py`
  - `build_real_chatbi_v1_runtime()`

当前 API 行为：

- `definition_version="v1"` 使用 `build_real_chatbi_v1_runtime()`。
- `question.classify`、`question.rewrite`、`intent.recognize`、`knowledge.retrieve`、`answer.*` 走真实 adapter。
- `knowledge.retrieve` 依赖 DB session，runtime 必须注入 `HeadlessSchemaBuilder(session)`。
- 已接入真实 adapter 的节点异常会导致节点失败，不再回退到 placeholder。
- `sql.generate`、`sql.execute`、`question.recommend` 已接入真实 adapter；这些节点异常会作为真实节点失败暴露。

复用能力：

- 当前直接复用项目默认大模型配置：
  - `get_default_config()`
  - `LLMFactory.create_llm(config).llm`
- 当前环境默认模型记录为：
  - 名称：`DeepSeek`
  - 模型：`deepseek-v4-flash`
  - 协议：OpenAI 兼容协议
- `QueryUnderstandingService` 暂不作为分类主链路，后续可用于 `rewrite_question` 和 `intent.recognize`。

失败处理：

- 空问题 -> `forbidden / empty_question / medium / confidence=1.0`
- 缺 dataset_id -> `forbidden / missing_dataset / medium / confidence=1.0`
- 模型调用失败 -> 抛出 `CLASSIFICATION_MODEL_CALL_FAILED`，由 `ChatBIV1CapabilityNode` 标记 `classify_question` 失败并中断 run。
- 模型输出不是 JSON、字段非法、枚举越界、置信度越界 -> 抛出 `CLASSIFICATION_MODEL_OUTPUT_INVALID`，由 `ChatBIV1CapabilityNode` 标记 `classify_question` 失败并中断 run。
- 解析层支持从模型误输出的 Markdown 包裹文本中提取第一个 JSON 对象。

实测样例：

```json
{"question": "你好", "classification": {"category": "chitchat", "reason": "问候性对话，非业务数据分析问题", "risk_level": "low", "confidence": 1.0}}
```

```json
{"question": "今日的访问量", "classification": {"category": "data", "reason": "用户询问今日访问量，属于标准数据查询", "risk_level": "low", "confidence": 0.95}}
```

测试覆盖：

- prompt 约束模型只做分类、只输出稳定 JSON。
- 正常 JSON 输出解析。
- Markdown 包裹 JSON 输出解析。
- 非法模型输出会让分类节点失败，不进入后续数据链路。
- 模型调用失败会让分类节点失败，不进入后续数据链路。
- `RealChatBICapabilityGateway` 对已接入真实 adapter 的能力直接调用真实实现；未接入能力才继续 fallback。
- 已接入真实 adapter 的能力异常不会 fallback 到 placeholder。

### 5.2 `reject_answer`

职责：把禁止/越权原因转成用户可读回复。

真实逻辑：

- 读取 `variables.classification.reason`。
- 对权限、危险 SQL、越权数据、无 datasource 等原因生成不同文案。
- 不暴露内部错误、SQL、权限规则细节。

输出写入：

- `variables.answer`

### 5.3 `chitchat_answer`

职责：回答非数据闲聊，但保持 ChatBI 定位。

真实逻辑：

- 已由 `AnswerAdapter.chitchat()` 接入大模型。
- prompt 要求只生成用户可读回复，不输出 Markdown 代码块，不泄露内部变量、trace、SQL 原文或权限规则细节。
- 输出必须符合 `AnswerOutput`：
  - `answer`
  - `warnings`
  - `render_type`
  - `citations`
- 模型失败时返回固定引导文案：“你好，我可以帮你分析业务数据问题。”
- 闲聊分支不进入知识检索和 SQL。

### 5.4 `rewrite_question`

职责：把用户原始问题和澄清回答合并成可理解、可检索的问题。

真实逻辑：

1. 已由 `QuestionAdapter.rewrite()` 接入大模型。
2. prompt 明确限定节点职责：
   - 只做问题重写。
   - 不回答用户问题。
   - 不生成 SQL。
   - 不解释业务指标。
3. prompt 强制模型只返回 JSON 对象，字段固定为：

   ```json
   {
     "rewritten_question": "补全上下文后的用户问题",
     "need_user_input": false,
     "missing_slots": [],
     "image_profile_hint": null
   }
   ```

4. 输入会合并：
   - 原始 `request.question`
   - `conversation_context`
   - `variables.rewrite_response`
5. 模型输出通过 `QuestionRewriteOutput` 校验后写入 `variables.rewrite`。
6. 模型失败或输出非法时：
   - 默认保留原问题继续推进。
   - 如果问题明显包含“需要澄清/信息不足/补充”，且用户还没有提供 `rewrite_response`，保留 waiting_input 兜底，`missing_slots=["metric"]`。

复用能力：

- 当前先复用默认大模型配置。
- 后续可接：
  - `QueryUnderstandingTool`
  - `QueryUnderstandingCandidateBuilder`
  - `QueryUnderstandingService`

注意：

- `rewrite_question` 不应该直接决定 SQL 策略。
- 用户澄清后的 confirmed slots 必须优先级高于模型重新识别结果。

### 5.5 `ask_rewrite_clarification`

职责：在问题信息不足时暂停 run，等待用户补充。

真实逻辑：

- 已由 `InteractionAdapter.ask_rewrite_clarification()` 接入。
- 根据 `variables.rewrite.missing_slots` 生成 prompt、options、response_schema。
- 当前支持的缺失槽位：
  - `metric` / `analysis_object`：优先从 `variables.knowledge.candidate_groups.metrics` 生成真实指标选项；无候选时回退访问人数、销售额、订单数等示例选项。
  - `time_range`：生成今天、最近 7 天、本月等时间选项。
  - `dimension`：优先从 `variables.knowledge.candidate_groups.dimensions` 生成真实维度选项；无候选时回退按日期、按店铺、按商品等示例选项。
- 如果尚未执行 `knowledge.retrieve`，真实 runtime 会给 `InteractionAdapter` 注入 `HeadlessSchemaBuilder(session)`，按 `request.dataset_id` 轻量加载 schema，并从 schema.metrics/schema.dimensions 生成候选。
- schema 加载失败不会让交互节点失败，会继续使用内置示例选项，保证澄清链路可用。
- 回答写入 `variables.rewrite_response`，恢复后回到 `rewrite_question`。

复用能力：

- 当前为本地规则版。
- 真实候选优先来自上游 `knowledge.retrieve` 已写入的 `candidate_groups`。
- 当尚未有 knowledge 上下文时，真实 runtime 允许交互节点通过 session-backed `HeadlessSchemaBuilder` 做轻量候选加载。

输出：

- `interaction_request` 表
- 用户回答写回 `variables.rewrite_response`

### 5.6 `draw_image_profile`

职责：提前生成图表/展示倾向，不参与关键路由。

真实逻辑：

- 如果 intent 是趋势：候选 `line`
- 如果是排行：候选 `bar`
- 如果是明细：候选 `table`
- 如果 SQL 结果字段包含时间字段：提高 `line` 优先级

输入来源：

- `variables.rewrite`
- `variables.intent`
- `variables.knowledge`

输出：

- `profile`
- `chart_candidates`

### 5.7 `recognize_intent`

职责：识别查询意图、自然语言槽位线索和槽位置信度，决定是否需要澄清。

真实逻辑：

- 已由 `QuestionAdapter.recognize_intent()` 接入大模型。
- prompt 明确限定节点职责：
  - 只做意图识别和自然语言槽位提取。
  - 不回答用户问题。
  - 不生成 SQL。
  - 不解释业务指标。
  - 不选择 Headless 资产 ID、`biz_name` 或数据库字段。
- prompt 强制模型只返回 JSON 对象，字段固定为：

  ```json
  {
    "intent_type": "metric_query",
    "confidence": 0.0,
    "metric_mentions": [],
    "dimension_mentions": [],
    "time_mentions": [],
    "filter_mentions": [],
    "required_slot_types": [],
    "query_shape": {},
    "ambiguous_slots": [],
    "conflict_slots": []
  }
  ```

- 新增字段边界：
  - `metric_mentions`：用户原话里的指标短语，例如“访问人数”“销售额”。
  - `dimension_mentions`：用户原话里的分组/维度短语，例如“店铺”“商品”。
  - `time_mentions`：用户原话里的时间短语，例如“今日”“最近 7 天”。
  - `filter_mentions`：用户原话里的过滤条件短语，只表达自然语言名称和值。
  - `required_slot_types`：后续必须确认的槽位，例如 `metric`、`dimension`、`time_range`、`time_dimension`。
  - `query_shape`：查询形态线索，例如聚合、分组、排序、limit、时间粒度。
- 这些字段只作为 `knowledge.retrieve` 的检索线索，不代表已经绑定到真实 Headless 资产。

- 当前支持的 `intent_type`：
  - `metric_query`
  - `trend_analysis`
  - `ranking_analysis`
  - `comparison_analysis`
  - `detail_query`
  - `share_analysis`
  - `anomaly_analysis`
  - `unknown`
- 模型输出通过 `IntentRecognitionOutput` 校验后写入 `variables.intent`。
- 模型失败或输出非法时使用轻量规则兜底：
  - 趋势/走势/按天/按月 -> `trend_analysis`
  - 最高/最低/TopN/排名 -> `ranking_analysis`
  - 同比/环比/对比 -> `comparison_analysis`
  - 占比/构成/比例 -> `share_analysis`
  - 异常/波动/原因 -> `anomaly_analysis`
  - 明细/列表/清单 -> `detail_query`
  - 模糊问题如“看一下情况” -> `unknown`，`ambiguous_slots=["metric"]`
  - 其他业务问题 -> `metric_query`
- 兜底逻辑也会尽量补充 `metric_mentions`、`dimension_mentions`、`time_mentions`、`required_slot_types` 和 `query_shape`，但仍不做资产确认。
- 规则明确命中时置信度为 `0.85`，避免被 `confidence < 0.8` 误路由到澄清；模糊问题置信度为 `0.4`。

路由影响：

- `confidence < 0.8` 或存在 `ambiguous_slots/conflict_slots` -> `ask_intent_clarification`
- 否则进入 `retrieve_knowledge`

### 5.8 `ask_intent_clarification`

职责：处理意图低置信度、冲突、歧义。

真实逻辑：

- 已由 `InteractionAdapter.ask_intent_clarification()` 接入。
- 优先处理 `conflict_slots`，提示用户选择优先分析方式。
- 再处理 `ambiguous_slots`，当包含 `metric` 时提示确认指标或分析方式。
- 低置信度场景给出固定意图选项：
  - 查指标数值
  - 看趋势
  - 看排名
  - 看对比
  - 看明细

输出：

- 用户回答写回 `variables.intent_response`

### 5.9 `retrieve_knowledge`

职责：根据意图节点输出的自然语言线索确认 Headless 语义资产，并收集生成 SQL 所需的业务知识、schema 和例子。

当前已落地逻辑：

1. 从请求读取：
   - `request.dataset_id`
   - `request.tenant_id` / `request.oid`
   - `variables.rewrite.rewritten_question`
   - `variables.intent`
2. 使用 `HeadlessSchemaBuilder(session).build_dataset_schema(oid, dataset_id)` 加载最新 Headless dataset schema。
3. 优先读取 `variables.intent` 的自然语言槽位：
   - `metric_mentions` 只召回 metrics。
   - `dimension_mentions` 只召回 dimensions。
   - `filter_mentions` 召回 values / dimensions。
   - `time_mentions` 召回时间相关 dimensions / values，并补充默认时间维度候选。
4. `variables.rewrite.rewritten_question` 只做补漏：当意图要求的槽位没有候选时，再用整句 fallback 补充对应候选，不再作为主检索文本。
5. 每个 mention 内部使用两类召回：
   - `HeadlessSchemaMapper.map_schema(text, schema)` 做 schema name/alias 匹配。
   - `HeadlessAssetDocumentBuilder.build_from_schema()` 构建运行时 asset documents，并用 `HeadlessDocumentRetriever` 做轻量 top-k 检索。
6. 合并 schema mapper 和 asset document 两类候选。
7. 在 `CandidateGate` 前执行 slot-aware rerank：
   - 完整短语命中强加分，例如“访问人数”完整命中指标名或别名。
   - 关键词覆盖加分，例如同时覆盖“访问”和“人数”高于只覆盖“人数”。
   - 缺少完整短语时降权，避免“关注人数/转化人数”仅因包含“人数”打平。
   - 字段权重区分 `name/alias/description/biz_name/field`。
   - 输出 `base_score`、`rerank_strategy`、`rerank_reason`，便于 trace 排查。
8. 使用 `CandidateGate` 判定：
   - `hit`
   - `missed`
   - `metric_ambiguous`
9. 输出写入 `variables.knowledge`，包括：
   - `dataset_id`
   - `schema_version`
   - `index_version`
   - `candidate_groups`
   - `selected_assets`
   - `slot_bindings`
   - `decision`
   - `ambiguities`

后续增强逻辑：

1. 用 `RuntimeAssetService.load_runtime_schema()` 获取运行时资产：
   - metrics
   - dimensions
   - terms
   - examples
   - fields
2. 接入 BM25 / embedding / hybrid score，替换或增强当前规则召回与 rerank。
3. 用 `SchemaTool` 获取 checked tables/fields，作为 SQL 安全边界。
4. 继续增强合并输出：
   - `tables`
   - `fields`
   - `metrics`
   - `terms`
   - `examples`
   - `ambiguities`

路由：

- 无 checked schema 且无语义资产 -> `knowledge.missed`
- 多个高分 metric 且差距小 -> `knowledge.metric_ambiguous`
- 有可用 schema/metric -> `knowledge.hit`

注意：

- `knowledge.retrieve` 不生成 SQL。
- allowed tables 应显式写入 knowledge，供 SQL 校验使用。
- 当前如果真实 Headless schema 加载、检索或输出校验失败，应让节点失败，不允许回退到 placeholder knowledge。
- 真实数据集可能因为多个指标分数接近而进入 `ask_metric_selection`，例如多个指标都包含“人数”时，这是有效业务歧义，不是执行失败。
- 当前已完成第一阶段可解释 rerank：当 mention 足够具体时优先绑定完整短语匹配的资产；当 mention 只有“人数/次数/率”等弱词时，仍保留 `metric_ambiguous` 让用户选择。

### 5.10 `ask_metric_selection`

职责：在指标候选歧义时让用户选择。

真实逻辑：

- 已由 `InteractionAdapter.ask_metric_selection()` 接入。
- 从 `variables.knowledge.ambiguities` 生成 options，优先使用 `display_name/name/biz_name/title` 展示，值使用 `asset_id/biz_name`。
- 当 ambiguity candidates 为空但 `variables.knowledge.candidate_groups.metrics` 有候选时，回退使用 candidate groups 前 5 个指标候选。
- 回答写入 `variables.metric_selection`。
- 恢复时通过 ChatBI v1 的 interaction response patcher 把用户选择合并回 `variables.knowledge`：
  - `status` 改为 `hit`，`ambiguities` 清空。
  - `metrics` 改为用户确认的单一指标。
  - `selected_assets.metrics` 写入用户确认的指标资产。
  - `slot_bindings.metrics` 写入 `source=user_selected`、`confidence=1.0` 的绑定结果。
  - `decision.status` 改为 `user_selected`。
- 恢复后直接进入 `generate_sql`，不必重新检索知识，除非用户选择了“其他，请补充”。

### 5.11 `generate_sql`

职责：只生成 SQL，不执行 SQL。

当前已实现：

1. `SqlAdapter.generate()` 从 v1 request 中读取 `dataset_id`、`tenant_id/oid`、重写问题和 `variables.knowledge`。
2. 通过 `HeadlessSchemaBuilder.build_dataset_schema()` 加载 Headless dataset schema。
3. 从 `knowledge.slot_bindings` 和 `knowledge.selected_assets` 抽取 metric/dimension asset id。
4. 如果上一轮 `sql_error.repair_plan.action=regenerate_sql`，从 `variables.sql_error` 和 `variables.sql` 构造 `repair_context`，包含错误码、错误信息、失败 SQL、候选表/字段。
5. 调用 `SemanticSQLCompiler` 生成 SQL，并把 `repair_context` 传入 `SemanticSQLCompileRequest`。
6. `SemanticSQLCompiler` 已消费 `candidate_tables`：当原 metric/dimension/filter 落在失败表上时，会切换到候选表上同名或同 `biz_name` 的语义资产。
7. 如果重试生成的 SQL 与上一轮失败 SQL 规范化后一致，抛出 `SQL_REPAIR_REGENERATED_SAME_SQL`，避免重复执行同一条失败 SQL。
8. 调用 `SqlValidateTool` 做只读 SQL、多语句、安全关键词、allowed tables 校验，并统一补 `limit`。
9. 输出 `strategy=semantic_sql_compiler`、`datasource_id` 和 `used_assets`。

未完成：

- fallback `SqlGenerateTool`：基于 checked tables 和 schema evidence 生成保守 SQL。
- SQL 生成失败后的图分支：当前 `sql.generate` 抛错会让节点失败，后续可新增 SQL 生成失败条件或转入 `handle_sql_error`。
- `SemanticSQLCompiler` 当前已支持基于候选表切换同名资产；后续可继续消费 `candidate_fields`，并扩展多表 join 修复策略。

输出：

- `sql`
- `strategy`
- `datasource_id`
- `explanation`
- `used_assets`

注意：

- 当前 `generate_sql` 只做语义编译和 SQL 安全校验，不执行真实 SQL，也没有替代 SQL 生成策略。

### 5.12 `execute_sql`

职责：执行已生成的 SQL，并把执行结果标准化为 v1 图上下文。

复用能力：

- `SqlExecuteTool`
- `apps.db.db.exec_sql`

真实逻辑：

- 从 `variables.sql.sql` 和 `variables.sql.datasource_id` 读取执行上下文。
- 调用 `SqlExecuteTool`，复用项目现有 datasource 查询与 `apps.db.db.exec_sql`。
- 成功时输出 `status=succeeded`、sample `rows`、`row_count`、`fields`、`execution_ms`。
- 失败时输出 `status=failed`、`error_code`、`message`，让 v1 图路由到 `handle_sql_error`。
- `rows` 只保留前 `sample_row_limit` 条；`row_count` 保留真实结果行数。
- 输出 `sampled_row_count`、`result_truncated` 和 `artifact_ref` 协议字段；真实 artifact 存储后续接入。

输出：

- `status`
- `rows` 或 sample rows
- `row_count`
- `fields`
- `execution_ms`
- `artifact_ref`
- `sampled_row_count`
- `result_truncated`
- 失败时 `error_code/message`

### 5.13 `handle_sql_error`

职责：把 SQL 执行失败转为用户可读解释，并给出稳定修复建议。

复用能力：

- `strategies/sql_repair.py` 已接入保守修复策略。
- 可复用 validator 的 error code 和 executor 的 exception message。

真实逻辑：

- 从 `variables.sql_execution` 读取 `error_code` 和 `message`。
- 从 `variables.sql.sql` 和 `variables.knowledge` 读取当前 SQL 与已命中语义资产。
- 输出 `SQL 执行失败：...` 格式的用户可读错误信息。
- 针对 `datasource_not_found` 输出不可重试的数据源检查计划。
- 针对表不存在、字段不存在等 SQL 执行错误输出 `retryable=true` 的 `regenerate_sql` 计划。
- 针对缺少执行上下文、运行时未注入执行工具等系统错误输出不可重试检查计划。
- `repair_plan.action=regenerate_sql` 且 `retryable=true` 时，v1 图通过 `sql.error_retryable` 条件从 `handle_sql_error` 回到 `generate_sql`。
- 重试在图层显式发生，trace 可见；adapter 内不静默二次执行 SQL，避免隐藏失败链路。
- `max_loop_iterations=3` 继续作为循环保护，防止同类错误无限重试。

输出：

- `error_code`
- `message`
- `retryable`
- `repair_hint`
- `repair_plan`

### 5.14 `generate_question_answer`

职责：基于 SQL 结果、知识未命中或错误处理结果生成用户回答。

真实逻辑：

- 已由 `AnswerAdapter.generate()` 接入大模型。
- prompt 要求只输出 `AnswerOutput` JSON，不泄露内部变量、trace、SQL 原文或权限规则细节。
- 输入包含：
  - `request.question`
  - `variables.sql_execution`
  - `variables.knowledge`
  - `variables.sql_error`
  - 其他节点已写入的上下文变量
- 模型输出通过 `AnswerOutput` 校验后写入 `variables.answer`。
- 模型失败时返回稳定降级文案：“暂时无法生成完整回答，请稍后重试。”
- 模型输出非法时返回同一降级文案，并在 `warnings` 中标记 `answer_generation_parse_failed`。

复用能力：

- 当前先复用默认大模型配置。
- 后续可接 `AnswerGenerateTool` 或新的 result summarizer，但必须适配 v1 `AnswerOutput`。
- 后续需要补 result artifact/sample rows 的输入摘要，避免大结果集进入 prompt。

### 5.15 `recommend_questions`

职责：给用户推荐下一步可问的问题。

当前状态：

- 已由 `RecommendationAdapter.recommend()` 接入规则版真实实现。

真实逻辑：

- 输入读取：
  - `request.question`
  - `variables.knowledge.selected_assets.metrics`
  - `variables.knowledge.selected_assets.dimensions`
  - `variables.sql_execution.status`
- 优先使用已确认资产的 `display_name/name/biz_name` 生成面向用户的问题。
- 当前规则生成 3 类追问：
  - 最近 7 天趋势
  - 按已选维度拆解；无维度时按日期看趋势
  - 较昨日变化；SQL 失败时建议换口径查看
- 输出通过 `RecommendationOutput` 校验后写入 `variables.recommendations`。
- 后续可在同一 adapter 内接 LLM 或推荐策略服务，但仍需保持稳定 JSON 输出。

### 5.16 `compose_final_reply`

职责：统一前端输出结构。

真实逻辑：

- 已由 `AnswerAdapter.compose()` 本地合成，不调用大模型。
- 合并：
  - `variables.answer`
  - `variables.recommendations`
  - `variables.image_profile`
  - SQL artifact refs
  - warnings/citations
- 输出 `variables.final_reply`。
- metadata 标记 `source=real_chatbi_v1`。

这个节点应保留为本地 composer，不建议依赖 LLM。它是前端契约稳定层。

## 6. 推荐迁移顺序

### 阶段 1：QuestionAdapter

已完成：

- `question.classify`
  - 大模型结构化分类。
  - prompt 约束只做分类、不回答、不生成 SQL。
  - JSON 输出校验。
  - 非法输出和模型失败降级。
  - API v1 接入真实 classify runtime。
- `question.rewrite`
  - 大模型结构化重写。
  - prompt 约束只做重写、不回答、不生成 SQL。
  - JSON 输出校验。
  - 非法输出和模型失败降级。
  - 明显澄清场景保留 waiting_input 兜底。
- `intent.recognize`
  - 大模型结构化意图识别。
  - prompt 约束只做意图识别、不回答、不生成 SQL。
  - prompt 约束只输出自然语言 mention，不选择 Headless 资产 ID、`biz_name` 或数据库字段。
  - 输出 `metric_mentions`、`dimension_mentions`、`time_mentions`、`filter_mentions`、`required_slot_types`、`query_shape`，供知识检索确认资产。
  - JSON 输出校验。
  - 非法输出和模型失败规则兜底。
  - 模糊意图保留 `ask_intent_clarification` 路由入口。

下一步目标能力：

- interaction prompt/options 生成

原因：

- 它决定路由质量。
- 可以先不执行真实 SQL，只验证问题理解、澄清和知识检索输入是否正确。

验收：

- 数据问题进入知识检索。
- 闲聊不进入 SQL。
- 缺指标/时间范围时进入 waiting_input。
- 用户补充后恢复并继续推进。

### 阶段 2：KnowledgeAdapter

已完成：

- `knowledge.retrieve`
- metric ambiguity detection
- 基于 Headless dataset schema 的候选检索
- schema mapper 与 asset document candidate 合并
- 基于 intent mention 的分槽位召回
- `rewritten_question` 仅作为缺槽位时的 fallback，不再主导知识检索
- 候选进入 `CandidateGate` 前执行可解释 rerank，输出 `base_score`、`rerank_strategy`、`rerank_reason`
- `CandidateGate` 判定 hit/missed/metric_ambiguous
- runtime 注入 `HeadlessSchemaBuilder(session)`
- 真实 knowledge 异常不 fallback 到 placeholder
- 用户完成 `ask_metric_selection` 后，已通过 v1 interaction response patcher 合并回 `selected_assets.metrics`

下一步目标能力：

- 接入更强的检索实现，例如 BM25 / embedding / hybrid score。
- 补充更完整的同义词/别名治理，例如“访问人数/访客数/UV/访问量”。
- 补充 dataset schema/index version 的可观测字段与 rebuild 时机。

原因：

- SQL 质量取决于 evidence。
- 可以先用 trace 检查知识输出，不急于执行真实 SQL。

验收：

- checked tables/fields 正确输出。
- 已审核 metric/dimension 能命中。
- 多指标歧义能触发 `ask_metric_selection`。
- 未命中时不生成 SQL。
- Headless adapter 缺 session、dataset 不存在或 schema 加载失败时，v1 run 失败并在 trace 中暴露节点失败，不回退到 placeholder。

### 阶段 3：SqlAdapter

目标能力：

- `sql.generate`
- `sql.execute`
- `sql.handle_error`

策略：

- 优先 semantic compiler。
- fallback schema SQL generator。
- 生成后必须 validator。
- 执行结果进入 artifact/sample summary。

验收：

- 只读 SQL。
- allowed tables 生效。
- SQL 失败进入 `handle_sql_error`。
- trace 不泄露 SQL 原文和大结果集。

### 阶段 4：AnswerAdapter 和 RecommendationAdapter

已完成：

- `answer.reject`
- `answer.chitchat`
- `answer.generate`
- `answer.compose`
- `question.recommend`

说明：

- 当前 `AnswerAdapter` 已接入回复节点。
- `answer.compose` 已作为本地 composer，不依赖 LLM。
- `question.recommend` 已接入规则版推荐。
- 后续重点是让 `answer.generate` 使用真实 result artifact/sample rows，并将推荐策略从规则版升级为可配置策略或 LLM。

验收：

- 最终 `variables.final_reply` 稳定。
- 前端不需要理解内部节点差异。
- 推荐问题与当前查询上下文相关。

## 7. 测试策略

每个 adapter 接入都必须保留三类测试：

1. **adapter 单元测试**
   - 输入旧 tool/service 的典型输出，验证映射到 v1 schema。
   - 模拟失败，验证 error code、节点失败或明确降级路径。
   - 对已接入真实 adapter 的节点，测试不得期望 fallback 到 placeholder。

2. **v1 flow 集成测试**
   - 主路径。
   - 越权/闲聊。
   - rewrite clarification。
   - intent clarification。
   - knowledge missed。
   - metric selection。
   - SQL execution failed。

3. **API/trace 测试**
   - `/graph/queries` 返回状态正确。
   - `/graph/runs/{run_id}/trace` 不泄露 SQL 和大结果。
   - `node_execution` 有输入/输出/路由摘要。
   - cancel/retry/resume 行为不回退。
   - v1 主路径测试必须使用真实 Headless dataset fixture；不能用不存在的 dataset_id 靠 placeholder knowledge 跑通。

## 8. 风险和约束

| 风险 | 说明 | 处理 |
| --- | --- | --- |
| 旧工具输出结构不稳定 | 旧 `ToolResult.payload` 并不完全等于 v1 schema | adapter 必须做显式映射和 schema 校验 |
| 旧 semantic tool 是空实现 | `SemanticAssetTool` 当前 degraded | KnowledgeAdapter 应直接接 semantic service/runtime asset |
| 真实节点被 placeholder 掩盖 | 已接入真实 adapter 的节点如果异常后 fallback，会让 trace 显示成功但数据是假的 | 已接入 adapter 的能力不得在 gateway 内吞异常；由节点失败语义暴露问题 |
| Headless adapter 依赖 DB session | `HeadlessSchemaBuilder` 没有 session 会抛 `HEADLESS_SESSION_REQUIRED` | `build_real_chatbi_v1_runtime(session)` 必须注入 session-backed schema builder |
| 弱词造成指标歧义 | 真实数据集里多个指标可能同时包含“人数/次数/率”等词 | 已完成第一阶段 slot-aware rerank；仍保留 `ask_metric_selection` 分支，并继续增强同义词/别名治理 |
| SQL 生成失败分支不够细 | 当前图只在 execute 后有 SQL error 分支 | 接 SqlAdapter 时需要统一 generate/validate/execute 的错误模型 |
| 大结果集污染 context/trace | `SqlExecuteTool` 可能返回完整 data | 必须引入 artifact/sample summary |
| LLM 调用耗时 | `classify_question` 已接入默认大模型，后续问题理解节点也可能调用模型 | 节点 timeout、结构化输出校验、fallback 和可配置开关必须保留 |
| 权限策略源未完全接入 | `PermissionAdapter` 已支持策略 provider、行过滤 AST 改写和禁用列拒绝，但默认 `PermissionTool` 仍是透传 | 后续把旧权限表/current_user 接成正式 `PermissionPolicyProvider`，并扩展复杂 SQL 改写覆盖面 |

## 9. 推荐的下一份执行计划

下一步不建议一次性接所有真实能力。建议先做：

1. 保留已完成的 `RealChatBICapabilityGateway`、`QuestionAdapter.classify()`、`QuestionAdapter.rewrite()`、`QuestionAdapter.recognize_intent()`、`HeadlessKnowledgeAdapter`、`SqlAdapter`、`AnswerAdapter` 和 `RecommendationAdapter`。
2. 完善真实 knowledge 后半段：
   - 指标歧义选项的展示字段。
   - BM25 / embedding / hybrid score 的候选重排。
   - Headless 资产别名和同义词治理。
3. 继续增强结构化 slot 结果质量，保持 `recognize_intent` 只输出自然语言线索，由 `knowledge.retrieve` 负责资产确认。
4. 接入真实权限策略源，替换当前默认空策略 provider，并补多表/子查询权限改写测试。
5. 回归 v1 主路径、真实 knowledge 命中、真实 metric ambiguity、模型分类降级、rewrite 澄清分支、SQL 失败分支、trace 输出和“不回退 placeholder”测试。

这样可以继续保持图运行时稳定，同时让已经接入的真实节点暴露真实状态，不再用占位数据制造假成功。
