# Semantic 语义资产存储差距与表结构改造方案

**状态：** 资产存储层改造方案  
**创建日期：** 2026-06-03  
**关联模块：** `backend/apps/semantic/`、`frontend/src/views/system/semantic/index.vue`  
**参考对象：** Supersonic Semantic 的 `Domain -> Model -> Metric / Dimension -> DataSet -> DataSetSchema -> OpenAPI` 主链路

## 1. 背景

当前项目已经新增了一套 Semantic 风格的语义资产管理模块，核心表包括：

- `headless_domain`
- `headless_model`
- `headless_model_relation`
- `headless_metric`
- `headless_dimension`
- `headless_dataset`
- `headless_term`
- `headless_schema_index`

这套结构已经能支撑基本的主题域、模型、指标、维度、数据集、术语管理，也能构建 `DataSetSchema` 和初版 `Ontology`，并由 `SemanticSQLCompiler` 生成基础 SQL。

但如果目标是对齐 Supersonic Semantic，当前资产存储层还不够完整。主要问题不是缺少几个页面，而是资产之间的结构化关系、字段/度量/维值的持久化方式、数据集暴露范围、运行时检索文档、Join 关系和 SQL 编译依赖信息还没有形成稳定闭环。

本文只讨论资产存储层，不展开检索算法、问题理解、SQL Compiler 规则实现。

## 2. 对齐目标

资产存储层需要支撑以下能力：

1. 用户可以从主题域开始建设资产。
2. 模型必须明确绑定数据源、表或 SQL。
3. 模型字段、标识、维度、度量需要稳定存储，不能只依赖页面临时状态。
4. 指标需要明确来源：由度量定义、由字段定义、由指标定义。
5. 指标需要持久化依赖字段集，供 SQL Compiler 和资产质量检查使用。
6. 维度需要明确字段表达式、数据类型、时间语义、主键语义。
7. 维度值和维值别名需要独立管理，不能只放在维度 JSON 中。
8. 数据集需要明确绑定哪些模型，暴露哪些指标、维度、术语。
9. 多模型 Join 关系需要规范化，能被运行时 Ontology 直接消费。
10. 术语、别名、样例问题、字段、指标、维度之间的关系需要可追踪。
11. 需要为后续知识库、embedding、混合召回保留运行时资产文档表。

## 3. 当前实现形式

### 3.1 当前后端结构

当前 Semantic 后端主要位于：

```text
backend/apps/semantic/
  api.py
  models.py
  schemas.py
  service.py
  sql_compiler.py
```

核心逻辑如下：

```text
前端配置资产
  -> /semantic/* API
  -> headless_* 表落库
  -> SemanticSchemaBuilder.build_dataset_schema()
  -> DataSetSchema
  -> build_ontology_from_schema()
  -> SemanticSQLCompiler
```

### 3.2 当前表结构

#### headless_domain

当前用途：主题域管理。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `name` | 显示名 |
| `biz_name` | 业务唯一名 |
| `parent_id` | 父主题域 |
| `status` | 状态 |
| `admin` | 管理员 |
| `is_open` | 是否开放 |
| `created_at` / `updated_at` | 时间字段 |

当前问题：

- 缺少 `description`，但运行时构建 `DataSetSchema` 时已经尝试读取 `domain.description`。
- 缺少创建人、更新人、资产负责人等治理字段。

#### headless_model

当前用途：语义模型，绑定数据源、表或 SQL。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `domain_id` | 所属主题域 |
| `datasource_id` | 数据源 |
| `name` | 显示名 |
| `biz_name` | 业务唯一名 |
| `description` | 描述 |
| `status` | 状态 |
| `model_detail` | 模型详情 JSON |
| `depends` | 依赖 JSON |
| `filter_sql` | 模型过滤条件 |
| `alias` | 别名 JSON |
| `source_type` | `TABLE` 或 `SQL` |
| `ext` | 扩展 JSON |

当前 `model_detail` 主要存储：

```json
{
  "queryType": "table_query",
  "tableQuery": {"table": "customers"},
  "sqlQuery": {},
  "fields": [],
  "identifiers": [],
  "dimensions": [],
  "measures": [],
  "sqlVariables": []
}
```

