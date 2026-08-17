# Agentic Query Understanding 资产管理改造技术方案

**关联文档：** [04-agentic-chatbi-full-flow-technical-document.md](./04-agentic-chatbi-full-flow-technical-document.md)、[01-metric-dimension-semantic-layer-tech-design.md](./01-metric-dimension-semantic-layer-tech-design.md)  
**状态：** 草案  
**创建日期：** 2026-05-27  
**适用范围：** SQLBot 语义资产、术语、SQL 示例、字段 Schema 与 Agentic `understand_query` 的召回链路。

## 1. 背景

`04-agentic-chatbi-full-flow-technical-document.md` 中的 `understand_query` 目标方案已经从“一轮大 Prompt 理解”升级为 **Rewrite-first Grounded Query Understanding**：

```text
raw_question
  -> question_rewrite
  -> Query Retrieval Representation
  -> hybrid recall against Asset Retrieval Documents
  -> candidate groups
  -> grounded_understanding
```

这个方案意味着资产管理侧也必须升级。否则即使问题侧先做了标准化改写，资产侧如果仍然只是“指标表、维度表、字段表、术语表、样例问题表各自独立存储”，召回仍然会不稳定。

当前最大矛盾是：

- 用户问题是自然语言，且经常口语化。
- 指标、维度、字段、术语、SQL 示例的存储结构不同。
- 现有召回主要围绕指标/维度原始字段、别名、描述和 embedding。
- `understand_query` 需要的是统一、可解释、可分组、可兜底的候选资产。

因此，资产管理需要从“管理指标维度原始资产”扩展为“管理可被问数理解召回的资产检索文档和资产关系”。

## 2. 当前资产管理现状

### 2.1 语义指标 `semantic_metric`

当前已经有指标资产模型：

| 字段 | 当前作用 |
| --- | --- |
| `oid` | 工作空间隔离 |
| `datasource_id` | 数据源隔离 |
| `table_id` / `field_id` | 来源表字段 |
| `name` | 技术名或稳定标识 |
| `display_name` | 展示名 |
| `aliases` | 别名数组 |
| `description` | 业务口径说明 |
| `define_type` | 指标定义方式，当前主要是 `MEASURE` / `DERIVED` |
| `expr` | 指标表达式 |
| `default_agg` | 默认聚合 |
| `filter_sql` | 指标级过滤条件 |
| `data_type` / `data_format` | 数据类型和展示格式 |
| `default_time_dimension_id` | 默认时间维度 |
| `related_dimension_ids` | 推荐关联维度 |
| `status` | `CANDIDATE` / `APPROVED` / `DISABLED` / `DEPRECATED` |
| `origin` | `FIELD_INIT` / `MANUAL` / `SQL_EXAMPLE` / `IMPORT` |
| `embedding` | 指标向量 |

已有能力：

- 支持候选初始化。
- 支持审核、禁用、维护。
- 支持表达式校验。
- 支持 embedding 重建。
- 支持使用记录 `chat_record_semantic_asset`。

当前不足：

- 指标没有显式 `metric_group`，例如流量表现、咨询表现、转化表现。
- 指标没有 `related_terms`、`related_examples` 等检索增强字段。
- 指标 embedding 文本只覆盖展示名、技术名、别名、描述、表达式、聚合方式，缺少业务画像、样例问题、指标分组等信息。
- `related_dimension_ids` 有关系字段，但没有形成“召回时可解释”的关系文档。
- 指标只按 `datasource_id` 归属，缺少面向会话绑定数据集的 `dataset_id` 作用域，无法表达同一数据源下不同数据集的核心指标和默认口径差异。

### 2.2 语义维度 `semantic_dimension`

当前已经有维度资产模型：

| 字段 | 当前作用 |
| --- | --- |
| `oid` | 工作空间隔离 |
| `datasource_id` | 数据源隔离 |
| `table_id` / `field_id` | 来源表字段 |
| `name` | 技术名或稳定标识 |
| `display_name` | 展示名 |
| `aliases` | 别名数组 |
| `description` | 说明 |
| `expr` | 维度表达式 |
| `dimension_type` | `CATEGORY` / `TIME` / `GEO` / `ID` |
| `semantic_type` | `DATE` / `DATETIME` / `REGION` / `CHANNEL` 等 |
| `time_granularities` | 时间粒度 |
| `default_values` | 常用默认值 |
| `status` / `origin` | 状态和来源 |
| `embedding` | 维度向量 |

