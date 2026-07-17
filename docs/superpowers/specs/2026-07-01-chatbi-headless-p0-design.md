# ChatBI Semantic 单表查询 P0 改造设计

## 1. 背景

首期 20 个问题的真实接口回归结果为：

- 一级意图类型正确：20/20；
- 接口执行成功：8/20；
- SQL 执行结果与标准答案一致：0/20；
- 12 个问题在 SQL 生成阶段失败。

问题主要集中在三个环节：Semantic 资产缺失或定义不准确、意图结果没有形成完整查询计划、SQL 编译直接消费候选资产。

## 2. 目标和范围

本期目标是让当前五张首期业务表稳定支持单表自然语言查询：

1. 档口订单日事实表；
2. 未发订单快照表；
3. 商品库存快照表；
4. 客户交易日事实表；
5. 客户欠款快照表。

本期交付包括：

- 修正当前数据集中的模型粒度、默认业务时间和指标定义；
- 查询计划支持多指标、分组、过滤、时间、排序和 TopN；
- 先锁定主模型，再在模型范围内检索资产；
- SQL 仅使用查询计划明确选择的指标、维度和过滤条件；
- 使用首期 20 问完成真实接口回归。

本期不实现：

- 跨表关联；
- 同比、环比等需要多阶段查询的复杂分析；
- 大模型自由生成并直接执行 SQL；
- 与当前五张表无关的语义资产治理。

## 3. 方案选择

采用“代码与 Semantic 资产同步整改”方案。

不采用只修改代码兼容错误资产的方案，因为它会把资产口径问题固化成工作流特例；不采用大模型自由生成 SQL 的方案，因为首期验证要求结果稳定、可解释并且可以重复回归。

## 4. 架构与数据流

查询链路调整为：

```text
用户问题
  -> 意图理解
  -> 结构化 QueryPlan
  -> 主模型选择
  -> 模型内资产检索
  -> 候选门控/必要交互
  -> 确定性 SQL 编译
  -> SQL 语义校验
  -> 执行与回答
```

候选资产只用于构建 QueryPlan，不得直接进入最终 SQL。

## 5. Semantic 资产设计

### 5.1 模型粒度和默认时间

| 模型 | 业务粒度 | 默认业务时间 |
|---|---|---|
| 档口订单日事实表 | `stat_date + stall_id` | `stat_date` |
| 未发订单快照表 | `snapshot_date + order_id` | `snapshot_date` |
| 商品库存快照表 | `snapshot_date + stall_id + product_id` | `snapshot_date` |
| 客户交易日事实表 | `stat_date + customer_id + stall_id` | `stat_date` |
| 客户欠款快照表 | `snapshot_date + customer_id + stall_id` | `snapshot_date` |

`created_at` 和 `updated_at` 只作为审计字段，不参与默认时间选择和自动分组。

### 5.2 业务指标

补齐或修正以下指标：

- 未发订单数：`COUNT(DISTINCT order_id)`；
- 超时未发订单数：`COUNT(DISTINCT CASE WHEN is_overtime = 1 THEN order_id END)`；
- 负库存商品数：`COUNT(DISTINCT CASE WHEN stock_qty < 0 THEN product_id END)`；
- 30 天未动销商品数：`COUNT(DISTINCT CASE WHEN days_unsold >= 30 THEN product_id END)`；
- 逾期客户数：`COUNT(DISTINCT CASE WHEN overdue_amt > 0 THEN customer_id END)`；
- 欠款客户数：`COUNT(DISTINCT CASE WHEN arrears_amt > 0 THEN customer_id END)`；
- 销售客单价：`SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0)`。

指标必须绑定所属模型；派生指标必须保留完整公式和依赖，不能降级为单个基础度量。

### 5.3 业务过滤语义

统一维护以下业务谓词：

| 业务语义 | SQL 条件 |
|---|---|
| 负库存 | `stock_qty < 0` |
| 超时未发 | `is_overtime = 1` |
| 30 天未动销 | `days_unsold >= 30` |
| 逾期客户 | `overdue_amt > 0` |
| 欠款客户 | `arrears_amt > 0` |