当前问题：

- 字段、标识、维度、度量都压在 `model_detail` 中，无法单独查询、更新、审计。
- 度量没有独立 ID，指标基于度量创建时只能使用 `bizName` 关联，稳定性不足。
- 字段角色变化后，很难追踪哪些维度、指标需要同步更新。
- 表来源只存 `tableQuery.table`，缺少库名、schema、表别名、表注释等规范字段。
- SQL 来源只存 SQL 文本，缺少 SQL 变量、解析后的字段、校验状态。
- 缺少模型主键、模型粒度、默认时间字段等语义信息。

#### headless_model_relation

当前用途：模型 Join 关系。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `domain_id` | 主题域 |
| `left_model_id` | 左模型 |
| `right_model_id` | 右模型 |
| `join_type` | Join 类型 |
| `join_conditions` | Join 条件 JSON |
| `status` | 状态 |
| `ext` | 扩展 JSON |

当前问题：

- Join 条件是 JSON 数组，缺少条件行级 ID。
- 缺少关系类型、基数、优先级、是否默认路径等信息。
- 缺少 Join 关系质量检查字段，例如是否字段存在、类型是否一致。

#### headless_metric

当前用途：指标资产。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `model_id` | 所属模型 |
| `name` | 显示名 |
| `biz_name` | 业务唯一名 |
| `description` | 描述 |
| `status` | 状态 |
| `type` | 指标类型 |
| `default_agg` | 默认聚合 |
| `alias` | 别名 JSON |
| `relate_dimensions` | 关联维度 JSON |
| `type_params` | 指标定义 JSON |
| `define_type` | 定义方式 |
| `is_publish` | 是否发布 |
| `is_tag` | 是否标签 |

当前问题：

- 指标依赖字段集通过运行时 `_metric_fields()` 临时计算，没有持久化。
- 指标与度量、字段、其他指标的关系只在 `type_params` 中表达，难以做影响分析。
- 关联维度是 JSON，缺少规范关系表。
- 缺少指标过滤条件、指标口径版本、指标质量状态等字段。

#### headless_dimension

当前用途：维度资产。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `model_id` | 所属模型 |
| `name` | 显示名 |
| `biz_name` | 业务唯一名 |
| `description` | 描述 |
| `type` | 维度类型 |
| `semantic_type` | 语义类型 |
| `alias` | 别名 JSON |
| `default_values` | 默认值 JSON |
| `dim_value_maps` | 维值映射 JSON |
| `type_params` | 类型参数 JSON |
| `expr` | 表达式 |
| `data_type` | 数据类型 |
| `is_tag` | 是否标签 |

当前问题：

- 维度值和维值别名全部存储在 `dim_value_maps` 中，不利于检索、审核、批量维护。
- 缺少 `field_name`、`is_primary_key`、`is_default_time`、`time_granularities` 等常用字段。
- 时间维度、分区时间、主键维度依赖 `type` / `semantic_type` / `type_params` 混合判断，规则不够清晰。

#### headless_dataset

当前用途：面向问数暴露的数据集。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `domain_id` | 主题域 |
| `name` | 显示名 |
| `biz_name` | 业务唯一名 |
| `description` | 描述 |
| `status` | 状态 |
| `alias` | 别名 JSON |
| `data_set_detail` | 数据集模型配置 JSON |
| `query_config` | 查询配置 JSON |

当前 `data_set_detail` 示例：

```json
{
  "dataSetModelConfigs": [
    {
      "id": 1,
      "includesAll": false,
      "metrics": [1, 2],
      "dimensions": [3, 4]
    }
  ]
}
```

当前问题：

- 数据集暴露关系只在 JSON 中，无法直接查询“某指标被哪些数据集暴露”。
- 数据集模型配置没有独立状态、排序、默认模型、默认时间维度等信息。
- 数据集变更后，索引、运行时 Schema、缓存刷新缺少清晰版本字段。

#### headless_term