已有能力：

- 支持候选初始化。
- 支持审核、禁用、维护。
- 支持 embedding 重建。
- 支持维度值表 `semantic_dimension_value`。

当前不足：

- 维度没有明确“适用指标范围”或“关联指标组”。
- 维度值没有在当前问数理解里形成稳定召回能力。
- 维度和字段、表、指标之间的关系没有统一成为检索文档的一部分。
- 维度缺少数据集级默认配置，例如是否为数据集默认时间维度、是否参与概览、是否为主键 / 实体维度等运行时提示。

### 2.3 维度值 `semantic_dimension_value`

当前有维度值模型：

| 字段 | 当前作用 |
| --- | --- |
| `dimension_id` | 归属维度 |
| `value` | 实际查询值 |
| `display_value` | 展示值 |
| `aliases` | 维值别名 |
| `enabled` | 是否启用 |
| `embedding` | 维值向量 |

当前不足：

- 维度值没有完整接入 `understand_query`。
- 用户问题里的实体过滤，例如“3 号档口”“华东地区”，还不能稳定映射到维度值候选。
- 维度值没有成为一等候选资产，缺少 `VALUE` 类型候选分组、召回证据和回源兜底策略。

### 2.4 术语 `terminology`

当前术语模型包含：

| 字段 | 当前作用 |
| --- | --- |
| `word` | 术语词 |
| `description` | 术语解释 |
| `embedding` | 术语向量 |
| `specific_ds` | 是否绑定特定数据源 |
| `datasource_ids` | 绑定数据源列表 |
| `enabled` | 是否启用 |

已有能力：

- 可以按数据源范围管理术语。
- `QueryUnderstandingCandidateBuilder` 当前可以根据问题文本命中术语。

当前不足：

- 术语没有结构化 `aliases` 字段，现有 `TerminologyInfo.other_words` 没有对应模型持久化字段。
- 术语没有结构化 `mapped_assets`，无法稳定映射到指标、维度、字段或指标组。
- 术语召回主要依赖文本包含和简单切分，不能解释“该术语为什么展开到这些资产”。
- 术语的 `datasource_ids` 只能表达数据源范围，不能表达数据集范围，也不能描述术语在不同数据集内映射到不同资产的情况。

### 2.5 SQL 示例 / 训练数据 `data_training`

当前训练数据模型包含：

| 字段 | 当前作用 |
| --- | --- |
| `question` | 示例问题 |
| `description` | 示例描述，通常可包含 SQL 或说明 |
| `embedding` | 示例向量 |
| `datasource` | 绑定数据源 |
| `enabled` | 是否启用 |
| `advanced_application` | 高级应用关联 |

已有能力：

- 已有训练数据管理。
- 已有 embedding 字段。
- 旧链路中可作为 SQL 示例或问法示例。

当前不足：

- 示例问题没有结构化 `linked_assets`，不能说明它关联哪些指标、维度、过滤条件或意图。
- 示例问题不能直接作为 `Asset Retrieval Document` 的强召回证据。
- 无法区分“SQL 示例”“问法样例”“概览样例”“澄清样例”等类型。

### 2.6 字段 Schema `CoreTable` / `CoreField`

当前字段 Schema 是最基础的数据资产来源：

- 语义资产候选初始化从 `CoreField` 推断。
- `SchemaTool` 直接查询表和字段用于 SQL 生成。
- `QueryUnderstandingCandidateBuilder` 当前能构建 `schema_summary`。

当前不足：

- 字段没有统一进入资产检索文档。
- 字段注释只能作为 Schema 摘要注入，不能作为候选召回证据保存。
- 字段命中后无法稳定转成“低置信指标候选”或“维度候选”。

### 2.7 运行时资产索引

当前项目已经有以下能力：

- `retrieve_semantic_assets` 内部维护 approved asset cache。
- 指标、维度、术语、训练样例都有 embedding 字段。
- 资产审核、禁用、更新后可以通过业务服务显式重建部分 embedding。

当前不足：

