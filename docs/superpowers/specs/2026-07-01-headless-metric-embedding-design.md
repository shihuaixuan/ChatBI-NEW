# Headless 指标向量化设计

## 背景

当前 Headless 语义资产在问答检索中主要依赖关键词和文本重叠匹配。为了提升指标召回能力，第一阶段只对 Headless 指标做 embedding，不处理维度、维值和术语。向量化不在资产创建或更新时自动执行，而是由前端按钮手动触发。

## 目标

1. 支持用户在前端手动触发当前数据集的指标向量化。
2. 第一版只向量化指标资产，即 `asset_type = 'METRIC'`。
3. 向量化原始文本只保留业务语义字段，避免技术字段噪声。
4. embedding 结果存入独立表，不写入 `headless_asset_document`。
5. 支持重新 embedding：重建前删除当前数据集已有指标向量，再重新生成。
6. 问答检索中保留现有关键词召回，并将指标 embedding 召回作为补充候选。

## 非目标

1. 第一版不向量化维度、维值、术语。
2. 第一版不支持多批次 active 切换，不保留历史向量版本。
3. 第一版不引入外部向量数据库，继续使用 PostgreSQL + pgvector。
4. 第一版不把 SQL 表达式、字段物理名、聚合方式、`payload` 全量 JSON 放入 embedding 文本。

## 数据模型

新增表：`headless_asset_embedding`。

字段设计：

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `oid` | 租户/组织 ID |
| `dataset_id` | Headless 数据集 ID |
| `asset_type` | 资产类型，第一版只写 `METRIC` |
| `asset_id` | 原始资产 ID，对应 `headless_metric.id` |
| `document_id` | 对应 `headless_asset_document.id`，用于追踪检索文档来源 |
| `embedding_text` | 实际送入 embedding 服务的精简文本 |
| `embedding_text_hash` | 精简文本 hash，用于判断文本是否变化 |
| `embedding` | pgvector 向量 |
| `embedding_provider` | embedding 服务提供方，例如 `siliconflow` 或 `openai_compatible` |
| `embedding_model` | embedding 模型名 |
| `embedding_dim` | 向量维度 |
| `embedding_batch_id` | 本次重建批次 ID，用于排查和审计 |
| `status` | `PENDING`、`PROCESSING`、`SUCCEEDED`、`FAILED` |
| `error_message` | 失败原因 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

唯一索引：

```text
oid + dataset_id + asset_type + asset_id
```

第一版产品语义是“一个数据集当前只保留一套指标向量”，所以不保留同一指标的多模型并存记录。模型变更或用户重新触发时，删除旧记录后重建。

查询索引：

```text
oid + dataset_id + asset_type + status
```

后续如果数据量明显增长，可以在 `embedding` 上增加 pgvector HNSW 或 IVFFlat 索引。

## 向量化文本

指标 embedding 原始文本只保留 3 类字段：

```text
指标名称: 销售额
指标别名: GMV, 成交额
指标说明: 订单实付金额汇总
```

字段来源：

| 文本字段 | 来源 |
| --- | --- |
| 指标名称 | `headless_metric.name`，或 `headless_asset_document.title` 的指标标题 |
| 指标别名 | `headless_metric.alias`，去空、去重后用逗号拼接 |
| 指标说明 | `headless_metric.description`，去除首尾空白 |

清洗规则：

1. 空字段不输出该行。
2. 别名去空、去重。
3. 多余空白归一化。
4. 不输出 SQL、字段名、`biz_name`、聚合方式、技术配置和 JSON 快照。
5. 如果三类字段全部为空，则跳过该指标并记录为 `FAILED` 或 `SKIPPED`。

## 触发方式

前端在 Headless 数据集或指标管理页面提供“向量化指标”按钮。

后端提供手动触发接口：

```text
POST /api/v1/headless/datasets/{dataset_id}/metric-embeddings/rebuild
```

接口行为：

1. 校验当前用户有数据集访问权限。
2. 创建本次 `embedding_batch_id`。
3. 删除当前数据集旧指标向量。
4. 查询当前数据集暴露的指标资产。
5. 为每个指标构造精简 embedding 文本。
6. 调用 embedding 服务生成向量。
7. 写入 `headless_asset_embedding`。
8. 返回处理数量、成功数量、失败数量。

## 重新 embedding 语义

重新 embedding 使用“先删再建”的策略。

删除范围：

```sql
DELETE FROM headless_asset_embedding
WHERE oid = :oid
  AND dataset_id = :dataset_id
  AND asset_type = 'METRIC';
```

该策略保证当前表中只保留最新一次指标向量结果。外部 embedding 服务失败时，可能出现该数据集暂无指标向量的短暂状态；问答检索必须降级到现有关键词匹配。

## 检索策略

问答阶段只对指标引入 embedding 召回：

1. 维度、维值、术语继续使用现有关键词匹配。
2. 指标候选由两路合并：
   - 当前关键词/文本重叠召回。
   - `headless_asset_embedding` 中 `asset_type = 'METRIC'` 且 `status = 'SUCCEEDED'` 的向量 TopK 召回。
3. 合并后按资产去重，保留更高分候选。
4. 最终仍交给现有 `CandidateGate` 判断命中、低置信和歧义。

向量检索条件：

```sql
WHERE oid = :oid
  AND dataset_id = :dataset_id
  AND asset_type = 'METRIC'
  AND status = 'SUCCEEDED'
```

## embedding 服务

第一版需要支持 OpenAI-compatible embedding API，以便接入硅基流动等服务。

建议配置项：

```text
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_API_BASE_URL=<部署时配置的 embedding API 地址>
EMBEDDING_API_KEY=<部署时配置的 embedding API Key>
EMBEDDING_MODEL=<部署时配置的 embedding 模型名>
EMBEDDING_DIMENSION=<部署时配置的向量维度>
```

本地 HuggingFace embedding 可以继续保留，但不作为本次设计的核心路径。

## 错误处理

1. 单个指标 embedding 失败不影响其他指标处理。
2. 失败记录写入 `headless_asset_embedding`，状态为 `FAILED`，并保存 `error_message`。
3. 如果旧向量已删除但新向量全部失败，问答阶段降级到关键词检索。
4. 前端展示成功数量和失败数量，失败详情可通过任务结果接口或列表接口查看。

## 测试范围

后端测试：

1. 构造指标 embedding 文本时只输出指标名称、指标别名、指标说明。
2. 重新 embedding 会先删除当前数据集旧的 `METRIC` embedding。
3. 重建只处理指标，不处理维度、维值、术语。
4. 单个指标失败时其他指标仍可成功写入。
5. 问答检索在没有可用 embedding 时降级到现有关键词匹配。
6. 向量 TopK 结果能合并到指标候选中，并保留现有 CandidateGate 流程。

前端测试：

1. 点击“向量化指标”能触发后端重建接口。
2. 能展示成功数量、失败数量。
3. 接口失败时展示明确错误信息。

## 后续扩展

1. 支持维度、维值向量化。
2. 支持多 embedding 模型版本并存。
3. 引入批次 active 切换，避免先删再建带来的向量空窗期。
4. 增加 pgvector ANN 索引以支持更大规模资产。