当前用途：业务术语。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `domain_id` | 主题域 |
| `name` | 术语 |
| `alias` | 别名 JSON |
| `description` | 描述 |
| `related_metrics` | 关联指标 JSON |
| `related_dimensions` | 关联维度 JSON |
| `status` | 状态 |

当前问题：

- 术语与指标、维度关系是 JSON，无法做关系查询和影响分析。
- 缺少术语类型、业务口径、示例问题、优先级等字段。

#### headless_schema_index

当前用途：运行时知识索引雏形。

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 组织 ID |
| `dataset_id` | 数据集 |
| `element_type` | 资产类型 |
| `element_id` | 资产 ID |
| `search_text` | 检索文本 |
| `payload` | 资产快照 |
| `updated_at` | 更新时间 |

当前问题：

- 只是文本快照，没有区分业务文本、技术文本、别名、字段、术语、样例问题。
- 没有 embedding 状态、索引版本、召回通道、质量分等字段。
- 不能支撑 Supersonic 风格的多路召回，只能作为简单文本索引雏形。

## 4. 与 Supersonic Semantic 的存储差距

### 4.1 字段和度量没有稳定资产身份

Supersonic 的模型详情里会明确维护字段、标识、维度、度量。当前项目虽然也把这些内容放入 `model_detail`，但没有把“模型字段”和“模型度量”作为可单独管理的资产。

这会导致：

- 前端编辑字段角色时，只能整体改 `model_detail`。
- 指标从度量创建时缺少稳定度量 ID。
- SQL Compiler 只能从 JSON 中临时找表达式。
- 字段变化后无法知道影响了哪些指标、维度、数据集。

### 4.2 维度值没有独立存储

当前维值放在 `headless_dimension.dim_value_maps` 中。这样可以满足简单配置，但不能支撑后续：

- 大批量维值导入。
- 维值别名审核。
- 高频维值自动发现。
- 维值检索。
- 维值从业务值映射到技术值。

### 4.3 数据集暴露关系没有规范化

当前数据集通过 `data_set_detail` JSON 记录模型、指标、维度暴露范围。这个方式与 Supersonic 的存储风格接近，但在当前项目里后续还有问答绑定数据集、资产检索、索引重建、影响分析等需求，仅靠 JSON 会让查询和同步逻辑复杂。

建议保留 JSON 快照，同时增加规范化关系表。

### 4.4 指标依赖关系没有持久化

当前指标的依赖字段通过 `_metric_fields()` 运行时计算。这不利于：

- SQL Compiler 快速判断指标依赖哪些字段。
- 指标质量检查。
- 字段删除或改名时做影响分析。
- 派生指标递归展开。

### 4.5 通用资产关系不足

当前关系散落在：

- `metric.relate_dimensions`
- `term.related_metrics`
- `term.related_dimensions`
- `dataset.data_set_detail`
- `dimension.dim_value_maps`

这些关系都可以用 JSON 表达，但缺少统一资产关系表会导致后续检索、解释、调试、影响分析难以统一。

## 5. 目标存储模型

目标采用“主资产表 + 规范化子表 + JSON 快照兼容”的方式。

```text
Domain
  -> Model
      -> ModelField
      -> ModelMeasure
      -> Metric
      -> Dimension
          -> DimensionValue
  -> ModelRelation
  -> DataSet
      -> DataSetModelConfig
      -> DataSetAsset
  -> Term
  -> AssetAlias
  -> AssetRelation
  -> AssetDocument
```

其中：

- 主资产表保留当前 `headless_*` 命名。
- `model_detail`、`data_set_detail`、`type_params` 继续保留，用于兼容 Supersonic 风格 JSON 和前端快照。
- 新增规范化表用于支撑查询、同步、检索、影响分析和 SQL 编译。

## 6. 目标表结构

### 6.1 headless_domain

建议保留当前表，补充字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `description` | text | 主题域描述 |
| `owner` | varchar(128) | 资产负责人 |
| `created_by` | bigint / varchar | 创建人 |
| `updated_by` | bigint / varchar | 更新人 |

目标说明：

- `description` 是必要字段，当前运行时代码已经有读取需求。
- `owner`、`created_by`、`updated_by` 用于资产治理，可以后续再启用。