- 缺少统一的资产变更事件，例如指标、维度、术语、样例问题变更后无法自动触发检索文档重建。
- 缺少“管理态资产 -> 运行时检索资产”的标准投影层。
- 缺少独立的关键词 / 别名索引。当前 exact / alias 匹配在请求时遍历内存资产，难以扩展到维值、术语、样例问题。
- 缺少索引版本、索引更新时间和索引重建状态，无法判断 Prompt 中候选是否来自最新资产。

## 3. 当前召回实现现状

### 3.1 语义资产召回

当前 `retrieve_semantic_assets` 主要流程：

```text
question
  -> question embedding
  -> 加载 APPROVED 指标和维度
  -> alias_score + embedding_score + exact_name_score + usage_boost
  -> 过滤低于 MIN_SEMANTIC_SCORE 的候选
  -> TopK 返回 metrics / dimensions
```

当前综合分：

```text
0.45 * alias_score
+ 0.35 * embedding_score
+ 0.10 * exact_name_score
+ 0.10 * usage_boost
```

现状特点：

- 已经有关键词/别名和 embedding 混合的雏形。
- 已经有 approved asset cache。
- embedding 模型不可用时会 degraded 到 alias score。

不足：

- 输入只有一个 `question` 字符串，还没有 `standardized_question`、`query_expansions`、`intent_hint`。
- 没有问题侧 `Query Retrieval Representation`。
- 没有资产侧统一 `Asset Retrieval Document`。
- 没有样例问题召回、术语映射召回、overview fallback、候选分组。
- `usage_boost` 当前为 0。
- TopK 是平铺截断，不能保护强命中候选。

### 3.2 Agentic `understand_query` 候选构建

当前 `QueryUnderstandingCandidateBuilder`：

- 指标候选：按 `SemanticMetric.updated_at desc` 取最多 8 个。
- 维度候选：按 `SemanticDimension.updated_at desc` 取最多 10 个。
- 术语候选：按问题文本包含命中。
- Schema 摘要：按已勾选表和字段构建。

当前问题：

- 候选不是“基于问题召回”，而是“取最近资产 + 简单术语命中”。
- 用户真正需要的指标可能不在 Top8 内。
- 候选进入 Prompt 前再次被 `compact_candidates_for_prompt(limit=8)` 截断。
- 没有统一召回证据，也没有候选不可裁剪规则。

## 4. 目标资产管理模型

### 4.1 目标原则

资产管理侧需要支持以下目标：

- 问题侧和资产侧都要有可检索表示。
- 所有召回必须限定在会话绑定数据集范围内。
- 指标、维度、字段、术语、样例问题都能统一进入候选召回。
- 每个候选都要有召回证据，便于模型消歧和 trace 展示。
- 强命中候选不能被普通 TopK 裁剪。
- 召回失败时有 overview、指标组、Schema、澄清等兜底。

### 4.2 Query Retrieval Representation

问题侧结构由 `question_rewrite` 结果和数据集画像共同生成。

```json
{
  "raw_question": "今天咋样",
  "standardized_question": "查询今天绑定数据集的整体表现",
  "query_expansions": [
    "今日核心指标概览",
    "今天访问、咨询、关注、转化表现"
  ],
  "intent_hint": "overview",
  "time_range_hint": "today",
  "business_domain_hints": ["整体表现", "核心指标", "流量", "咨询", "转化"],
  "possible_metric_group_hints": ["流量表现", "咨询表现", "关注表现", "转化表现"],
  "search_text": "今天 整体表现 今日核心指标概览 访问 咨询 关注 转化 数据情况"
}
```

字段生成规则：

| 字段 | 来源 | 生成方式 |
| --- | --- | --- |
| `raw_question` | 用户输入 | 原样保留 |
| `standardized_question` | `question_rewrite` | 独立 Prompt 输出，作为最终标准化问题 |
| `query_expansions` | `question_rewrite` | 独立 Prompt 输出，只用于召回 |
| `intent_hint` | `question_rewrite` | 独立 Prompt 输出，作为召回提示 |
| `time_range_hint` | `question_rewrite` + 时间规则 | 模型提示和规则解析合并 |
| `business_domain_hints` | 改写结果 + 数据集画像 + intent 规则 | 从标准化问题、扩展问题、overview hint、业务域别名中抽取并去重 |
| `possible_metric_group_hints` | 数据集画像 + intent 规则 | `overview` 时取数据集核心指标组；明确问法时取相关指标组 |
| `search_text` | 上述字段 | 拼接、清洗、去重后的检索文本 |