“当前”在快照模型中表示默认业务时间字段的最新快照日期。

## 6. QueryPlan 设计

意图理解必须输出可执行查询计划，而不只是意图类型：

```json
{
  "intent": "ranking_analysis",
  "model": "fct_stall_order_daily",
  "metrics": ["gmv_sale"],
  "dimensions": [
    {"name": "stall_id", "role": "group_by"}
  ],
  "filters": [],
  "time_range": {
    "field": "stat_date",
    "start": "2026-06-01",
    "end_exclusive": "2026-07-01"
  },
  "order_by": [
    {"field": "gmv_sale", "direction": "desc"}
  ],
  "limit": 5
}
```

约束如下：

- `metrics` 是数组，必须保留用户明确要求的全部指标；
- 维度角色分为 `group_by`、`filter` 和 `display`；
- 绝对月份使用左闭右开的时间范围；
- 相对时间基于工作流的统一当前时间计算；
- “最高 N 个”和“最低 N 个”必须生成排序与限制；
- “线上和线下”默认按渠道分组对比，不拼接成单个过滤值；
- 无法唯一确定模型、指标或业务口径时才触发用户交互。

## 7. 主模型选择和资产检索

检索顺序固定为：

1. 根据显式指标所属模型、业务实体词和表粒度选择主模型；
2. 主模型唯一后，只在该模型内检索指标；
3. 只在该模型内检索维度和过滤字段；
4. 根据 QueryPlan 中的角色裁剪候选；
5. 多个高置信候选无法区分时触发交互。

通用词如“ID”“时间”“客户”不能单独触发其他模型字段进入候选集合。

## 8. SQL 编译与校验

确定性编译映射为：

| QueryPlan | SQL |
|---|---|
| `metrics` | 聚合表达式和别名 |
| `group_by`/`display` 维度 | `SELECT` |
| `group_by` 维度 | `GROUP BY` |
| `filters`、`time_range` | `WHERE` |
| `order_by` | `ORDER BY` |
| `limit` | `LIMIT` |

编译前校验：

- 所有资产必须属于同一个主模型；
- 用户要求的指标必须全部存在；
- 时间过滤必须绑定模型默认业务时间；
- 派生指标依赖必须完整；
- 单表查询不得包含其他模型字段。

编译后通过 SQL AST 校验：

- 显式时间要求必须出现在 `WHERE`；
- `GROUP BY` 只能包含 QueryPlan 指定的分组维度；
- 排名查询必须包含 `ORDER BY` 和 `LIMIT`；
- SQL 不得引用主模型之外的表；
- 派生指标的公式必须与资产定义一致。

校验失败时返回可解释错误并回到资产选择或查询计划修复节点，不执行错误 SQL。

## 9. 兼容与数据修改策略

- 不覆盖工作区现有未提交修改；
- 资产修正通过可重复执行的脚本或服务接口完成；
- 修改前读取当前资产，按稳定标识更新，避免重复创建；
- 脚本输出修改摘要，并支持重复执行后结果不变；
- 不修改首期五张业务表中的模拟业务数据。

## 10. 测试策略

所有行为修改采用测试驱动：

1. 先增加失败单元测试；
2. 实现最小修复；
3. 运行相关模块回归；
4. 最后启动真实服务执行 20 问接口回归。

重点测试：

- 多指标不会被门控为一个指标；
- 主模型确定后不会混入其他模型资产；
- 绝对月份和最近 30 天正确绑定默认业务时间；
- 审计时间不会进入自动分组；
- 派生指标正确编译；
- 业务谓词、排序和 TopN 正确生成；
- 单表问题不会要求 Join。

## 11. 验收标准

- 20 个问题均能完成接口调用；
- 一级意图类型正确率为 100%；
- 主模型、指标、维度和过滤完整率为 100%；
- 生成 SQL 可执行率为 100%；
- 单表问题不出现跨模型字段；
- 生成结果与标准答案一致率达到 20/20；
- 现有 ChatBI Workflow 和 Semantic 测试无新增失败。