### 6.2 headless_model

建议保留当前表，补充和规范字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `database_name` | varchar(256) | 数据库名，可从数据源解析 |
| `schema_name` | varchar(256) | Schema 名，PostgreSQL / Doris / Hive 等可用 |
| `table_name` | varchar(256) | TABLE 模型绑定的物理表 |
| `sql_query` | text | SQL 模型绑定的 SQL 文本 |
| `primary_key` | jsonb | 主键字段列表 |
| `model_grain` | jsonb | 模型粒度说明 |
| `default_time_field` | varchar(128) | 默认时间字段 |
| `is_view` | boolean | 是否 SQL 视图模型 |
| `schema_version` | bigint | 模型结构版本 |
| `last_schema_sync_at` | timestamp | 最近一次字段同步时间 |

`model_detail` 继续保留，目标格式统一为：

```json
{
  "queryType": "table_query",
  "tableQuery": {
    "database": "demo",
    "schema": "public",
    "table": "customers"
  },
  "sqlQuery": {
    "sql": ""
  },
  "fields": [],
  "identifiers": [],
  "dimensions": [],
  "measures": [],
  "sqlVariables": []
}
```

改造原则：

- SQL Compiler 运行时优先使用规范字段。
- `model_detail` 作为兼容 Supersonic 风格的快照。
- 前端仍可以一次性提交 `model_detail`，后端负责同步到规范子表。

### 6.3 headless_model_field

新增表：模型字段表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `model_id` | bigint | 所属模型 |
| `field_name` | varchar(128) | 物理字段名 |
| `name` | varchar(128) | 显示名 |
| `biz_name` | varchar(128) | 业务名 |
| `expr` | text | 字段表达式 |
| `data_type` | varchar(64) | 数据类型 |
| `field_role` | varchar(32) | `FIELD` / `IDENTIFIER` / `DIMENSION` / `MEASURE` |
| `semantic_type` | varchar(64) | 时间、金额、枚举等语义类型 |
| `alias` | jsonb | 字段别名 |
| `type_params` | jsonb | 类型参数 |
| `source_order` | int | 字段顺序 |
| `is_available` | boolean | 是否可用 |
| `status` | int | 状态 |
| `created_at` / `updated_at` | timestamp | 时间字段 |

唯一索引：

```text
ux_headless_model_field_biz_name(oid, model_id, biz_name)
idx_headless_model_field_role(oid, model_id, field_role, status)
```

作用：

- 稳定保存模型字段。
- 支撑字段角色编辑。
- 支撑维度、度量、指标依赖追踪。
- 支撑模型字段同步和差异检查。

### 6.4 headless_model_measure

新增表：模型度量表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `model_id` | bigint | 所属模型 |
| `field_id` | bigint | 来源字段，可为空 |
| `name` | varchar(128) | 显示名 |
| `biz_name` | varchar(128) | 业务名 |
| `expr` | text | 度量表达式 |
| `agg` | varchar(32) | 默认聚合 |
| `data_type` | varchar(64) | 数据类型 |
| `alias` | jsonb | 别名 |
| `description` | text | 描述 |
| `type_params` | jsonb | 类型参数 |
| `status` | int | 状态 |
| `created_at` / `updated_at` | timestamp | 时间字段 |

唯一索引：

```text
ux_headless_model_measure_biz_name(oid, model_id, biz_name)
idx_headless_model_measure_status(oid, model_id, status)
```

作用：

- 把“度量”从 `model_detail.measures` 中提出来。
- 指标可以稳定引用度量 ID。
- 前端可以做“基于度量批量创建指标”。
- SQL Compiler 可以直接读取度量表达式和聚合方式。

### 6.5 headless_metric

建议保留当前表，补充字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `measure_id` | bigint | 来源度量 ID，按度量定义时使用 |
| `field_id` | bigint | 来源字段 ID，按字段定义时使用 |
| `expr` | text | 指标表达式快照 |
| `filter_sql` | text | 指标级过滤条件 |
| `fields` | jsonb | 指标依赖字段集 |
| `metric_refs` | jsonb | 派生指标引用的其他指标 ID |
| `version` | bigint | 指标口径版本 |
| `quality_status` | varchar(32) | `VALID` / `INVALID` / `WARNING` |
| `quality_message` | text | 质量检查说明 |