### 4.3 Asset Retrieval Document

资产侧统一检索文档不一定要作为首期物理表存在，但必须成为召回层的标准结构。

通用字段：

| 字段 | 说明 |
| --- | --- |
| `doc_id` | 文档 ID，例如 `metric:101`、`dimension:201`、`field:301`、`term:401`、`example:501` |
| `asset_type` | `METRIC`、`DIMENSION`、`FIELD`、`TERM`、`EXAMPLE_QUESTION` |
| `asset_id` | 原始资产 ID |
| `title` | 展示名或问题标题 |
| `aliases` | 别名 |
| `business_text` | 业务描述、口径、用途 |
| `technical_text` | 技术字段、表达式、SQL 片段 |
| `related_terms` | 相关术语 |
| `related_examples` | 相关问法 |
| `linked_assets` | 关联资产 |
| `metric_group` | 指标分组 |
| `dataset_id` / `datasource_id` | 绑定数据范围 |
| `table` / `fields` | 来源表字段 |
| `search_text` | 合成检索文本 |
| `embedding` | 检索向量 |
| `updated_at` | 文档更新时间 |

### 4.4 数据集画像

当前项目还没有显式 `semantic_dataset`，但会话已经绑定至少一个数据集。目标方案需要为绑定数据集构建轻量画像，可以先用配置或虚拟结构实现。

```json
{
  "dataset_id": 8,
  "datasource_id": 8,
  "dataset_name": "档口流量指标",
  "business_domain": "流量分析",
  "business_aliases": ["流量", "人气", "访问", "咨询", "转化", "表现", "情况"],
  "metric_groups": [
    {
      "name": "流量表现",
      "metrics": [101, 102],
      "aliases": ["流量", "访问", "人气"]
    },
    {
      "name": "咨询表现",
      "metrics": [103, 104],
      "aliases": ["咨询", "联系"]
    }
  ],
  "overview_metrics": [101, 102, 103, 104],
  "overview_hint": "整体表现通常包含访问、点击、关注、咨询、转化"
}
```

### 4.5 Supersonic Semantic 参考启发

`extra/supersonic/headless` 的资产管理设计对当前方案有直接参考价值。它不是在问答链路里直接读取指标表、维度表和术语表，而是先把管理态资产统一投影成运行时 Schema：

```text
ModelDetail
  -> DataSetResp / DataSetModelConfig
  -> DataSetSchema
  -> SchemaElement
     - DATASET
     - METRIC
     - DIMENSION
     - VALUE
     - TERM
     - TAG
```

这个结构说明资产管理至少需要分为两层：

| 层级 | 作用 | 当前项目对应 |
| --- | --- | --- |
| 管理态资产 | 负责资产编辑、审核、禁用、来源、表达式、权限 | `SemanticMetric`、`SemanticDimension`、`Terminology`、`DataTraining`、`CoreField` |
| 运行时资产 | 负责问答召回、Prompt 注入、候选分组、SQL 生成 | 目标新增 `DatasetProfile`、`AssetRetrievalDocument`、`CandidateGroup` |

从 Supersonic 可以提炼出四个关键原则：

1. **数据集是问答资产的组织核心**  
   Supersonic 使用 `DataSetModelConfig` 描述一个数据集包含哪些模型、指标和维度。当前项目会话已经绑定至少一个数据集，因此资产召回也必须从 `datasource_id` 范围收敛到 `dataset_id` 范围。同一个数据源下，不同数据集可以有不同核心指标、默认时间维度、默认过滤条件和业务术语映射。

2. **统一运行时资产模型比直接查管理表更稳定**  
   Supersonic 统一使用 `SchemaElement` 承载指标、维度、维值、术语。当前项目不需要照搬 `SchemaElement`，但需要等价的 `AssetRetrievalDocument`。它要把不同结构的资产转成统一检索文本、统一证据、统一关系字段。

3. **资产变更必须驱动检索索引同步**  
   Supersonic 通过 `DataEvent` 同步更新 HanLP 字典和 meta embedding。当前项目也需要资产变更事件，触发 cache 失效、检索文档重建、关键词索引更新、embedding 重建。否则资产治理和问答召回会逐渐不一致。

4. **维度值和术语是一等召回对象**  
   Supersonic 把维度值投影为 `VALUE`，把术语投影为 `TERM`，并支持术语描述继续递归映射资产。当前项目的维度值、术语、样例问题都应进入候选资产，而不是只作为指标/维度的辅助文本。

## 5. 需要变动的资产能力

### 5.0 数据集作用域需要补充

| 能力 | 当前情况 | 建议 |
| --- | --- | --- |
| 数据集资产范围 | 当前主要按 `datasource_id` 管理 | 新增 `dataset_id` 作用域，至少在运行时资产文档中保存 |
| 数据集包含资产 | 缺少数据集到指标、维度、字段的显式关系 | 新增数据集资产绑定或从会话绑定配置生成 |
| 数据集画像 | 当前没有显式画像 | 生成 `DatasetProfile`，包含业务域、核心指标、指标组、默认时间维度 |
| 数据集默认配置 | 当前分散在指标 / 维度字段里 | 数据集级保存默认时间、默认概览指标、默认过滤条件、默认展示维度 |
| 数据集术语映射 | 术语只到数据源 | 扩展到数据集范围，同一术语在不同数据集内可以映射不同资产 |

### 5.1 指标资产需要补充

| 能力 | 说明 | 建议落地 |
| --- | --- | --- |
| 数据集归属 | 支持同一数据源下不同数据集拥有不同指标范围 | 新增数据集资产绑定或在 `semantic_asset_document.dataset_id` 中落地 |
| 指标分组 | 支持流量表现、咨询表现、转化表现等分组 | 新增 `metric_group` 字段或独立 `semantic_metric_group` 表 |
| 相关术语 | 指标可直接配置“人气”“成交”“额度”等术语 | 新增映射表或进入 Asset Retrieval Document |
| 相关样例问题 | 指标关联自然语言问法 | 通过 SQL 示例 / 问法样例资产关联 |
| 召回文本 | 统一生成 `search_text` | 由资产文档构建器生成 |
| 召回证据 | 记录候选为何命中 | 召回服务返回 `evidence[]` |
| 核心指标标记 | 支持 overview fallback | 字段或指标组配置 |
| 默认下钻维度 | 指标天然适合按哪些维度分析 | 将 `related_dimension_ids` 升级为可解释关系，进入 `linked_assets` |

### 5.2 维度资产需要补充

| 能力 | 说明 | 建议落地 |
| --- | --- | --- |
| 适用指标 / 指标组 | 维度是否可用于某指标或指标组 | 使用 `related_dimension_ids` 的反向关系或新增关系表 |
| 维度值召回 | 支持实体过滤识别 | 接入 `semantic_dimension_value` |
| 相关样例问法 | 例如“按档口看”“哪个区域最好” | 进入 Asset Retrieval Document |
| 字段注释增强 | 维度字段的注释、别名、表注释进入 `search_text` | 资产文档构建器生成 |
| 默认角色 | 主键、实体维度、默认时间维度、可分组维度 | 在 `DatasetProfile` 和资产文档中保存 |

### 5.2.1 维度值资产需要补充

| 能力 | 当前情况 | 建议 |
| --- | --- | --- |
| 一等候选类型 | 当前未进入 `understand_query` 候选 | 增加 `VALUE` 类型 Asset Retrieval Document |
| 维值归属关系 | 当前只通过 `dimension_id` 归属 | 在候选中返回归属维度、表字段、数据集 |
| 别名归一 | 当前有 `aliases`，但未稳定召回 | exact / alias / embedding 都要支持维值 |
| 回源兜底 | 资产库没有维值时无法识别 | 候选不足时按维度字段回源查询 TopN / Like 结果 |
| 低置信澄清 | 维值可能命中多个维度 | 多归属或低分时进入澄清，不直接绑定 |

### 5.3 术语资产需要补充

| 能力 | 当前情况 | 建议 |
| --- | --- | --- |
| 别名持久化 | 模型没有 `aliases` 字段 | 新增 `aliases JSONB` 或复用 `description` 前先结构化 |
| 资产映射 | 无结构化 `mapped_assets` | 新增 `mapped_assets JSONB` 或 `terminology_asset_mapping` 表 |
| 术语类型 | 无类型 | 增加 `term_type`，如指标别名、维度别名、业务域、口径说明 |
| 数据集范围 | 目前支持 `specific_ds` / `datasource_ids` | 后续扩展到 dataset scope |
| 描述递归映射 | 术语描述未参与资产映射 | 可参考 Supersonic `TermDescMapper`，用术语描述再次召回指标 / 维度 / 维值 |