目标 `type_params` 格式：

```json
{
  "metricDefineType": "MEASURE",
  "metricDefineByMeasureParams": {
    "measureId": 1,
    "measures": [
      {
        "id": 1,
        "bizName": "pay_amount",
        "expr": "pay_amount",
        "agg": "SUM"
      }
    ],
    "expr": "pay_amount",
    "filterSql": ""
  }
}
```

定义方式约束：

| `define_type` | 说明 |
| --- | --- |
| `MEASURE` | 基于模型度量定义 |
| `FIELD` | 基于模型字段定义 |
| `METRIC` | 基于已有指标定义 |
| `EXPRESSION` | 手写表达式定义 |

改造原则：

- `type_params` 保留完整定义。
- `fields` 持久化 SQL Compiler 运行时所需字段集。
- `measure_id`、`field_id`、`metric_refs` 用于影响分析。

### 6.6 headless_dimension

建议保留当前表，补充字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `field_id` | bigint | 来源字段 ID |
| `field_name` | varchar(128) | 来源字段名 |
| `is_primary_key` | boolean | 是否主键维度 |
| `is_default_time` | boolean | 是否默认时间维度 |
| `time_granularities` | jsonb | 支持的时间粒度 |
| `value_type` | varchar(64) | 维值类型 |
| `value_source_type` | varchar(32) | `MANUAL` / `QUERY` / `AUTO` |
| `value_query_sql` | text | 维值自动发现 SQL |

改造原则：

- 维度表达式仍使用 `expr`。
- 维度字段来源通过 `field_id` 或 `field_name` 明确。
- 时间维度规则不再只依赖 `type_params`。
- `dim_value_maps` 保留为兼容字段，但新逻辑优先读 `headless_dimension_value`。

### 6.7 headless_dimension_value

新增表：维度值表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `dimension_id` | bigint | 所属维度 |
| `model_id` | bigint | 冗余模型 ID |
| `value` | varchar / text | 技术值，用于 SQL |
| `display_value` | varchar / text | 展示值 |
| `biz_name` | varchar(128) | 业务名 |
| `alias` | jsonb | 维值别名 |
| `description` | text | 描述 |
| `source_type` | varchar(32) | `MANUAL` / `QUERY` / `AUTO` |
| `frequency` | bigint | 出现频次 |
| `enabled` | boolean | 是否启用 |
| `status` | int | 状态 |
| `created_at` / `updated_at` | timestamp | 时间字段 |

索引：

```text
idx_headless_dimension_value_dimension(oid, dimension_id, status)
idx_headless_dimension_value_value(oid, dimension_id, value)
```

作用：

- 支撑“女 / 女性 / female -> 女”的归一。
- 支撑维值检索和维值别名维护。
- 支撑后续维值字典离线构建。

### 6.8 headless_dataset

建议保留当前表，补充字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | bigint | 数据集 Schema 版本 |
| `index_version` | bigint | 知识索引版本 |
| `default_model_id` | bigint | 默认模型 |
| `default_time_dimension_id` | bigint | 默认时间维度 |
| `owner` | varchar(128) | 数据集负责人 |

`data_set_detail` 继续保留，用于兼容快照：

```json
{
  "dataSetModelConfigs": [
    {
      "id": 1,
      "includesAll": false,
      "metrics": [1],
      "dimensions": [2]
    }
  ]
}
```

新增规范化子表见下文。

### 6.9 headless_dataset_model_config

新增表：数据集模型配置。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `dataset_id` | bigint | 数据集 |
| `model_id` | bigint | 模型 |
| `includes_all` | boolean | 是否暴露该模型全部资产 |
| `is_default` | boolean | 是否默认模型 |
| `sort_order` | int | 排序 |
| `status` | int | 状态 |
| `created_at` / `updated_at` | timestamp | 时间字段 |

唯一索引：

```text
ux_headless_dataset_model(oid, dataset_id, model_id)
```