### 5.4 样例问题需要补充

| 能力 | 当前情况 | 建议 |
| --- | --- | --- |
| 样例类型 | 当前只有 `DataTraining.question/description` | 增加 `example_type`，区分 SQL 示例、问法样例、概览样例 |
| 关联资产 | 无结构化 `linked_assets` | 新增 `linked_assets JSONB` |
| 关联意图 | 无结构化 intent | 新增 `intent` |
| 关联 SQL | 当前可能在 description 中 | 保持兼容，同时提供结构化 SQL 字段或解析层 |
| 向量文本 | 当前 embedding 直接依赖 question/description | 改为基于 Asset Retrieval Document 的 `search_text` |

### 5.5 字段 Schema 需要补充

| 能力 | 说明 |
| --- | --- |
| 字段文档化 | 将 `CoreField` 转成 `FIELD` 类型 Asset Retrieval Document |
| 表画像 | 将表名、表注释、字段摘要进入数据集画像 |
| 字段低置信候选 | 字段命中但无语义资产时，可以作为低置信候选进入澄清 |
| 字段到资产关系 | 字段与指标/维度关系必须能反查 |

### 5.6 资产索引同步能力需要补充

| 能力 | 说明 | 建议落地 |
| --- | --- | --- |
| 资产变更事件 | 管理态资产变更后通知运行时索引 | 新增 `SemanticAssetChangedEvent` 或领域事件表 |
| 缓存失效 | 指标、维度、术语、样例变更后清理召回缓存 | 复用并扩展 `invalidate_semantic_asset_cache` |
| 检索文档重建 | 将变更资产重新投影为 `AssetRetrievalDocument` | 新增 `AssetRetrievalDocumentSyncService` |
| embedding 重建 | 资产文档 `search_text` 变更后更新向量 | 后台任务或事件监听器异步执行 |
| 索引状态 | 可观测索引是否最新 | 记录 `index_version`、`indexed_at`、`index_status` |

## 6. 目标召回链路

```text
question_rewrite
  -> Query Retrieval Representation
  -> load bound dataset profiles
  -> build / load Asset Retrieval Documents
  -> exact / alias recall
  -> BM25 / keyword recall
  -> fuzzy recall
  -> embedding recall
  -> terminology mapping
  -> example question recall
  -> overview fallback
  -> score fusion
  -> candidate grouping
  -> grounded_understanding
```

### 6.1 召回通道

| 通道 | 输入 | 资产字段 | 作用 |
| --- | --- | --- | --- |
| exact / alias | 原始问题、标准化问题、扩展问题 | `title`、`aliases`、字段名 | 强确定命中 |
| BM25 / keyword | `search_text` | `search_text`、`business_text` | 标准业务表达 |
| fuzzy / trigram | 原始问题和标准化问题 | `title`、`aliases` | 简称、错字、近似词 |
| embedding | 问题侧 `search_text` | 资产侧 `search_text` | 口语化语义召回 |
| terminology mapping | `business_domain_hints` | 术语文档、映射资产 | 术语展开 |
| example question | `query_expansions` | 样例问题文档 | 相似问法召回 |
| overview fallback | `intent_hint=overview` | 数据集核心指标 | 泛问句兜底 |

### 6.2 候选返回结构

```json
{
  "asset_type": "METRIC",
  "asset_id": 101,
  "display_name": "访问人数",
  "score": 0.86,
  "matched_by": ["embedding", "example_question", "overview_fallback"],
  "evidence": [
    {
      "channel": "example_question",
      "query_source": "query_expansion",
      "query": "今日核心指标概览",
      "score": 0.82,
      "reason": "样例问题“今天人气怎么样”关联该指标"
    }
  ],
  "snapshot": {
    "name": "visit_uv",
    "expr": "visit_uv",
    "default_agg": "SUM",
    "metric_group": "流量表现"
  }
}
```

### 6.3 候选分组

候选不要平铺 TopN，应按类型和来源分组：

```json
{
  "metric_candidate_groups": [
    {
      "group": "overview_core_metrics",
      "reason": "问题被标准化为整体表现，命中 overview 意图",
      "candidates": []
    },
    {
      "group": "exact_or_alias_hits",
      "reason": "原始问题或标准化问题直接命中",
      "candidates": []
    }
  ],
  "dimension_candidate_groups": [],
  "term_candidates": [],
  "field_candidates": [],
  "example_candidates": []
}
```

压缩规则：

```text
confirmed_slots
> exact / alias hits
> terminology mapping
> example question hits
> overview core metrics
> embedding hits
> schema comment hits
```

强命中候选不可被普通 TopK 裁剪。

## 7. 数据模型改造建议

### 7.1 P1 不新增物理表的最小方案

首期可以不新增 `semantic_asset_document` 物理表，而是在召回时动态构建资产文档：

```text
SemanticMetric / SemanticDimension / CoreField / Terminology / DataTraining
  -> AssetRetrievalDocumentBuilder
  -> in-memory documents
  -> hybrid recall
```

优点：

- 改造小。
- 不需要新增迁移。
- 可以快速验证召回效果。

缺点：

- 每次动态构建成本较高，需要缓存。
- 难以做独立索引和增量更新。
- 不利于召回可观测和离线评估。

### 7.2 P2 新增资产检索文档表

后续建议新增：

```text
semantic_asset_document
```

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 工作空间 |
| `datasource_id` | bigint | 数据源 |
| `dataset_id` | bigint nullable | 数据集 |
| `doc_id` | varchar | `metric:101` 等稳定 ID |
| `asset_type` | varchar | `METRIC` / `DIMENSION` / `FIELD` / `TERM` / `EXAMPLE_QUESTION` |
| `asset_id` | bigint | 原始资产 ID |
| `title` | text | 标题 |
| `aliases` | jsonb | 别名 |
| `business_text` | text | 业务描述 |
| `technical_text` | text | 技术描述 |
| `related_terms` | jsonb | 相关术语 |
| `related_examples` | jsonb | 相关样例问题 |
| `linked_assets` | jsonb | 关联资产 |
| `metric_group` | varchar nullable | 指标分组 |
| `table_id` | bigint nullable | 来源表 |
| `field_ids` | jsonb | 来源字段 |
| `search_text` | text | 合成检索文本 |
| `embedding` | vector nullable | 向量 |
| `index_version` | varchar nullable | 文档构建版本 |
| `indexed_at` | datetime nullable | 最近索引时间 |
| `index_status` | varchar | `PENDING` / `READY` / `FAILED` |
| `enabled` | boolean | 是否启用 |
| `updated_at` | datetime | 更新时间 |

索引建议：

- `(oid, datasource_id, asset_type)`
- `(oid, dataset_id, asset_type)`
- `(oid, datasource_id, doc_id)` 唯一
- `search_text` 的全文索引或 trigram 索引
- `embedding` 的向量索引

### 7.3 新增关系表建议

为了避免所有关系都塞进 JSON，后续可新增：

| 表 | 作用 |
| --- | --- |
| `semantic_dataset` | 管理数据集画像、默认时间、默认概览指标、业务域 |
| `semantic_dataset_asset` | 管理数据集与指标、维度、字段、术语、样例问题的绑定 |
| `semantic_metric_group` | 管理指标组和核心指标 |
| `semantic_metric_group_item` | 指标组与指标关系 |
| `semantic_asset_example` | 样例问题与资产关系 |
| `terminology_asset_mapping` | 术语与指标、维度、字段、指标组关系 |
| `semantic_asset_index_task` | 记录资产文档和 embedding 重建任务 |

## 8. 服务改造建议

### 8.1 新增服务

| 服务 | 职责 |
| --- | --- |
| `QuestionRewriteService` | 独立 Prompt，将口语问题改写成标准化问题和检索扩展 |
| `QueryRetrievalRepresentationBuilder` | 根据 rewrite 结果和数据集画像生成问题侧检索表示 |
| `DatasetProfileService` | 加载或生成绑定数据集画像 |
| `AssetRetrievalDocumentBuilder` | 将指标、维度、字段、术语、样例问题转换成统一资产文档 |
| `AssetRetrievalDocumentSyncService` | 根据资产变更事件同步检索文档和索引状态 |
| `HybridAssetRecallService` | 多路召回和分数融合 |
| `CandidateGroupingService` | 候选分组、强命中保护和 Prompt 压缩 |
| `GroundedUnderstandingService` | 基于候选分组做最终槽位绑定和澄清判断 |