作用：

- 明确数据集绑定了哪些模型。
- 支撑一个数据集多个模型。
- 支撑默认模型和多模型 Join 路径选择。

### 6.10 headless_dataset_asset

新增表：数据集暴露资产关系。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `dataset_id` | bigint | 数据集 |
| `model_id` | bigint | 模型 |
| `asset_type` | varchar(32) | `METRIC` / `DIMENSION` / `TERM` / `TAG` |
| `asset_id` | bigint | 资产 ID |
| `is_default` | boolean | 是否默认展示 |
| `sort_order` | int | 排序 |
| `status` | int | 状态 |
| `created_at` / `updated_at` | timestamp | 时间字段 |

唯一索引：

```text
ux_headless_dataset_asset(oid, dataset_id, asset_type, asset_id)
idx_headless_dataset_asset_model(oid, dataset_id, model_id, asset_type, status)
```

作用：

- 明确数据集暴露的指标、维度、术语。
- 支撑影响分析：某指标在哪些数据集中被使用。
- 支撑运行时 Schema 快速构建。

### 6.11 headless_term

建议保留当前表，补充字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `term_type` | varchar(32) | `BUSINESS` / `METRIC_GROUP` / `DIMENSION_GROUP` |
| `priority` | int | 匹配优先级 |
| `example_questions` | jsonb | 示例问题 |

`related_metrics`、`related_dimensions` 保留兼容，新增关系表承载主逻辑。

### 6.12 headless_asset_alias

新增表：统一别名表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `asset_type` | varchar(32) | 资产类型 |
| `asset_id` | bigint | 资产 ID |
| `alias` | text | 别名 |
| `alias_type` | varchar(32) | `MANUAL` / `SYSTEM` / `VALUE` / `TERM` |
| `language` | varchar(32) | 语言 |
| `priority` | int | 优先级 |
| `status` | int | 状态 |

作用：

- 统一管理模型、字段、度量、指标、维度、维值、术语的别名。
- 后续构建词典和 embedding 文本时不用扫描多张表的 JSON。

### 6.13 headless_asset_relation

新增表：统一资产关系表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `source_type` | varchar(32) | 源资产类型 |
| `source_id` | bigint | 源资产 ID |
| `relation_type` | varchar(64) | 关系类型 |
| `target_type` | varchar(32) | 目标资产类型 |
| `target_id` | bigint | 目标资产 ID |
| `weight` | numeric | 权重 |
| `metadata` | jsonb | 扩展信息 |
| `status` | int | 状态 |

关系类型建议：

| 关系类型 | 说明 |
| --- | --- |
| `USES_FIELD` | 指标、维度使用字段 |
| `DEFINED_BY_MEASURE` | 指标由度量定义 |
| `DEFINED_BY_METRIC` | 派生指标引用指标 |
| `ANALYZABLE_BY` | 指标可按维度分析 |
| `BELONGS_TO` | 维值属于维度 |
| `RELATED_TO` | 术语关联指标或维度 |
| `EXPOSED_BY_DATASET` | 资产被数据集暴露 |

作用：

- 替代散落在 JSON 里的关系。
- 支撑影响分析、检索增强、解释链路。

### 6.14 headless_asset_document

新增表：运行时资产检索文档。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 主键 |
| `oid` | bigint | 组织 ID |
| `dataset_id` | bigint | 数据集 |
| `asset_type` | varchar(32) | 资产类型 |
| `asset_id` | bigint | 资产 ID |
| `doc_key` | varchar(128) | 文档唯一键 |
| `title` | text | 标题 |
| `business_text` | text | 业务文本 |
| `technical_text` | text | 技术文本 |
| `alias_text` | text | 别名文本 |
| `search_text` | text | 合并检索文本 |
| `payload` | jsonb | 资产快照 |
| `index_version` | bigint | 索引版本 |
| `embedding_status` | varchar(32) | `PENDING` / `READY` / `FAILED` |
| `embedding_ref` | varchar(256) | 向量库引用 ID |
| `updated_at` | timestamp | 更新时间 |

唯一索引：