### 8.2 现有服务改造

| 现有服务 | 改造 |
| --- | --- |
| `QueryUnderstandingService` | 从一轮理解改为编排 rewrite、recall、grounded understanding |
| `QueryUnderstandingCandidateBuilder` | 从“取最近指标维度”改为调用 `HybridAssetRecallService` |
| `retrieve_semantic_assets` | 扩展为支持多 query、多通道、召回证据和候选分组 |
| `semantic_embedding` | 从指标/维度 embedding 文本升级为 Asset Retrieval Document `search_text` embedding |
| `semantic_context` | 从 Prompt 文本拼接升级为候选分组摘要 |
| `semantic asset CRUD service` | 在指标、维度、术语、样例问题变更后发布资产变更事件 |
| `semantic_dimension_value` 相关服务 | 接入维值召回、回源兜底和候选分组 |

## 9. 分阶段落地

### P1.1 文档和动态文档构建

- 明确 `QuestionRewriteService` Prompt。
- 动态构建 `Query Retrieval Representation`。
- 动态构建当前绑定数据集的 `DatasetProfile`。
- 动态构建 `Asset Retrieval Document`。
- 在绑定数据集内先支持 exact、alias、embedding、overview fallback。
- 让维度值以 `VALUE` 类型进入候选，但首期可只支持已入库维值。
- 保留当前一轮理解作为 fallback。

### P1.2 混合召回和候选分组

- 接入 BM25 / keyword。
- 接入术语映射。
- 接入样例问题召回。
- 实现召回证据和分数融合。
- 实现候选分组压缩。
- 支持术语描述递归召回资产。
- 支持维值回源兜底召回。

### P2 资产治理增强

- 新增 `semantic_dataset` 和 `semantic_dataset_asset`。
- 新增 `semantic_asset_document`。
- 新增指标组和核心指标管理。
- 新增术语资产映射。
- 新增样例问题关联资产。
- 新增资产变更事件和索引同步任务。
- 接入离线评估和召回效果监控。

## 10. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 标准化问题改写偏差 | 后续召回偏离用户意图 | 保留 `raw_question` 参与召回，低置信触发澄清 |
| 资产文档质量差 | embedding 和关键词召回都不稳定 | 管理端增加别名、业务描述、样例问题、指标组治理 |
| 动态构建资产文档成本高 | 请求延迟上升 | 按数据源缓存，P2 落物理表和索引 |
| embedding 模型不可用 | 口语化召回下降 | exact / alias / BM25 / overview fallback 兜底 |
| 候选过多 | Prompt 过长 | 候选分组压缩，强命中保护，弱候选裁剪 |
| 术语映射错误 | 指标误判 | 术语映射需要审核，候选内理解仍需判断歧义 |
| 数据集资产范围不准确 | 召回到其他业务数据集资产 | 资产召回强制绑定 `dataset_id`，缺失绑定时不进入新流程 |
| 索引同步延迟 | 管理端更新后问答仍使用旧资产 | 索引状态可观测，关键资产更新后同步清理缓存 |
| 维值回源成本高 | 请求延迟和数据库压力上升 | 仅候选不足时触发，限制 TopN 和超时时间 |

## 11. 验收标准

- 能说明当前资产管理已有指标、维度、维度值、术语、SQL 示例和字段 Schema。
- 能将问题侧转换成 `Query Retrieval Representation`。
- 能将资产侧转换成 `Asset Retrieval Document`。
- 能在会话绑定数据集范围内构建 `DatasetProfile`。
- 能解释当前候选资产来自哪个数据集、哪个资产文档、哪个召回通道。
- 口语化问题“今天咋样”能先生成标准化问题，再通过 overview 或样例问题召回核心指标。
- 召回结果包含证据来源和分数。
- 强命中候选不会因为 TopK 截断丢失。
- 维度值可以作为 `VALUE` 候选进入 `understand_query`。
- 术语可以结构化映射到指标、维度、字段或指标组。
- 资产更新后可以触发检索文档和 embedding 的同步或重建任务。
- 候选不足时不强行绑定指标，而是进入澄清或兜底。
- 当前一轮理解实现可以作为 fallback，目标方案可分阶段上线。