```text
ux_headless_asset_document(oid, dataset_id, asset_type, asset_id)
idx_headless_asset_document_dataset(oid, dataset_id, index_version)
```

作用：

- 替代当前较弱的 `headless_schema_index`。
- 支撑后续关键词检索、embedding 召回、调试输出。
- 保存数据集视角下的资产快照，避免运行时反复拼接。

当前 `headless_schema_index` 可以保留作为兼容表，后续逐步迁移到 `headless_asset_document`。

## 7. 目标运行时构建关系

改造后的运行时构建建议如下：

```text
headless_dataset
  -> headless_dataset_model_config
  -> headless_dataset_asset
  -> headless_model
  -> headless_model_field
  -> headless_model_measure
  -> headless_metric
  -> headless_dimension
  -> headless_dimension_value
  -> headless_model_relation
  -> headless_term
  -> headless_asset_relation
  -> DataSetSchema
  -> Ontology
```

构建原则：

1. `DataSetSchema` 只暴露数据集授权范围内的资产。
2. `SchemaElement.fields` 直接来自 `headless_metric.fields` 或 `headless_asset_relation`。
3. `SchemaElement.schema_value_maps` 来自 `headless_dimension_value`。
4. `JoinRelation` 来自 `headless_model_relation`。
5. `models[].fields`、`models[].dimensions`、`models[].measures` 优先来自规范子表，再回写兼容 `model_detail`。

## 8. 迁移策略

### 8.1 第一阶段：补字段，不破坏现有接口

新增字段：

- `headless_domain.description`
- `headless_model.table_name`
- `headless_model.sql_query`
- `headless_model.schema_version`
- `headless_metric.fields`
- `headless_metric.measure_id`
- `headless_metric.field_id`
- `headless_metric.expr`
- `headless_metric.filter_sql`
- `headless_dimension.field_id`
- `headless_dimension.field_name`
- `headless_dimension.is_primary_key`
- `headless_dimension.is_default_time`
- `headless_dimension.time_granularities`
- `headless_dataset.schema_version`
- `headless_dataset.index_version`

这一阶段目标是让现有代码可以继续运行，同时为后续规范表同步做准备。

### 8.2 第二阶段：新增规范化子表

新增表：

- `headless_model_field`
- `headless_model_measure`
- `headless_dimension_value`
- `headless_dataset_model_config`
- `headless_dataset_asset`
- `headless_asset_alias`
- `headless_asset_relation`
- `headless_asset_document`

这一阶段不删除旧 JSON 字段。

### 8.3 第三阶段：从 JSON 回填规范表

回填逻辑：

1. 从 `headless_model.model_detail.fields` 回填 `headless_model_field`。
2. 从 `headless_model.model_detail.measures` 回填 `headless_model_measure`。
3. 从 `headless_dimension.dim_value_maps` 回填 `headless_dimension_value`。
4. 从 `headless_dataset.data_set_detail.dataSetModelConfigs` 回填 `headless_dataset_model_config` 和 `headless_dataset_asset`。
5. 从 `headless_metric.relate_dimensions` 回填 `headless_asset_relation`。
6. 从 `headless_term.related_metrics`、`related_dimensions` 回填 `headless_asset_relation`。
7. 从各资产 `alias` JSON 回填 `headless_asset_alias`。

### 8.4 第四阶段：运行时读取切换

读取优先级：

```text
规范化表
  -> JSON 兼容字段
  -> 空默认值
```

写入策略：

```text
前端保存
  -> 后端写规范化表
  -> 同步回写 JSON 快照
```

这样可以兼容当前前端和已有数据，同时逐步把真实主逻辑迁移到规范表。

### 8.5 第五阶段：弱化旧表职责

`headless_schema_index` 后续可以降级为兼容表，主索引改为：

- `headless_asset_document`
- 后续 embedding collection
- 后续词典 / Trie / 分词索引

## 9. 改造后的表结构总览

| 表 | 类型 | 是否新增 | 说明 |
| --- | --- | --- | --- |
| `headless_domain` | 主资产表 | 否，补字段 | 主题域 |
| `headless_model` | 主资产表 | 否，补字段 | 语义模型 |
| `headless_model_field` | 子资产表 | 是 | 模型字段 |
| `headless_model_measure` | 子资产表 | 是 | 模型度量 |
| `headless_model_relation` | 关系表 | 否，增强 | 模型 Join 关系 |
| `headless_metric` | 主资产表 | 否，补字段 | 指标 |
| `headless_dimension` | 主资产表 | 否，补字段 | 维度 |
| `headless_dimension_value` | 子资产表 | 是 | 维度值 |
| `headless_dataset` | 主资产表 | 否，补字段 | 数据集 |
| `headless_dataset_model_config` | 关系表 | 是 | 数据集绑定模型 |
| `headless_dataset_asset` | 关系表 | 是 | 数据集暴露资产 |
| `headless_term` | 主资产表 | 否，补字段 | 术语 |
| `headless_asset_alias` | 辅助表 | 是 | 统一别名 |
| `headless_asset_relation` | 关系表 | 是 | 统一资产关系 |
| `headless_asset_document` | 运行时表 | 是 | 检索文档和资产快照 |
| `headless_schema_index` | 兼容表 | 否，后续弱化 | 旧文本索引 |

## 10. 实施优先级

### P0：必须先做

1. 补 `headless_domain.description`。
2. 新增 `headless_model_field`。
3. 新增 `headless_model_measure`。
4. 新增 `headless_dimension_value`。
5. 新增 `headless_dataset_model_config`。
6. 新增 `headless_dataset_asset`。
7. 补 `headless_metric.fields`。
8. 修改 `DataSetSchemaBuilder`，优先从规范表构建运行时 Schema。

### P1：支撑后续检索和解释

1. 新增 `headless_asset_alias`。
2. 新增 `headless_asset_relation`。
3. 新增 `headless_asset_document`。
4. 将 `knowledge/rebuild` 从写 `headless_schema_index` 改为写 `headless_asset_document`。
5. 提供资产文档调试接口。

### P2：支撑治理和质量

1. 增加资产版本、质量状态、校验结果。
2. 增加字段变更影响分析。
3. 增加数据集 Schema 版本和索引版本。
4. 增加资产发布、下线、审核字段。

## 11. 风险与兼容

### 11.1 JSON 与规范表双写风险

短期内需要同时维护：

- `model_detail`
- `data_set_detail`
- `type_params`
- 新增规范表

风险是数据不一致。

解决方式：

- 写入入口集中到 Service 层。
- API 不直接散写多张表。
- 每次保存模型、指标、维度、数据集时统一重建 JSON 快照。

### 11.2 历史数据迁移风险

历史数据可能存在：

- `model_detail.fields` 缺失。
- `dataSetModelConfigs` 为空。
- 指标 `type_params` 不符合目标格式。
- 维度 `dim_value_maps` 格式不统一。

解决方式：

- 回填脚本要容错。
- 无法识别的数据写入 `ext.migration_warning`。
- 迁移后提供资产质量检查接口。

### 11.3 前端兼容风险

当前前端仍围绕 `model_detail` 和 `data_set_detail` 组织表单。

解决方式：

- 短期 API 返回结构不变。
- 后端读取规范表后组装成前端需要的 JSON。
- 前端逐步切换为按字段、度量、数据集资产关系接口操作。

## 12. 结论

当前项目已经具备 Semantic 语义资产的主对象，但资产存储还偏“主表 + 大 JSON”，缺少字段、度量、维值、数据集暴露关系、通用资产关系和运行时资产文档的规范化存储。

要真正对齐 Supersonic Semantic，第一步不是直接增强检索或 SQL Compiler，而是先把资产存储补齐：

```text
模型字段稳定化
度量资产稳定化
维值独立化
数据集暴露关系规范化
指标依赖字段持久化
资产关系统一化
运行时检索文档资产化
```

完成这一步后，后续才能稳定实现：

- Supersonic 风格 Schema Mapper。
- Semantic embedding 召回。
- DataSetSchema / Ontology 的稳定构建。
- Semantic SQL Compiler 的规则化 SQL 生成。
- 问答链路中可解释的资产命中和 SQL 生成过程。
